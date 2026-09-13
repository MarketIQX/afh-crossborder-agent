"""GOLD01-06. The eval contract, before any eval content exists.

No professional content is in this repository and none is invented
here. What is being checked is the harness's honesty: that a bank the
agent must not see cannot be loaded into its context, and that an
absent or empty bank cannot be reported as a pass.

GOLD04 is the one worth having. An eval harness whose empty state reads
as success will eventually be quoted as evidence that something works,
and by then nobody will remember it graded nothing.

No database, no credentials.

    python tests/enquiry_eval_contract_smoke.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.evals import enquiry_contract as contract  # noqa: E402

FAILURES = []
PASSES = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


WELL_FORMED = {
    "case_key": "shape_only_not_a_real_enquiry",
    "bank": contract.AUTHORING,
    "subject": "subject line",
    "body": "body text",
    "material_date": "2026-04-01",
    "expected_primary_service": "some_service",
    "expected_decision_state": "MISSING_FACTS",
    "expected_missing_material_facts": ["some_predicate"],
    "why_this_case_exists": (
        "Shape only. Carries no professional content and is not an "
        "eval case: it exists so the validator has something valid to "
        "accept, and lives in this file rather than in a bank."
    ),
}


RICH_CASE = dict(
    WELL_FORMED,
    allowed_secondary_services=["another_service"],
    expected_established_facts=["country_of_residence"],
    prohibited_claims=["the return has already been filed"],
    receipt_assertions={"actionable": False},
)


def gold07_the_expected_decision_is_not_agent_visible():
    seen = contract.agent_visible(RICH_CASE)

    check(
        "GOLD07 THE EXPECTED DECISION DOES NOT REACH THE AGENT",
        "expected_decision_state" not in seen
        and "expected_primary_service" not in seen,
        f"agent_visible() returned {seen!r}, which still names the "
        f"answer",
    )


def gold08_missing_facts_and_prohibitions_cannot_enter_context():
    seen = contract.agent_visible(RICH_CASE)

    check(
        "GOLD08 GRADER-ONLY FIELDS CANNOT ENTER THE AGENT'S CONTEXT",
        "expected_missing_material_facts" not in seen
        and "prohibited_claims" not in seen
        and "expected_established_facts" not in seen
        and "receipt_assertions" not in seen,
        f"agent_visible() returned {seen!r}, which still carries "
        f"grader-only content",
    )


def gold09_held_out_metadata_stays_grader_side():
    held_out_case = dict(RICH_CASE, bank=contract.HELD_OUT_CAPABILITY)

    seen = contract.agent_visible(held_out_case)
    exact = set(seen) == contract.AGENT_VISIBLE_FIELDS & set(
        held_out_case
    )

    check(
        "GOLD09 HELD-OUT ANSWER METADATA REMAINS GRADER-SIDE ONLY",
        exact
        and set(seen) <= contract.AGENT_VISIBLE_FIELDS
        and "bank" not in seen
        and "case_key" not in seen,
        f"a held-out case reduced to {seen!r}; only subject, body and "
        f"material_date may survive, whatever bank the case is in",
    )


def gold01_the_banks_are_named_and_distinct():
    check(
        "GOLD01 FOUR BANKS, AND THE HELD-OUT ONES ARE MARKED",
        len(set(contract.BANKS)) == 4
        and contract.NEVER_IN_RUNTIME
        == {contract.HELD_OUT_CAPABILITY, contract.ADVERSARIAL},
        f"banks={contract.BANKS} never_in_runtime="
        f"{sorted(contract.NEVER_IN_RUNTIME)}",
    )


def gold02_an_incomplete_case_is_refused():
    missing = dict(WELL_FORMED)
    del missing["material_date"]

    vague = dict(WELL_FORMED)
    del vague["expected_missing_material_facts"]

    check(
        "GOLD02 A CASE MISSING A REQUIRED FIELD IS REFUSED",
        any("material_date" in p for p in contract.validate(missing)),
        f"validate() said {contract.validate(missing)}",
    )
    check(
        "GOLD03 A CASE THAT ASSERTS ALMOST NOTHING IS REFUSED",
        any("almost any output" in p for p in contract.validate(vague)),
        f"a case with only a decision state was accepted: "
        f"{contract.validate(vague)}",
    )


def gold04_an_absent_bank_is_not_a_pass():
    outcomes = {}

    for bank in contract.BANKS:
        try:
            contract.load(bank)
            outcomes[bank] = "LOADED"
        except contract.EvalBankRefused as exc:
            outcomes[bank] = str(exc)[:40]

    present = contract.status()

    check(
        "GOLD04 AN ABSENT OR EMPTY BANK IS NOT A PASS",
        all(
            value != "LOADED" or present[bank]["cases"] > 0
            for bank, value in outcomes.items()
        ),
        f"a bank loaded without cases: {outcomes}",
    )
    check(
        "GOLD05 ABSENT AND EMPTY ARE REPORTED SEPARATELY",
        all(
            set(entry) == {"present", "cases"}
            for entry in present.values()
        ),
        f"status() reports {present}",
    )


def gold06_a_held_out_bank_cannot_reach_the_runtime():
    outcomes = {}

    for bank in contract.BANKS:
        try:
            contract.load(bank, for_runtime=True)
            outcomes[bank] = "LOADED"
        except contract.EvalBankRefused as exc:
            outcomes[bank] = (
                "REFUSED_AS_HELD_OUT"
                if "never enter the runtime" in str(exc)
                else "REFUSED_AS_ABSENT"
            )

    check(
        "GOLD06 A HELD-OUT BANK IS REFUSED TO THE RUNTIME BY NAME",
        outcomes[contract.HELD_OUT_CAPABILITY] == "REFUSED_AS_HELD_OUT"
        and outcomes[contract.ADVERSARIAL] == "REFUSED_AS_HELD_OUT",
        f"the refusal must be because of the bank, not because the "
        f"file happens to be absent: {outcomes}",
    )


CHECKS = (
    gold01_the_banks_are_named_and_distinct,
    gold02_an_incomplete_case_is_refused,
    gold04_an_absent_bank_is_not_a_pass,
    gold06_a_held_out_bank_cannot_reach_the_runtime,
    gold07_the_expected_decision_is_not_agent_visible,
    gold08_missing_facts_and_prohibitions_cannot_enter_context,
    gold09_held_out_metadata_stays_grader_side,
)


def main():
    for run in CHECKS:
        try:
            run()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(
                f"{run.__name__} RAISED {exc.__class__.__name__}: {exc}"
            )

    for name in sorted(PASSES):
        print(f"PASS  {name}")

    for line in sorted(FAILURES):
        print(f"FAIL  {line}")

    print(f"\nENQUIRY EVAL CONTRACT: {len(PASSES)} passed, "
          f"{len(FAILURES)} failed")

    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
