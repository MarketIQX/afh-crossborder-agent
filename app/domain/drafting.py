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
            c.reference,
            c.service_id::text
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
        "service_id": row[6],
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


def _prompt_hints(cur, service_id, predicates):
    """The client-facing wording for each predicate, from the database."""
    if not predicates:
        return {}

    cur.execute(
        """
        SELECT predicate, coalesce(prompt_hint, '')
        FROM app.service_required_facts
        WHERE service_id = %s AND predicate = ANY(%s)
        """,
        (service_id, list(predicates)),
    )

    return {row[0]: row[1].strip() for row in cur.fetchall()}


def _question_lines(cur, revision):
    """The questions to ask, in words written for a client.

    Only `prompt_hint` is used. The agent's own phrasing is not trusted
    here even when it is good, because "sometimes the model's words and
    sometimes ours" means nobody can say what a client will receive.

    Raises rather than improvising. A predicate with no hint is a
    five minute fix; a letter asking for
    "days_present_in_india_current_year" is a client deciding the firm
    is careless.
    """
    predicates = [
        p for p in (revision["missing_predicates"] or []) if str(p).strip()
    ]

    if not predicates:
        return []

    hints = _prompt_hints(cur, revision["service_id"], predicates)
    lines = []
    missing = []

    for predicate in predicates:
        hint = hints.get(predicate, "")

        if hint:
            lines.append(hint)
        else:
            missing.append(predicate)

    if missing:
        raise DraftingRefused(
            "these facts have no client-facing wording recorded, so no "
            "letter can ask for them: "
            + ", ".join(sorted(missing))
            + ". Add a prompt_hint in service_required_facts. Nothing "
            "will be improvised from the column name."
        )

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

        # Read the client-facing wording while the cursor is open, and
        # pass it on as data. A refusal here is deliberate: see
        # _question_lines.
        questions = _question_lines(cur, revision)

    if enquiry is None:
        raise DraftingRefused(
            "the case carries no enquiry, so there is no correspondent "
            "to write to"
        )

    recipient, original_subject = enquiry
    subject = original_subject or f"Your enquiry {revision['reference']}"

    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    body = _body_for(state, revision, questions)

    return approval_domain.create_draft(
        conn, revision_id, recipient, subject, body
    )


def _body_for(state, revision, questions):
    reference = revision["reference"]

    if state == "MISSING_FACTS":
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
