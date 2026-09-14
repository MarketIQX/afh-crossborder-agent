"""Thin Amazon Bedrock AgentCore Runtime transport for Nicole.

Professional state, tools, and agent construction remain behind
``app.agent.runner.execute``. This module validates a small untrusted
transport payload and returns a deliberately bounded execution summary.
"""

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from app.agent import runner
from app.agent.bedrock import BedrockStrandsModel

app = BedrockAgentCoreApp()

_REQUEST_FIELDS = frozenset(("case_id", "operation_id"))


def _error(error_class):
    return {"ok": False, "error": error_class}


def _validate(payload):
    if not isinstance(payload, dict) or set(payload) != _REQUEST_FIELDS:
        return None

    values = []

    for name in ("case_id", "operation_id"):
        value = payload[name]

        if not isinstance(value, str) or not value.strip():
            return None

        # Validation is intentionally non-normalising: operation_id must
        # reach runner.execute unchanged for its idempotency boundary.
        values.append(value)

    return tuple(values)


def _tool_summary(tool_calls):
    return [
        {
            "sequence": call.get("sequence"),
            "tool_name": call.get("tool_name"),
            "status": "REFUSED" if call.get("error") else "OK",
        }
        for call in tool_calls
    ]


def _project(result):
    return {
        "run_id": result.run_id,
        "case_id": result.case_id,
        "operation_id": result.operation_id,
        "runner": result.runner,
        "model_id": result.model_id,
        "result_state": result.result_state,
        "knowledge_release_id": result.knowledge_release_id,
        "decision_state": result.decision_state,
        "proposal_id": result.proposal_id,
        "revision": result.revision,
        "latency_ms": result.latency_ms,
        "permitted_states": list(result.permitted_states),
        "recommended_state": result.recommended_state,
        "tool_trajectory": _tool_summary(result.tool_calls),
    }


@app.entrypoint
def invoke(payload):
    """Validate one Runtime request and delegate it to Nicole exactly once."""
    request = _validate(payload)

    if request is None:
        return _error("INVALID_REQUEST")

    case_id, operation_id = request

    try:
        result = runner.execute(
            BedrockStrandsModel(),
            case_id,
            operation_id=operation_id,
            agent_profile_id=None,
        )
    except runner.DuplicateOperation:
        return _error("DUPLICATE_OPERATION")
    except runner.RunRefused:
        return _error("RUN_REFUSED")
    except runner.ContextNotRecorded:
        return _error("CONTEXT_NOT_RECORDED")
    except Exception:  # noqa: BLE001
        return _error("RUN_FAILED")

    response = _project(result)

    if result.result_state == "REFUSED":
        return {"ok": False, "error": "RUN_REFUSED", "run": response}

    if result.result_state == "FAILED":
        return {"ok": False, "error": "RUN_FAILED", "run": response}

    return {"ok": True, "run": response}


if __name__ == "__main__":
    app.run()
