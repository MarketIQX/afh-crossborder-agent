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


def _active_reviewer(cur, reviewer_id):
    cur.execute(
        "SELECT display_name, is_active FROM app.reviewers WHERE id = %s",
        (reviewer_id,),
    )
    return cur.fetchone()


def _has_active_grant(cur, reviewer_id, case_id):
    cur.execute(
        """
        SELECT 1 FROM app.reviewer_case_grants
        WHERE reviewer_id = %s AND case_id = %s AND revoked_at IS NULL
        """,
        (reviewer_id, case_id),
    )
    return cur.fetchone() is not None


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


def revoke(conn, approval_id, reason):
    """Withdraw an approval. Reviewer role."""
    if not reason or not reason.strip():
        raise ApprovalRefused("a revocation must state a reason")

    with conn.transaction():
        with conn.cursor() as cur:
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
