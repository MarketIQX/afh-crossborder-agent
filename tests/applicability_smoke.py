"""APPLY01-APPLY14. A rule that is true can still not be yours.

This suite exists because of a failure it now prevents. A test drove
`decision.evaluate` directly, with no model involved: a verified,
on-topic, in-date rule about RESIDENT individuals, a client established
as a NON-RESIDENT, and every gate unrelated to applicability
deliberately passing. The layer permitted SUPPORTED_WITHIN_POLICY, cited
that rule as the basis of the answer, recorded
requires_professional_verification = false, and gave as its rationale
"in scope, material facts confirmed, effective guidance retrieved, no
declared conflict". Every clause of that sentence was true and the
conclusion was wrong.

It was not a missing check. `evaluate` read five context fields and none
of them was a case attribute, so there was no field in which
applicability could be expressed at all.

The fix is three-valued, and the third value is the point:

    TRUE     the rule's conditions match what is established
    FALSE    they contradict it
    UNKNOWN  the rule turns on something nobody has established

A boolean would have to fold UNKNOWN into one of the others and both
choices are wrong. TRUE admits a rule that may not apply. FALSE removes
evidence silently and hides the reason: if residency is unestablished,
dropping every resident-rule conceals the fact that residency is
exactly what needs establishing. So UNKNOWN becomes a question.

No model and no database. That is deliberate. A safe result produced by
Claude is not evidence of a safe architecture; a safe result produced
while Claude is assumed to be wrong is. These checks run the
deterministic layer alone.

Usage:

    python tests/applicability_smoke.py
"""

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.domain import applicability, decision  # noqa: E402
from app.domain.context import CaseContext, Fact  # noqa: E402
from app.domain.knowledge import KnowledgeUnit  # noqa: E402

FAILURES = []
PASSES = []

RELEASE = "20000000-0000-0000-0000-000000000001"

RESIDENT_RULE = (
    "A resident and ordinarily resident individual is liable to Indian "
    "tax on global income wherever earned, including salary earned "
    "outside India."
)


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


def unit(
    unit_id,
    *,
    verified=True,
    topic="tax_residency",
    residency=None,
    citizenship=None,
    statement=RESIDENT_RULE,
):
    """A fixture unit. Nothing here is written to any database."""
    return KnowledgeUnit(
        unit_id=unit_id,
        unit_key=unit_id,
        topic=topic,
        statement=statement,
        source_locator="applicability_smoke fixture, not a real source",
        verification_status=(
            "PROFESSIONALLY_VERIFIED" if verified else "SOURCE_RECORDED"
        ),
        effective_from=date(2020, 4, 1),
        effective_to=None,
        scope_tags=(),
        applies_to_residency=residency,
        applies_to_citizenship=citizenship,
    )


@dataclass(frozen=True)
class StubContext:
    """Exactly the fields `evaluate` reads. Nothing else exists."""

    in_scope_topics: tuple
    out_of_scope_topics: tuple
    is_unroutable: bool
    knowledge_release_id: str
    missing_material_predicates: tuple
    confirmed_facts: dict


def evaluating(units, confirmed=None, missing=()):
    """Every gate except applicability deliberately satisfied."""
    return decision.evaluate(
        StubContext(
            in_scope_topics=("tax_residency",),
            out_of_scope_topics=(),
            is_unroutable=False,
            knowledge_release_id=RELEASE,
            missing_material_predicates=tuple(missing),
            confirmed_facts=dict(confirmed or {}),
        ),
        tuple(units),
        conflicts=(),
    )


# -- the three-valued core --------------------------------------------


def apply01_unrestricted_applies():
    verdict, predicates = applicability.applies(unit("u1"), {})

    check(
        "APPLY01 A RULE THAT DOES NOT RESTRICT APPLIES TO ANYONE",
        verdict == applicability.TRUE and predicates == (),
        f"got {verdict} {predicates}",
    )


def apply02_matching_attribute_applies():
    attributes = applicability.case_attributes(
        {"residency_status": "resident"}
    )
    verdict, _ = applicability.applies(
        unit("u2", residency="RESIDENT"), attributes
    )

    check(
        "APPLY02 A RESIDENT RULE APPLIES TO A CONFIRMED RESIDENT",
        verdict == applicability.TRUE,
        f"got {verdict} from attributes {attributes}",
    )


def apply03_contradiction_excludes():
    attributes = applicability.case_attributes(
        {"residency_status": "non-resident"}
    )
    verdict, predicates = applicability.applies(
        unit("u3", residency="RESIDENT"), attributes
    )

    check(
        "APPLY03 A RESIDENT RULE DOES NOT APPLY TO A NON-RESIDENT",
        verdict == applicability.FALSE
        and predicates == ("residency_status",),
        f"got {verdict} {predicates}",
    )


def apply04_unestablished_is_unknown_not_a_guess():
    verdict, predicates = applicability.applies(
        unit("u4", residency="RESIDENT"), {}
    )

    check(
        "APPLY04 UNESTABLISHED APPLICABILITY IS UNKNOWN AND NAMES THE FACT",
        verdict == applicability.UNKNOWN
        and predicates == ("residency_status",),
        f"got {verdict} {predicates}",
    )


def apply05_uninterpretable_value_is_not_established():
    attributes = applicability.case_attributes(
        {"residency_status": "probably resident, needs checking"}
    )
    verdict, _ = applicability.applies(
        unit("u5", residency="RESIDENT"), attributes
    )

    check(
        "APPLY05 A VALUE NOBODY CAN INTERPRET IS NOT AN ESTABLISHED FACT",
        "residency" not in attributes
        and verdict == applicability.UNKNOWN,
        f"attributes {attributes}, verdict {verdict}",
    )


def apply06_proposed_facts_never_reach_applicability():
    """The boundary that matters most in this file.

    If a fact the model proposed could settle applicability, the model
    would be deciding which rules govern the client by asserting what
    the client is. Only CONFIRMED rows reach the comparison.
    """
    context = CaseContext(
        case_id="00000000-0000-0000-0000-000000000001",
        service_id="00000000-0000-0000-0000-000000000002",
        service_key="nri_india_tax_filing",
        service_name="NRI India tax filing",
        reference="APPLY06",
        enquiry=None,
        material_date=date(2025, 7, 1),
        facts=(
            Fact(
                predicate="residency_status",
                value_text="resident",
                status="PROPOSED",
                origin="AGENT",
            ),
        ),
        confirmed_predicates=frozenset(),
        material_predicates=(),
        missing_material_predicates=(),
        fact_prompts={},
        topic_matches=(),
        knowledge_release_id=RELEASE,
    )

    attributes = applicability.case_attributes(context.confirmed_facts)
    verdict, predicates = applicability.applies(
        unit("u6", residency="RESIDENT"), attributes
    )

    check(
        "APPLY06 A FACT THE AGENT PROPOSED CANNOT SETTLE APPLICABILITY",
        context.confirmed_facts == {}
        and attributes == {}
        and verdict == applicability.UNKNOWN
        and predicates == ("residency_status",),
        f"confirmed {context.confirmed_facts}, verdict {verdict}",
    )


# -- the gate, driven directly, with no model ------------------------


def apply07_q9_supported_is_refused():
    """Q9 Level 1. This is the check the original failure would fail."""
    probe = unit("q9-probe", residency="RESIDENT")
    result = evaluating(
        [probe], confirmed={"residency_status": "non-resident"}
    )

    supported = decision.SUPPORTED_WITHIN_POLICY in result.permitted_states
    cited = set(result.citations_for(decision.SUPPORTED_WITHIN_POLICY))

    check(
        "APPLY07 A NON-APPLICABLE RULE CANNOT SUPPORT AN ANSWER",
        not supported
        and probe.unit_id not in set(result.approved_unit_ids)
        and probe.unit_id not in cited,
        f"permitted {sorted(result.permitted_states)}, "
        f"approved {result.approved_unit_ids}, cited {sorted(cited)}",
    )


def apply08_exclusion_is_recorded_not_just_applied():
    """A reviewer must be able to tell the two cases apart.

    "We hold nothing on this topic" and "we hold something that does
    not apply to you" both end in MISSING_KNOWLEDGE. Only one of them
    means the corpus has a gap, so the record has to say which.
    """
    probe = unit("q9-probe", residency="RESIDENT")
    result = evaluating(
        [probe], confirmed={"residency_status": "non-resident"}
    )

    named = any(
        "do not apply" in line and "residency_status" in line
        for line in result.rationale
    )

    check(
        "APPLY08 THE RECORD SAYS WHAT WAS EXCLUDED AND ON WHAT GROUND",
        result.excluded_unit_ids == (probe.unit_id,)
        and result.recommended_state == decision.MISSING_KNOWLEDGE
        and result.requires_professional_verification
        and named,
        f"excluded {result.excluded_unit_ids}, "
        f"state {result.recommended_state}, "
        f"rationale {result.rationale}",
    )


def apply09_unknown_becomes_a_question_not_a_gap():
    """The reason UNKNOWN is a third value rather than a fold into FALSE.

    Treated as FALSE, this case would report MISSING_KNOWLEDGE: "no
    professionally verified guidance covers this", sending it to a
    professional as a corpus gap. That is false. The guidance exists and
    may well apply. What is missing is a fact, and MISSING_FACTS asks
    for it.
    """
    probe = unit("unknown-probe", residency="RESIDENT")
    result = evaluating([probe])

    check(
        "APPLY09 UNKNOWN APPLICABILITY ASKS FOR THE FACT, NOT FOR A HUMAN",
        decision.SUPPORTED_WITHIN_POLICY not in result.permitted_states
        and result.recommended_state == decision.MISSING_FACTS
        and "residency_status" in result.missing_predicates
        and result.coverage_gaps == ()
        and result.unknown_unit_ids == (probe.unit_id,),
        f"state {result.recommended_state}, "
        f"missing {result.missing_predicates}, "
        f"gaps {result.coverage_gaps}",
    )


def apply10_the_gate_still_lets_a_real_answer_through():
    """A gate that blocks everything proves nothing.

    Without this check the three above could all pass with an
    applicability function that returned FALSE unconditionally.
    """
    good = unit("applies-here", residency="NON_RESIDENT")
    result = evaluating(
        [good], confirmed={"residency_status": "non-resident"}
    )

    check(
        "APPLY10 AN APPLICABLE VERIFIED RULE STILL SUPPORTS AN ANSWER",
        decision.SUPPORTED_WITHIN_POLICY in result.permitted_states
        and result.recommended_state == decision.SUPPORTED_WITHIN_POLICY
        and good.unit_id in set(result.approved_unit_ids)
        and good.unit_id
        in set(result.citations_for(decision.SUPPORTED_WITHIN_POLICY))
        and not result.requires_professional_verification,
        f"permitted {sorted(result.permitted_states)}, "
        f"rationale {result.rationale}",
    )


def apply11_unverified_knowledge_cannot_drive_a_question():
    """Unapproved knowledge must not reach the client, even as a question.

    An unverified candidate can never be the basis of an answer. If it
    could still make the agent ask a client for their residency, the
    corpus would be steering client contact through material no
    professional has signed.
    """
    candidate = unit("unsigned", verified=False, residency="RESIDENT")
    result = evaluating([candidate])

    check(
        "APPLY11 AN UNVERIFIED UNIT DOES NOT DEMAND A FACT FROM THE CLIENT",
        result.missing_predicates == ()
        and result.applicability_unresolved == ()
        and result.recommended_state == decision.MISSING_KNOWLEDGE,
        f"missing {result.missing_predicates}, "
        f"unresolved {result.applicability_unresolved}, "
        f"state {result.recommended_state}",
    )


def apply12_the_server_refuses_the_state_not_just_recommends_against_it():
    """The model still proposes. This is what happens when it proposes
    the wrong thing: `validate` raises, so a confident model cannot
    talk its way past the gate."""
    probe = unit("q9-probe", residency="RESIDENT")
    result = evaluating(
        [probe], confirmed={"residency_status": "non-resident"}
    )

    try:
        decision.validate(result, decision.SUPPORTED_WITHIN_POLICY)
    except decision.DecisionRefused as exc:
        refused = "residency_status" in str(exc)
    else:
        refused = False

    check(
        "APPLY12 THE SERVER REFUSES A SUPPORTED ANSWER AND SAYS WHY",
        refused,
        "validate did not refuse SUPPORTED_WITHIN_POLICY, or refused "
        "without naming the ground",
    )


def apply13_a_contradiction_beats_an_open_question():
    """Kleene conjunction, and it matters which way it resolves.

    A rule restricted on two dimensions where one contradicts and the
    other is unestablished must be FALSE, not UNKNOWN. Getting this
    backwards would ask a client for their citizenship in order to
    decide whether a resident-only rule applies to a confirmed
    non-resident, which it cannot, whatever the answer.
    """
    both = unit("two-dimensions", residency="RESIDENT", citizenship="INDIAN")
    verdict, predicates = applicability.applies(
        both,
        applicability.case_attributes(
            {"residency_status": "non-resident"}
        ),
    )

    result = evaluating(
        [both], confirmed={"residency_status": "non-resident"}
    )

    check(
        "APPLY13 A CONTRADICTION SETTLES IT WITHOUT ASKING THE REST",
        verdict == applicability.FALSE
        and predicates == ("residency_status",)
        and "citizenship_status" not in result.missing_predicates,
        f"verdict {verdict} {predicates}, "
        f"missing {result.missing_predicates}",
    )


def apply14_an_answered_fact_is_never_asked_twice():
    """The confirmation loop this check exists to prevent.

    A reviewer confirms residency_status as "Non Resident Indian". The
    vocabulary has no entry for that exact wording, so the attribute is
    unset, so a resident-only rule is UNKNOWN, so the predicate becomes
    a missing fact, so the next draft asks the client a question they
    have already answered -- and the reviewer watches the system ignore
    them with no explanation.

    Unestablished and unreadable are different failures. The first is a
    question for the client. The second is ours: either the wording is
    restated or the vocabulary gains a row, and both are a
    professional's call, so it escalates and drafts nothing.
    """
    probe = unit("unreadable-probe", residency="RESIDENT")
    result = evaluating(
        [probe],
        confirmed={"residency_status": "shifted mid-year, see file note"},
    )

    named = any(
        "cannot read" in line and "residency_status" in line
        for line in result.rationale
    )

    check(
        "APPLY14 AN ANSWERED FACT IS NEVER PUT TO THE CLIENT AGAIN",
        "residency_status" not in result.missing_predicates
        and result.applicability_unreadable == ("residency_status",)
        and result.recommended_state == decision.MISSING_KNOWLEDGE
        and decision.SUPPORTED_WITHIN_POLICY not in result.permitted_states
        and named,
        f"missing {result.missing_predicates}, "
        f"unreadable {result.applicability_unreadable}, "
        f"state {result.recommended_state}, "
        f"rationale {result.rationale}",
    )


CHECKS = (
    apply01_unrestricted_applies,
    apply02_matching_attribute_applies,
    apply03_contradiction_excludes,
    apply04_unestablished_is_unknown_not_a_guess,
    apply05_uninterpretable_value_is_not_established,
    apply06_proposed_facts_never_reach_applicability,
    apply07_q9_supported_is_refused,
    apply08_exclusion_is_recorded_not_just_applied,
    apply09_unknown_becomes_a_question_not_a_gap,
    apply10_the_gate_still_lets_a_real_answer_through,
    apply11_unverified_knowledge_cannot_drive_a_question,
    apply12_the_server_refuses_the_state_not_just_recommends_against_it,
    apply13_a_contradiction_beats_an_open_question,
    apply14_an_answered_fact_is_never_asked_twice,
)


def main():
    for run in CHECKS:
        run()

    for name in PASSES:
        print(f"PASS  {name}")

    for line in FAILURES:
        print(f"FAIL  {line}")

    print(f"\nAPPLICABILITY: {len(PASSES)} passed, {len(FAILURES)} failed")

    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
