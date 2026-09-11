"""Put one realistic enquiry through the real ingestion path.

Not a test fixture. This registers a mailbox, ingests a message exactly
as the Gmail adapter would hand it over, and triages it into a service,
so the case the agent then reads arrived the same way a client's message
would.

Usage:

    python scripts/seed_demo_case.py
    python scripts/seed_demo_case.py --service nri_india_tax_filing
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.ingestion import core  # noqa: E402

MAILBOX_ADDRESS = "advisory@example.test"
CLIENT_ADDRESS = "priya.menon@example.test"

SUBJECT = "Tax residency for FY 2025-26 after moving to Dubai"

BODY = """Hello,

I relocated to Dubai in June 2025 for a new role and have been there
since. I still have a rented flat in Bengaluru that earns me rent each
month, and I kept my Indian savings account open.

I am not sure whether I count as a resident in India for the year, or
whether I still need to file a return there at all. A colleague said
something about a 120 day rule but I did not follow it.

Could you advise?

Thanks,
Priya
"""


def ensure_mailbox(admin_kwargs):
    """Register the firm's mailbox. The runtime role cannot do this."""
    with psycopg.connect(**admin_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM app.mailboxes "
                "WHERE provider = 'gmail' AND address = %s",
                (MAILBOX_ADDRESS,),
            )
            row = cur.fetchone()

            if row:
                return row[0]

            mailbox_id = str(uuid.uuid4())

            cur.execute(
                "INSERT INTO app.mailboxes (id, provider, address) "
                "VALUES (%s, 'gmail', %s)",
                (mailbox_id, MAILBOX_ADDRESS),
            )
        conn.commit()

    return mailbox_id


def main(argv):
    service_key = (
        argv[argv.index("--service") + 1]
        if "--service" in argv
        else "nri_india_tax_filing"
    )

    settings = config.database_settings()
    mailbox_id = ensure_mailbox(settings.admin_kwargs())

    print(f"MAILBOX: {MAILBOX_ADDRESS} {mailbox_id}")

    message = core.ProviderMessage(
        provider_message_id=f"demo-{uuid.uuid4().hex[:12]}",
        sender_address=CLIENT_ADDRESS,
        recipient_addresses=(MAILBOX_ADDRESS,),
        received_at=datetime.now(timezone.utc),
        rfc_message_id=f"{uuid.uuid4().hex}@example.test",
        subject=SUBJECT,
        body_text=BODY,
    )

    with psycopg.connect(**settings.app_kwargs(), autocommit=True) as conn:
        report = core.ingest(conn, mailbox_id, [message])

        outcome = report.outcomes[0]
        print(f"INGEST: {outcome.action} {outcome.correlation_method or ''}")

        if outcome.case_id is None:
            raise SystemExit(f"no case created: {outcome.reason}")

        case_id = outcome.case_id

        # Triage is a human decision. The agent cannot reach it, and
        # context assembly refuses a case that has not been through it.
        core.assign_service(conn, case_id, service_key)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT reference FROM app.cases WHERE id = %s", (case_id,)
            )
            reference = cur.fetchone()[0]

    print(f"CASE: {case_id}")
    print(f"REFERENCE: {reference}")
    print(f"SERVICE: {service_key}")
    print()
    print("Next:")
    print(f"  python scripts/run_case.py {case_id} --record")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
