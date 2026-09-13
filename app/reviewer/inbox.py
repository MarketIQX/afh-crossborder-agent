"""What the working day actually looks like.

The console showed one matter, reached by typing its URL. That is a
demonstration, not a tool. A professional opens this between client
calls and needs one question answered: what needs me.

So matters are grouped by what each is waiting on, not by date and not
by status code. The lanes are ordered by who is blocked. Anything
waiting on the reviewer comes first, because that is the only thing they
can act on; anything waiting on a client or on the agent comes after,
because seeing it is useful and doing something about it is not.

Two lanes exist that no status field would have produced. `NEEDS_TRIAGE`
holds matters the router declined, with the reason it declined them, so
a person can see what the machine found ambiguous rather than wondering
why a case is sitting still. `QUARANTINED` holds mail that referenced a
case its sender has no standing on.
"""

NEEDS_TRIAGE = "NEEDS_TRIAGE"
NEEDS_PROFESSIONAL = "NEEDS_PROFESSIONAL"
NEEDS_DECISION = "NEEDS_DECISION"
READY_TO_SEND = "READY_TO_SEND"
QUARANTINED = "QUARANTINED"
AGENT_WORKING = "AGENT_WORKING"
AGENT_BLOCKED = "AGENT_BLOCKED"
WAITING_ON_CLIENT = "WAITING_ON_CLIENT"
CLOSED = "CLOSED"

# Ordered by who is blocked. The reviewer's own work first.
LANES = (
    (NEEDS_DECISION, "Needs your decision"),
    (NEEDS_PROFESSIONAL, "Anika cannot answer this"),
    (READY_TO_SEND, "Approved, not sent"),
    (NEEDS_TRIAGE, "Needs triage"),
    (QUARANTINED, "Quarantined"),
    (AGENT_BLOCKED, "Anika could not finish"),
    (AGENT_WORKING, "Anika is working"),
    (WAITING_ON_CLIENT, "Waiting on the client"),
    (CLOSED, "Closed"),
)

LANE_NOTE = {
    NEEDS_DECISION: "A letter is drafted and waiting for you.",
    NEEDS_PROFESSIONAL: (
        "Anika has no professionally verified guidance for this, "
        "so it was escalated to you rather than answered."
    ),
    READY_TO_SEND: "You approved it. Sending is a separate step.",
    NEEDS_TRIAGE: "Anika would not guess which service applies.",
    QUARANTINED: "The sender has no standing on the case they quoted.",
    AGENT_BLOCKED: (
        "The run broke or declined to proceed. There is no recommendation "
        "to act on, and the attempt is on the case with its outcome."
    ),
    AGENT_WORKING: "Triaged, not yet reasoned about.",
    WAITING_ON_CLIENT: "We asked the client for something.",
    CLOSED: "Sent, or returned and not resent.",
}

# Decision states that are never drafted to a client. Reaching one of
# these is the agent saying, accurately, that the firm has nothing it is
# allowed to tell this person yet.
INTERNAL_ONLY_STATES = frozenset(
    {
        "MISSING_KNOWLEDGE",
        "SOURCE_CONFLICT",
        "OUT_OF_SCOPE",
        "SYSTEM_FAILURE",
    }
)

# One row per matter, with everything the lane needs, assembled in one
# query so a queue of any size costs one round trip.
QUEUE_SQL = """
WITH latest_message AS (
    SELECT DISTINCT ON (case_id)
        case_id,
        sender_address,
        subject,
        received_at,
        correlation_status
    FROM app.inbound_messages
    WHERE case_id IS NOT NULL
    ORDER BY case_id, received_at DESC
),
latest_revision AS (
    SELECT DISTINCT ON (p.case_id)
        p.case_id,
        r.id AS revision_id,
        r.decision_state,
        r.summary,
        r.created_at
    FROM app.action_proposals p
    JOIN app.proposal_revisions r ON r.proposal_id = p.id
    JOIN app.agent_runs a ON a.id = r.run_id
    -- app.domain.actionability.CURRENT_WORK_SQL, and a check fails if
    -- this drifts from it. Only a run that finished and reached a
    -- conclusion may supply one. A proposal written by a run that then
    -- broke, declined, or never ended stays readable on its case; it
    -- does not get to be the newest thing a professional is asked to
    -- act on.
    WHERE a.result_state = 'SUCCEEDED'
    ORDER BY p.case_id, r.created_at DESC
),
latest_run AS (
    -- The most recent attempt, whatever became of it. This is how the
    -- lane can tell a run that broke from one still in flight.
    SELECT DISTINCT ON (case_id)
        case_id,
        result_state,
        failure_reason
    FROM app.agent_runs
    ORDER BY case_id, started_at DESC
),
latest_draft AS (
    -- Keyed on the case, not on the current revision. A letter someone
    -- has already written must not disappear because the filter above
    -- declined the revision it came from.
    SELECT DISTINCT ON (p.case_id)
        p.case_id,
        d.id AS draft_id,
        d.subject AS draft_subject,
        d.recipient,
        d.authored_by,
        d.created_at AS drafted_at
    FROM app.draft_messages d
    JOIN app.proposal_revisions r ON r.id = d.proposal_revision_id
    JOIN app.action_proposals p ON p.id = r.proposal_id
    ORDER BY p.case_id, d.created_at DESC
),
decision AS (
    SELECT DISTINCT ON (ld.case_id)
        ld.case_id,
        a.id AS approval_id,
        a.decision,
        a.note,
        a.revoked_at
    FROM latest_draft ld
    JOIN app.approvals a ON a.draft_message_id = ld.draft_id
    ORDER BY ld.case_id, a.created_at DESC
),
sent AS (
    SELECT DISTINCT ON (d.case_id)
        d.case_id,
        x.state AS dispatch_state
    FROM decision d
    JOIN app.dispatches x ON x.approval_id = d.approval_id
    ORDER BY d.case_id, x.started_at DESC
),
triage AS (
    SELECT DISTINCT ON (case_id)
        case_id, outcome, detail
    FROM app.triage_attempts
    ORDER BY case_id, attempted_at DESC
)
SELECT
    c.id::text,
    c.reference,
    c.service_id IS NOT NULL       AS triaged,
    c.triage_method,
    m.sender_address,
    m.subject,
    m.received_at,
    m.correlation_status,
    lr.decision_state,
    lr.summary,
    ld.draft_id::text,
    ld.draft_subject,
    ld.authored_by,
    d.decision,
    d.note,
    d.revoked_at,
    s.dispatch_state,
    t.outcome                      AS triage_outcome,
    t.detail                       AS triage_detail,
    lrun.result_state              AS run_state,
    lrun.failure_reason            AS run_failure_reason
FROM app.cases c
LEFT JOIN latest_message  m  ON m.case_id  = c.id
LEFT JOIN latest_revision lr ON lr.case_id = c.id
LEFT JOIN latest_run      lrun ON lrun.case_id = c.id
LEFT JOIN latest_draft    ld ON ld.case_id = c.id
LEFT JOIN decision        d  ON d.case_id  = c.id
LEFT JOIN sent            s  ON s.case_id  = c.id
LEFT JOIN triage          t  ON t.case_id  = c.id
ORDER BY m.received_at DESC NULLS LAST, c.created_at DESC
"""


def _lane_for(row):
    """Which lane a matter belongs in, from what is actually true of it.

    Deliberately derived rather than stored. A status column would need
    updating from six places and would eventually disagree with the
    rows it claims to summarise.
    """
    if row["correlation_status"] == "AMBIGUOUS":
        return QUARANTINED

    if not row["triaged"]:
        return NEEDS_TRIAGE

    if row["dispatch_state"] in ("SENT", "SEND_UNKNOWN"):
        return CLOSED

    if row["decision"] == "REJECTED":
        return CLOSED

    if row["decision"] == "APPROVED" and not row["revoked"]:
        return READY_TO_SEND

    if row["draft_id"]:
        return NEEDS_DECISION

    # No current recommendation, and why there is none matters.
    # A run that broke or declined is not a run still thinking,
    # and neither is a case nothing has read yet. Labelling all
    # three the same way tells a professional something untrue.
    if not row["decision_state"]:
        if row["run_state"] in ("FAILED", "REFUSED"):
            return AGENT_BLOCKED

        return AGENT_WORKING

    # States that must never be drafted to a client. The agent reached a
    # conclusion and the conclusion is that a person is needed.
    if row["decision_state"] in INTERNAL_ONLY_STATES:
        return NEEDS_PROFESSIONAL

    if row["decision_state"] == "MISSING_FACTS":
        return WAITING_ON_CLIENT

    return NEEDS_DECISION


def queue(conn):
    """Every matter, in its lane. One query, one pass."""
    with conn.cursor() as cur:
        cur.execute(QUEUE_SQL)
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, values)) for values in cur.fetchall()]

    items = []

    for raw in rows:
        row = {
            "case_id": raw["id"],
            "reference": raw["reference"] or (raw["id"][:8]),
            "triaged": raw["triaged"],
            "triage_method": raw["triage_method"],
            "sender": raw["sender_address"] or "",
            "subject": raw["subject"] or "(no subject)",
            "received_at": raw["received_at"],
            "correlation_status": raw["correlation_status"],
            "decision_state": raw["decision_state"],
            "run_state": raw["run_state"],
            "run_failure_reason": raw["run_failure_reason"] or "",
            "summary": raw["summary"] or "",
            "draft_id": raw["draft_id"],
            "draft_subject": raw["draft_subject"],
            "authored_by": raw["authored_by"],
            "decision": raw["decision"],
            "note": raw["note"] or "",
            "revoked": raw["revoked_at"] is not None,
            "dispatch_state": raw["dispatch_state"],
            "triage_outcome": raw["triage_outcome"],
            "triage_detail": raw["triage_detail"] or "",
        }
        row["lane"] = _lane_for(row)
        items.append(row)

    return items


def grouped(items):
    """The queue as ordered lanes, empty ones dropped."""
    buckets = {key: [] for key, _ in LANES}

    for item in items:
        buckets[item["lane"]].append(item)

    return [
        (key, label, buckets[key]) for key, label in LANES if buckets[key]
    ]


def counts(items):
    """How many matters sit in each lane, for the toolbar."""
    tally = {key: 0 for key, _ in LANES}

    for item in items:
        tally[item["lane"]] += 1

    return tally


def needs_you(items):
    """The number a professional actually cares about."""
    return sum(
        1
        for item in items
        if item["lane"]
        in (
            NEEDS_DECISION,
            READY_TO_SEND,
            NEEDS_TRIAGE,
            NEEDS_PROFESSIONAL,
        )
    )
