"""Deterministic evaluation of what a case permits.

This module does not decide what to do. It decides what may honestly be
claimed. The model still makes the judgement call, but only inside the
set of states the evidence permits, and the server rejects anything
outside it.

That split matters. A purely deterministic decision would make the
model decorative. A purely model-driven decision would let a confident
answer be produced with missing facts, no sources, or a known conflict
between sources. Here the model has real latitude, for example choosing
between asking the client for facts and escalating a knowledge gap when
both are true, but it cannot claim support that does not exist.

Precedence for the deterministic recommendation, strongest blocker
first:

    SYSTEM_FAILURE
    OUT_OF_SCOPE
    SOURCE_CONFLICT
    MISSING_KNOWLEDGE
    MISSING_FACTS
    SUPPORTED_WITHIN_POLICY

Scope, conflict and coverage are checked before missing client facts on
purpose: there is no point asking a client for information when we
already know we will not, or cannot, advise on the question.

Applicability is evaluated before any of them. A rule can be true,
verified, on topic and in date and still be about a class of person the
client is not; admitting it as evidence and only then checking its
provenance gets the order backwards. So the evidence set is narrowed to
the client first, and what survives is what the states below are
computed from.
"""

from dataclasses import dataclass

from app.domain import applicability

DECISION_RULES_VERSION = "decision-rules-v1"

SYSTEM_FAILURE = "SYSTEM_FAILURE"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
SOURCE_CONFLICT = "SOURCE_CONFLICT"
MISSING_KNOWLEDGE = "MISSING_KNOWLEDGE"
MISSING_FACTS = "MISSING_FACTS"
SUPPORTED_WITHIN_POLICY = "SUPPORTED_WITHIN_POLICY"

PRECEDENCE = (
    SYSTEM_FAILURE,
    OUT_OF_SCOPE,
    SOURCE_CONFLICT,
    MISSING_KNOWLEDGE,
    MISSING_FACTS,
    SUPPORTED_WITHIN_POLICY,
)


class DecisionRefused(Exception):
    """The proposed decision is not supported by the evidence."""


@dataclass(frozen=True)
class Evaluation:
    permitted_states: frozenset
    recommended_state: str

    cited_unit_ids: tuple
    approved_unit_ids: tuple
    conflict_unit_ids: tuple
    missing_predicates: tuple
    coverage_gaps: tuple
    provisional_topics: tuple
    out_of_scope_topics: tuple
    conflicts: tuple

    requires_professional_verification: bool
    knowledge_release_id: str | None
    rationale: tuple
    system_failure_reason: str | None

    # Applicability, recorded rather than merely applied. A reviewer
    # seeing MISSING_KNOWLEDGE deserves to know whether we hold nothing
    # on the topic or hold something that does not apply here.
    excluded_unit_ids: tuple = ()
    unknown_unit_ids: tuple = ()
    applicability_unresolved: tuple = ()
    applicability_unreadable: tuple = ()
    case_attributes: dict = None

    rules_version: str = DECISION_RULES_VERSION

    def citations_for(self, state):
        """Citations that make the state honest, and pass the DB checks."""
        if state == SOURCE_CONFLICT:
            return self.conflict_unit_ids

        if state == SUPPORTED_WITHIN_POLICY:
            # Only professionally verified units may be
            # offered as the basis of a supported answer.
            return self.approved_unit_ids

        # Other states may still cite what was consulted, which is what
        # lets a reviewer see the agent looked before saying it could
        # not answer.
        return self.cited_unit_ids



def evaluate(context, units, conflicts, failure_reason=None):
    """Work out which decision states the evidence permits."""
    permitted = set()
    rationale = []

    if failure_reason:
        return Evaluation(
            permitted_states=frozenset({SYSTEM_FAILURE}),
            recommended_state=SYSTEM_FAILURE,
            cited_unit_ids=(),
            approved_unit_ids=(),
            conflict_unit_ids=(),
            missing_predicates=(),
            coverage_gaps=(),
            provisional_topics=(),
            out_of_scope_topics=(),
            conflicts=(),
            requires_professional_verification=True,
            knowledge_release_id=context.knowledge_release_id,
            rationale=("retrieval or context failure, not a knowledge gap",),
            system_failure_reason=failure_reason,
        )

    unit_ids = tuple(unit.unit_id for unit in units)

    conflict_unit_ids = tuple(
        sorted(
            {c["unit_id"] for c in conflicts}
            | {c["conflicting_unit_id"] for c in conflicts}
        )
    )

    in_scope = context.in_scope_topics
    out_of_scope = context.out_of_scope_topics

    # Applicability first, and three-valued. The knowledge tool calls
    # this same function, so the two cannot disagree about what
    # applicability means.
    #
    # They can still be judging different units. The tool retrieves with
    # the query the model chose; this layer's units were retrieved with
    # the client's own words. Release, in-scope topics and material date
    # are identical, so only the ranked text arm can differ -- but
    # neither set contains the other. Seeing fewer units blocks, which
    # is safe. Seeing more can cite a unit the model never read, which
    # is a provenance defect rather than a safety one. Recorded in
    # docs/ARCHITECTURE.md rather than papered over here.
    assessment = applicability.assess(units, context.confirmed_facts)

    # The approved-guidance bar. An ACTIVE release is not
    # the same as approved knowledge: a unit counts here
    # only once a qualified professional has verified it.
    # `usable` has already had inapplicable units removed.
    approved = assessment.usable
    approved_ids = tuple(unit.unit_id for unit in approved)

    gaps = tuple(
        topic
        for topic in in_scope
        if topic not in assessment.covered_topics
    )

    provisional_topics = tuple(
        sorted(
            {
                unit.topic
                for unit in assessment.partition.applicable
                + assessment.partition.unknown
                if not unit.professionally_verified
                and unit.topic in gaps
            }
        )
    )

    # UNKNOWN applicability becomes a question, not a silent exclusion.
    # Dropping a resident-rule because residency is unestablished would
    # conceal the fact that residency is exactly what needs
    # establishing; adding the predicate here makes the system ask.
    missing_facts = tuple(
        sorted(
            set(context.missing_material_predicates)
            | set(assessment.unresolved_predicates)
        )
    )

    blocked = False

    if assessment.excluded:
        rationale.append(
            f"{len(assessment.excluded)} retrieved unit(s) do not apply "
            f"to this client and were excluded on: "
            f"{', '.join(assessment.excluded_on)}"
        )

    if assessment.unresolved_predicates:
        rationale.append(
            f"applicability of retrieved guidance cannot be determined "
            f"until these are established: "
            f"{', '.join(assessment.unresolved_predicates)}"
        )

    if assessment.unreadable_predicates:
        # Established, and unreadable to us. Asking again would be
        # asking a reviewer to repeat themselves in words we never told
        # them we needed, so this goes to a professional instead. It is
        # deliberately not added to the missing facts: a predicate
        # already answered is never put to the client twice.
        permitted.add(MISSING_KNOWLEDGE)
        rationale.append(
            f"confirmed but recorded in wording the applicability "
            f"vocabulary cannot read, so a professional must restate it "
            f"or the vocabulary must be extended: "
            f"{', '.join(assessment.unreadable_predicates)}"
        )
        blocked = True

    if context.is_unroutable:
        permitted.add(OUT_OF_SCOPE)
        rationale.append(
            "no declared topic matched the enquiry, so it needs human "
            "triage rather than an answer"
        )
        blocked = True

    if out_of_scope:
        permitted.add(OUT_OF_SCOPE)
        rationale.append(
            f"declined topics present: {', '.join(out_of_scope)}"
        )
        blocked = True

    if conflicts:
        permitted.add(SOURCE_CONFLICT)
        rationale.append(
            f"{len(conflicts)} declared source conflict(s) among the "
            f"retrieved units"
        )
        blocked = True

    if gaps:
        permitted.add(MISSING_KNOWLEDGE)
        rationale.append(
            f"in-scope topics with no professionally verified "
            f"guidance: {', '.join(gaps)}"
        )
        blocked = True

    if context.knowledge_release_id is None:
        permitted.add(MISSING_KNOWLEDGE)
        rationale.append("no active knowledge release for this service")
        blocked = True

    if missing_facts:
        permitted.add(MISSING_FACTS)
        rationale.append(
            f"unconfirmed material facts: {', '.join(missing_facts)}"
        )
        blocked = True

    if not blocked:
        permitted.add(SUPPORTED_WITHIN_POLICY)
        rationale.append(
            "in scope, material facts confirmed, effective guidance "
            "retrieved, no declared conflict"
        )

    recommended = next(
        state for state in PRECEDENCE if state in permitted
    )

    # What an answer may rest on, not what the search returned. A pool
    # containing unverified candidates alongside verified ones is normal
    # once retrieval is wider than an exact topic match.
    requires_verification = not approved

    if requires_verification:
        rationale.append(
            "no professionally verified guidance covers this, so any "
            "client-facing output requires professional sign-off"
        )

    return Evaluation(
        permitted_states=frozenset(permitted),
        recommended_state=recommended,
        cited_unit_ids=unit_ids,
        approved_unit_ids=approved_ids,
        conflict_unit_ids=conflict_unit_ids,
        missing_predicates=missing_facts,
        coverage_gaps=gaps,
        provisional_topics=provisional_topics,
        out_of_scope_topics=out_of_scope,
        conflicts=conflicts,
        requires_professional_verification=requires_verification,
        knowledge_release_id=context.knowledge_release_id,
        rationale=tuple(rationale),
        system_failure_reason=None,
        excluded_unit_ids=tuple(
            unit.unit_id for unit in assessment.excluded
        ),
        unknown_unit_ids=tuple(
            unit.unit_id for unit in assessment.unknown
        ),
        applicability_unresolved=assessment.unresolved_predicates,
        applicability_unreadable=assessment.unreadable_predicates,
        case_attributes=dict(assessment.attributes),
    )


def validate(evaluation, proposed_state):
    """Refuse a decision the evidence does not permit."""
    if proposed_state not in PRECEDENCE:
        raise DecisionRefused(
            f"unknown decision state: {proposed_state!r}"
        )

    if proposed_state not in evaluation.permitted_states:
        permitted = ", ".join(sorted(evaluation.permitted_states))
        raise DecisionRefused(
            f"{proposed_state} is not supported by the evidence. "
            f"Permitted: {permitted}. Because: "
            f"{'; '.join(evaluation.rationale)}"
        )
