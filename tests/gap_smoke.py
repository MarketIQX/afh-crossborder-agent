"""GAP01-GAP09. A request for help must state what help it needs.

The decision layer computed why an enquiry could not be completed long
before anything turned that into a request. Contract section 3 requires
the record; this suite exists because two specific things went wrong
while building it, and both were found by reading real output rather
than by reasoning about the code.

**The precedence winner was being hidden.** The six states have a strict
precedence, so one of them decides what the agent does. A gap is not a
decision though, it is a question put to a person, and an enquiry can
genuinely be out of scope *and* missing a client fact. On a real case
the recorded reasons came back as `('MISSING_FACTS',)` while the
evaluation recommended `OUT_OF_SCOPE`: a reviewer reading that would
have answered a fact question and believed the case was progressing,
when the real problem was that nothing matched the service at all.

**A genuine reason reported the wrong problem.** `OUT_OF_SCOPE` is
permitted by two different branches of `decision.evaluate` -- declared
topics were declined, or nothing matched the service at all. The first
can name the topics; the second has nothing to name. Only the first was
written, so an unroutable enquiry produced "This enquiry asks about ,
which is outside the service Anika is trained on."

That sentence is the interesting kind of wrong. It is not empty, so
nothing rejected it, and it does not crash anything. It simply tells the
reviewer that a topic was declined when the truth is that the enquiry
never matched the service and needs triage -- a different problem with a
different answer. GAP04 therefore tests for the punctuation an empty
enumeration leaves behind, not for emptiness, because emptiness was
never the symptom.

Both properties are checked by reverting the fix in memory and requiring
the suite to fail. That exercise is what found the paragraph above: the
first version of this file claimed the defect crashed the proposal
transaction, and reverting the code showed it never could.

No model and no database. The gap's content is computed from an
evaluation the deterministic layer produced, so neither is needed to
prove it correct.

Usage:

    python tests/gap_smoke.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.domain import decision, gaps  # noqa: E402

FAILURES = []
PASSES = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


def evaluation(
    recommended,
    missing=(),
    coverage=(),
    out_of_scope=(),
    conflicts=(),
    failure=None,
):
    """A real Evaluation, so a change to its shape fails here too.

    Built through the actual dataclass rather than a stand-in: a
    stand-in would keep passing after the real one grew a field the gap
    record ought to read.
    """
    return decision.Evaluation(
        permitted_states=frozenset({recommended}),
        recommended_state=recommended,
        cited_unit_ids=(),
        approved_unit_ids=(),
        conflict_unit_ids=tuple(f"unit-{i}" for i in range(len(conflicts))),
        missing_predicates=tuple(missing),
        coverage_gaps=tuple(coverage),
        provisional_topics=(),
        out_of_scope_topics=tuple(out_of_scope),
        conflicts=tuple(conflicts),
        requires_professional_verification=True,
        knowledge_release_id=None,
        rationale=(),
        system_failure_reason=failure,
    )


def gap01_the_recommended_state_is_never_hidden():
    """A lower-precedence choice must not conceal the real problem."""
    # The agent filed a fact request; the layer says nothing matched the
    # service. Both are permitted, and the reviewer needs both.
    subject = evaluation(
        decision.OUT_OF_SCOPE, missing=("residency_status",)
    )
    reasons = gaps.reasons_for(subject, decision.MISSING_FACTS)

    check(
        "GAP01 THE RECOMMENDED STATE IS NEVER HIDDEN",
        decision.OUT_OF_SCOPE in reasons
        and decision.MISSING_FACTS in reasons,
        f"reasons were {reasons}",
    )


def gap02_reasons_are_ordered_by_precedence():
    """The most serious reason must read first, and appear once."""
    subject = evaluation(
        decision.MISSING_FACTS,
        missing=("residency_status",),
        coverage=("tax_residency",),
        out_of_scope=("company_law",),
    )
    reasons = gaps.reasons_for(subject, decision.MISSING_FACTS)

    expected = (
        decision.OUT_OF_SCOPE,
        decision.MISSING_KNOWLEDGE,
        decision.MISSING_FACTS,
    )

    check(
        "GAP02 REASONS ARE ORDERED BY PRECEDENCE",
        reasons == expected,
        f"got {reasons}, expected {expected}",
    )


def gap03_a_supported_state_is_never_a_reason():
    """Nothing is missing in that state, so it cannot be a request."""
    subject = evaluation(decision.SUPPORTED_WITHIN_POLICY)
    reasons = gaps.reasons_for(
        subject, decision.SUPPORTED_WITHIN_POLICY
    )

    check(
        "GAP03 A SUPPORTED STATE IS NEVER A REASON",
        reasons == (),
        f"reasons were {reasons}",
    )


def gap04_no_reason_asks_a_malformed_question():
    """Each reason, alone, with nothing recorded against it.

    This is the worst case for a branch that formats a list, and it is
    where the real defect lived. The original `OUT_OF_SCOPE` branch
    joined an empty tuple and produced "This enquiry asks about , which
    is outside the service Anika is trained on." -- not empty, so
    nothing refused it, but it names no topic and tells the reviewer a
    topic was declined when in fact nothing matched the service at all.

    So emptiness is not the property to test. A question must be
    non-empty *and* free of the punctuation that betrays an empty
    enumeration, because that artefact is the visible symptom of a
    branch asserting more than the evaluation recorded.
    """
    ARTEFACTS = (
        "about ,",
        ": .",
        "covers: .",
        "established: .",
        ",,",
        ": ;",
    )

    bad = []

    for reason in gaps.GAP_REASONS:
        subject = evaluation(reason)
        question = gaps.question_for(subject, (reason,))
        stripped = question.strip()

        if not stripped:
            bad.append(f"{reason}: nothing to ask")
            continue

        for artefact in ARTEFACTS:
            if artefact in question:
                bad.append(f"{reason}: empty list, {artefact!r}")
                break

        if stripped.endswith(":") or stripped.endswith(": "):
            bad.append(f"{reason}: question trails off")

    check(
        "GAP04 NO REASON ASKS A MALFORMED QUESTION",
        not bad,
        f"malformed: {bad}",
    )


def gap05_an_unroutable_enquiry_asks_for_triage():
    """Two causes wear one state, and they need different sentences."""
    nothing_matched = gaps.question_for(
        evaluation(decision.OUT_OF_SCOPE), (decision.OUT_OF_SCOPE,)
    )
    declined = gaps.question_for(
        evaluation(decision.OUT_OF_SCOPE, out_of_scope=("company_law",)),
        (decision.OUT_OF_SCOPE,),
    )

    check(
        "GAP05 AN UNROUTABLE ENQUIRY ASKS FOR TRIAGE",
        "triage" in nothing_matched
        and "company_law" in declined
        and nothing_matched != declined,
        f"unroutable={nothing_matched!r} declined={declined!r}",
    )


def gap06_the_question_uses_the_reviewers_wording():
    """A predicate name is not a question a professional should read."""
    subject = evaluation(
        decision.MISSING_FACTS, missing=("residency_status",)
    )
    hints = {"residency_status": "Residential status for the year."}

    with_hint = gaps.question_for(
        subject, (decision.MISSING_FACTS,), hints
    )
    without = gaps.question_for(subject, (decision.MISSING_FACTS,))

    check(
        "GAP06 THE QUESTION USES THE REVIEWER'S WORDING",
        "Residential status for the year." in with_hint
        and "residency_status" in without,
        f"with={with_hint!r} without={without!r}",
    )


def gap07_deduplication_ignores_wording():
    """The same gap asked twice is one gap, however it is phrased."""
    first = evaluation(
        decision.MISSING_FACTS, missing=("residency_status",)
    )
    same = evaluation(
        decision.MISSING_FACTS, missing=("residency_status",)
    )

    reasons = gaps.reasons_for(first, decision.MISSING_FACTS)

    check(
        "GAP07 DEDUPLICATION IGNORES WORDING",
        gaps.dedupe_key(reasons, first)
        == gaps.dedupe_key(reasons, same),
        "two identical gaps produced different keys",
    )


def gap08_deduplication_tracks_substance():
    """A different thing missing is a different gap."""
    one = evaluation(
        decision.MISSING_FACTS, missing=("residency_status",)
    )
    two = evaluation(
        decision.MISSING_FACTS,
        missing=("residency_status", "citizenship_status"),
    )

    key_one = gaps.dedupe_key(
        gaps.reasons_for(one, decision.MISSING_FACTS), one
    )
    key_two = gaps.dedupe_key(
        gaps.reasons_for(two, decision.MISSING_FACTS), two
    )

    check(
        "GAP08 DEDUPLICATION TRACKS SUBSTANCE",
        key_one != key_two,
        "a gap missing one more fact produced the same key",
    )


def gap09_the_key_does_not_carry_the_case():
    """Cross-case merging is the database's guarantee, not the digest's.

    Section 3 forbids merging unrelated cases because their wording is
    similar. That is enforced by `UNIQUE (case_id, dedupe_key)`, which
    holds however the key is computed -- so a future bug in key
    generation still cannot merge two clients. The key is therefore
    about substance only, and this check pins that division of labour
    so nobody later "fixes" it by folding the case id in and quietly
    moving the guarantee into application code.
    """
    subject = evaluation(
        decision.MISSING_FACTS, missing=("residency_status",)
    )
    reasons = gaps.reasons_for(subject, decision.MISSING_FACTS)
    key = gaps.dedupe_key(reasons, subject)

    migration = (
        REPO_ROOT / "db/migrations/023_knowledge_gaps.sql"
    ).read_text(encoding="utf-8")

    check(
        "GAP09 THE KEY DOES NOT CARRY THE CASE",
        len(key) == 64
        and "UNIQUE (case_id, dedupe_key)" in migration,
        f"key length {len(key)}; constraint present: "
        f"{'UNIQUE (case_id, dedupe_key)' in migration}",
    )


CHECKS = (
    gap01_the_recommended_state_is_never_hidden,
    gap02_reasons_are_ordered_by_precedence,
    gap03_a_supported_state_is_never_a_reason,
    gap04_no_reason_asks_a_malformed_question,
    gap05_an_unroutable_enquiry_asks_for_triage,
    gap06_the_question_uses_the_reviewers_wording,
    gap07_deduplication_ignores_wording,
    gap08_deduplication_tracks_substance,
    gap09_the_key_does_not_carry_the_case,
)


def main():
    for run in CHECKS:
        run()

    for name in PASSES:
        print(f"PASS  {name}")

    for line in FAILURES:
        print(f"FAIL  {line}")

    print(f"\nKNOWLEDGE GAPS: {len(PASSES)} passed, {len(FAILURES)} failed")

    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
