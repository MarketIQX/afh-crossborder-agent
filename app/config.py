"""Single place the application reads environment configuration.

Rules enforced here:

- The real process environment always wins over the `.env` file, so CI
  and deployment can override without editing files.
- Required settings fail fast with the name of the missing key. Values
  are never printed, logged or included in exception messages.
- Every database consumer uses the same connection contract, so a test
  and the application can never disagree about which database they are
  talking to.
"""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

_SECRET_KEYS = frozenset(
    {
        "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_APP_PASSWORD",
        "POSTGRES_REVIEWER_PASSWORD",
    }
)

_file_cache = None


def _load_env_file():
    """Parse KEY=VALUE lines from `.env`. Missing file is not an error."""
    global _file_cache

    if _file_cache is not None:
        return _file_cache

    parsed = {}

    if ENV_FILE.exists():
        text = ENV_FILE.read_text(encoding="utf-8")

        for raw_line in text.splitlines():
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]

            if key:
                parsed[key] = value

    _file_cache = parsed
    return _file_cache


def get(name, default=None):
    """Return a setting from the process environment, then `.env`."""
    if name in os.environ:
        return os.environ[name]

    return _load_env_file().get(name, default)


def require(name):
    """Return a setting or exit with a message that names only the key."""
    value = get(name)

    if value is None or value == "":
        raise SystemExit(
            f"Missing required configuration: {name}. "
            f"Set it in the environment or in {ENV_FILE.name}."
        )

    return value


def describe_presence(names):
    """Report which keys are set without revealing any value."""
    report = {}

    for name in names:
        value = get(name)
        present = value is not None and value != ""

        if name in _SECRET_KEYS:
            report[name] = "SET" if present else "MISSING"
        else:
            report[name] = value if present else "MISSING"

    return report


@dataclass(frozen=True)
class DatabaseSettings:
    """Resolved connection settings for one PostgreSQL instance."""

    host: str
    port: int
    dbname: str
    admin_user: str
    app_user: str
    reviewer_user: str

    def admin_kwargs(self):
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.admin_user,
            "password": require("POSTGRES_ADMIN_PASSWORD"),
        }

    def app_kwargs(self):
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.app_user,
            "password": require("POSTGRES_APP_PASSWORD"),
        }

    def reviewer_kwargs(self):
        """Connection for actions only a reviewer may perform."""
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.reviewer_user,
            "password": require("POSTGRES_REVIEWER_PASSWORD"),
        }

    def target(self):
        """Human readable target with no credential material."""
        return f"{self.host}:{self.port}/{self.dbname}"


def database_settings():
    """Build database settings from the environment contract."""
    return DatabaseSettings(
        host=get("POSTGRES_HOST", "127.0.0.1"),
        port=int(get("POSTGRES_PORT", "5433")),
        dbname=get("POSTGRES_DB", "agents_for_humans"),
        admin_user=get("POSTGRES_ADMIN_USER", "agents_admin"),
        app_user=get("POSTGRES_APP_USER", "agents_app"),
        reviewer_user=get(
            "POSTGRES_REVIEWER_USER", "agents_reviewer"
        ),
    )


# ---------------------------------------------------------------------
# The declared contract.
#
# Four documents drifted apart because each was maintained by hand:
# this module, `.env.example`, the README and BUILD_STATE. The fix is
# not to correct them once but to give them a single source. Every
# setting the software reads is declared here, `.env.example` is
# generated from it, and a check fails if any call site reads a key
# that is not declared.
#
# `secret` decides two things: the value is redacted in any presence
# report, and `.env.example` must never carry a usable value for it.
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ContractKey:
    """One environment setting the software reads."""

    name: str
    group: str
    required: bool
    secret: bool
    example: str
    note: str


CONTRACT = (
    ContractKey(
        "POSTGRES_ADMIN_PASSWORD", "database", True, True,
        "change-me-admin",
        "Owns the schema. Used by bootstrap and migrations only.",
    ),
    ContractKey(
        "POSTGRES_APP_PASSWORD", "database", True, True,
        "change-me-app",
        "The runtime role. Can draft and dispatch, cannot approve.",
    ),
    ContractKey(
        "POSTGRES_REVIEWER_PASSWORD", "database", True, True,
        "change-me-reviewer",
        "The reviewer role. Can approve, cannot dispatch.",
    ),
    ContractKey(
        "POSTGRES_HOST", "database", False, False, "127.0.0.1",
        "Defaults to 127.0.0.1.",
    ),
    ContractKey(
        "POSTGRES_PORT", "database", False, False, "5433",
        "Defaults to 5433, matching docker-compose.",
    ),
    ContractKey(
        "POSTGRES_DB", "database", False, False, "agents_for_humans",
        "Defaults to agents_for_humans.",
    ),
    ContractKey(
        "POSTGRES_ADMIN_USER", "database", False, False, "agents_admin",
        "Defaults to agents_admin.",
    ),
    ContractKey(
        "POSTGRES_APP_USER", "database", False, False, "agents_app",
        "Defaults to agents_app.",
    ),
    ContractKey(
        "POSTGRES_REVIEWER_USER", "database", False, False,
        "agents_reviewer", "Defaults to agents_reviewer.",
    ),
    ContractKey(
        "AWS_REGION", "bedrock", False, False, "us-east-1",
        "Region for Bedrock. Defaults to us-east-1.",
    ),
    ContractKey(
        "AWS_PROFILE", "bedrock", False, False, "your-aws-profile",
        "Named profile in your AWS credentials file. Optional.",
    ),
    ContractKey(
        "BEDROCK_MODEL_ID", "bedrock", False, False,
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "Inference profile id. Defaults to Claude Sonnet 4.5.",
    ),
    ContractKey(
        "AFH_AWS_ACCOUNT_ID", "bedrock", True, False, "000000000000",
        "Required by the identity gate. Your own 12 digit account id. "
        "Without it no Bedrock call is attempted at all.",
    ),
    ContractKey(
        "AFH_AWS_EXPECTED_PRINCIPAL", "bedrock", True, False,
        "arn:aws:iam::000000000000:user/your-deploy-user",
        "Required by the identity gate. The exact caller ARN expected. "
        "Root is refused even when it matches the account.",
    ),
    ContractKey(
        "GOOGLE_OAUTH_CLIENT_FILE", "gmail", True, False,
        "C:\\path\\outside\\repo\\google_oauth_client.json",
        "OAuth client JSON from Google Cloud. Keep it out of the repo.",
    ),
    ContractKey(
        "GMAIL_TOKEN_FILE", "gmail", True, False,
        "C:\\path\\outside\\repo\\gmail_token.json",
        "Where the authorised token is persisted. Out of the repo.",
    ),
    ContractKey(
        "GMAIL_EXPECTED_ADDRESS", "gmail", True, False,
        "agent-mailbox@example.com",
        "The mailbox the token must belong to. Checked before any "
        "read or send, so a token for the wrong account is refused.",
    ),
)

CONTRACT_BY_NAME = {key.name: key for key in CONTRACT}

GROUP_ORDER = ("database", "bedrock", "gmail")

GROUP_TITLES = {
    "database": "PostgreSQL. Required for everything.",
    "bedrock": "AWS Bedrock. Required to run the agent.",
    "gmail": "Gmail. Required to ingest real mail or send a real reply.",
}

# Settings that gate a real outbound effect are deliberately absent
# from the contract. They are read straight from the process
# environment by the tools that send, so that authorising a real send
# is always a deliberate act in a shell and can never be inherited
# from a file that someone copied.
DELIBERATELY_NOT_IN_CONTRACT = frozenset(
    {
        "GMAIL_EXTERNAL_SEND_APPROVED",
        "GMAIL_EXTERNAL_TEST_RECIPIENT",
        "GMAIL_SEND_SMOKE_APPROVED",
        "GMAIL_TEST_RECIPIENT",
        "GMAIL_ROUNDTRIP_STATE_FILE",
    }
)


@dataclass(frozen=True)
class GmailSettings:
    """Resolved Gmail settings. Built only when Gmail is actually used."""

    client_file: Path
    token_file: Path
    expected_address: str


def gmail_settings():
    """Build Gmail settings, failing fast and naming any missing key."""
    return GmailSettings(
        client_file=Path(require("GOOGLE_OAUTH_CLIENT_FILE")),
        token_file=Path(require("GMAIL_TOKEN_FILE")),
        expected_address=require("GMAIL_EXPECTED_ADDRESS").strip().lower(),
    )
