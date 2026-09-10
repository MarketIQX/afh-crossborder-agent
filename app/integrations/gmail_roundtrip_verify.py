import base64
import json
import os
import re
import sys

from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
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


def decode_b64url(value: str) -> str:
    if not value:
        return ""

    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(value + padding)

    return raw.decode("utf-8", errors="replace")


def headers_dict(message: dict) -> dict:
    return {
        h["name"].lower(): h["value"]
        for h in message.get("payload", {}).get("headers", [])
        if "name" in h and "value" in h
    }


def exact_address(value: str) -> str:
    return parseaddr(value)[1].strip().lower()


def recipient_addresses(value: str) -> set[str]:
    return {
        address.strip().lower()
        for _, address in getaddresses([value])
        if address.strip()
    }


def normalize_subject(value: str) -> str:
    subject = value.strip()

    while True:
        cleaned = re.sub(
            r"^\s*re\s*:\s*",
            "",
            subject,
            flags=re.IGNORECASE,
        )

        if cleaned == subject:
            break

        subject = cleaned

    return subject.strip()


class AuthoredHTMLExtractor(HTMLParser):
    """
    Extract visible text that is outside known quoted-reply containers.

    This is sufficient for the controlled Gmail reply fixture we observed.
    It is NOT claimed as a universal email-client parser.
    """

    QUOTE_CLASSES = {
        "gmail_quote",
        "gmail_extra",
        "yahoo_quoted",
    }

    QUOTE_IDS = {
        "divrplyfwdmsg",
    }

    def __init__(self):
        super().__init__()
        self.skip_stack = []
        self.authored_chunks = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        classes = {
            value.lower()
            for value in attrs_dict.get("class", "").split()
            if value
        }

        element_id = attrs_dict.get("id", "").lower()

        parent_skipped = (
            self.skip_stack[-1]
            if self.skip_stack
            else False
        )

        current_is_quote = (
            tag.lower() == "blockquote"
            or bool(classes & self.QUOTE_CLASSES)
            or element_id in self.QUOTE_IDS
        )

        self.skip_stack.append(
            parent_skipped or current_is_quote
        )

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        if self.skip_stack:
            self.skip_stack.pop()

    def handle_data(self, data):
        if self.skip_stack and self.skip_stack[-1]:
            return

        text = " ".join(data.split()).strip()

        if text:
            self.authored_chunks.append(text)


def extract_authored_chunks(payload: dict) -> list[str]:
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        text = decode_b64url(
            payload.get("body", {}).get("data", "")
        )

        chunks = []

        for line in text.splitlines():
            clean = " ".join(line.split()).strip()

            if clean:
                chunks.append(clean)

        return chunks

    if mime_type == "text/html":
        html = decode_b64url(
            payload.get("body", {}).get("data", "")
        )

        parser = AuthoredHTMLExtractor()
        parser.feed(html)

        return parser.authored_chunks

    chunks = []

    for part in payload.get("parts", []):
        chunks.extend(
            extract_authored_chunks(part)
        )

    return chunks


def main() -> int:
    token_file = Path(
        require_env("GMAIL_TOKEN_FILE")
    )

    state_file = Path(
        require_env("GMAIL_ROUNDTRIP_STATE_FILE")
    )

    expected_mailbox = require_env(
        "GMAIL_EXPECTED_ADDRESS"
    ).lower()

    if not token_file.is_file():
        raise FileNotFoundError(
            f"Gmail token not found: {token_file}"
        )

    if not state_file.is_file():
        raise FileNotFoundError(
            f"Round-trip state not found: {state_file}"
        )

    state = json.loads(
        state_file.read_text(encoding="utf-8")
    )

    marker = state["marker"]
    expected_ack = f"ACK {marker}"

    original_subject = state["subject"]
    external_address = state["recipient"].strip().lower()
    original_gmail_id = state["gmail_message_id"]
    original_thread_id = state["gmail_thread_id"]
    original_rfc_id = state["rfc_message_id"].strip()

    creds = Credentials.from_authorized_user_file(
        str(token_file),
        SCOPES,
    )

    if not creds.valid:
        raise RuntimeError(
            "Stored Gmail credential is not valid. "
            "Do not silently reauthorize."
        )

    service = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )

    # Gate 1: authenticated mailbox identity
    profile = service.users().getProfile(
        userId="me"
    ).execute()

    mailbox = profile.get(
        "emailAddress", ""
    ).strip().lower()

    if mailbox != expected_mailbox:
        raise RuntimeError(
            f"Mailbox mismatch: expected {expected_mailbox}, got {mailbox}"
        )

    # Gate 2: independently re-verify original outbound message
    original = service.users().messages().get(
        userId="me",
        id=original_gmail_id,
        format="metadata",
        metadataHeaders=[
            "From",
            "To",
            "Subject",
            "Message-ID",
        ],
    ).execute()

    original_headers = headers_dict(original)

    if original.get("threadId") != original_thread_id:
        raise RuntimeError(
            "Stored original thread ID does not match Gmail."
        )

    if exact_address(
        original_headers.get("from", "")
    ) != expected_mailbox:
        raise RuntimeError(
            "Original sender verification failed."
        )

    if external_address not in recipient_addresses(
        original_headers.get("to", "")
    ):
        raise RuntimeError(
            "Original recipient verification failed."
        )

    if original_headers.get(
        "subject", ""
    ) != original_subject:
        raise RuntimeError(
            "Original subject verification failed."
        )

    if original_headers.get(
        "message-id", ""
    ).strip() != original_rfc_id:
        raise RuntimeError(
            "Original RFC Message-ID verification failed."
        )

    # Gate 3: inspect replies in the known Gmail conversation
    thread = service.users().threads().get(
        userId="me",
        id=original_thread_id,
        format="full",
    ).execute()

    candidates = []

    for message in thread.get("messages", []):
        if message.get("id") == original_gmail_id:
            continue

        headers = headers_dict(message)

        sender = exact_address(
            headers.get("from", "")
        )

        recipients = recipient_addresses(
            headers.get("to", "")
        )

        if sender != external_address:
            continue

        if expected_mailbox not in recipients:
            continue

        authored_chunks = extract_authored_chunks(
            message.get("payload", {})
        )

        reply_subject = headers.get("subject", "")
        in_reply_to = headers.get(
            "in-reply-to", ""
        ).strip()

        references = headers.get(
            "references", ""
        ).strip()

        candidate = {
            "gmail_message_id": message.get("id"),

            "internal_date": int(
                message.get("internalDate", "0")
            ),

            "same_thread":
                message.get("threadId")
                == original_thread_id,

            "subject_match":
                normalize_subject(reply_subject)
                == normalize_subject(original_subject),

            "ack_match":
                expected_ack in authored_chunks,

            "in_reply_to_match":
                in_reply_to == original_rfc_id,

            "references_match":
                original_rfc_id in references,
        }

        candidates.append(candidate)

    matching = [
        c
        for c in candidates
        if (
            c["same_thread"]
            and c["subject_match"]
            and c["ack_match"]
            and c["in_reply_to_match"]
        )
    ]

    print(f"Authenticated mailbox: {mailbox}")
    print("Original outbound independently verified: PASS")
    print(f"External replies inspected: {len(candidates)}")
    print(
        "Replies satisfying correlation contract: "
        f"{len(matching)}"
    )

    for index, candidate in enumerate(
        sorted(
            candidates,
            key=lambda item: item["internal_date"],
        ),
        start=1,
    ):
        result = (
            "ACCEPT"
            if candidate in matching
            else "REJECT"
        )

        print(
            f"Candidate {index}: "
            f"thread={candidate['same_thread']} "
            f"subject={candidate['subject_match']} "
            f"authored_ack={candidate['ack_match']} "
            f"in_reply_to={candidate['in_reply_to_match']} "
            f"references={candidate['references_match']} "
            f"=> {result}"
        )

    if len(matching) != 1:
        print(
            "ROUND-TRIP CORRELATION FAIL",
            file=sys.stderr,
        )
        return 2

    print(
        "EXTERNAL HUMAN ROUND-TRIP CORRELATION PASS"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HttpError as exc:
        print(
            f"Gmail API error: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except Exception as exc:
        print(
            f"Error: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
