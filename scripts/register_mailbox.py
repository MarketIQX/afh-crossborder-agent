"""Register a mailbox. Operator action, requires admin credentials.

The runtime role holds only SELECT on app.mailboxes, so ingestion
cannot invent a mailbox for itself. Deciding which mailbox this system
ingests from is an operator decision, so it lives here rather than in
the ingestion path.

Idempotent: registering the same address twice reports the existing row.

Usage:

    python scripts/register_mailbox.py someone@example.com [provider]
"""

import sys
import uuid
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402


def register(address, provider="gmail"):
    normalized = address.strip().lower()

    if not normalized or "@" not in normalized:
        raise SystemExit(f"Not an email address: {address!r}")

    settings = config.database_settings()

    with psycopg.connect(**settings.admin_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM app.mailboxes "
                "WHERE provider = %s AND address = %s",
                (provider, normalized),
            )
            row = cur.fetchone()

            if row:
                print(f"MAILBOX ALREADY REGISTERED: {normalized} {row[0]}")
                return row[0]

            mailbox_id = str(uuid.uuid4())

            cur.execute(
                "INSERT INTO app.mailboxes (id, provider, address) "
                "VALUES (%s, %s, %s)",
                (mailbox_id, provider, normalized),
            )
        conn.commit()

    print(f"MAILBOX REGISTERED: {normalized} {mailbox_id}")
    return mailbox_id


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(
            "Usage: register_mailbox.py <address> [provider]"
        )

    register(sys.argv[1], *sys.argv[2:])
