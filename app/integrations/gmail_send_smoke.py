import base64
import os
import sys
import uuid
from email.message import EmailMessage
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    token_file = Path(require_env("GMAIL_TOKEN_FILE"))
    expected_address = require_env("GMAIL_EXPECTED_ADDRESS").strip().lower()
    approved = os.environ.get("GMAIL_SEND_SMOKE_APPROVED", "").strip()

    if approved != "YES":
        raise RuntimeError(
            "Refusing external effect. "
            "Set GMAIL_SEND_SMOKE_APPROVED=YES explicitly."
        )

    if not token_file.is_file():
        raise FileNotFoundError(f"Gmail token not found: {token_file}")

    creds = Credentials.from_authorized_user_file(
        str(token_file),
        SCOPES,
    )

    if not creds.valid:
        raise RuntimeError(
            "Stored Gmail credential is not currently valid. "
            "Do not silently reauthorize in this send test."
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

    marker = f"MIQX-SEND-SMOKE-{uuid.uuid4().hex[:12]}"

    message = EmailMessage()
    message["To"] = expected_address
    message["From"] = expected_address
    message["Subject"] = marker
    message.set_content(
        "Controlled Gmail API send smoke test for the new hackathon project."
    )

    raw = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode("utf-8")

    sent = (
        service.users()
        .messages()
        .send(
            userId="me",
            body={"raw": raw},
        )
        .execute()
    )

    message_id = sent.get("id")

    if not message_id:
        raise RuntimeError("Gmail send returned no message id.")

    verified = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["To", "From", "Subject"],
        )
        .execute()
    )

    headers = {
        h["name"].lower(): h["value"]
        for h in verified.get("payload", {}).get("headers", [])
    }

    labels = set(verified.get("labelIds", []))

    if "SENT" not in labels:
        raise RuntimeError(
            f"Sent message verification failed: SENT label missing. "
            f"labels={sorted(labels)}"
        )

    if headers.get("subject") != marker:
        raise RuntimeError("Sent message subject verification failed.")

    print(f"Authenticated mailbox: {actual_address}")
    print(f"Provider message id: {message_id}")
    print(f"Verification marker: {marker}")
    print("SENT label verified")
    print("GMAIL SEND PROOF PASS")
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
