"""Provider-agnostic ingestion of inbound messages into durable state.

Until now the Gmail modules proved transport only: none of them wrote
to the database, so no enquiry had ever been persisted by application
code. This closes that gap.

There is no outbound capability in this module, by design. It reads
provider messages and writes rows. It cannot reply, and nothing here
approves anything.

Ordering rule that makes replay safe: messages are committed one at a
time, and the cursor is advanced only afterwards, by a separate call.
A crash between the two replays the same messages, which the provider
dedup constraint absorbs. The reverse order would silently skip mail.

Correlation is deliberately conservative. When the evidence points at
more than one case, the message is quarantined as AMBIGUOUS for a human
rather than guessed into a case, because attaching a client's message
to the wrong case is worse than leaving it unassigned.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

INGESTION_VERSION = "ingestion-v1"

CASE_REFERENCE_RE = re.compile(r"\bAFH-([0-9A-Za-z]{4,12})\b")

_MESSAGE_ID_RE = re.compile(r"<([^>]+)>")
_ADDRESS_RE = re.compile(r"<([^>]+)>")

INSERTED = "INSERTED"
DUPLICATE = "DUPLICATE"
SKIPPED_SELF = "SKIPPED_SELF"
QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class ProviderMessage:
    """One message as the provider presented it."""

    provider_message_id: str
    sender_address: str
    recipient_addresses: tuple
    received_at: datetime

    provider_thread_id: str | None = None
    rfc_message_id: str | None = None
    in_reply_to: str | None = None
    references_header: str | None = None
    subject: str | None = None
    body_text: str | None = None


@dataclass
class Outcome:
    provider_message_id: str
    action: str
    message_id: str | None = None
    case_id: str | None = None
    correlation_method: str | None = None
    reason: str | None = None


@dataclass
class Report:
    mailbox_id: str
    outcomes: list = field(default_factory=list)

    def counts(self):
        tally = {}

        for outcome in self.outcomes:
            tally[outcome.action] = tally.get(outcome.action, 0) + 1

        return tally

    def summary(self):
        tally = self.counts()
        parts = [f"{action}={count}" for action, count in sorted(tally.items())]
        return " ".join(parts) if parts else "nothing to ingest"


def email_only(value):
    """Reduce `Name <a@b>` to `a@b`, lowercased."""
    if not value:
        return ""

    match = _ADDRESS_RE.search(value)
    address = match.group(1) if match else value

    return address.strip().strip("<>").lower()


def parse_message_ids(*headers):
    """Collect RFC message ids from In-Reply-To and References."""
    found = []

    for header in headers:
        if not header:
            continue

        bracketed = _MESSAGE_ID_RE.findall(header)

        if bracketed:
            found.extend(item.strip() for item in bracketed)
        else:
            found.extend(
                token.strip()
                for token in header.split()
                if token.strip()
            )

    return tuple(dict.fromkeys(item for item in found if item))


def mailbox_address(conn, mailbox_id):
    """The mailbox's own address, read from the database not the caller."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT address FROM app.mailboxes WHERE id = %s",
            (mailbox_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise ValueError(f"mailbox {mailbox_id} does not exist")

    return email_only(row[0])


def _candidate_cases_from_thread(cur, mailbox_id, message, referenced_ids):
    """Cases already linked to this RFC thread or provider thread."""
    candidates = set()

    if referenced_ids:
        cur.execute(
            """
            SELECT DISTINCT case_id::text
            FROM app.inbound_messages
            WHERE mailbox_id = %s
              AND case_id IS NOT NULL
              AND rfc_message_id = ANY(%s)
            """,
            (mailbox_id, list(referenced_ids)),
        )
        candidates.update(row[0] for row in cur.fetchall())

    if message.provider_thread_id:
        cur.execute(
            """
            SELECT DISTINCT case_id::text
            FROM app.inbound_messages
            WHERE mailbox_id = %s
              AND case_id IS NOT NULL
              AND provider_thread_id = %s
            """,
            (mailbox_id, message.provider_thread_id),
        )
        candidates.update(row[0] for row in cur.fetchall())

    return candidates


def _candidate_cases_from_reference(cur, message):
    """Cases named by an explicit reference in the subject."""
    if not message.subject:
        return set()

    tokens = CASE_REFERENCE_RE.findall(message.subject)

    if not tokens:
        return set()

    references = [f"AFH-{token}" for token in tokens]

    cur.execute(
        "SELECT id::text FROM app.cases WHERE reference = ANY(%s)",
        (references,),
    )

    return {row[0] for row in cur.fetchall()}


def _case_participants(cur, case_id):
    """Addresses that have already appeared on this case."""
    cur.execute(
        "SELECT DISTINCT sender_address FROM app.inbound_messages "
        "WHERE case_id = %s",
        (case_id,),
    )

    return {email_only(row[0]) for row in cur.fetchall()}


def _insert_message(cur, mailbox_id, message):
    message_id = str(uuid.uuid4())

    cur.execute(
        """
        INSERT INTO app.inbound_messages (
            id, mailbox_id, provider_message_id, provider_thread_id,
            rfc_message_id, in_reply_to, references_header,
            sender_address, recipient_addresses, subject, body_text,
            received_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            message_id,
            mailbox_id,
            message.provider_message_id,
            message.provider_thread_id,
            message.rfc_message_id,
            message.in_reply_to,
            message.references_header,
            message.sender_address,
            Jsonb(list(message.recipient_addresses)),
            message.subject,
            message.body_text,
            message.received_at,
        ),
    )

    return message_id


def _new_case(cur):
    """Create an untriaged case.

    No service is assigned. Service scope is a human triage decision,
    and context assembly refuses any case without one, so an untriaged
    case cannot be reasoned about by the agent.
    """
    case_id = str(uuid.uuid4())
    reference = f"AFH-{uuid.uuid4().hex[:8].upper()}"

    cur.execute(
        "INSERT INTO app.cases (id, reference) VALUES (%s, %s)",
        (case_id, reference),
    )

    return case_id, reference


def _link(cur, message_id, case_id, method):
    cur.execute(
        """
        UPDATE app.inbound_messages
        SET case_id = %s,
            correlation_status = 'MATCHED',
            correlation_method = %s
        WHERE id = %s
        """,
        (case_id, method, message_id),
    )


def _quarantine(cur, message_id):
    cur.execute(
        """
        UPDATE app.inbound_messages
        SET correlation_status = 'AMBIGUOUS'
        WHERE id = %s
        """,
        (message_id,),
    )


def ingest_one(conn, mailbox_id, message, self_address):
    """Persist and correlate one message. Never raises on a duplicate."""
    if email_only(message.sender_address) == self_address:
        return Outcome(
            provider_message_id=message.provider_message_id,
            action=SKIPPED_SELF,
            reason=(
                "sender is this mailbox, so ingesting it would let the "
                "system respond to itself"
            ),
        )

    referenced_ids = parse_message_ids(
        message.in_reply_to, message.references_header
    )

    try:
        with conn.transaction():
            with conn.cursor() as cur:
                message_id = _insert_message(cur, mailbox_id, message)

                thread_cases = _candidate_cases_from_thread(
                    cur, mailbox_id, message, referenced_ids
                )
                reference_cases = _candidate_cases_from_reference(cur, message)

                candidates = thread_cases | reference_cases

                if len(candidates) > 1:
                    _quarantine(cur, message_id)

                    return Outcome(
                        provider_message_id=message.provider_message_id,
                        action=QUARANTINED,
                        message_id=message_id,
                        reason=(
                            f"{len(candidates)} candidate cases matched; "
                            f"a human must decide"
                        ),
                    )

                if len(candidates) == 1:
                    case_id = next(iter(candidates))

                    # Matching a case is not being entitled to it. Both
                    # a reply header and a subject reference can be
                    # supplied by anyone.
                    if email_only(
                        message.sender_address
                    ) not in _case_participants(cur, case_id):
                        _quarantine(cur, message_id)

                        return Outcome(
                            provider_message_id=(
                                message.provider_message_id
                            ),
                            action=QUARANTINED,
                            message_id=message_id,
                            reason=(
                                "the reference matched a case, but this "
                                "sender has never appeared on it; a "
                                "human must decide"
                            ),
                        )

                    method = (
                        "THREAD_RFC"
                        if case_id in thread_cases
                        else "CASE_REFERENCE"
                    )
                else:
                    case_id, _reference = _new_case(cur)
                    method = "NEW_CASE"

                _link(cur, message_id, case_id, method)

                return Outcome(
                    provider_message_id=message.provider_message_id,
                    action=INSERTED,
                    message_id=message_id,
                    case_id=case_id,
                    correlation_method=method,
                )
    except UniqueViolation:
        return Outcome(
            provider_message_id=message.provider_message_id,
            action=DUPLICATE,
            reason="this provider message is already stored",
        )


def ingest(conn, mailbox_id, messages):
    """Persist a batch. The cursor is deliberately NOT advanced here."""
    self_address = mailbox_address(conn, mailbox_id)
    report = Report(mailbox_id=mailbox_id)

    for message in messages:
        report.outcomes.append(
            ingest_one(conn, mailbox_id, message, self_address)
        )

    return report


def advance_cursor(conn, mailbox_id, cursor):
    """Record provider progress, only after messages are durable.

    Kept separate from `ingest` on purpose. If the process dies between
    the two, the same messages arrive again and are absorbed as
    duplicates. Advancing first would lose mail permanently.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE app.ingestion_cursors
                SET provider_cursor = %s, updated_at = now()
                WHERE mailbox_id = %s
                """,
                (cursor, mailbox_id),
            )

            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO app.ingestion_cursors "
                    "(mailbox_id, provider_cursor) VALUES (%s, %s)",
                    (mailbox_id, cursor),
                )


def read_cursor(conn, mailbox_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT provider_cursor FROM app.ingestion_cursors "
            "WHERE mailbox_id = %s",
            (mailbox_id,),
        )
        row = cur.fetchone()

    return row[0] if row else None


def assign_service(conn, case_id, service_key):
    """Triage a case into a service scope.

    This is an operator action, not an agent action. The agent has no
    tool that reaches it, and context assembly refuses cases that have
    not been through it.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM app.services "
                "WHERE service_key = %s AND is_active",
                (service_key,),
            )
            row = cur.fetchone()

            if row is None:
                raise ValueError(f"no active service {service_key!r}")

            cur.execute(
                "UPDATE app.cases SET service_id = %s, updated_at = now() "
                "WHERE id = %s",
                (row[0], case_id),
            )

    return row[0]
