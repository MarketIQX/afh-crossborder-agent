"""The request for help, written out for the person who has to answer it.

Contract section 3 specifies this letter almost line by line, and the
specification is stricter than it first looks: every bracketed field is
a *template* field, and replacing one with an invented value is worse
than leaving it marked unknown. A reviewer who cannot tell which facts
Nicole actually holds cannot tell which part of the answer is theirs.

So nothing here is composed. Every line is read from a stored row -- the
gap, the revision that caused it, the facts on the case, the release and
units that were consulted -- and anything absent says so in words rather
than being quietly omitted. An empty section and a missing section look
identical to a reader, and only one of them is honest.

Two things the contract is explicit about and this module refuses to do.

It does not send. Rendering a draft is not dispatching it, and neither
the letter nor the link it carries confers any authority: a reply to
this mail cannot publish knowledge, and only an authenticated action in
the application can. The letter says so itself, because the person
reading it is the one most likely to assume otherwise.

It does not attach client records. Financial detail stays in the
protected case store and the letter points at it, so a request for help
does not become a second, less protected copy of someone's affairs.
"""

from app.domain import decision

# The reason codes, in the words a professional would use. The stored
# codes stay as they are -- they are the decision layer's vocabulary and
# the database checks them -- but a letter is not the place for them.
REASON_WORDING = {
    decision.SYSTEM_FAILURE: (
        "an operation failed, so the enquiry could not be assessed"
    ),
    decision.OUT_OF_SCOPE: (
        "the enquiry falls outside the service Nicole is trained on"
    ),
    decision.SOURCE_CONFLICT: (
        "approved sources disagree and the conflict is unresolved"
    ),
    decision.MISSING_KNOWLEDGE: (
        "no approved, applicable guidance covers the point"
    ),
    decision.MISSING_FACTS: (
        "a fact the answer turns on has not been established"
    ),
}

NOTHING_IDENTIFIED = "None identified."


def _rows(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchall()


def assemble(cur, gap_id, console_base=""):
    """Everything the letter needs, read from stored rows."""
    gap = _rows(
        cur,
        """
        SELECT g.id::text, g.question, g.reason_codes, g.gap_state,
               c.id::text, c.reference, v.id::text, v.summary,
               v.decision_state, v.knowledge_release_id::text,
               v.cited_unit_ids, v.missing_predicates, c.service_id::text,
               g.assigned_reviewer_id::text
        FROM app.knowledge_gaps g
        JOIN app.cases c ON c.id = g.case_id
        JOIN app.proposal_revisions v ON v.id = g.revision_id
        WHERE g.id = %s
        """,
        (gap_id,),
    )

    if not gap:
        return None

    (
        gap_id,
        question,
        reason_codes,
        gap_state,
        case_id,
        reference,
        revision_id,
        summary,
        decision_state,
        release_id,
        cited_unit_ids,
        missing_predicates,
        service_id,
        reviewer_id,
    ) = gap[0]

    reviewer = None

    if reviewer_id:
        found = _rows(
            cur,
            "SELECT display_name, email, professional_qualification "
            "FROM app.reviewers WHERE id = %s",
            (reviewer_id,),
        )
        reviewer = found[0] if found else None

    # Every assertion keeps its own provenance. Status, origin, the
    # operation that recorded it and when are all part of what a
    # reviewer needs, and none of them can be reconstructed from a
    # collapsed value.
    facts = _rows(
        cur,
        """
        SELECT predicate, value_text, status, origin, evidence_refs,
               run_id::text, created_at
        FROM app.case_facts
        WHERE case_id = %s
        ORDER BY predicate, created_at
        """,
        (case_id,),
    )

    hints = _rows(
        cur,
        "SELECT predicate, prompt_hint FROM app.service_required_facts "
        "WHERE service_id = %s",
        (service_id,),
    )

    release = None

    if release_id:
        found = _rows(
            cur,
            "SELECT version, status FROM app.knowledge_releases "
            "WHERE id = %s",
            (release_id,),
        )
        release = found[0] if found else None

    unit_ids = list(cited_unit_ids or [])
    units = []

    if unit_ids:
        # The view, not the table. The runtime role holds no privilege of
        # any kind on `app.knowledge_units` -- reading it here raised
        # permission denied, which is the separation of powers working
        # rather than a configuration slip. `app.active_knowledge_units`
        # is what production retrieval reads, and it is restricted to
        # active releases.
        units = _rows(
            cur,
            """
            SELECT unit_key, topic, source_locator, verification_status,
                   effective_from
            FROM app.active_knowledge_units
            WHERE id = ANY(%s::uuid[])
            ORDER BY topic, unit_key
            """,
            (unit_ids,),
        )

    return {
        "gap_id": gap_id,
        "gap_state": gap_state,
        "question": question,
        "reason_codes": list(reason_codes or []),
        "case_id": case_id,
        "reference": reference,
        "revision_id": revision_id,
        "summary": summary,
        "decision_state": decision_state,
        "release": release,
        "units": units,
        # A cited unit the active view cannot show is not dropped
        # quietly. It means the release moved on, and a reviewer reading
        # a shorter list than the record cites would be misled about
        # what was consulted.
        "units_cited": len(unit_ids),
        "units_not_visible": max(len(unit_ids) - len(units), 0),
        "facts": facts,
        "hints": {p: h for p, h in hints if h},
        "missing_predicates": list(missing_predicates or []),
        "reviewer": reviewer,
        "link": (
            f"{console_base}/case/{case_id}" if console_base
            else f"/case/{case_id}"
        ),
    }


def _locator(evidence):
    """Evidence as text, whatever shape it was recorded in.

    `evidence_refs` is free-form jsonb. The stub model records objects
    and the live agent has recorded plain strings, so joining the list
    directly raised TypeError on the offline path. Nothing is dropped:
    an unexpected shape is stringified rather than skipped, because a
    reviewer being shown less evidence than exists is the worse failure.
    """
    if not evidence:
        return "no evidence locator"

    parts = []

    for item in evidence:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(
                ", ".join(f"{k}={v}" for k, v in sorted(item.items()))
            )
        else:
            parts.append(str(item))

    return "; ".join(parts) or "no evidence locator"


def _assertion_lines(label, row):
    """One assertion, with the provenance that makes it assessable."""
    _predicate, value, status, origin, evidence, run_id, created = row

    shown = (value or "").strip() or "(no value recorded)"
    source = (origin or "").lower().replace("_", " ")
    when = f"{created:%d %b %Y %H:%M}" if created else "time not recorded"
    operation = f"operation {run_id[:8]}" if run_id else "no operation"

    return [
        f"- {label}",
        f"    value: {shown}",
        f"    recorded: {when}, {source}, {operation}",
        f"    evidence: {_locator(evidence)}",
    ]


def _facts_block(data):
    """What is established, and separately what has merely been claimed.

    Authority decides the ordering here, not time. A professionally
    confirmed fact is established; an agent proposal is a claim, however
    recently it arrived. Presenting the newest assertion as "current"
    let a proposal displace a confirmation on screen while the decision
    layer went on using the confirmed value -- two surfaces disagreeing
    about one case, which is worse than either being merely incomplete.

    Every assertion is listed with its own status, origin, operation and
    time. Grouping them away would be tidier and would destroy the thing
    a reviewer is being asked to judge.

    Where a predicate carries more than one recorded value, that is
    stated as a count and nothing more. Deciding whether two spellings
    of a country are the same fact is professional judgement, and this
    function has no basis for making it.
    """
    rows = data["facts"]

    if not rows:
        return "None recorded on this case yet."

    confirmed = [row for row in rows if row[2] == "CONFIRMED"]
    proposed = [row for row in rows if row[2] != "CONFIRMED"]

    lines = ["ESTABLISHED FACTS (professionally confirmed)", ""]

    if confirmed:
        for row in confirmed:
            label = data["hints"].get(row[0]) or row[0]
            lines.extend(_assertion_lines(label, row))
    else:
        lines.append(
            "- None. No fact on this case has been professionally "
            "confirmed, so nothing here is established."
        )

    lines.extend(
        ["", "UNCONFIRMED ASSERTIONS (recorded, not established)", ""]
    )

    if not proposed:
        lines.append("- None.")

        return "\n".join(lines)

    values = {}

    for row in proposed:
        values.setdefault(row[0], set()).add((row[1] or "").strip())

    for row in proposed:
        label = data["hints"].get(row[0]) or row[0]
        lines.extend(_assertion_lines(label, row))

        distinct = len(values.get(row[0], ()))

        if distinct > 1:
            lines.append(
                f"    note: this fact has {distinct} different recorded "
                f"values on this case; which applies is a professional "
                f"judgement"
            )

    return "\n".join(lines)


def _missing_block(data):
    if not data["missing_predicates"]:
        return NOTHING_IDENTIFIED

    return "\n".join(
        f"- {data['hints'].get(predicate) or predicate}"
        for predicate in sorted(data["missing_predicates"])
    )


def _guidance_block(data):
    """What was consulted, stated as what it was and no more.

    Section 3 forbids implying an exhaustive search. So this names the
    release and the units that were actually retrieved, and says
    explicitly that it is one collection rather than every source.
    """
    lines = []

    if data["release"]:
        version, status = data["release"]
        lines.append(
            f"- Knowledge release: version {version} ({status})"
        )
    else:
        lines.append("- Knowledge release: none was active for this case")

    if data["units"]:
        for key, topic, locator, verification, effective in data["units"]:
            when = (
                f"effective from {effective}" if effective
                else "no effective date recorded"
            )
            lines.append(
                f"- {topic}: {key}\n"
                f"    source: {locator or 'no locator recorded'}\n"
                f"    verification: {verification}\n"
                f"    {when}"
            )
    else:
        lines.append("- No units were retrieved for this enquiry.")

    if data.get("units_not_visible"):
        lines.append(
            f"- {data['units_not_visible']} of the "
            f"{data['units_cited']} unit(s) this decision cited are no "
            f"longer in the active release and cannot be shown here. "
            f"The decision record still names them."
        )

    lines.append(
        "\nThis is the firm's approved collection for this service only. "
        "It is not a search of every available source."
    )

    return "\n".join(lines)


def _why_block(data):
    """Why Nicole stopped, in the reviewer's language.

    The stored reason codes are the decision layer's vocabulary and stay
    in the record. Here they become sentences, because a letter that
    says MISSING_KNOWLEDGE to a person is the same defect the steering
    guard exists to catch in client copy.
    """
    reasons = [
        REASON_WORDING.get(code, code)
        for code in data["reason_codes"]
    ]

    lines = [f"- {reason}" for reason in reasons]
    lines.append("")
    lines.append(
        f"Nicole's own summary of the position: {data['summary']}"
    )
    lines.append("")
    lines.append(
        "What the available material does not establish is set out "
        "under the question above. Nicole has not answered this part and "
        "has recorded no conclusion about it."
    )

    return "\n".join(lines)


def render(data):
    """The letter. Assembled from `assemble`, never from a model."""
    reviewer_name = "colleague"

    if data["reviewer"]:
        reviewer_name = data["reviewer"][0]

    return f"""Subject: Guidance needed: {data['reference']} | \
{data['decision_state']}

Hello {reviewer_name},

I could not complete the following part of this enquiry using the
approved guidance available to this case:

{data['question']}

Facts currently available:

{_facts_block(data)}

Missing facts or unresolved conflicts:

{_missing_block(data)}

Guidance consulted:

{_guidance_block(data)}

Why I stopped:

{_why_block(data)}

Please provide or verify:
1. The answer suitable for this case.
2. The reusable rule or procedure, if one can safely be generalised.
3. Its primary source or approved operational basis.
4. Applicable jurisdiction, dates, prerequisites and exceptions.
5. Whether it should remain case-specific or enter shared knowledge.

Please review the case in the application:
{data['link']}

Your response will be retained as a teaching candidate. Reuse requires
the applicable verification, publication authorisation and tests.
Replying to this email does not itself publish knowledge, and neither
this message nor its link grants any authority to approve.
"""


def persist(cur, gap_id, body):
    """Store the draft against its gap.

    The runtime holds UPDATE on exactly these two columns and nothing
    else on this table, so rendering a letter cannot alter the question,
    the reasons, the assignee or the gap's state.
    """
    cur.execute(
        "UPDATE app.knowledge_gaps "
        "SET draft_body = %s, draft_rendered_at = now() "
        "WHERE id = %s",
        (body, gap_id),
    )

    return cur.rowcount == 1


def render_and_persist(cur, gap_id, console_base=""):
    """Assemble, render, store. Returns the body, or None if no such gap."""
    data = assemble(cur, gap_id, console_base)

    if data is None:
        return None

    body = render(data)
    persist(cur, gap_id, body)

    return body
