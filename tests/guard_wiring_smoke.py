"""WIRE01-WIRE09. A guard that runs and inspects nothing governs nothing.

This suite exists because of a defect that every other check in this
repository missed, and missed for a structural reason worth stating.

`AGENT25` and `AGENT26` prove the copy inspector works: they hand
`steering_module.inspect_copy` a genuinely defective client letter and
require it to object. It does. What they never do is drive
`before_tool_call` with the event Strands actually delivers. So they
prove the inspector and say nothing about whether anything reaches it.

Nothing did. The tool is declared `propose_next_action(action: dict)`,
so the arguments arrive as `{"action": {...}}`, while
`client_facing_text` and `EvidenceFirstGuard` both read
`client_message`, `summary` and `decision_state` off the top level of
that dict. Both guards were registered, fired on every proposal, read an
empty string, and returned `Proceed` -- on copy the inspector flags with
two problems.

That is the same failure this build documented in AWS's own reference
customer-service agent: a steering handler that executes, produces
confident prose, and decides nothing, whose ledger shows no problems
because it never saw any. The difference is that this one was ours, and
a passing suite is what concealed it.

Two rules follow, and they are the reason this file is separate from
`agent_slice_smoke.py`.

**Events are constructed from Strands' own class, never hand-written.**
A hand-written stub is a second opinion about the framework's shape, and
it will be wrong in exactly the way that hides a defect. Proving this
defect took two false negatives first: a stub exposing `cancel` where
the real event writes `cancel_tool`, and a stub whose payload was flat
where the real one nests. `BeforeToolCallEvent` also defaults
`cancel_tool` to `False` rather than `None`, so even the sentinel
differs.

**Payloads are shaped from the derived tool schema, not from memory.**
WIRE09 reads the schema the SDK generates and fails if the shape these
checks use stops matching it, so this suite cannot drift back into
testing a fiction.

No model, no network, no database.

Usage:

    python tests/guard_wiring_smoke.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from strands.hooks import BeforeToolCallEvent  # noqa: E402
from strands.interventions import Guide, Proceed  # noqa: E402

from app.agent import bedrock  # noqa: E402
from app.agent import hooks as hooks_module  # noqa: E402
from app.agent import steering as steering_module  # noqa: E402

FAILURES = []
PASSES = []

PROPOSE = "propose_next_action"
RETRIEVE = "get_service_knowledge"

SERVICE_ID = "11111111-1111-1111-1111-111111111111"
SERVICE_KEY = "nri_india_tax_filing"
OTHER_SERVICE = "22222222-2222-2222-2222-222222222222"

DEFECTIVE_COPY = (
    "Per SUPPORTED_WITHIN_POLICY, section 6(1)(a) of the Income-tax "
    "Act 2025 applies. decision_state=MISSING_FACTS"
)

CLEAN_COPY = (
    "Thank you for your enquiry. To confirm your position for the year "
    "we need to know how many days you spent in India. Once we have "
    "that, we will confirm what you need to file."
)


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


def event(tool_name, tool_input):
    """A genuine BeforeToolCallEvent, built from Strands' own class.

    `agent` and `selected_tool` are None because no guard or hook reads
    them; everything under test reads `tool_use`. Using the real class
    means a change to its fields or to the name of the cancellation
    field breaks these checks instead of silently passing.
    """
    return BeforeToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={
            "name": tool_name,
            "input": tool_input,
            "toolUseId": "wire-probe",
        },
        invocation_state={},
    )


def proposal(client_message=None, decision_state="MISSING_FACTS"):
    """The payload shape the declared tool signature actually produces.

    `propose_next_action(action: dict)` means one property named
    `action`, so everything the guards care about sits one level down.
    """
    action = {"decision_state": decision_state, "summary": "summary"}

    if client_message is not None:
        action["payload"] = {"client_message": client_message}

    return {"action": action}


def guard_named(name):
    for guard in steering_module.build_interventions():
        if guard.__class__.__name__ == name:
            return guard

    raise AssertionError(f"no guard named {name}")


# ---------------------------------------------------------------------
# P0-1: the guards must read the payload Strands delivers.
# ---------------------------------------------------------------------


def wire01_defective_copy_is_guided_through_the_real_payload():
    """The defect itself: unsafe copy, nested as the tool nests it."""
    outcome = guard_named("ClientCopyGuard").before_tool_call(
        event(PROPOSE, proposal(client_message=DEFECTIVE_COPY))
    )

    check(
        "WIRE01 DEFECTIVE COPY IS GUIDED, NOT PROCEEDED",
        isinstance(outcome, Guide),
        f"returned {outcome.__class__.__name__} on copy that "
        f"inspect_copy flags with "
        f"{len(steering_module.inspect_copy(DEFECTIVE_COPY))} problem(s)",
    )


def wire02_clean_copy_proceeds():
    """A guard that objects to everything is as useless as one that
    objects to nothing."""
    outcome = guard_named("ClientCopyGuard").before_tool_call(
        event(PROPOSE, proposal(client_message=CLEAN_COPY))
    )

    check(
        "WIRE02 CLEAN COPY PROCEEDS",
        isinstance(outcome, Proceed),
        f"returned {outcome.__class__.__name__} on acceptable copy",
    )


def wire03_support_without_retrieval_is_guided():
    """EvidenceFirstGuard reads `decision_state` from the same place."""
    outcome = guard_named("EvidenceFirstGuard").before_tool_call(
        event(
            PROPOSE,
            proposal(decision_state="SUPPORTED_WITHIN_POLICY"),
        )
    )

    check(
        "WIRE03 SUPPORT WITHOUT RETRIEVAL IS GUIDED",
        isinstance(outcome, Guide),
        f"returned {outcome.__class__.__name__} for support claimed "
        f"with no retrieval in the run",
    )


def wire04_support_after_retrieval_proceeds():
    """Retrieval in the same run is what makes support permissible."""
    guard = guard_named("EvidenceFirstGuard")
    guard.before_tool_call(event(RETRIEVE, {"query": "residency"}))

    outcome = guard.before_tool_call(
        event(
            PROPOSE,
            proposal(decision_state="SUPPORTED_WITHIN_POLICY"),
        )
    )

    check(
        "WIRE04 SUPPORT AFTER RETRIEVAL PROCEEDS",
        isinstance(outcome, Proceed),
        f"returned {outcome.__class__.__name__} after a retrieval call",
    )


# ---------------------------------------------------------------------
# P0-2: execution is not enforcement. Each hook must cancel.
# ---------------------------------------------------------------------


def wire05_the_scope_hook_allows_the_bound_service():
    hook = hooks_module.ServiceScopeHook(
        SERVICE_ID, bound_service_key=SERVICE_KEY
    )
    probe = event(RETRIEVE, {"service_id": SERVICE_ID})
    hook.on_before_tool_call(probe)

    check(
        "WIRE05 THE SCOPE HOOK ALLOWS THE BOUND SERVICE",
        probe.cancel_tool is False,
        f"cancel_tool became {probe.cancel_tool!r} for the bound service",
    )


def wire06_the_scope_hook_cancels_another_service():
    """Cancellation, on the real field, with a reason the model can read."""
    hook = hooks_module.ServiceScopeHook(
        SERVICE_ID, bound_service_key=SERVICE_KEY
    )
    probe = event(RETRIEVE, {"service_id": OTHER_SERVICE})
    hook.on_before_tool_call(probe)

    check(
        "WIRE06 THE SCOPE HOOK CANCELS ANOTHER SERVICE",
        isinstance(probe.cancel_tool, str)
        and probe.cancel_tool.strip() != "",
        f"cancel_tool is {probe.cancel_tool!r}, so the call would "
        f"have proceeded",
    )


def wire07_the_budget_hook_allows_calls_within_budget():
    hook = hooks_module.ToolBudgetHook(budget=3)
    verdicts = []

    for _ in range(3):
        probe = event("get_case_context", {})
        hook.on_before_tool_call(probe)
        verdicts.append(probe.cancel_tool)

    check(
        "WIRE07 THE BUDGET HOOK ALLOWS CALLS WITHIN BUDGET",
        all(v is False for v in verdicts),
        f"cancel_tool values within budget: {verdicts}",
    )


def wire08_the_budget_hook_cancels_past_its_budget():
    hook = hooks_module.ToolBudgetHook(budget=3)

    for _ in range(3):
        hook.on_before_tool_call(event("get_case_context", {}))

    probe = event("get_case_context", {})
    hook.on_before_tool_call(probe)

    check(
        "WIRE08 THE BUDGET HOOK CANCELS PAST ITS BUDGET",
        isinstance(probe.cancel_tool, str)
        and probe.cancel_tool.strip() != "",
        f"the fourth call against a budget of 3 gave "
        f"cancel_tool={probe.cancel_tool!r}",
    )


# ---------------------------------------------------------------------
# The anchor: these checks must keep matching the real tool contract.
# ---------------------------------------------------------------------


def wire09_the_probe_shape_matches_the_declared_tool():
    """If the tool's schema changes, this suite must fail rather than drift.

    Without this, a future signature change could return the guards to
    inspecting nothing while every check above still passed against a
    shape the SDK no longer produces.
    """
    spec = None

    for fn in bedrock.build_tool_functions(None):
        if fn.tool_spec["name"] == PROPOSE:
            spec = fn.tool_spec
            break

    schema = (spec or {}).get("inputSchema", {}).get("json", {})
    properties = sorted(schema.get("properties", {}))
    probe = sorted(proposal(client_message="x"))

    check(
        "WIRE09 THE PROBE SHAPE MATCHES THE DECLARED TOOL",
        spec is not None
        and properties == ["action"]
        and probe == ["action"],
        f"schema properties={properties}, probe keys={probe}",
    )


CHECKS = (
    wire01_defective_copy_is_guided_through_the_real_payload,
    wire02_clean_copy_proceeds,
    wire03_support_without_retrieval_is_guided,
    wire04_support_after_retrieval_proceeds,
    wire05_the_scope_hook_allows_the_bound_service,
    wire06_the_scope_hook_cancels_another_service,
    wire07_the_budget_hook_allows_calls_within_budget,
    wire08_the_budget_hook_cancels_past_its_budget,
    wire09_the_probe_shape_matches_the_declared_tool,
)


def main():
    for run in CHECKS:
        run()

    for name in PASSES:
        print(f"PASS  {name}")

    for line in FAILURES:
        print(f"FAIL  {line}")

    print(f"\nGUARD WIRING: {len(PASSES)} passed, {len(FAILURES)} failed")

    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
