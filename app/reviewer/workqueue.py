"""Queries for the reviewer's working view.

Separate from `queries.py`, which serves the read-only inspection pages.
These answer the operating questions: what is waiting on me, what
exactly am I being asked to authorise, and what happened to the things I
already decided.
"""

import psycopg

from app import config
from app.domain import actionability


def app_connection():
    """Runtime role: reads, drafting, dispatch."""
    return psycopg.connect(
        **config.database_settings().app_kwargs(), autocommit=True
    )


def reviewer_connection():
    """Reviewer role: may approve, may not send."""
    return psycopg.connect(
        **config.database_settings().reviewer_kwargs(), autocommit=True
    )


def reviewers(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, display_name, email, "
            "professional_qualification, may_verify_knowledge "
            "FROM app.reviewers WHERE is_active ORDER BY display_name"
        )
        return cur.fetchall()


def queue(conn, reviewer_id=None):
    """Cases with a decision outstanding, most recent first.

    `awaiting` is what the reviewer must do next, derived from how far
    the case has travelled: a proposal with no draft, a draft with no
    decision, an approval with no dispatch, or nothing.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (p.case_id)
                    p.case_id,
                    r.id            AS revision_id,
                    r.decision_state,
                    r.summary,
                    r.created_at
                FROM app.proposal_revisions r
                JOIN app.action_proposals p ON p.id = r.proposal_id
                JOIN app.agent_runs a ON a.id = r.run_id
                -- app.domain.actionability.CURRENT_WORK_SQL, and a
                -- check fails if this drifts from it. A proposal from
                -- a run that did not finish is evidence, not a
                -- recommendation: it stays readable on the case with
                -- its run's outcome, but it does not get to be the
                -- newest thing a reviewer is asked to decide. Without
                -- the join the ORDER BY takes whichever revision is
                -- most recent, and a run that died after a tool wrote
                -- is the most recent thing there is. The earlier form
                -- was <> 'FAILED', which still admitted RUNNING and
                -- REFUSED.
                WHERE a.result_state = 'SUCCEEDED'
                ORDER BY p.case_id, r.created_at DESC
            )
            SELECT
                c.id::text,
                c.reference,
                s.service_key,
                l.revision_id::text,
                l.decision_state,
                l.summary,
                d.id::text                                  AS draft_id,
                a.id::text                                  AS approval_id,
                a.decision,
                a.revoked_at,
                x.state                                     AS dispatch_state,
                l.created_at,
                EXISTS (
                    SELECT 1 FROM app.reviewer_case_grants g
                    WHERE g.case_id = c.id
                      AND g.revoked_at IS NULL
                      AND (%s::uuid IS NULL OR g.reviewer_id = %s::uuid)
                )                                           AS granted
            FROM latest l
            JOIN app.cases c ON c.id = l.case_id
            LEFT JOIN app.services s ON s.id = c.service_id
            LEFT JOIN app.draft_messages d
                   ON d.proposal_revision_id = l.revision_id
            LEFT JOIN app.approvals a
                   ON a.draft_message_id = d.id AND a.revoked_at IS NULL
            LEFT JOIN app.dispatches x
                   ON x.approval_id = a.id AND x.state <> 'FAILED'
            ORDER BY l.created_at DESC
            LIMIT 50
            """,
            (reviewer_id, reviewer_id),
        )
        rows = cur.fetchall()

    items = []

    for row in rows:
        (
            case_id,
            reference,
            service_key,
            revision_id,
            decision_state,
            summary,
            draft_id,
            approval_id,
            decision,
            revoked_at,
            dispatch_state,
            created_at,
            granted,
        ) = row

        if dispatch_state:
            awaiting = "sent"
        elif approval_id and decision == "APPROVED":
            awaiting = "send"
        elif approval_id and decision == "REJECTED":
            awaiting = "rejected"
        elif draft_id:
            awaiting = "decision"
        else:
            awaiting = "draft"

        items.append(
            {
                "case_id": case_id,
                "reference": reference,
                "service_key": service_key,
                "revision_id": revision_id,
                "decision_state": decision_state,
                "summary": summary,
                "draft_id": draft_id,
                "approval_id": approval_id,
                "decision": decision,
                "dispatch_state": dispatch_state,
                "created_at": created_at,
                "granted": granted,
                "awaiting": awaiting,
            }
        )

    return items


def draft_for_case(conn, case_id):
    """The newest draft on the case, with its live digest and decision."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                d.id::text,
                d.recipient,
                d.subject,
                d.body_text,
                d.attachments,
                d.content_digest,
                d.created_at,
                d.proposal_revision_id::text,
                a.id::text,
                a.decision,
                a.approved_digest,
                a.note,
                a.revoked_at,
                v.display_name
            FROM app.draft_messages d
            JOIN app.proposal_revisions r
                 ON r.id = d.proposal_revision_id
            JOIN app.action_proposals p ON p.id = r.proposal_id
            LEFT JOIN app.approvals a
                 ON a.draft_message_id = d.id AND a.revoked_at IS NULL
            LEFT JOIN app.reviewers v ON v.id = a.reviewer_id
            WHERE p.case_id = %s
            ORDER BY d.created_at DESC
            LIMIT 1
            """,
            (case_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "draft_id": row[0],
        "recipient": row[1],
        "subject": row[2],
        "body_text": row[3],
        "attachments": row[4],
        "content_digest": row[5],
        "created_at": row[6],
        "revision_id": row[7],
        "approval_id": row[8],
        "decision": row[9],
        "approved_digest": row[10],
        "note": row[11],
        "revoked_at": row[12],
        "decided_by": row[13],
    }


def dispatch_for_approval(conn, approval_id):
    if not approval_id:
        return None

    with conn.cursor() as cur:
        cur.execute(
            "SELECT state, provider_message_id, failure_reason, "
            "completed_at FROM app.dispatches WHERE approval_id = %s "
            "ORDER BY started_at DESC LIMIT 1",
            (approval_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "state": row[0],
        "provider_message_id": row[1],
        "failure_reason": row[2],
        "completed_at": row[3],
    }


def latest_revision(conn, case_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id::text, r.decision_state, r.summary, r.payload,
                   r.missing_predicates, r.cited_unit_ids,
                   r.requires_professional_verification, r.run_id::text,
                   a.result_state
            FROM app.proposal_revisions r
            JOIN app.action_proposals p ON p.id = r.proposal_id
            JOIN app.agent_runs a ON a.id = r.run_id
            -- app.domain.actionability.CURRENT_WORK_SQL, and a check
            -- fails if this drifts from it. The case page offers the
            -- actions, so this is the selection that decides whether a
            -- half-finished run can be drafted from.
            WHERE p.case_id = %s AND a.result_state = 'SUCCEEDED'
            ORDER BY r.created_at DESC
            LIMIT 1
            """,
            (case_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "revision_id": row[0],
        "decision_state": row[1],
        "summary": row[2],
        "payload": row[3] or {},
        "missing_predicates": list(row[4] or []),
        "cited_unit_ids": list(row[5] or []),
        "requires_verification": row[6],
        "run_id": row[7],
        "run_state": row[8],
        # Always true given the filter above. Returned anyway so the
        # callers read the answer rather than assuming one, and so the
        # assumption is visible if the filter is ever relaxed.
        "actionable": actionability.run_is_actionable(row[8]),
    }
