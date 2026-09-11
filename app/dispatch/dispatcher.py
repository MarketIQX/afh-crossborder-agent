"""Carry out an approved send, or refuse and say why.

Ordering rule, the same one ingestion uses: the attempt is committed
before the provider is called. If the process dies mid-send there is a
PENDING row showing an attempt was made, which a human can resolve.
Calling first and recording after would lose the fact that anything
happened, which is the worst of the available outcomes.

Every refusal below happens before the provider is touched, so a refused
dispatch cannot have sent anything.
"""

import uuid

import psycopg

from app.domain import approval as approval_domain
from app.dispatch import providers

DISPATCH_RULES_VERSION = "dispatch-rules-v1"


class DispatchRefused(Exception):
    """The send may not proceed. Nothing was sent."""


def _load_approval(cur, approval_id):
    cur.execute(
        """
        SELECT
            a.id::text,
            a.decision,
            a.approved_digest,
            a.revoked_at,
            a.reviewer_id::text,
            d.id::text,
            d.recipient,
            d.subject,
            d.body_text,
            d.attachments,
            p.case_id::text
        FROM app.approvals a
        JOIN app.draft_messages d ON d.id = a.draft_message_id
        JOIN app.proposal_revisions r ON r.id = d.proposal_revision_id
        JOIN app.action_proposals p ON p.id = r.proposal_id
        WHERE a.id = %s
        """,
        (approval_id,),
    )
    row = cur.fetchone()

    if row is None:
        return None

    return {
        "approval_id": row[0],
        "decision": row[1],
        "approved_digest": row[2],
        "revoked_at": row[3],
        "reviewer_id": row[4],
        "draft_id": row[5],
        "recipient": row[6],
        "subject": row[7],
        "body_text": row[8],
        "attachments": row[9],
        "case_id": row[10],
    }


def _check(conn, approval_id):
    """Every reason a send must not happen, evaluated before sending."""
    with conn.cursor() as cur:
        record = _load_approval(cur, approval_id)

    if record is None:
        raise DispatchRefused(f"approval {approval_id} does not exist")

    if record["decision"] != "APPROVED":
        raise DispatchRefused(
            f"approval {approval_id} records {record['decision']}, "
            f"which does not authorise a send"
        )

    if record["revoked_at"] is not None:
        raise DispatchRefused(
            f"approval {approval_id} was revoked at {record['revoked_at']}"
        )

    current = approval_domain.digest_of_draft(record)

    if current != record["approved_digest"]:
        raise DispatchRefused(
            "the draft no longer matches what was approved. The approval "
            "covers specific content and cannot be applied to different "
            "text."
        )

    return record


def dispatch(conn, approval_id, provider, operation_id=None):
    """Send an approved draft exactly once, or refuse.

    `conn` is the runtime role, which can dispatch and cannot approve.
    """
    record = _check(conn, approval_id)

    operation_id = operation_id or f"dispatch-{uuid.uuid4()}"
    dispatch_id = str(uuid.uuid4())

    # Commit the attempt before the provider sees anything, so a crash
    # mid-send leaves evidence rather than silence.
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app.dispatches "
                    "(id, approval_id, operation_id) VALUES (%s, %s, %s)",
                    (dispatch_id, approval_id, operation_id),
                )
    except psycopg.errors.UniqueViolation as exc:
        constraint = getattr(exc.diag, "constraint_name", "") or ""

        if "operation" in constraint:
            raise DispatchRefused(
                f"operation {operation_id} has already been attempted"
            ) from exc

        raise DispatchRefused(
            "this approval already has a dispatch that did not "
            "definitively fail. A send that succeeded, or whose outcome "
            "is unknown, must not be retried."
        ) from exc

    result = provider.send(
        record["recipient"],
        record["subject"],
        record["body_text"],
        record["attachments"],
    )

    _finalise(conn, dispatch_id, result)

    return {
        "dispatch_id": dispatch_id,
        "operation_id": operation_id,
        "state": result.state,
        "provider_message_id": result.provider_message_id,
        "failure_reason": result.failure_reason,
        "case_id": record["case_id"],
        "recipient": record["recipient"],
    }


def _finalise(conn, dispatch_id, result):
    from psycopg.types.json import Jsonb

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE app.dispatches
                SET state = %s,
                    provider_message_id = %s,
                    provider_response = %s,
                    failure_reason = %s,
                    completed_at = now()
                WHERE id = %s
                """,
                (
                    result.state,
                    result.provider_message_id,
                    Jsonb(result.response or {}),
                    result.failure_reason,
                    dispatch_id,
                ),
            )


def outcome(conn, dispatch_id):
    """What actually happened, for a reviewer or an audit."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT state, provider_message_id, failure_reason,
                   started_at, completed_at
            FROM app.dispatches WHERE id = %s
            """,
            (dispatch_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "state": row[0],
        "provider_message_id": row[1],
        "failure_reason": row[2],
        "started_at": row[3],
        "completed_at": row[4],
        "delivered": None,
        "note": (
            "provider acceptance is not proof of delivery"
            if row[0] == providers.SENT
            else None
        ),
    }
