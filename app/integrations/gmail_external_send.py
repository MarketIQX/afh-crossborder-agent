import base64
import json
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

# The single address an outbound test may reach, supplied by the
# environment. Unset means no recipient is allowed, so the guard
# fails closed rather than shipping a real address in source.
ALLOWED_TEST_RECIPIENT = os.environ.get(
    "GMAIL_TEST_RECIPIENT", ""
).strip()


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    token_file = Path(require_env("GMAIL_TOKEN_FILE"))
    expected_sender = require_env("GMAIL_EXPECTED_ADDRESS").lower()
    recipient = require_env("GMAIL_EXTERNAL_TEST_RECIPIENT").lower()
    state_file = Path(require_env("GMAIL_ROUNDTRIP_STATE_FILE"))
    approved = os.environ.get("GMAIL_EXTERNAL_SEND_APPROVED", "").strip()

    if approved != "YES":
        raise RuntimeError(
            "Refusing external effect. "
            "Set GMAIL_EXTERNAL_SEND_APPROVED=YES explicitly."
        )

    if not ALLOWED_TEST_RECIPIENT or recipient != ALLOWED_TEST_RECIPIENT:
        raise RuntimeError(
            f"Recipient not allowed for this controlled test: {recipient}"
        )

    if recipient == expected_sender:
        raise RuntimeError("External test recipient must differ from sender.")

    if any(c in recipient for c in ["\r", "\n", ",", ";"]):
        raise RuntimeError("Invalid recipient format.")

    if not token_file.is_file():
        raise FileNotFoundError(f"Gmail token not found: {token_file}")

    creds = Credentials.from_authorized_user_file(
        str(token_file),
        SCOPES,
    )

    if not creds.valid:
        raise RuntimeError(
            "Stored Gmail credential is not valid. "
            "Do not silently reauthorize during this test."
        )

    service = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )

    profile = service.users().getProfile(userId="me").execute()
    actual_sender = profile.get("emailAddress", "").strip().lower()

    if actual_sender != expected_sender:
        raise RuntimeError(
            f"Mailbox identity mismatch: expected {expected_sender}, "
            f"got {actual_sender}"
        )

    marker = f"MIQX-ROUNDTRIP-{uuid.uuid4().hex[:12]}"
    subject = f"MarketIQX controlled round-trip test [{marker}]"

    message = EmailMessage()
    message["From"] = expected_sender
    message["To"] = recipient
    message["Subject"] = subject

    message.set_content(
        "This is a controlled email round-trip test for a new hackathon project.\n\n"
        "Please use the normal Reply button on this message and reply with:\n\n"
        f"ACK {marker}\n\n"
        "Do not start a new email."
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

    gmail_message_id = sent.get("id")
    gmail_thread_id = sent.get("threadId")

    if not gmail_message_id or not gmail_thread_id:
        raise RuntimeError(
            "Gmail did not return both message id and thread id."
        )

    verified = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=gmail_message_id,
            format="metadata",
            metadataHeaders=[
                "From",
                "To",
                "Subject",
                "Message-ID",
            ],
        )
        .execute()
    )

    headers = {
        h["name"].lower(): h["value"]
        for h in verified.get("payload", {}).get("headers", [])
    }

    labels = set(verified.get("labelIds", []))

    if "SENT" not in labels:
        raise RuntimeError("SENT label missing.")

    if headers.get("subject") != subject:
        raise RuntimeError("Subject verification failed.")

    if recipient not in headers.get("to", "").lower():
        raise RuntimeError("Recipient verification failed.")

    rfc_message_id = headers.get("message-id")

    if not rfc_message_id:
        raise RuntimeError("RFC Message-ID header missing.")

    state = {
        "marker": marker,
        "subject": subject,
        "sender": expected_sender,
        "recipient": recipient,
        "gmail_message_id": gmail_message_id,
        "gmail_thread_id": gmail_thread_id,
        "rfc_message_id": rfc_message_id,
    }

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )

    print(f"Authenticated sender: {actual_sender}")
    print(f"Controlled recipient: {recipient}")
    print(f"Gmail message id: {gmail_message_id}")
    print(f"Gmail thread id: {gmail_thread_id}")
    print(f"RFC Message-ID: {rfc_message_id}")
    print(f"Marker: {marker}")
    print("SENT label verified")
    print("EXTERNAL SEND PROOF PASS")
    print(f"Round-trip state saved outside repo: {state_file}")

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
