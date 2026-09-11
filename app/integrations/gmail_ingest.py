"""Gmail adapter for the ingestion core.

This is the only place Gmail message shapes are understood. It maps
provider payloads onto `ProviderMessage` and hands them to
`app.ingestion.core`, which owns all persistence and correlation.

Read-only by construction: it requests the readonly scope, and it holds
no send capability. Ingestion must never be able to reply.

It is not exercised by the deterministic test suite, because it needs a
live OAuth token and a real mailbox. The ingestion behaviour it depends
on is tested directly against the core with fabricated provider
messages, which is where the logic actually lives.

Usage:

    python -m app.integrations.gmail_ingest            # ingest a batch
    python -m app.integrations.gmail_ingest --dry-run  # fetch, no write
"""

import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app import config
from app.ingestion import core

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

MAX_MESSAGES = 25


def _credentials():
    token_file = Path(config.require("GMAIL_TOKEN_FILE"))

    if not token_file.is_file():
        raise SystemExit(f"Gmail token not found: {token_file}")

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds.valid:
        return creds

    # An expired access token is the normal state: they last about an
    # hour. Exchanging the refresh token for a new one involves no
    # person and no consent screen, so it is not the thing that has to
    # be deliberate.
    if not creds.refresh_token:
        raise SystemExit(
            "Stored Gmail credential has expired and carries no refresh "
            "token. Reauthorize deliberately:\n"
            "    python -m app.integrations.gmail_auth_smoke"
        )

    try:
        creds.refresh(Request())
    except RefreshError as exc:
        # This is what withdrawn consent, a revoked grant or an expired
        # refresh token actually look like. Stop, and make a human do it.
        raise SystemExit(
            f"Gmail refused to refresh the stored credential ({exc}). "
            "Consent was withdrawn or the refresh token expired. "
            "Reauthorize deliberately:\n"
            "    python -m app.integrations.gmail_auth_smoke"
        ) from exc

    # Deliberately not persisted. Writing tokens stays the sole
    # responsibility of the auth flow, which only saves one after the
    # Gmail API has proved it works.
    return creds


def _service_for_expected_mailbox():
    """Build the client and refuse to act on the wrong mailbox."""
    expected = config.require("GMAIL_EXPECTED_ADDRESS").strip().lower()

    service = build(
        "gmail", "v1", credentials=_credentials(), cache_discovery=False
    )

    profile = service.users().getProfile(userId="me").execute()
    actual = (profile.get("emailAddress") or "").strip().lower()

    if actual != expected:
        raise SystemExit(
            f"MAILBOX IDENTITY FAIL: expected {expected}, got {actual}"
        )

    return service, actual


def _header(payload, name):
    for entry in payload.get("headers", []):
        if entry.get("name", "").lower() == name.lower():
            return entry.get("value")

    return None


def _decode(data):
    if not data:
        return None

    padded = data + "=" * (-len(data) % 4)

    try:
        return base64.urlsafe_b64decode(padded).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, TypeError):
        return None


def _plain_text(payload):
    """Depth-first search for the first text/plain part."""
    if payload.get("mimeType") == "text/plain":
        text = _decode(payload.get("body", {}).get("data"))

        if text:
            return text

    for part in payload.get("parts", []) or []:
        text = _plain_text(part)

        if text:
            return text

    return None


def _to_provider_message(raw):
    payload = raw.get("payload", {})

    recipients = [
        address
        for address in (
            core.email_only(_header(payload, "To")),
            core.email_only(_header(payload, "Cc")),
        )
        if address
    ]

    internal_ms = int(raw.get("internalDate", "0"))

    return core.ProviderMessage(
        provider_message_id=raw["id"],
        provider_thread_id=raw.get("threadId"),
        rfc_message_id=core.email_only(_header(payload, "Message-ID"))
        or None,
        in_reply_to=_header(payload, "In-Reply-To"),
        references_header=_header(payload, "References"),
        sender_address=core.email_only(_header(payload, "From")),
        recipient_addresses=tuple(recipients) or ("unknown@invalid",),
        subject=_header(payload, "Subject"),
        body_text=_plain_text(payload),
        received_at=datetime.fromtimestamp(
            internal_ms / 1000, tz=timezone.utc
        ),
    )


def _resolve_mailbox(conn, address):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text FROM app.mailboxes "
            "WHERE provider = 'gmail' AND address = %s",
            (address,),
        )
        row = cur.fetchone()

    if row is None:
        raise SystemExit(
            f"Mailbox {address} is not registered. The runtime role "
            f"cannot create mailboxes. Run: "
            f"python scripts/register_mailbox.py {address}"
        )

    return row[0]


def fetch(service, cursor):
    """Fetch a bounded batch of messages newer than the cursor."""
    query = {"userId": "me", "maxResults": MAX_MESSAGES}

    if cursor:
        query["q"] = f"after:{cursor}"

    listing = service.users().messages().list(**query).execute()
    stubs = listing.get("messages", []) or []

    messages = []

    for stub in stubs:
        raw = (
            service.users()
            .messages()
            .get(userId="me", id=stub["id"], format="full")
            .execute()
        )
        messages.append(_to_provider_message(raw))

    messages.sort(key=lambda message: message.received_at)

    return messages


def main(argv):
    dry_run = "--dry-run" in argv

    service, address = _service_for_expected_mailbox()

    with psycopg.connect(
        **config.database_settings().app_kwargs(), autocommit=True
    ) as conn:
        mailbox_id = _resolve_mailbox(conn, address)
        cursor = core.read_cursor(conn, mailbox_id)

        print(f"MAILBOX: {address}")
        print(f"CURSOR: {cursor or 'none, first run'}")

        messages = fetch(service, cursor)
        print(f"FETCHED: {len(messages)} message(s)")

        if dry_run:
            for message in messages:
                print(
                    f"  would ingest {message.provider_message_id} "
                    f"from {message.sender_address}"
                )

            print("DRY RUN: nothing written")
            return 0

        report = core.ingest(conn, mailbox_id, messages)

        for outcome in report.outcomes:
            detail = outcome.correlation_method or outcome.reason or ""
            print(
                f"  {outcome.action} {outcome.provider_message_id} "
                f"{detail}"
            )

        print(f"INGEST: {report.summary()}")

        # Only now, and only if every message above is durable.
        if messages:
            newest = max(message.received_at for message in messages)
            core.advance_cursor(
                conn, mailbox_id, str(int(newest.timestamp()))
            )
            print(f"CURSOR ADVANCED: {int(newest.timestamp())}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
