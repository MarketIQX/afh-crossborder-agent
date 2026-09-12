"""Outbound providers, and the three honest outcomes of a send.

Every provider reports one of:

    SENT           the provider accepted it and named it
    FAILED         it definitively did not go
    SEND_UNKNOWN   we cannot tell

The third is the one that matters. A timeout after the request left, or
a server error mid-call, leaves delivery genuinely undetermined.
Recording that as FAILED invites a retry that double-sends a client.
Recording it as SENT claims something we do not know. So it gets its own
state, and the database blocks a retry on it.

Note also what a provider accepting a message does and does not mean. It
means the provider took it. It is not proof of delivery, and nothing in
this system claims otherwise.
"""

from dataclasses import dataclass, field
from pathlib import Path

SENT = "SENT"
FAILED = "FAILED"
SEND_UNKNOWN = "SEND_UNKNOWN"


@dataclass
class SendResult:
    state: str
    provider_message_id: str | None = None
    response: dict = field(default_factory=dict)
    failure_reason: str | None = None


class SimulatedProvider:
    """Records what it was asked to send and returns a chosen outcome.

    Used wherever a test needs to assert the exact payload that reached
    the boundary, without a message leaving the building.
    """

    name = "simulated"

    def __init__(self, outcome=SENT, failure_reason=None):
        self.outcome = outcome
        self.failure_reason = failure_reason
        self.sent = []

    def send(self, recipient, subject, body_text, attachments):
        self.sent.append(
            {
                "recipient": recipient,
                "subject": subject,
                "body_text": body_text,
                "attachments": list(attachments or []),
            }
        )

        if self.outcome == SENT:
            return SendResult(
                state=SENT,
                provider_message_id=f"simulated-{len(self.sent):06d}",
                response={"provider": self.name, "accepted": True},
            )

        return SendResult(
            state=self.outcome,
            response={"provider": self.name, "accepted": False},
            failure_reason=self.failure_reason
            or f"simulated {self.outcome}",
        )


class GmailProvider:
    """Sends through Gmail, mapping failures to the three outcomes.

    The mapping is deliberately cautious: anything that could have been
    delivered is SEND_UNKNOWN rather than FAILED, because the cost of a
    wrong FAILED is a duplicate message to a client.
    """

    name = "gmail"

    def __init__(self, service, sender_address):
        self._service = service
        self._sender = sender_address

    @staticmethod
    def _build_mime(sender, recipient, subject, body_text):
        import base64
        from email.message import EmailMessage

        message = EmailMessage()
        message["To"] = recipient
        message["From"] = sender
        message["Subject"] = subject
        message.set_content(body_text)

        return {
            "raw": base64.urlsafe_b64encode(
                message.as_bytes()
            ).decode("ascii")
        }

    def send(self, recipient, subject, body_text, attachments):
        from googleapiclient.errors import HttpError

        if attachments:
            return SendResult(
                state=FAILED,
                failure_reason=(
                    "attachments are not implemented; refusing rather "
                    "than sending a message missing its enclosures"
                ),
            )

        payload = self._build_mime(
            self._sender, recipient, subject, body_text
        )

        try:
            result = (
                self._service.users()
                .messages()
                .send(userId="me", body=payload)
                .execute()
            )
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)

            if status is not None and 400 <= int(status) < 500:
                return SendResult(
                    state=FAILED,
                    failure_reason=f"HttpError {status}",
                    response={"provider": self.name, "status": status},
                )

            return SendResult(
                state=SEND_UNKNOWN,
                failure_reason=f"HttpError {status}, delivery undetermined",
                response={"provider": self.name, "status": status},
            )
        except Exception as exc:  # noqa: BLE001
            # A transport error after the request left the process gives
            # no way to know whether Gmail accepted it.
            return SendResult(
                state=SEND_UNKNOWN,
                failure_reason=(
                    f"{exc.__class__.__name__}, delivery undetermined"
                ),
                response={"provider": self.name},
            )

        message_id = result.get("id")

        if not message_id:
            return SendResult(
                state=SEND_UNKNOWN,
                failure_reason="provider returned no message id",
                response={"provider": self.name, "raw": result},
            )

        return SendResult(
            state=SENT,
            provider_message_id=message_id,
            response={
                "provider": self.name,
                "thread_id": result.get("threadId"),
                "label_ids": result.get("labelIds"),
            },
        )


# Authorising real outbound mail is a deliberate shell act, never a
# default and never inferred. Absent or unrecognised means simulated.
REAL_SEND_APPROVAL_KEY = "CONSOLE_REAL_SEND_APPROVED"

_APPROVED = frozenset({"yes", "true", "1", "approved"})

# Reading is needed as well as sending: the identity of the authenticated
# mailbox is checked before anything leaves.
SEND_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class ProviderRefused(Exception):
    """Real sending was authorised and could not be prepared.

    Deliberately not a fallback to simulation. A reviewer told their
    message was handed to a provider must not have been shown a
    simulation they did not ask for.
    """


def real_send_authorised(approval):
    """Only an explicit, recognised value authorises real mail."""
    return str(approval or "").strip().lower() in _APPROVED


def gmail_provider(token_file, expected_address):
    """Build a GmailProvider, or raise. Never returns a substitute.

    The authenticated mailbox must be the one the contract names. A
    token that silently belongs to another account would send client
    correspondence from the wrong address, which is not recoverable by
    apologising afterwards.
    """
    token_path = Path(token_file)

    if not token_path.is_file():
        raise ProviderRefused(
            f"real sending is authorised but the Gmail token is not at "
            f"{token_path}. Nothing was sent."
        )

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ProviderRefused(
            f"real sending is authorised but the Google client library "
            f"is unavailable: {exc}. Nothing was sent."
        ) from exc

    try:
        creds = Credentials.from_authorized_user_file(
            str(token_path), SEND_SCOPES
        )
        service = build(
            "gmail", "v1", credentials=creds, cache_discovery=False
        )
        profile = (
            service.users().getProfile(userId="me").execute()
        )
    except Exception as exc:
        raise ProviderRefused(
            f"real sending is authorised and Gmail could not be "
            f"reached: {type(exc).__name__}. Nothing was sent."
        ) from exc

    actual = (profile.get("emailAddress") or "").strip().lower()
    expected = (expected_address or "").strip().lower()

    if actual != expected:
        raise ProviderRefused(
            f"the authenticated mailbox is {actual or 'unknown'} but the "
            f"contract names {expected}. Refusing to send client "
            f"correspondence from an unexpected address."
        )

    return GmailProvider(service, actual)


def selected(approval, token_file=None, expected_address=None):
    """Simulated unless real sending is explicitly authorised.

    Raises ProviderRefused when authorised and unpreparable, so the
    caller reports a refusal rather than a silent simulation.
    """
    if not real_send_authorised(approval):
        return SimulatedProvider()

    return gmail_provider(token_file, expected_address)
