"""Transport-only contract checks for the AgentCore Runtime entrypoint."""

import inspect
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import runner  # noqa: E402
import agentcore_runtime  # noqa: E402


def result(state="SUCCEEDED", failure_reason=None):
    return runner.RunResult(
        run_id="run-1",
        case_id="CASE-1",
        operation_id="OP-1",
        runner="GROQ_STRANDS",
        model_id="model-1",
        result_state=state,
        knowledge_release_id="release-1",
        decision_state="MISSING_FACTS",
        proposal_id="proposal-1",
        revision=1,
        latency_ms=12,
        failure_reason=failure_reason,
        tool_calls=(
            {"sequence": 1, "tool_name": "get_case_context", "error": None},
            {"sequence": 2, "tool_name": "propose_next_action", "error": "refused"},
        ),
        permitted_states=("MISSING_FACTS",),
        recommended_state="MISSING_FACTS",
    )


def invoke(payload, side_effect=None, run_result=None):
    with patch.object(agentcore_runtime, "BedrockStrandsModel") as model:
        with patch.object(
            agentcore_runtime.runtime_secrets,
            "hydrate_postgres_app_password",
        ) as hydrate:
            with patch.object(
                agentcore_runtime.runner,
                "execute",
                side_effect=side_effect,
                return_value=run_result or result(),
            ) as execute:
                response = agentcore_runtime.invoke(payload)
                expected_hydrations = (
                    0 if response.get("error") == "INVALID_REQUEST" else 1
                )
                if hydrate.call_count != expected_hydrations:
                    raise RuntimeError(
                        "runtime secrets hydrated "
                        f"{hydrate.call_count} times, expected "
                        f"{expected_hydrations}"
                    )
                return response, model, execute


def test_valid_request_delegates_once():
    response, model, execute = invoke(
        {"case_id": "CASE-1", "operation_id": "OP-1"}
    )

    if response["ok"] is not True:
        raise RuntimeError(f"valid request was not successful: {response!r}")

    if execute.call_count != 1:
        raise RuntimeError(f"runner called {execute.call_count} times")

    args, kwargs = execute.call_args

    if args != (model.return_value, "CASE-1"):
        raise RuntimeError(f"runner positional arguments changed: {args!r}")

    if kwargs != {"operation_id": "OP-1", "agent_profile_id": None}:
        raise RuntimeError(f"runner authority contract changed: {kwargs!r}")

    print("ACR01 VALID REQUEST DELEGATES ONCE: PASS")


def test_validation_rejects_before_runner():
    invalid = (
        {},
        {"case_id": "CASE-1"},
        {"operation_id": "OP-1"},
        {"case_id": " ", "operation_id": "OP-1"},
        {"case_id": "CASE-1", "operation_id": " "},
        {"case_id": "CASE-1", "operation_id": "OP-1", "agent_profile_id": "x"},
        {"case_id": "CASE-1", "operation_id": "OP-1", "service_id": "x"},
        "not-an-object",
    )

    for payload in invalid:
        response, _model, execute = invoke(payload)

        if response != {"ok": False, "error": "INVALID_REQUEST"}:
            raise RuntimeError(f"invalid payload was accepted: {payload!r}")

        if execute.call_count != 0:
            raise RuntimeError(f"invalid payload reached runner: {payload!r}")

    print("ACR02 REQUEST VALIDATION AND NO CALLER AUTHORITY: PASS")


def test_safe_response_and_failure_classes():
    response, _model, _execute = invoke(
        {"case_id": "CASE-1", "operation_id": "OP-1"},
        run_result=result(failure_reason="internal details must not leak"),
    )
    run = response["run"]

    if "failure_reason" in run or "internal details" in repr(response):
        raise RuntimeError("raw failure reason leaked")

    if run["tool_trajectory"][0]["status"] != "OK" or run["tool_trajectory"][1]["status"] != "REFUSED":
        raise RuntimeError("tool trajectory was not safely classified")

    cases = (
        (runner.DuplicateOperation("internal"), "DUPLICATE_OPERATION"),
        (runner.RunRefused("internal"), "RUN_REFUSED"),
        (runner.ContextNotRecorded("internal"), "CONTEXT_NOT_RECORDED"),
        (RuntimeError("internal"), "RUN_FAILED"),
    )

    for error, expected in cases:
        failure, _model, execute = invoke(
            {"case_id": "CASE-1", "operation_id": "OP-1"},
            side_effect=error,
        )

        if failure != {"ok": False, "error": expected}:
            raise RuntimeError(f"wrong bounded failure: {failure!r}")

        if "internal" in repr(failure) or execute.call_count != 1:
            raise RuntimeError("failure leaked or did not make one runner call")

    print("ACR03 SAFE RESPONSE AND FAILURE CLASSES: PASS")


def test_entrypoint_has_no_second_agent_or_direct_db_access():
    source = inspect.getsource(agentcore_runtime)

    for forbidden in ("strands.Agent", "AgentTools", "ToolTrace", "psycopg", ".connect(", "SELECT "):
        if forbidden in source:
            raise RuntimeError(f"entrypoint contains forbidden implementation: {forbidden}")

    print("ACR04 NO SECOND AGENT OR DIRECT DATABASE ACCESS: PASS")


def main():
    test_valid_request_delegates_once()
    test_validation_rejects_before_runner()
    test_safe_response_and_failure_classes()
    test_entrypoint_has_no_second_agent_or_direct_db_access()
    print("AGENTCORE RUNTIME ADAPTER: PASS")


if __name__ == "__main__":
    main()
