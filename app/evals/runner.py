"""Run a golden suite, score it, and keep the score.

The 120 checks in this repository prove the system refuses what it
should refuse. That is the harder half and not the whole job: a system
that refused everything would pass all of them. An eval scores what it
accepts.

Three decisions worth stating.

Every result is recorded, including a run that scores badly. The value
of history is entirely in the bad runs staying in it; a score board that
only keeps good days is marketing.

A case carries the reason it exists. A golden case whose purpose nobody
remembers is deleted the first time it fails, usually by someone in a
hurry who assumes it was wrong.

Nothing here decides whether to ship. It reports. A gate that blocks on
a number invites the number to be adjusted, and this suite is small
enough that one genuinely hard case could drag it under any threshold
somebody picked.
"""

import json
import subprocess
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SUITE_DIR = REPO_ROOT / "evals"

PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"


def git_commit():
    """Which commit produced this score. Empty if that cannot be known."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )

        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def load(name):
    path = SUITE_DIR / f"{name}.json"

    if not path.is_file():
        raise FileNotFoundError(f"no eval suite at {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def start_run(conn, suite, runner, model_id, prompt_version="",
              prompt_digest="", notes=""):
    run_id = str(uuid.uuid4())

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app.eval_runs (
                id, suite, runner, model_id, prompt_version,
                prompt_digest, git_commit, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                suite,
                runner,
                model_id,
                prompt_version,
                prompt_digest,
                git_commit(),
                notes,
            ),
        )

    return run_id


def record(conn, run_id, case_key, expected, observed, outcome,
           rationale="", latency_ms=None, detail="", usage=None):
    usage = usage or {}

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app.eval_results (
                id, eval_run_id, case_key, expected, observed, outcome,
                rationale, latency_ms, input_tokens, output_tokens, detail
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                run_id,
                case_key,
                expected,
                observed,
                outcome,
                rationale[:900],
                latency_ms,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                detail[:900],
            ),
        )


def finish_run(conn, run_id, total, passed, failed, errored):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app.eval_runs SET ended_at = now(), total = %s, "
            "passed = %s, failed = %s, errored = %s WHERE id = %s",
            (total, passed, failed, errored, run_id),
        )


def run_claim_verification(conn, verify_callable, runner, model_id,
                           suite_name="claim_verification"):
    """Score the verification graph against the golden claims.

    `verify_callable(statement, topic, passages)` returns
    (verdict, reason, usage), so the same runner scores the real graph
    and any stub.
    """
    suite = load(suite_name)
    passages = suite["passages"]

    run_id = start_run(
        conn, suite["suite"], runner, model_id,
        notes=suite.get("about", "")[:400],
    )

    tallies = {PASS: 0, FAIL: 0, ERROR: 0}
    rows = []

    for case in suite["cases"]:
        passage_text = passages[case["passage"]]
        cited = [{"ordinal": 1, "passage": passage_text}]

        started = time.monotonic()

        try:
            verdict, reason, usage = verify_callable(
                case["claim"], case["topic"], cited
            )
            outcome = PASS if verdict == case["expected"] else FAIL
            detail = reason
        except Exception as exc:  # noqa: BLE001
            verdict = ""
            usage = {}
            outcome = ERROR
            detail = f"{exc.__class__.__name__}: {exc}"

        latency = int((time.monotonic() - started) * 1000)
        tallies[outcome] += 1

        record(
            conn,
            run_id,
            case["key"],
            case["expected"],
            verdict,
            outcome,
            rationale=case.get("rationale", ""),
            latency_ms=latency,
            detail=detail,
            usage=usage,
        )

        rows.append(
            {
                "key": case["key"],
                "expected": case["expected"],
                "observed": verdict,
                "outcome": outcome,
                "latency_ms": latency,
                "detail": detail,
                "rationale": case.get("rationale", ""),
            }
        )

    finish_run(
        conn,
        run_id,
        len(suite["cases"]),
        tallies[PASS],
        tallies[FAIL],
        tallies[ERROR],
    )

    return {
        "run_id": run_id,
        "suite": suite["suite"],
        "total": len(suite["cases"]),
        "passed": tallies[PASS],
        "failed": tallies[FAIL],
        "errored": tallies[ERROR],
        "rows": rows,
    }


def history(conn, suite, limit=10):
    """Previous scores for this suite, newest first.

    One number is a feeling. A number next to the last nine is evidence,
    and a change of direction is the thing worth seeing.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT started_at, runner, model_id, git_commit,
                   total, passed, failed, errored
            FROM app.eval_runs
            WHERE suite = %s AND ended_at IS NOT NULL
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (suite, limit),
        )

        return [
            {
                "started_at": row[0],
                "runner": row[1],
                "model_id": row[2],
                "git_commit": row[3],
                "total": row[4],
                "passed": row[5],
                "failed": row[6],
                "errored": row[7],
                "score": (
                    round(100 * row[5] / row[4]) if row[4] else None
                ),
            }
            for row in cur.fetchall()
        ]
