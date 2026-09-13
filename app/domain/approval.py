"""Drafting, approval, and the checks that stand between them.

An approval answers one question precisely: which person authorised
exactly which text to go to exactly which recipient. Everything here
exists to keep that answer unambiguous.

The digest is the mechanism. A reviewer sees a draft and approves the
digest of what they saw. Dispatch recomputes that digest from the stored
row and refuses if it differs, so an approval cannot be carried over to
different content. The digest is taken over the stored values exactly,
with no normalisation, because any difference at all should invalidate
an authorisation rather than be quietly tolerated.

Identity separation is enforced by the database, not here. The runtime
role holds no insert privilege on approvals and the reviewer role holds
none on dispatches, so these functions describe the intended flow while
the privileges make the flow the only one available.
"""

import hashlib
import json
import uuid

import psycopg
from psycopg.types.json import Jsonb

from app.ingestion.core import email_only

APPROVAL_RULES_VERSION = "approval-rules-v1"


class ApprovalRefused(Exception):
    """The approval may not be recorded, and the reason is stated."""


class DraftRefused(Exception):
    """The draft may not be created."""


def content_digest(recipient, subject, body_text, attachments):
    """Digest of exactly what would be sent.

    Canonical JSON with sorted keys, so the same content always produces
    the same digest, and any change to recipient, subject, body or
    attachments produces a different one.
    """
    payload = json.dumps(
        {
            "recipient": recipient,
            "subject": subject,
            "body_text": body_text,
            "attachments": attachments or [],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def digest_of_draft(row):
    """Recompute a stored draft's digest from its own columns."""
    return content_digest(
        row["recipient"], row["subject"], row["body_text"], row["attachments"]
    )


def _fetch_draft(cur, draft_id):
    cur.execute(
        """
        SELECT
            d.id::text,
            d.recipient,
            d.subject,
            d.body_text,
            d.attachments,
            d.content_digest,
            d.proposal_revision_id::text,
            p.case_id::text,
            r.decision_state
        FROM app.draft_messages d
        JOIN app.proposal_revisions r ON r.id = d.proposal_revision_id
        JOIN app.action_proposals p ON p.id = r.proposal_id
        WHERE d.id = %s
        """,
        (draft_id,),
    )
    row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "recipient": row[1],
        "subject": row[2],
        "body_text": row[3],
        "attachments": row[4],
        "stored_digest": row[5],
        "proposal_revision_id": row[6],
        "case_id": row[7],
        "decision_state": row[8],
    }


def create_draft(conn, proposal_revision_id, recipient, subject, body_text,
                 attachments=None):
    """Create the concrete message a reviewer will be asked to approve.

    Runtime role. Writing a draft is not authorising it; the reviewer
    role cannot write drafts and the runtime role cannot approve them.
    """
    attachments = list(attachments or [])
    draft_id = str(uuid.uuid4())

    digest = content_digest(recipient, subject, body_text, attachments)

    try:
        with conn.transaction():
            with conn.cursor() as cur:
                # A draft may only be addressed to someone who has
                # actually written in on this case. Naming a recipient
                # is not the same as that recipient having standing,
                # and a reviewer's attention is not a control.
                known = _case_correspondents(cur, proposal_revision_id)

                if email_only(recipient) not in known:
                    raise DraftRefused(
                        f"{recipient} has never corresponded on this "
                        f"case. Writing to a new address is an escalation "
                        f"a human must arrange, not something a draft may "
                        f"do quietly."
                    )

                cur.execute(
                    """
                    INSERT INTO app.draft_messages (
                        id, proposal_revision_id, recipient, subject,
                        body_text, attachments, content_digest
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        draft_id,
                        proposal_revision_id,
                        recipient,
                        subject,
                        body_text,
                        Jsonb(attachments),
                        digest,
                    ),
                )
    except psycopg.errors.UniqueViolation as exc:
        raise DraftRefused(
            "this proposal revision already has a draft; a different "
            "message requires a new revision"
        ) from exc
    except psycopg.errors.CheckViolation as exc:
        raise DraftRefused(f"draft rejected: {exc.diag.constraint_name}") from exc

    return {"draft_id": draft_id, "content_digest": digest}


def edit_draft(conn, draft_id, reviewer_id, subject, body_text):
    """Author a replacement for a draft. Reviewer role.

    Returns the new draft id and digest, which the reviewer then
    approves exactly as they would the agent's own words.
    """
    from app.agent import steering

    subject = (subject or "").strip()
    body_text = (body_text or "").strip()

    if not subject or not body_text:
        raise DraftRefused(
            "an edited letter needs both a subject and a body"
        )

    problems = steering.inspect_copy(f"{subject}\n{body_text}")

    if problems:
        raise DraftRefused(
            "this wording would not be allowed from the agent, so it is "
            "not allowed from a person either: " + "; ".join(problems)
        )

    new_id = str(uuid.uuid4())

    with conn.transaction():
        with conn.cursor() as cur:
            original = _fetch_draft(cur, draft_id)

            if original is None:
                raise DraftRefused(f"no draft {draft_id}")

            case_id = original["case_id"]

            if not _active_reviewer(cur, reviewer_id):
                raise DraftRefused(
                    "this reviewer is not active and may not author"
                )

            if not _has_active_grant(cur, reviewer_id, case_id):
                raise DraftRefused(
                    "this reviewer holds no active grant on the case, so "
                    "they may not write on it"
                )

            attachments = list(original["attachments"] or [])
            digest = content_digest(
                original["recipient"], subject, body_text, attachments
            )

            cur.execute(
                """
                INSERT INTO app.draft_messages (
                    id, proposal_revision_id, recipient, subject,
                    body_text, attachments, content_digest,
                    authored_by, authored_by_reviewer_id,
                    supersedes_draft_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'REVIEWER', %s, %s
                )
                """,
                (
                    new_id,
                    original["proposal_revision_id"],
                    original["recipient"],
                    subject,
                    body_text,
                    Jsonb(attachments),
                    digest,
                    reviewer_id,
                    draft_id,
                ),
            )

    return {"draft_id": new_id, "content_digest": digest}



def _case_correspondents(cur, proposal_revision_id):
    """Addresses that have actually written in on this case."""
    cur.execute(
        """
        SELECT DISTINCT m.sender_address
        FROM app.inbound_messages m
        JOIN app.action_proposals p ON p.case_id = m.case_id
        JOIN app.proposal_revisions r ON r.proposal_id = p.id
        WHERE r.id = %s
        """,
        (proposal_revision_id,),
    )

    return {email_only(row[0]) for row in cur.fetchall()}


def _case_of_approval(cur, approval_id):
    cur.execute(
        """
        SELECT p.case_id::text
        FROM app.approvals a
        JOIN app.draft_messages d ON d.id = a.draft_message_id
        JOIN app.proposal_revisions r ON r.id = d.proposal_revision_id
        JOIN app.action_proposals p ON p.id = r.proposal_id
        WHERE a.id = %s
        """,
        (approval_id,),
    )
    row = cur.fetchone()

    return row[0] if row else None


def _active_reviewer(cur, reviewer_id):
    cur.execute(
        "SELECT display_name, is_active FROM app.reviewers WHERE id = %s",
        (reviewer_id,),
    )
    return cur.fetchone()


def _has_active_grant(cur, reviewer_id, case_id):
    """Delegated so there is one rule, not two that can drift.

    This function's own query omitted the reviewer's active state and
    relied on each caller checking it separately, which three of them
    did. `access.may_access_case` asks both questions in one statement.
    """
    from app.domain import access

    return access.may_access_case(cur, reviewer_id, case_id)


def record_decision(conn, draft_id, reviewer_id, decision, seen_digest,
                    note=None):
    """Record a reviewer's decision on a draft.

    `seen_digest` is the digest the reviewer was shown. Requiring it
    closes the window between rendering a draft and deciding on it: if
    the content is not what they saw, the decision is refused rather
    than applied to text they never read.

    Reviewer role. The runtime role has no insert privilege here.
    """
    if decision not in ("APPROVED", "REJECTED"):
        raise ApprovalRefused(f"unknown decision {decision!r}")

    approval_id = str(uuid.uuid4())

    with conn.transaction():
        with conn.cursor() as cur:
            draft = _fetch_draft(cur, draft_id)

            if draft is None:
                raise ApprovalRefused(f"draft {draft_id} does not exist")

            reviewer = _active_reviewer(cur, reviewer_id)

            if reviewer is None:
                raise ApprovalRefused(
                    f"reviewer {reviewer_id} does not exist"
                )

            if not reviewer[1]:
                raise ApprovalRefused(
                    f"reviewer {reviewer[0]} is not active"
                )

            if not _has_active_grant(cur, reviewer_id, draft["case_id"]):
                raise ApprovalRefused(
                    f"reviewer {reviewer[0]} holds no active grant on case "
                    f"{draft['case_id']}. A reviewer login is not authority "
                    f"over every case."
                )

            current = digest_of_draft(draft)

            if current != seen_digest:
                raise ApprovalRefused(
                    "the draft has changed since it was shown. Refusing to "
                    "record a decision about text the reviewer did not read."
                )

            try:
                cur.execute(
                    """
                    INSERT INTO app.approvals (
                        id, draft_message_id, reviewer_id, decision,
                        approved_digest, note
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        approval_id,
                        draft_id,
                        reviewer_id,
                        decision,
                        current,
                        note,
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ApprovalRefused(
                    "this draft already carries a live approval; revoke it "
                    "before recording another"
                ) from exc

    return {
        "approval_id": approval_id,
        "decision": decision,
        "approved_digest": current,
    }


def revoke(conn, approval_id, reviewer_id, reason):
    """Withdraw an approval. Reviewer role, and only on a granted case."""
    if not reason or not reason.strip():
        raise ApprovalRefused("a revocation must state a reason")

    with conn.transaction():
        with conn.cursor() as cur:
            case_id = _case_of_approval(cur, approval_id)

            if case_id is None:
                raise ApprovalRefused(
                    f"approval {approval_id} does not exist"
                )

            reviewer = _active_reviewer(cur, reviewer_id)

            if reviewer is None or not reviewer[1]:
                raise ApprovalRefused(
                    f"reviewer {reviewer_id} is absent or inactive"
                )

            if not _has_active_grant(cur, reviewer_id, case_id):
                raise ApprovalRefused(
                    f"reviewer {reviewer[0]} holds no active grant on "
                    f"case {case_id}, so may not withdraw its approvals"
                )

            cur.execute(
                """
                UPDATE app.approvals
                SET revoked_at = now(), revoked_reason = %s
                WHERE id = %s AND revoked_at IS NULL
                """,
                (reason, approval_id),
            )
            affected = cur.rowcount

    if affected == 0:
        raise ApprovalRefused(
            f"approval {approval_id} is absent or already revoked"
        )

    return {"approval_id": approval_id, "revoked": True}
