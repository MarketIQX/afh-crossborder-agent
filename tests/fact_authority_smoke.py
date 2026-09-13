"""FACT01-FACT06. Authority decides what is established, not recency.

This suite exists because 188 checks were green while the function that
renders facts to a professional had four defects, one of which crashed
it outright on the offline path. That is not a gap in coverage volume.
It is a gap in what the coverage was about: no check had ever called
`_facts_block` with anything but the one shape the live database
happened to contain.

The four defects, each now pinned by a check here.

`FACT01` -- the most recent assertion was presented as the current
position. A newer agent proposal therefore displaced an older
professional confirmation on screen, while `decision.evaluate` went on
using the confirmed value, because applicability reads confirmed facts
only. Two surfaces disagreeing about one case is worse than either being
incomplete, and a chartered accountant would find it in seconds.

`FACT03` -- differing values were labelled a contradiction the reviewer
must resolve. "UAE", "United Arab Emirates" and "UAE (Dubai)" are not
three contradictory professional facts. There is no country
normalisation in this system and none is being added, so the honest
output is a count and a note that the judgement is professional.

`FACT04` -- the restatement counter subtracted distinct differing
*values* from the assertion count, so it reported earlier assertions of
a value that had never been asserted. The counter is gone rather than
corrected: per-assertion provenance makes it unnecessary.

`FACT05` -- structured evidence raised `TypeError`. `app/agent/model.py`
records `[{"source": ..., "case": ...}]`, so the deterministic stub --
the path an offline demonstration uses -- crashed this function. Live
evidence happened to be strings, which is why nothing noticed.

No model, no database, no HTTP. Rows are passed in the shape `assemble`
selects them, so a change to that query's column order fails here rather
than rendering something plausible and wrong.

Usage:

    python tests/fact_authority_smoke.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.domain import teaching  # noqa: E402

FAILURES = []
PASSES = []

EARLIER = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 12, 17, 0, tzinfo=timezone.utc)

RUN_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
RUN_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


def fact(
    predicate="country_of_residence",
    value="UAE",
    status="PROPOSED",
    origin="AGENT_PROPOSED",
    evidence=(),
    run_id=RUN_A,
    created=EARLIER,
):
    """One row in the shape `assemble` selects."""
    return (
        predicate,
        value,
        status,
        origin,
        list(evidence),
        run_id,
        created,
    )


def block(*facts, hints=None):
    return teaching._facts_block(
        {"facts": list(facts), "hints": hints or {}}
    )


def fact01_a_confirmed_fact_is_not_displaced_by_a_later_proposal():
    """The defect a professional would have found first.

    Applicability reads confirmed facts only, so a screen that shows the
    proposal as current contradicts the engine that decided the case.
    """
    rendered = block(
        fact(
            value="UAE",
            status="CONFIRMED",
            origin="REVIEWER_CONFIRMED",
            created=EARLIER,
        ),
        fact(value="UK", status="PROPOSED", created=LATER, run_id=RUN_B),
    )

    established, _, unconfirmed = rendered.partition(
        "UNCONFIRMED ASSERTIONS"
    )

    check(
        "FACT01 A CONFIRMED FACT IS NOT DISPLACED BY A LATER PROPOSAL",
        "UAE" in established
        and "UK" not in established
        and "UK" in unconfirmed,
        f"established section: {established[:160]!r}",
    )


def fact02_nothing_is_established_without_a_confirmation():
    """An empty established section must say so, not stay silent."""
    rendered = block(fact())

    established, _, _rest = rendered.partition(
        "UNCONFIRMED ASSERTIONS"
    )

    check(
        "FACT02 NOTHING IS ESTABLISHED WITHOUT A CONFIRMATION",
        "None" in established
        and "professionally confirmed" in established,
        f"established section: {established[:160]!r}",
    )


def fact03_differing_values_are_not_called_contradictions():
    """Two spellings of a country are not a professional conflict."""
    rendered = block(
        fact(value="UAE", created=EARLIER),
        fact(value="United Arab Emirates", created=LATER, run_id=RUN_B),
    )

    check(
        "FACT03 DIFFERING VALUES ARE NOT CALLED CONTRADICTIONS",
        "confirm which is correct" not in rendered
        and "contradict" not in rendered.lower()
        and "2 different recorded values" in rendered,
        f"rendered: {rendered[:200]!r}",
    )


def fact04_every_assertion_keeps_its_own_provenance():
    """Collapsing assertions destroys what the reviewer must judge."""
    rendered = block(
        fact(value="UAE", created=EARLIER, run_id=RUN_A),
        fact(value="UAE", created=LATER, run_id=RUN_B),
    )

    check(
        "FACT04 EVERY ASSERTION KEEPS ITS OWN PROVENANCE",
        rendered.count("value: UAE") == 2
        and RUN_A[:8] in rendered
        and RUN_B[:8] in rendered
        and "01 Sep" in rendered
        and "12 Sep" in rendered
        and "earlier assertion" not in rendered,
        f"UAE mentions={rendered.count('value: UAE')}, "
        f"runs present={RUN_A[:8] in rendered and RUN_B[:8] in rendered}",
    )


def fact05_structured_evidence_does_not_crash():
    """The shape the deterministic stub actually records."""
    try:
        rendered = block(
            fact(
                evidence=[
                    {"source": "enquiry_body", "case": "abc-123"}
                ]
            )
        )
        raised = None
    except Exception as exc:  # noqa: BLE001
        rendered = ""
        raised = f"{exc.__class__.__name__}: {exc}"

    check(
        "FACT05 STRUCTURED EVIDENCE DOES NOT CRASH",
        raised is None
        and "source=enquiry_body" in rendered
        and "no evidence locator" not in rendered,
        raised or f"rendered: {rendered[:160]!r}",
    )


def fact06_mixed_evidence_shapes_all_survive():
    """A string, an object and something unforeseen, in one list.

    Nothing is skipped. A reviewer shown less evidence than exists is a
    worse failure than one shown an ugly rendering of it.
    """
    try:
        rendered = block(
            fact(evidence=["enquiry body", {"doc": "passport"}, 7])
        )
        raised = None
    except Exception as exc:  # noqa: BLE001
        rendered = ""
        raised = f"{exc.__class__.__name__}: {exc}"

    check(
        "FACT06 MIXED EVIDENCE SHAPES ALL SURVIVE",
        raised is None
        and "enquiry body" in rendered
        and "doc=passport" in rendered
        and "7" in rendered,
        raised or f"rendered: {rendered[:200]!r}",
    )


CHECKS = (
    fact01_a_confirmed_fact_is_not_displaced_by_a_later_proposal,
    fact02_nothing_is_established_without_a_confirmation,
    fact03_differing_values_are_not_called_contradictions,
    fact04_every_assertion_keeps_its_own_provenance,
    fact05_structured_evidence_does_not_crash,
    fact06_mixed_evidence_shapes_all_survive,
)


def main():
    for run in CHECKS:
        run()

    for name in PASSES:
        print(f"PASS  {name}")

    for line in FAILURES:
        print(f"FAIL  {line}")

    print(f"\nFACT AUTHORITY: {len(PASSES)} passed, {len(FAILURES)} failed")

    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
