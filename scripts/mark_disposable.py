"""Declare a database disposable, so test suites may write to it.

This is an administrator action and it is deliberately awkward. The
database name must be typed as an argument and must match the database
the connection actually reaches, so a misconfigured environment cannot
mark something unintended.

Marking a database disposable permits the test suites to create and
delete rows in it. Never run this against an instance holding anything
you would miss.

Usage:

    python scripts/mark_disposable.py agents_for_humans_test
    python scripts/mark_disposable.py <dbname> --unmark
"""

import secrets
import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402


def mark(confirmed_name, note=None):
    settings = config.database_settings()

    with psycopg.connect(**settings.admin_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            current = cur.fetchone()[0]

            if confirmed_name != current:
                raise SystemExit(
                    f"REFUSING: you typed {confirmed_name!r} but this "
                    f"connection reaches {current!r}. Nothing was marked."
                )

            cur.execute(
                f"SELECT to_regclass('{testguard.MARKER_TABLE}') "
                f"IS NOT NULL"
            )

            if not cur.fetchone()[0]:
                raise SystemExit(
                    "The marker table does not exist. Run "
                    "`python -m app.db.migrate apply` first."
                )

            identity = testguard.server_identity(cur)

            if identity is None:
                raise SystemExit(
                    "REFUSING: cannot read this cluster's system "
                    "identifier, so the mark could not be tied to a "
                    "specific server. Nothing was marked."
                )

            token = secrets.token_hex(8)

            cur.execute(
                f"INSERT INTO {testguard.MARKER_TABLE} "
                f"(dbname, purpose, token, note, system_identifier) "
                f"VALUES (%s, %s, %s, %s, %s) "
                f"ON CONFLICT (id) DO UPDATE SET "
                f"dbname = EXCLUDED.dbname, purpose = EXCLUDED.purpose, "
                f"token = EXCLUDED.token, marked_at = now(), "
                f"marked_by = current_user, note = EXCLUDED.note, "
                f"system_identifier = EXCLUDED.system_identifier",
                (current, testguard.PURPOSE, token, note, identity),
            )
        conn.commit()

    print(f"MARKED DISPOSABLE: {current} token={token}")
    print(f"  server cluster identifier: {identity}")
    print(
        "  Confirm that is the throwaway server you intended. The mark "
        "authorises fixture writes on that cluster only."
    )
    print(
        "Test suites may now create and delete rows here. Export the "
        "matching intent before running them:"
    )
    print(f"  {testguard.ENV_TARGET}={current}")

    return token


def unmark(confirmed_name):
    settings = config.database_settings()

    with psycopg.connect(**settings.admin_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            current = cur.fetchone()[0]

            if confirmed_name != current:
                raise SystemExit(
                    f"REFUSING: you typed {confirmed_name!r} but this "
                    f"connection reaches {current!r}."
                )

            cur.execute(f"DELETE FROM {testguard.MARKER_TABLE}")
        conn.commit()

    print(f"UNMARKED: {current}. Test suites will now refuse to write.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: mark_disposable.py <dbname> [--unmark] [note]"
        )

    name = sys.argv[1]

    if "--unmark" in sys.argv:
        unmark(name)
    else:
        extra = [a for a in sys.argv[2:] if not a.startswith("--")]
        mark(name, extra[0] if extra else None)
