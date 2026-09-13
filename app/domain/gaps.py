"""What Anika could not answer, recorded so a person can answer it.

The decision layer already computes why an enquiry cannot be completed
and writes that onto the proposal revision. Until now nothing turned
that computation into a request for help, so an enquiry that needed a
professional simply stopped, and no record said what was needed, of
whom, or whether anyone had responded. Contract section 3 requires the
record; the product's whole claim requires it to be visible.

Three decisions here are load-bearing.

**A gap records every reason, not only the winning one.** The six
decision states have a strict precedence, so exactly one of them decides
what the agent *does*. That is right for behaviour and wrong for a
request for help: an enquiry can genuinely be missing a client fact
*and* be short of approved guidance, and a reviewer who is told only the
higher-precedence reason will answer half the question and believe they
are done. Section 3 asks for multiple reasons in one request, so every
reason the recorded evidence supports is stored, with the precedence
winner always among them.

**Deduplication is by substance, never by wording.** The key digests
what is actually missing -- the reasons, the predicates, the uncovered
topics, the conflicting units -- and deliberately excludes the question
text, so rephrasing the same gap does not create a second one. It also
excludes the case id, because the database already carries that in
`UNIQUE (case_id, dedupe_key)`: keeping the case out of the digest and
in the constraint means a bug in key generation still cannot merge two
clients who asked the same thing, which section 3 forbids outright.

**Nothing here invents a reviewer.** An unassigned gap is an honest
state and is better than naming someone who never agreed to be
responsible. The assignee comes from an existing, unrevoked case grant
or is left null.
"""

import hashlib
import json
import uuid

from app.domain import decision

# Derived, not restated. SUPPORTED_WITHIN_POLICY is the state in which
# nothing is missing, so it can never be a reason to ask for help; the
# database rejects it too, and one definition is easier to keep true
# than two.
GAP_REASONS = tuple(
    state
    for state in decision.PRECEDENCE
    if state != decision.SUPPORTED_WITHIN_POLICY
)


def reasons_for(evaluation, state):
    """Every reason the recorded evidence supports, precedence winner first.

    Each branch reads a field the evaluation already persisted, so a
    reason can never be asserted here that the decision layer did not
    establish.

    Both the chosen state and the recommended one are included, and that
    is not redundant. The agent may choose any state the layer permits,
    which can be lower in precedence than the recommendation: an enquiry
    whose real problem is scope can be filed as a missing fact, both
    permitted, and a reviewer told only "a fact is missing" would answer
    the wrong question and believe they were finished.
    """
    found = []

    if state in GAP_REASONS:
        found.append(state)

    if evaluation.recommended_state in GAP_REASONS:
        found.append(evaluation.recommended_state)

    if evaluation.system_failure_reason:
        found.append(decision.SYSTEM_FAILURE)

    if evaluation.out_of_scope_topics:
        found.append(decision.OUT_OF_SCOPE)

    if evaluation.conflicts:
        found.append(decision.SOURCE_CONFLICT)

    if evaluation.coverage_gaps:
        found.append(decision.MISSING_KNOWLEDGE)

    if evaluation.missing_predicates:
        found.append(decision.MISSING_FACTS)

    # Ordered by precedence so the most serious reason reads first, and
    # de-duplicated without losing that order.
    ordered = [r for r in GAP_REASONS if r in found]

    return tuple(ordered)


def dedupe_key(reasons, evaluation):
    """A digest of what is missing, not of how it was phrased."""
    substance = {
        "reasons": sorted(reasons),
        "missing_predicates": sorted(evaluation.missing_predicates),
        "coverage_gaps": sorted(evaluation.coverage_gaps),
        "out_of_scope_topics": sorted(evaluation.out_of_scope_topics),
        "conflict_unit_ids": sorted(
            str(u) for u in evaluation.conflict_unit_ids
        ),
    }

    encoded = json.dumps(substance, sort_keys=True, separators=(",", ":"))

    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def question_for(evaluation, reasons, hints=None, absent=None):
    """The precise unanswered question, built only from recorded facts.

    Section 3 forbids leaving a placeholder unresolved, so every clause
    below names something the evaluation actually holds. A reason with
    nothing recorded against it contributes no sentence rather than an
    empty one.
    """
    hints = hints or {}
    parts = []

    # `absent` is the set of topics a caller has *proven* the firm holds
    # nothing on. Absent it, no claim about the corpus is made.

    if decision.SYSTEM_FAILURE in reasons:
        parts.append(
            "An operation failed and the enquiry could not be assessed: "
            + (
                evaluation.system_failure_reason
                or "the failure was not recorded in detail."
            )
        )

    if decision.OUT_OF_SCOPE in reasons:
        # Two different problems wear this one state, and they need
        # different sentences. `decision.evaluate` permits OUT_OF_SCOPE
        # either because declared topics were declined, in which case it
        # can name them, or because nothing matched the service at all,
        # in which case there is nothing to name and the enquiry needs
        # triage rather than a scope explanation.
        #
        # Writing only the first branch produced "This enquiry asks
        # about , which is outside the service Anika is trained on."
        # for the second. Not empty, so nothing rejected it, and that is
        # what made it dangerous: it reported a declined topic to a
        # reviewer whose actual problem was that the enquiry matched
        # nothing and needed triage. Every branch below therefore has a
        # sentence it can produce with nothing recorded against it.
        if evaluation.out_of_scope_topics:
            topics = ", ".join(sorted(evaluation.out_of_scope_topics))
            parts.append(
                f"This enquiry asks about {topics}, which is outside "
                f"the service Anika is trained on."
            )
        else:
            parts.append(
                "No declared topic matched this enquiry, so it needs "
                "human triage rather than an answer."
            )

    if decision.SOURCE_CONFLICT in reasons:
        count = len(evaluation.conflicts) or len(
            evaluation.conflict_unit_ids
        )
        parts.append(
            f"{count} piece(s) of approved guidance disagree on this "
            f"point and the conflict has not been resolved."
            if count
            else "Approved guidance is in conflict on this point and "
            "the conflict has not been resolved."
        )

    if decision.MISSING_KNOWLEDGE in reasons:
        # Three different problems, three different actions, and only
        # one of them is a teaching request.
        #
        # `provisional_topics` is where material exists and has not been
        # signed off. That needs verification of what is already
        # recorded, not new teaching, and sending a teaching request for
        # it invites the fair criticism that the learning loop
        # manufactures gaps out of an unverified corpus.
        #
        # The remainder splits on evidence. A topic in `proven_absent`
        # has been checked against the corpus and is genuinely not
        # there, so a professional has to establish the rule. A topic
        # that has not been checked gets the narrower statement, because
        # `coverage_gaps` only proves that no verified applicable unit
        # survived this retrieval -- a rule can exist, be verified, and
        # be excluded for not applying to this client.
        proven_absent = set(absent or ())
        provisional = set(evaluation.provisional_topics)
        uncovered = set(evaluation.coverage_gaps) - provisional

        missing_entirely = sorted(uncovered & proven_absent)
        unavailable = sorted(uncovered - proven_absent)
        unverified = sorted(provisional)

        if missing_entirely:
            parts.append(
                f"The firm has no recorded guidance on: "
                f"{', '.join(missing_entirely)}. Establishing the rule "
                f"needs a professional."
            )

        if unavailable:
            parts.append(
                f"No verified applicable guidance is currently "
                f"available to this case on: {', '.join(unavailable)}. "
                f"Whether the firm holds anything on it has not been "
                f"established here."
            )

        if unverified:
            parts.append(
                f"The firm holds guidance on {', '.join(unverified)} "
                f"that no professional has verified, so it cannot "
                f"support an answer. This needs verification of what is "
                f"already recorded, not new teaching."
            )

        if not (missing_entirely or unavailable or unverified):
            parts.append(
                "The approved guidance available to this case does not "
                "establish an answer."
            )

    if decision.MISSING_FACTS in reasons:
        if evaluation.missing_predicates:
            asked = [
                hints.get(predicate) or predicate
                for predicate in sorted(evaluation.missing_predicates)
            ]
            parts.append(
                "These facts about the client are not yet established: "
                + "; ".join(asked)
            )
        else:
            parts.append(
                "A fact this enquiry turns on has not been established."
            )

    return "\n\n".join(parts)


def corpus_absent(cur, service_id, topics):
    """Topics this service has no unit on anywhere, proven by query.

    Corpus-level means every release, not the active one, so this reads
    `app.knowledge_units` rather than the active view.

    The runtime role holds no privilege of any kind on that table -- by
    design, and proven at runtime when a read raised permission denied.
    So the privilege is checked first and an empty set is returned when
    the caller cannot see the corpus. That is not a fallback: a caller
    with no visibility has no evidence, and `question_for` then says
    only what this retrieval established. Claiming absence from a table
    you cannot read is exactly the inference this function exists to
    replace.

    `has_table_privilege` is asked rather than catching the error,
    because a failed statement would abort the surrounding transaction
    and the gap write shares it.
    """
    wanted = [str(topic) for topic in topics or ()]

    if not wanted:
        return frozenset()

    # Without a service there is nothing to scope the corpus to, and the
    # NOT EXISTS below would match nothing and report every topic as
    # proven absent -- the strongest claim this function can make,
    # produced by an argument not being passed. An unknown service
    # proves nothing.
    if not service_id:
        return frozenset()

    cur.execute(
        "SELECT has_table_privilege("
        "current_user, 'app.knowledge_units', 'SELECT')"
    )

    if not cur.fetchone()[0]:
        return frozenset()

    cur.execute(
        """
        SELECT t.topic
        FROM unnest(%s::text[]) AS t(topic)
        WHERE NOT EXISTS (
            SELECT 1
            FROM app.knowledge_units u
            JOIN app.knowledge_releases r ON r.id = u.release_id
            WHERE u.topic = t.topic
              AND r.service_id = %s
        )
        """,
        (wanted, service_id),
    )

    return frozenset(row[0] for row in cur.fetchall())


def fact_hints(cur, service_id):
    """Reviewer-facing wording for each required fact, from the service."""
    cur.execute(
        "SELECT predicate, prompt_hint FROM app.service_required_facts "
        "WHERE service_id = %s",
        (service_id,),
    )

    return {row[0]: row[1] for row in cur.fetchall() if row[1]}


def responsible_reviewer(cur, case_id):
    """The reviewer already granted this case, or nobody.

    A grant is the only evidence that a person accepted responsibility
    for a case, so it is the only thing consulted. Preferring a reviewer
    qualified to verify knowledge is a convenience for the common
    ordering, not a claim that an unqualified grantee would do.
    """
    cur.execute(
        """
        SELECT g.reviewer_id::text
        FROM app.reviewer_case_grants g
        JOIN app.reviewers r ON r.id = g.reviewer_id
        WHERE g.case_id = %s
          AND g.revoked_at IS NULL
          AND r.is_active
        ORDER BY r.may_verify_knowledge DESC, g.granted_at ASC
        LIMIT 1
        """,
        (case_id,),
    )
    row = cur.fetchone()

    return row[0] if row else None


def record(
    cur,
    case_id,
    revision_id,
    evaluation,
    state,
    hints=None,
    service_id=None,
):
    """Persist the gap. Returns its id, or None if it already existed.

    None is the deduplication working, not a failure: the same gap
    arising again on the same case is one gap that has not been answered
    yet, and a second row would present it as two outstanding requests.
    """
    reasons = reasons_for(evaluation, state)

    if not reasons:
        return None

    # The corpus query runs here because this is where a cursor exists.
    # What it can prove depends on the caller's privileges, and that is
    # the point: the claim is scoped to the evidence available.
    proven_absent = corpus_absent(
        cur, service_id, evaluation.coverage_gaps
    )

    question = question_for(evaluation, reasons, hints, proven_absent)

    if not question.strip():
        # Refuse rather than write a gap that cannot state its question.
        # The database would reject it anyway; failing here names why.
        raise ValueError(
            f"gap for case {case_id} has reasons {reasons} but nothing "
            f"recorded to ask about"
        )

    gap_id = str(uuid.uuid4())

    cur.execute(
        """
        INSERT INTO app.knowledge_gaps (
            id, case_id, revision_id, reason_codes, question,
            assigned_reviewer_id, dedupe_key, gap_state
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN')
        ON CONFLICT (case_id, dedupe_key) DO NOTHING
        RETURNING id::text
        """,
        (
            gap_id,
            case_id,
            revision_id,
            json.dumps(list(reasons)),
            question,
            responsible_reviewer(cur, case_id),
            dedupe_key(reasons, evaluation),
        ),
    )

    row = cur.fetchone()

    return row[0] if row else None
