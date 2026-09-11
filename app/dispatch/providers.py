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
