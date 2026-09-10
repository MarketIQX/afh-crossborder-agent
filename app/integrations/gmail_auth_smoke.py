import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
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
    client_file = Path(require_env("GOOGLE_OAUTH_CLIENT_FILE"))
    token_file = Path(require_env("GMAIL_TOKEN_FILE"))
    expected_address = require_env("GMAIL_EXPECTED_ADDRESS").strip().lower()

    if not client_file.is_file():
        raise FileNotFoundError(f"OAuth client file not found: {client_file}")

    creds = None
    token_existed = token_file.is_file()

    if token_existed:
        creds = Credentials.from_authorized_user_file(
            str(token_file),
            SCOPES,
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh only in memory. Do not persist until mailbox identity
            # has been independently verified through Gmail.
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_file),
                SCOPES,
            )
            creds = flow.run_local_server(
                port=0,
                open_browser=True,
            )

    service = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )

    # Independent mailbox identity gate.
    profile = service.users().getProfile(userId="me").execute()
    actual_address = profile.get("emailAddress", "").strip().lower()

    print(f"Authenticated Gmail profile: {actual_address}")

    if actual_address != expected_address:
        print(
            "MAILBOX VERIFICATION FAIL: "
            f"expected {expected_address}, got {actual_address}",
            file=sys.stderr,
        )

        if not token_existed:
            print(
                "No new OAuth token was persisted.",
                file=sys.stderr,
            )

        return 2

    # Only persist credentials after the Gmail API proves
    # the authenticated mailbox is the intended mailbox.
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(
        creds.to_json(),
        encoding="utf-8",
    )

    print("MAILBOX VERIFICATION PASS")
    print(f"Token persisted to private path: {token_file}")
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
