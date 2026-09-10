import os
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

MAX_MESSAGES = 5


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    token_file = Path(require_env("GMAIL_TOKEN_FILE"))
    expected_address = require_env("GMAIL_EXPECTED_ADDRESS").strip().lower()

    if not token_file.is_file():
        raise FileNotFoundError(f"Gmail token not found: {token_file}")

    creds = Credentials.from_authorized_user_file(
        str(token_file),
        SCOPES,
    )

    if not creds.valid:
        raise RuntimeError(
            "Stored Gmail credential is not currently valid. "
            "Do not silently reauthorize in this read test."
        )

    service = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )

    profile = service.users().getProfile(userId="me").execute()
    actual_address = profile.get("emailAddress", "").strip().lower()

    if actual_address != expected_address:
        print(
            f"MAILBOX IDENTITY FAIL: expected {expected_address}, "
            f"got {actual_address}",
            file=sys.stderr,
        )
        return 2

    result = service.users().messages().list(
        userId="me",
        maxResults=MAX_MESSAGES,
    ).execute()

    messages = result.get("messages", [])

    print(f"Authenticated mailbox: {actual_address}")
    print(f"Messages returned: {len(messages)}")
    print(f"Bounded maximum: {MAX_MESSAGES}")

    for index, item in enumerate(messages, start=1):
        message = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()

        headers = {
            h["name"].lower(): h["value"]
            for h in message.get("payload", {}).get("headers", [])
        }

        print(
            f"{index}. "
            f"id={message.get('id')} | "
            f"from={headers.get('from', '[missing]')} | "
            f"subject={headers.get('subject', '[missing]')} | "
            f"date={headers.get('date', '[missing]')}"
        )

    print("MAILBOX READ PROOF PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HttpError as exc:
        print(f"Gmail API error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
