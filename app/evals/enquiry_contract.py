"""The shape a new-enquiry eval case must have, and which bank it lives in.

The professional content for these cases is being prepared elsewhere
and none of it exists yet. That is deliberately not a blocker: the
contract and the loader can be settled now, and the cases can arrive
later without the harness being redesigned around whatever shape they
turn up in.

Four banks, and the difference between them is the whole point.

    AUTHORING             inspectable while building. Used to develop.
    HELD_OUT_CAPABILITY   never seen by the agent or by whoever tunes
                          it. This is the only bank that can measure
                          capability, and it is worth exactly as much
                          as its unseen-ness.
    REGRESSION            failures actually discovered, kept forever.
    ADVERSARIAL           judge-shaped cases nobody has rehearsed.

`load` refuses to hand the held-out and adversarial banks to anything
that is assembling runtime context. Optimising against a case the agent
can see measures memorisation, and the harness should make that
mistake impossible rather than merely discouraged.

Two things this module will not do.

It will not invent cases. A bank with plausible-looking placeholder
content would make the harness look finished and would produce eval
results that mean nothing, which is worse than an empty bank that
reports itself empty.

It will not report success over an empty bank. "0 of 0 passed" is not a
pass, and a harness that says otherwise will eventually be quoted as
evidence.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BANK_DIR = REPO_ROOT / "evals" / "enquiry"

CONTRACT_VERSION = "new-enquiry-eval-contract-v1"

AUTHORING = "AUTHORING"
HELD_OUT_CAPABILITY = "HELD_OUT_CAPABILITY"
REGRESSION = "REGRESSION"
ADVERSARIAL = "ADVERSARIAL"

BANKS = (AUTHORING, HELD_OUT_CAPABILITY, REGRESSION, ADVERSARIAL)

# Banks that must never reach anything building the agent's context or
# its prompts. Not a convention: `load` raises.
NEVER_IN_RUNTIME = frozenset({HELD_OUT_CAPABILITY, ADVERSARIAL})

# What a case has to say for a result to be gradeable. Each field
# exists because grading without it would come down to reading prose
# and deciding whether it felt right.
REQUIRED_FIELDS = (
    "case_key",
    "bank",
    "subject",
    "body",
    "material_date",
    "expected_primary_service",
    "expected_decision_state",
)

OPTIONAL_FIELDS = (
    "allowed_secondary_services",
    "expected_established_facts",
    "expected_missing_material_facts",
    "expected_next_action",
    "allowed_knowledge_families",
    "prohibited_claims",
    "requires_professional_approval",
    "receipt_assertions",
    "why_this_case_exists",
)

DECISION_STATES = (
    "MISSING_FACTS",
    "MISSING_KNOWLEDGE",
    "SOURCE_CONFLICT",
    "OUT_OF_SCOPE",
    "SUPPORTED_WITHIN_POLICY",
    "SYSTEM_FAILURE",
)


# What an agent may ever see of a case. Everything else -- the
# expected service, the expected decision, missing facts, prohibited
# claims, receipt assertions -- exists to grade the answer and must
# never travel with the question.
AGENT_VISIBLE_FIELDS = frozenset({"subject", "body", "material_date"})


def agent_visible(case):
    """The case, reduced to what may become a real enquiry.

    No exceptions and no case-by-case judgement calls: whatever is not
    named in `AGENT_VISIBLE_FIELDS` is dropped, unconditionally, so a
    new grader-only field added later is excluded by default rather
    than by remembering to add it here.
    """
    return {
        key: value
        for key, value in case.items()
        if key in AGENT_VISIBLE_FIELDS
    }


class EvalBankRefused(Exception):
    """A bank was asked for in a context that must not see it."""


def validate(case):
    """Every reason this case could not be graded. Empty means usable."""
    problems = []

    if not isinstance(case, dict):
        return ["case is not an object"]

    for field in REQUIRED_FIELDS:
        if not case.get(field):
            problems.append(f"missing required field {field!r}")

    bank = case.get("bank")

    if bank and bank not in BANKS:
        problems.append(f"unknown bank {bank!r}; expected one of {BANKS}")

    state = case.get("expected_decision_state")

    if state and state not in DECISION_STATES:
        problems.append(
            f"expected_decision_state {state!r} is not a decision state"
        )

    unknown = set(case) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)

    if unknown:
        problems.append(
            f"unrecognised field(s) {sorted(unknown)}; the contract is "
            f"{CONTRACT_VERSION}"
        )

    # A case that forbids nothing and expects nothing specific grades
    # every possible answer as correct.
    if not any(
        case.get(field)
        for field in (
            "expected_established_facts",
            "expected_missing_material_facts",
            "expected_next_action",
            "prohibited_claims",
        )
    ):
        problems.append(
            "case asserts nothing beyond the decision state, so almost "
            "any output would grade as correct"
        )

    return problems


def bank_path(bank):
    return BANK_DIR / f"{bank.lower()}.json"


def status():
    """Which banks exist, and how many cases each holds.

    Absent and empty are reported separately. A harness that treated
    them alike would let a missing file read as a bank that passed.
    """
    out = {}

    for bank in BANKS:
        path = bank_path(bank)

        if not path.exists():
            out[bank] = {"present": False, "cases": 0}
            continue

        body = json.loads(path.read_text(encoding="utf-8"))
        out[bank] = {
            "present": True,
            "cases": len(body.get("cases", [])),
        }

    return out


def load(bank, for_runtime=False):
    """The cases in one bank.

    `for_runtime` is the guard. Anything assembling the agent's context
    or its prompts passes True, and the held-out banks then refuse:
    optimising against a case the agent has seen measures memory, not
    capability, and the refusal is what makes that a mistake you cannot
    make by accident.
    """
    if bank not in BANKS:
        raise EvalBankRefused(f"unknown bank {bank!r}")

    if for_runtime and bank in NEVER_IN_RUNTIME:
        raise EvalBankRefused(
            f"{bank} must never enter the runtime context. It measures "
            f"capability only while the agent has not seen it."
        )

    path = bank_path(bank)

    if not path.exists():
        raise EvalBankRefused(
            f"{bank} has no cases yet ({path.name} does not exist). "
            f"An absent bank is not an empty pass."
        )

    body = json.loads(path.read_text(encoding="utf-8"))
    cases = body.get("cases", [])

    if not cases:
        raise EvalBankRefused(
            f"{bank} contains no cases. Zero of zero is not a pass."
        )

    return cases
