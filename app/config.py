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
