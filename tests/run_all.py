"""One command that runs every deterministic check in order.

The suites are deliberately ordered and stateful: each seeds fixtures,
asserts behaviour against them, and cleans up. That ordering is part of
what they prove, so they are run as a sequence rather than collected
and shuffled by a generic test framework.

Cleanups run last, after every assertion, so a failure leaves the
fixtures in place to inspect.

Exit code 0 means every check passed. Anything else means it did not,
and the failing step is named.

Usage:

    python tests/run_all.py
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STEPS = (
    ("schema is current", ["-m", "app.db.migrate", "verify"]),
    ("environment contract", ["tests/env_contract_smoke.py"]),
    ("applicability", ["tests/applicability_smoke.py"]),
    ("model provider", ["tests/model_provider_smoke.py"]),
    ("knowledge gaps", ["tests/gap_smoke.py"]),
    ("guard wiring", ["tests/guard_wiring_smoke.py"]),
    ("request surface", ["tests/request_surface_smoke.py"]),
    ("fact authority", ["tests/fact_authority_smoke.py"]),
    ("database integrity", ["tests/db_integrity_smoke.py", "phase1"]),
    ("authority boundaries", ["tests/authority_boundary_smoke.py", "phase1"]),
    ("agent slice end to end", ["tests/agent_slice_smoke.py", "phase1"]),
    ("ingestion", ["tests/ingestion_smoke.py", "phase1"]),
    ("reviewer console", ["tests/reviewer_console_smoke.py", "phase1"]),
    ("case access", ["tests/case_access_smoke.py", "phase1"]),
    ("decision receipt", ["tests/decision_receipt_smoke.py", "phase1"]),
    ("agent context", ["tests/agent_context_smoke.py", "phase1"]),
    ("enquiry eval contract",
     ["tests/enquiry_eval_contract_smoke.py"]),
    ("autonomy", ["tests/autonomy_smoke.py", "phase1"]),
    (
        "approval and dispatch",
        ["tests/approval_dispatch_smoke.py", "phase1"],
    ),
    (
        "approval fixture cleanup",
        ["tests/approval_dispatch_smoke.py", "cleanup"],
    ),
    ("autonomy fixture cleanup", ["tests/autonomy_smoke.py", "cleanup"]),
    ("case access cleanup", ["tests/case_access_smoke.py", "cleanup"]),
    ("receipt fixture cleanup",
     ["tests/decision_receipt_smoke.py", "cleanup"]),
    ("agent context cleanup",
     ["tests/agent_context_smoke.py", "cleanup"]),
    ("console fixture cleanup", ["tests/reviewer_console_smoke.py", "cleanup"]),
    ("ingestion fixture cleanup", ["tests/ingestion_smoke.py", "cleanup"]),
    ("agent fixture cleanup", ["tests/agent_slice_smoke.py", "cleanup"]),
    (
        "authority fixture cleanup",
        ["tests/authority_boundary_smoke.py", "cleanup"],
    ),
    ("database fixture cleanup", ["tests/db_integrity_smoke.py", "cleanup"]),
)


def run_step(label, args):
    print(f"\n===== {label} =====", flush=True)

    result = subprocess.run(
        [sys.executable] + args,
        cwd=str(REPO_ROOT),
        text=True,
    )

    return result.returncode == 0


def main():
    sys.stdout.reconfigure(line_buffering=True)

    outcomes = []

    for label, args in STEPS:
        passed = run_step(label, args)
        outcomes.append((label, passed))

        if not passed:
            break

    print("\n===== SUMMARY =====")

    for label, passed in outcomes:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")

    skipped = len(STEPS) - len(outcomes)

    if skipped:
        print(f"SKIPPED {skipped} step(s) after the first failure")

    if all(passed for _, passed in outcomes) and not skipped:
        print("\nALL CHECKS: PASS")
        return 0

    print("\nALL CHECKS: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
