"""ENV01-ENV08. The repository must not lie about how to run it.

Every other suite here tests the software. This one tests the documents,
because they failed in a way the software could not detect.

Four files drifted independently: `app/config.py`, `.env.example`, the
README and BUILD_STATE. `.env.example` documented Gmail keys the running
system no longer had, and omitted the reviewer password and all four AWS
keys, two of which the identity gate requires. The README named a test
file that had been deleted, claimed check ranges that had moved, and
stated that no model had ever been invoked, hours after a real Bedrock
run. A judge following those instructions would have reached a system
that could not run and a document telling them that was expected.

None of that was catchable by a test, so none of it was caught. These
checks make each claim a document makes about the code into an assertion
about the code.

This suite touches no database and needs no credentials.

Usage:

    python tests/env_contract_smoke.py
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import generate_env_example  # noqa: E402

FAILURES = []
PASSES = []

# Keys read from the process environment by tooling that is allowed to
# sit outside the contract, plus the standard AWS credential variables
# that boto3 resolves for itself.
EXEMPT = config.DELIBERATELY_NOT_IN_CONTRACT | frozenset(
    {
        "AGENTS_TEST_TARGET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
        "PATH",
        "HOME",
        "USERPROFILE",
        "TMPDIR",
    }
)

# Every way the codebase reads a named setting.
READERS = re.compile(
    r"""(?:config\.(?:get|require)|require_env|os\.environ\.get)"""
    r"""\(\s*["']([A-Z][A-Z0-9_]*)["']"""
)


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


def source_files():
    for folder in ("app", "scripts", "tests"):
        for path in sorted((REPO_ROOT / folder).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue

            yield path


def env01_example_is_generated():
    """`.env.example` matches what the generator would write."""
    target = REPO_ROOT / ".env.example"
    current = target.read_text(encoding="utf-8") if target.exists() else ""

    check(
        "ENV01 .env.example IS GENERATED FROM THE CONTRACT",
        current == generate_env_example.render(),
        "run: python scripts/generate_env_example.py",
    )


def env02_secret_lists_agree():
    """`_SECRET_KEYS` and the contract's secret flags cannot disagree."""
    from_contract = {k.name for k in config.CONTRACT if k.secret}

    check(
        "ENV02 SECRET KEY LISTS AGREE",
        from_contract == set(config._SECRET_KEYS),
        f"contract={sorted(from_contract)} "
        f"module={sorted(config._SECRET_KEYS)}",
    )


def env03_no_secret_values_in_example():
    """No secret in `.env.example` carries anything usable."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    leaked = []

    for key in config.CONTRACT:
        if not key.secret:
            continue

        for line in text.splitlines():
            if not line.startswith(f"{key.name}="):
                continue

            value = line.partition("=")[2].strip()

            if value and not value.startswith("change-me"):
                leaked.append(key.name)

    check(
        "ENV03 NO USABLE SECRET IN .env.example",
        not leaked,
        f"these carry non-placeholder values: {leaked}",
    )


def env04_no_real_account_id_in_example():
    """The public example must not carry a real AWS account id."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    found = set(re.findall(r"\b\d{12}\b", text))
    real = {value for value in found if value != "000000000000"}

    check(
        "ENV04 NO REAL AWS ACCOUNT ID IN .env.example",
        not real,
        f"twelve digit values that are not the placeholder: {sorted(real)}",
    )


def env05_every_read_key_is_declared():
    """Every setting the code reads is declared or explicitly exempt."""
    undeclared = {}

    for path in source_files():
        text = path.read_text(encoding="utf-8", errors="replace")

        for name in READERS.findall(text):
            if name in config.CONTRACT_BY_NAME or name in EXEMPT:
                continue

            undeclared.setdefault(name, str(path.relative_to(REPO_ROOT)))

    check(
        "ENV05 EVERY SETTING READ IS DECLARED",
        not undeclared,
        f"undeclared: {undeclared}",
    )


def env06_readme_test_files_exist():
    """Every test file the README names must exist."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    named = set(re.findall(r"(tests/[a-z_]+\.py)", text))
    missing = sorted(n for n in named if not (REPO_ROOT / n).exists())

    check(
        "ENV06 README NAMES ONLY TEST FILES THAT EXIST",
        not missing and named,
        f"named={sorted(named)} missing={missing}",
    )


def env07_readme_check_ranges_are_true():
    """Each range the README claims matches the ids that suite defines."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    claims = re.findall(
        r"(tests/[a-z_]+\.py)[^\n]*?([A-Z]+)(\d{2})-[A-Z]*(\d{2})", text
    )
    wrong = []

    for rel, prefix, low, high in claims:
        path = REPO_ROOT / rel

        if not path.exists():
            wrong.append(f"{rel} missing")
            continue

        body = path.read_text(encoding="utf-8", errors="replace")
        found = sorted(
            int(n) for n in re.findall(rf"\b{prefix}(\d{{2}})\b", body)
        )

        if not found:
            wrong.append(f"{rel} declares no {prefix} ids")
            continue

        if (found[0], found[-1]) != (int(low), int(high)):
            wrong.append(
                f"{rel} claims {prefix}{low}-{high} "
                f"but defines {prefix}{found[0]:02d}-{prefix}{found[-1]:02d}"
            )

    check(
        "ENV07 README CHECK RANGES MATCH THE SUITES",
        not wrong and claims,
        f"claims={len(claims)} wrong={wrong}",
    )


def env08_readme_does_not_claim_stub_only():
    """The README must not deny a real model run once one has happened."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    evidence = sorted((REPO_ROOT / "docs" / "evidence").glob("real-run-*.json"))

    denials = [
        "no model has been invoked",
        "every agent run recorded so far is stamped\n`DETERMINISTIC_STUB`",
        "every agent run recorded so far is stamped `DETERMINISTIC_STUB`",
    ]
    present = [d for d in denials if d in readme]

    check(
        "ENV08 README DOES NOT DENY A REAL RUN THAT HAPPENED",
        not (evidence and present),
        f"{len(evidence)} real-run evidence files exist, "
        f"but README still says: {present}",
    )


CHECKS = (
    env01_example_is_generated,
    env02_secret_lists_agree,
    env03_no_secret_values_in_example,
    env04_no_real_account_id_in_example,
    env05_every_read_key_is_declared,
    env06_readme_test_files_exist,
    env07_readme_check_ranges_are_true,
    env08_readme_does_not_claim_stub_only,
)


def main():
    for run in CHECKS:
        run()

    for name in PASSES:
        print(f"PASS  {name}")

    for line in FAILURES:
        print(f"FAIL  {line}")

    print(
        f"\nENV CONTRACT: {len(PASSES)} passed, {len(FAILURES)} failed"
    )

    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
