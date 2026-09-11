"""Run the checks and record evidence tied to the source that produced it.

"The command printed PASS" is not durable evidence. It does not say
which source, which dependencies, which migrations or which database
produced it, so it cannot be re-checked later or matched to a commit.

This runs the suite and writes a scrubbed record containing the git
state, the SHA-256 of every source file that would be committed, the
dependency lock digest, every migration and its digest, the test target
description with no credentials, and the full captured output.

Counts are reported honestly: the runner's steps are not independent
tests, so the individual check identifiers are extracted and counted
separately.

Usage:

    python scripts/record_evidence.py
    python scripts/record_evidence.py --out docs/evidence
"""

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT = REPO_ROOT / "docs" / "evidence"

CHECK_RE = re.compile(
    r"^((?:DB|AUTH|AGENT|APPROVE|INGEST|VIEW)\d{2}[^:\r\n]*): (PASS|FAIL)",
    re.MULTILINE,
)


def git(*args):
    result = subprocess.run(
        ["git"] + list(args),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inventory():
    """Every file git would commit, with its digest."""
    modified = git("diff", "--name-only").split()
    untracked = git("ls-files", "-o", "--exclude-standard").split()
    tracked = git("ls-files").split()

    inventory = {}

    for relative in sorted(set(modified) | set(untracked) | set(tracked)):
        path = REPO_ROOT / relative

        if path.is_file():
            inventory[relative] = digest(path)[:16]

    return inventory


def migrations():
    directory = REPO_ROOT / "db" / "migrations"

    return {
        path.name: digest(path)[:16]
        for path in sorted(directory.glob("*.sql"))
    }


def main(argv):
    out_dir = Path(
        argv[argv.index("--out") + 1] if "--out" in argv else DEFAULT_OUT
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)

    print("Running the full clean-slate verification. This takes a while.")

    completed = subprocess.run(
        [sys.executable, "scripts/verify_clean_slate.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )

    finished = datetime.now(timezone.utc)
    output = completed.stdout + completed.stderr

    checks = CHECK_RE.findall(output)
    passed = [name for name, verdict in checks if verdict == "PASS"]
    failed = [name for name, verdict in checks if verdict == "FAIL"]

    inventory = source_inventory()

    record = {
        "recorded_at": started.isoformat(),
        "duration_seconds": round(
            (finished - started).total_seconds(), 1
        ),
        "verdict": "PASS" if completed.returncode == 0 else "FAIL",
        "exit_code": completed.returncode,
        "git": {
            "head": git("rev-parse", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain")),
            "dirty_files": git("status", "--porcelain").splitlines(),
        },
        "python": sys.version.split()[0],
        "dependency_lock_sha256": digest(
            REPO_ROOT / "requirements-lock.txt"
        ),
        "migrations": migrations(),
        "target": {
            "kind": "disposable container provisioned by "
            "verify_clean_slate.py",
            "note": "no credentials recorded",
        },
        "checks": {
            "individual_passed": len(passed),
            "individual_failed": len(failed),
            "identifiers": passed,
            "failed_identifiers": failed,
            "note": "runner steps are not independent tests; the counts "
            "above are individual check identifiers",
        },
        "source_files": len(inventory),
    }

    stamp = started.strftime("%Y%m%dT%H%M%SZ")

    json_path = out_dir / f"evidence-{stamp}.json"
    log_path = out_dir / f"evidence-{stamp}.log"
    inventory_path = out_dir / f"evidence-{stamp}-sources.json"

    json_path.write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    log_path.write_text(output, encoding="utf-8")
    inventory_path.write_text(
        json.dumps(inventory, indent=2), encoding="utf-8"
    )

    print(f"\nVERDICT: {record['verdict']}")
    print(
        f"INDIVIDUAL CHECKS: {len(passed)} passed, {len(failed)} failed"
    )
    print(f"SOURCE FILES DIGESTED: {len(inventory)}")
    print(f"RECORD:    {json_path.relative_to(REPO_ROOT)}")
    print(f"OUTPUT:    {log_path.relative_to(REPO_ROOT)}")
    print(f"INVENTORY: {inventory_path.relative_to(REPO_ROOT)}")

    return completed.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
