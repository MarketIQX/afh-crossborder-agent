"""Run one persisted case through the real Strands agent on Bedrock.

This is the M1 gate. It is deliberately separate from the access probe:
proving the model answers is not proving the workflow. Here the model
reads a persisted enquiry through the bounded tools, and the result is
a persisted, source-linked proposal that a reviewer can open.

Nothing about this path is fixture data. The case must already exist,
having been ingested and triaged, and the knowledge it consults is
whatever the database actually holds. With the current corpus, which
is SOURCE_RECORDED throughout, a truthful MISSING_KNOWLEDGE proposal is
the expected and acceptable outcome.

The identity gate in the adapter runs first and cannot be overridden,
so a wrong or root principal produces no Bedrock call at all.

Usage:

    python scripts/run_case.py <case_id>
    python scripts/run_case.py <case_id> --stub     # no AWS, for comparison
    python scripts/run_case.py <case_id> --record
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.agent import bedrock  # noqa: E402
from app.agent import model as model_module  # noqa: E402
from app.agent import runner  # noqa: E402

EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"


def build_model(argv):
    if "--stub" in argv:
        return model_module.DeterministicStubModel()

    return bedrock.BedrockStrandsModel(
        boto_session=bedrock.build_session()
    )


def main(argv):
    if not argv or argv[0].startswith("--"):
        raise SystemExit("Usage: run_case.py <case_id> [--stub] [--record]")

    case_id = argv[0]
    model = build_model(argv)

    started = datetime.now(timezone.utc)

    record = {
        "started_at": started.isoformat(),
        "case_id": case_id,
        "model": (
            model.describe()
            if hasattr(model, "describe")
            else {
                "runner": model.runner,
                "model_id": model.model_id,
                "prompt_version": model.prompt_version,
            }
        ),
    }

    try:
        from app.domain import acting_agent
        from app.reviewer import workqueue

        with workqueue.app_connection() as conn:
            with conn.cursor() as cur:
                agent_profile_id = acting_agent.profile_id(cur)

        result = runner.execute(
            model, case_id, agent_profile_id=agent_profile_id
        )
    except bedrock.IdentityRefused as exc:
        record["outcome"] = {
            "ran": False,
            "stage": "identity gate",
            "error": str(exc),
            "note": "no Bedrock call was made and no run was recorded",
        }
        print(json.dumps(record, indent=2, default=str))
        return 3
    except runner.RunRefused as exc:
        record["outcome"] = {
            "ran": False,
            "stage": "context assembly",
            "error": str(exc),
        }
        print(json.dumps(record, indent=2, default=str))
        return 4

    record["outcome"] = {
        "ran": True,
        "run_id": result.run_id,
        "operation_id": result.operation_id,
        "runner": result.runner,
        "model_id": result.model_id,
        "result_state": result.result_state,
        "decision_state": result.decision_state,
        "proposal_id": result.proposal_id,
        "revision": result.revision,
        "knowledge_release_id": result.knowledge_release_id,
        "latency_ms": result.latency_ms,
        "failure_reason": result.failure_reason,
        "permitted_states": list(result.permitted_states),
        "tool_calls": [
            {
                "sequence": call["sequence"],
                "tool": call["tool_name"],
                "refused": bool(call["error"]),
                "error": call["error"],
            }
            for call in result.tool_calls
        ],
    }

    record["reviewer_view"] = f"/case/{case_id}"

    print(json.dumps(record, indent=2, default=str))

    if "--record" in argv:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        path = EVIDENCE_DIR / f"real-run-{stamp}.json"
        path.write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )
        print(f"\nRECORDED: {path.relative_to(REPO_ROOT)}")

    return 0 if result.result_state in ("SUCCEEDED", "REFUSED") else 5


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
