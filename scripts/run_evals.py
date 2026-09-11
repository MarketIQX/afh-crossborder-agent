"""Score the verification graph against the golden suite, and keep it.

    python scripts/run_evals.py                 # real Bedrock
    python scripts/run_evals.py --history       # past scores only
    python scripts/run_evals.py --dry-run       # what would be scored

Reports. Does not gate. A threshold in a script invites the threshold to
be lowered, and this suite is small enough that one genuinely hard case
could drag any number somebody picked.
"""

import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.evals import runner as evals  # noqa: E402

SUITE = "CLAIM_VERIFICATION"


def runtime_connection():
    return psycopg.connect(
        **config.database_settings().app_kwargs(), autocommit=True
    )


def show_history(conn):
    rows = evals.history(conn, SUITE)

    if not rows:
        print("No scores recorded yet.")
        return

    print(f"\n{SUITE}, newest first\n")
    print(f"  {'when':17} {'commit':9} {'score':>6}  "
          f"{'pass':>4} {'fail':>4} {'err':>4}  model")

    for row in rows:
        when = row["started_at"].strftime("%d %b %H:%M")
        print(
            f"  {when:17} {row['git_commit'] or '-':9} "
            f"{str(row['score']) + '%':>6}  "
            f"{row['passed']:>4} {row['failed']:>4} {row['errored']:>4}  "
            f"{row['model_id'][:42]}"
        )


def build_verify():
    """The real graph, on Bedrock."""
    from strands import Agent
    from strands.models import BedrockModel

    from app.agent import bedrock
    from app.training import verify_graph

    region = config.get("AWS_REGION", bedrock.DEFAULT_REGION)
    session = bedrock.build_session(region)
    bedrock.enforce_identity(session)

    model_id = config.get("BEDROCK_MODEL_ID", bedrock.DEFAULT_MODEL_ID)

    def factory(system_prompt):
        return Agent(
            model=BedrockModel(
                model_id=model_id,
                boto_session=bedrock.with_region(session, region),
                max_tokens=512,
                temperature=0.0,
                streaming=False,
            ),
            system_prompt=system_prompt,
        )

    def verify(statement, topic, passages):
        return verify_graph.verify(factory, statement, topic, passages)

    return verify, "BEDROCK_STRANDS", model_id


def main(argv):
    if "--history" in argv:
        with runtime_connection() as conn:
            show_history(conn)

        return 0

    suite = evals.load("claim_verification")

    if "--dry-run" in argv:
        print(f"{suite['suite']}: {len(suite['cases'])} case(s)\n")

        for case in suite["cases"]:
            print(f"  {case['key']:30} expects {case['expected']}")
            print(f"     {case['claim'][:76]}")

        return 0

    verify, runner_name, model_id = build_verify()

    print(f"scoring {len(suite['cases'])} case(s) on {model_id}")
    print("two challengers per case, run concurrently\n")

    with runtime_connection() as conn:
        result = evals.run_claim_verification(
            conn, verify, runner_name, model_id
        )

    for row in result["rows"]:
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "ERROR": "ERR "}[
            row["outcome"]
        ]
        print(f"  {mark} {row['key']:30} "
              f"expected {row['expected']:14} got {row['observed'] or '-'}")

        if row["outcome"] != "PASS":
            print(f"       why it exists: {row['rationale'][:120]}")
            print(f"       what happened: {row['detail'][:120]}")

    score = (
        round(100 * result["passed"] / result["total"])
        if result["total"]
        else 0
    )

    print(
        f"\n{SUITE}: {result['passed']}/{result['total']} = {score}%"
        f"  (failed {result['failed']}, errored {result['errored']})"
    )
    print(f"recorded as run {result['run_id'][:8]}")

    with runtime_connection() as conn:
        show_history(conn)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
