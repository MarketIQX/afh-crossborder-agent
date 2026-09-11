"""Compose the client-facing message a proposal implies.

The agent decides what is needed; this turns that decision into the
actual words a client would read. It is deliberately mechanical: the
model does not write client correspondence, because text that reaches a
client should be predictable and reviewable, not generated afresh each
time.

Not every decision warrants writing to a client. A knowledge gap, a
conflict between sources, an out-of-scope request and a system failure
all need a colleague, not an email to the person who asked. Drafting
refuses those rather than producing a message a reviewer then has to
notice is wrong.
"""

from app.domain import approval as approval_domain

DRAFTING_RULES_VERSION = "drafting-rules-v1"

# States that legitimately result in writing to the client.
CLIENT_FACING = {
    "MISSING_FACTS": "we need information only the client has",
    "SUPPORTED_WITHIN_POLICY": "we can answer within approved guidance",
}

INTERNAL_ONLY = {
    "MISSING_KNOWLEDGE": (
        "this needs a qualified professional, not a message to the client"
    ),
    "SOURCE_CONFLICT": (
        "sources disagree and a professional must resolve which applies"
    ),
    "OUT_OF_SCOPE": (
        "this needs human triage or a decision to decline, not a templated "
        "reply"
    ),
    "SYSTEM_FAILURE": (
        "nothing should be sent on the strength of a failed run"
    ),
}


class DraftingRefused(Exception):
    """This decision does not warrant a message to the client."""


def _load_revision(cur, revision_id):
    cur.execute(
        """
        SELECT
            r.decision_state,
            r.summary,
            r.payload,
            r.missing_predicates,
            p.case_id::text,
            c.reference
        FROM app.proposal_revisions r
        JOIN app.action_proposals p ON p.id = r.proposal_id
        JOIN app.cases c ON c.id = p.case_id
        WHERE r.id = %s
        """,
        (revision_id,),
    )
    row = cur.fetchone()

    if row is None:
        return None

    return {
        "decision_state": row[0],
        "summary": row[1],
        "payload": row[2] or {},
        "missing_predicates": list(row[3] or []),
        "case_id": row[4],
        "reference": row[5],
    }


def _enquiry(cur, case_id):
    cur.execute(
        """
        SELECT sender_address, subject
        FROM app.inbound_messages
        WHERE case_id = %s
        ORDER BY received_at ASC, id ASC
        LIMIT 1
        """,
        (case_id,),
    )
    return cur.fetchone()


def _question_lines(revision):
    """The questions to ask, preferring the agent's own phrasing."""
    requested = revision["payload"].get("requested_information") or []
    lines = []

    for item in requested:
        if not isinstance(item, dict):
            continue

        question = (item.get("question") or "").strip()
        predicate = (item.get("predicate") or "").strip()

        if question:
            lines.append(question)
        elif predicate:
            lines.append(predicate.replace("_", " "))

    if not lines:
        lines = [
            predicate.replace("_", " ")
            for predicate in revision["missing_predicates"]
        ]

    return lines


def compose(conn, revision_id):
    """Build and persist the draft this proposal implies.

    Runtime role. Composing is not authorising: the reviewer role cannot
    write drafts, and this role cannot approve one.
    """
    with conn.cursor() as cur:
        revision = _load_revision(cur, revision_id)

        if revision is None:
            raise DraftingRefused(f"revision {revision_id} does not exist")

        state = revision["decision_state"]

        if state in INTERNAL_ONLY:
            raise DraftingRefused(
                f"{state}: {INTERNAL_ONLY[state]}. No client message is "
                f"drafted."
            )

        if state not in CLIENT_FACING:
            raise DraftingRefused(f"unknown decision state {state!r}")

        enquiry = _enquiry(cur, revision["case_id"])

    if enquiry is None:
        raise DraftingRefused(
            "the case carries no enquiry, so there is no correspondent "
            "to write to"
        )

    recipient, original_subject = enquiry
    subject = original_subject or f"Your enquiry {revision['reference']}"

    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    body = _body_for(state, revision)

    return approval_domain.create_draft(
        conn, revision_id, recipient, subject, body
    )


def _body_for(state, revision):
    reference = revision["reference"]

    if state == "MISSING_FACTS":
        questions = _question_lines(revision)
        listed = "\n".join(f"  {n}. {q}" for n, q in enumerate(questions, 1))

        return (
            "Thank you for getting in touch.\n\n"
            "Before we can advise on your position we need a few details "
            "that only you can confirm:\n\n"
            f"{listed}\n\n"
            "Once we have those we will come back to you with our "
            "assessment.\n\n"
            f"Your reference for this matter is {reference}. Please keep "
            "it in the subject line when you reply.\n\n"
            "Kind regards"
        )

    return (
        "Thank you for getting in touch.\n\n"
        f"{revision['summary']}\n\n"
        "This response has been reviewed before sending. If anything here "
        "does not match your circumstances, please tell us and we will "
        "revisit it.\n\n"
        f"Your reference for this matter is {reference}.\n\n"
        "Kind regards"
    )
