"""Scan everything git would commit for credential material.

Two independent checks:

1. Literal leakage. Do the actual secret values in `.env` appear in a
   file that would be committed? This is the check that matters most,
   because a pattern scanner will not catch a password that looks like
   an ordinary word.

2. Pattern leakage. AWS keys, private key blocks, OAuth client secrets,
   refresh tokens, and assignments that look like real credentials.

Only values whose *key* indicates a secret are treated as secret. `.env`
also carries configuration such as a region, a profile name and an
account id, and treating those as secrets produced false positives. A
scanner that cries wolf is worse than none, because it teaches you to
ignore it.

Usage:

    python scripts/secret_scan.py
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SECRET_KEY_MARKERS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "CREDENTIAL",
    "API_KEY",
)

PATTERNS = (
    ("aws access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("oauth client secret", re.compile(r"\"client_secret\"\s*:")),
    ("oauth refresh token", re.compile(r"\"refresh_token\"\s*:")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}")),
    (
        "credential assignment",
        re.compile(
            r"(?i)\b(password|passwd|secret|api_key|apikey|token)\s*"
            r"[=:]\s*[\"'][^\"'\s]{8,}[\"']"
        ),
    ),
)

ALLOWED_ASSIGNMENT_HINTS = (
    "POSTGRES_ADMIN_PASSWORD",
    "POSTGRES_APP_PASSWORD",
    "POSTGRES_REVIEWER_PASSWORD",
    "os.environ",
    "config.require",
    "config.get",
)


def tracked_and_new():
    """Files git would include: modified tracked plus non-ignored new."""

    def run(*args):
        return subprocess.run(
            ["git", *args],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()

    return sorted(
        set(run("diff", "--name-only"))
        | set(run("ls-files", "-o", "--exclude-standard"))
        | set(run("ls-files"))
    )


def secret_values():
    """Values from `.env` whose key marks them as secret."""
    env_file = REPO / ".env"

    if not env_file.exists():
        return []

    values = []

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip().upper()
        value = value.strip().strip("\"'")

        if not any(marker in key for marker in SECRET_KEY_MARKERS):
            continue

        if len(value) >= 8:
            values.append(value)

    return values


def main():
    files = tracked_and_new()
    secrets = secret_values()

    print(f"SCANNING {len(files)} file(s) that git would commit")
    print(f"CHECKING {len(secrets)} secret value(s) from .env")

    findings = []

    for relative in files:
        path = REPO / relative

        if not path.is_file():
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"  unreadable: {relative} ({exc.__class__.__name__})")
            continue

        for value in secrets:
            if value in text:
                findings.append(
                    (relative, "LITERAL SECRET FROM .env", "redacted")
                )

        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                if label == "credential assignment" and any(
                    hint in text[max(0, match.start() - 120) : match.end()]
                    for hint in ALLOWED_ASSIGNMENT_HINTS
                ):
                    continue

                findings.append((relative, label, match.group(0)[:60]))

    if not findings:
        print("\nSECRET SCAN: PASS. No credential material found.")
        return 0

    print(f"\nSECRET SCAN: {len(findings)} finding(s)")

    for relative, label, snippet in findings:
        print(f"  {relative}: {label}: {snippet}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
