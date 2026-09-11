"""Ingestion checks: provider messages into durable, correlated state.

These run against the ingestion core with fabricated provider messages,
so no Gmail token or network is needed. That is deliberate: the logic
under test is correlation, dedup, quarantine and cursor ordering, all of
which live in the core. The Gmail adapter only maps payload shapes.

The last check is the one that matters most for the milestone: an
ingested message becomes a case, a human triages it into a service, and
the agent then produces a persisted proposal from it. Before this,
Gmail evidence was transport-only and nothing had ever been persisted
by application code.

Usage:

    python tests/ingestion_smoke.py phase1
    python tests/ingestion_smoke.py cleanup
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402
from app.agent import model as model_module  # noqa: E402
from app.agent import runner  # noqa: E402
from app.ingestion import core  # noqa: E402

SETTINGS = config.database_settings()

MAILBOX_ID = "97000000-0000-0000-0000-000000000001"
MAILBOX_ADDRESS = "ingest-fixture@example.test"

CLIENT = "client@example.test"
OTHER_CLIENT = "another@example.test"

BASE_TIME = datetime(2026, 6, 20, 9, 0, 0, tzinfo=timezone.utc)


def admin_conn():
    return psycopg.connect(**SETTINGS.admin_kwargs())


def app_conn():
    return psycopg.connect(**SETTINGS.app_kwargs(), autocommit=True)


def _fixture_case_ids(cur):
    cur.execute(
        "SELECT DISTINCT case_id::text FROM app.inbound_messages "
        "WHERE mailbox_id = %s AND case_id IS NOT NULL",
        (MAILBOX_ID,),
    )
    return [row[0] for row in cur.fetchall()]


def cleanup():
    """Remove this suite's mailbox and everything downstream of it."""
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cases = _fixture_case_ids(cur)

            if cases:
                cur.execute(
                    "DELETE FROM app.proposal_revisions WHERE proposal_id "
                    "IN (SELECT id FROM app.action_proposals "
                    "WHERE case_id = ANY(%s))",
                    (cases,),
                )
                cur.execute(
                    "DELETE FROM app.action_proposals "
                    "WHERE case_id = ANY(%s)",
                    (cases,),
                )
                cur.execute(
                    "DELETE FROM app.agent_tool_calls WHERE run_id IN "
                    "(SELECT id FROM app.agent_runs "
                    "WHERE case_id = ANY(%s))",
                    (cases,),
                )
                cur.execute(
                    "DELETE FROM app.case_facts WHERE case_id = ANY(%s)",
                    (cases,),
                )
                cur.execute(
                    "DELETE FROM app.agent_runs WHERE case_id = ANY(%s)",
                    (cases,),
                )

            cur.execute(
                "DELETE FROM app.inbound_messages WHERE mailbox_id = %s",
                (MAILBOX_ID,),
            )

            if cases:
                cur.execute(
                    "DELETE FROM app.cases WHERE id = ANY(%s)", (cases,)
                )

            cur.execute(
                "DELETE FROM app.ingestion_cursors WHERE mailbox_id = %s",
                (MAILBOX_ID,),
            )
            cur.execute(
                "DELETE FROM app.mailboxes WHERE id = %s", (MAILBOX_ID,)
            )
        conn.commit()

    print("INGEST FIXTURE CLEANUP: PASS")


def seed():
    cleanup()

    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.mailboxes (id, provider, address) "
                "VALUES (%s, 'gmail', %s)",
                (MAILBOX_ID, MAILBOX_ADDRESS),
            )
        conn.commit()

    print("INGEST FIXTURE SEEDED: PASS")


def message(
    provider_id,
    sender=CLIENT,
    subject="Question about my residency",
    body="I moved abroad and need to understand my residency position.",
    thread=None,
    rfc=None,
    in_reply_to=None,
    references=None,
    minutes=0,
):
    return core.ProviderMessage(
        provider_message_id=provider_id,
        sender_address=sender,
        recipient_addresses=(MAILBOX_ADDRESS,),
        received_at=BASE_TIME + timedelta(minutes=minutes),
        provider_thread_id=thread,
        rfc_message_id=rfc,
        in_reply_to=in_reply_to,
        references_header=references,
        subject=subject,
        body_text=body,
    )


def message_rows():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_message_id, case_id::text, "
                "correlation_status, correlation_method "
                "FROM app.inbound_messages WHERE mailbox_id = %s "
                "ORDER BY received_at, provider_message_id",
                (MAILBOX_ID,),
            )
            return cur.fetchall()


def case_row(case_id):
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reference, service_id::text, lifecycle_status "
                "FROM app.cases WHERE id = %s",
                (case_id,),
            )
            return cur.fetchone()


def only(report, expected_action):
    """Assert a single-message report took exactly the expected action."""
    if len(report.outcomes) != 1:
        raise RuntimeError(
            f"expected one outcome, got {len(report.outcomes)}"
        )

    outcome = report.outcomes[0]

    if outcome.action != expected_action:
        raise RuntimeError(
            f"expected {expected_action}, got {outcome.action}: "
            f"{outcome.reason}"
        )

    return outcome


STATE = {}

ROOT_A = "root-a@client.test"
ROOT_B = "root-b@client.test"


def ingest01_new_message_creates_untriaged_case():
    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [message("m1", thread="T1", rfc=ROOT_A, minutes=0)],
        )

    outcome = only(report, core.INSERTED)

    if outcome.correlation_method != "NEW_CASE":
        raise RuntimeError(
            f"INGEST01 FAIL: method {outcome.correlation_method!r}"
        )

    STATE["case_a"] = outcome.case_id

    reference, service_id, lifecycle = case_row(outcome.case_id)

    if not reference or not reference.startswith("AFH-"):
        raise RuntimeError(f"INGEST01 FAIL: reference {reference!r}")

    if service_id is not None:
        raise RuntimeError(
            "INGEST01 FAIL: ingestion assigned a service scope, which is "
            "a human triage decision"
        )

    if lifecycle != "OPEN":
        raise RuntimeError(f"INGEST01 FAIL: lifecycle {lifecycle!r}")

    STATE["reference_a"] = reference

    print(
        f"INGEST01 NEW MESSAGE CREATES AN UNTRIAGED CASE: PASS "
        f"({reference})"
    )


def ingest02_duplicate_provider_message_is_absorbed():
    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [message("m1", thread="T1", rfc=ROOT_A, minutes=0)],
        )

    only(report, core.DUPLICATE)

    rows = message_rows()

    if len(rows) != 1:
        raise RuntimeError(f"INGEST02 FAIL: {len(rows)} rows stored")

    print("INGEST02 DUPLICATE PROVIDER MESSAGE IS ABSORBED: PASS")


def ingest03_reply_joins_the_same_case():
    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [
                message(
                    "m2",
                    subject="Re: Question about my residency",
                    thread="T1",
                    rfc="reply-a@client.test",
                    in_reply_to=f"<{ROOT_A}>",
                    minutes=30,
                )
            ],
        )

    outcome = only(report, core.INSERTED)

    if outcome.case_id != STATE["case_a"]:
        raise RuntimeError(
            f"INGEST03 FAIL: reply landed on {outcome.case_id}, expected "
            f"{STATE['case_a']}"
        )

    if outcome.correlation_method != "THREAD_RFC":
        raise RuntimeError(
            f"INGEST03 FAIL: method {outcome.correlation_method!r}"
        )

    print("INGEST03 REPLY JOINS THE SAME CASE: PASS")


def ingest04_self_generated_mail_is_not_ingested():
    before = len(message_rows())

    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [
                message(
                    "m-self",
                    sender=f"Our Firm <{MAILBOX_ADDRESS}>",
                    subject="Re: Question about my residency",
                    thread="T1",
                    rfc="ours-1@firm.test",
                    in_reply_to=f"<{ROOT_A}>",
                    minutes=45,
                )
            ],
        )

    only(report, core.SKIPPED_SELF)

    after = len(message_rows())

    if after != before:
        raise RuntimeError(
            f"INGEST04 FAIL: self-mail stored, rows went {before} to "
            f"{after}"
        )

    print("INGEST04 SELF GENERATED MAIL IS NOT INGESTED: PASS")


def ingest05_ambiguous_correlation_is_quarantined():
    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [
                message(
                    "m3",
                    sender=OTHER_CLIENT,
                    subject="Separate enquiry about filing",
                    thread="T2",
                    rfc=ROOT_B,
                    minutes=60,
                )
            ],
        )

    second = only(report, core.INSERTED)
    STATE["case_b"] = second.case_id

    if second.case_id == STATE["case_a"]:
        raise RuntimeError("INGEST05 FAIL: unrelated mail joined case A")

    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [
                message(
                    "m4",
                    subject="Merging both of my questions",
                    rfc="merge-1@client.test",
                    references=f"<{ROOT_A}> <{ROOT_B}>",
                    minutes=90,
                )
            ],
        )

    outcome = only(report, core.QUARANTINED)

    row = next(
        entry for entry in message_rows() if entry[0] == "m4"
    )
    _provider, case_id, status, method = row

    if case_id is not None or status != "AMBIGUOUS" or method is not None:
        raise RuntimeError(
            f"INGEST05 FAIL: quarantined row is {row!r}"
        )

    print(
        f"INGEST05 AMBIGUOUS CORRELATION IS QUARANTINED: PASS "
        f"({outcome.reason})"
    )


def ingest06_cursor_is_not_advanced_by_ingest():
    with app_conn() as conn:
        cursor = core.read_cursor(conn, MAILBOX_ID)

    if cursor is not None:
        raise RuntimeError(
            f"INGEST06 FAIL: ingest advanced the cursor to {cursor!r}. "
            f"A crash after that point would lose mail permanently."
        )

    print("INGEST06 CURSOR IS NOT ADVANCED BY INGEST: PASS")


def ingest07_replay_after_interruption_is_absorbed():
    before = message_rows()

    batch = [
        message("m1", thread="T1", rfc=ROOT_A, minutes=0),
        message(
            "m2",
            thread="T1",
            rfc="reply-a@client.test",
            in_reply_to=f"<{ROOT_A}>",
            minutes=30,
        ),
        message(
            "m-self",
            sender=MAILBOX_ADDRESS,
            rfc="ours-1@firm.test",
            minutes=45,
        ),
        message("m3", sender=OTHER_CLIENT, thread="T2", rfc=ROOT_B, minutes=60),
        message(
            "m4",
            rfc="merge-1@client.test",
            references=f"<{ROOT_A}> <{ROOT_B}>",
            minutes=90,
        ),
    ]

    with app_conn() as conn:
        report = core.ingest(conn, MAILBOX_ID, batch)

    tally = report.counts()

    if tally.get(core.INSERTED):
        raise RuntimeError(
            f"INGEST07 FAIL: replay inserted {tally['INSERTED']} row(s)"
        )

    after = message_rows()

    if after != before:
        raise RuntimeError(
            "INGEST07 FAIL: replay changed stored state"
        )

    print(f"INGEST07 REPLAY AFTER INTERRUPTION IS ABSORBED: PASS ({tally})")


def ingest08_cursor_advances_only_when_asked():
    with app_conn() as conn:
        core.advance_cursor(conn, MAILBOX_ID, "1781000000")
        first = core.read_cursor(conn, MAILBOX_ID)

        core.advance_cursor(conn, MAILBOX_ID, "1781009999")
        second = core.read_cursor(conn, MAILBOX_ID)

    if first != "1781000000" or second != "1781009999":
        raise RuntimeError(
            f"INGEST08 FAIL: cursor went {first!r} then {second!r}"
        )

    print("INGEST08 CURSOR ADVANCES ONLY WHEN ASKED: PASS")


def ingest09_case_reference_correlates():
    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [
                message(
                    "m5",
                    subject=f"Following up on {STATE['reference_a']}",
                    rfc="followup-1@client.test",
                    minutes=120,
                )
            ],
        )

    outcome = only(report, core.INSERTED)

    if outcome.case_id != STATE["case_a"]:
        raise RuntimeError(
            f"INGEST09 FAIL: landed on {outcome.case_id}"
        )

    if outcome.correlation_method != "CASE_REFERENCE":
        raise RuntimeError(
            f"INGEST09 FAIL: method {outcome.correlation_method!r}"
        )

    print("INGEST09 SUBJECT CASE REFERENCE CORRELATES: PASS")


def ingest10_ingestion_has_no_outbound_capability():
    from app.integrations import gmail_ingest

    sending = [
        name
        for name in dir(core)
        if not name.startswith("_")
        and any(word in name.lower() for word in ("send", "reply", "draft"))
    ]

    if sending:
        raise RuntimeError(
            f"INGEST10 FAIL: ingestion core exposes {sending}"
        )

    if gmail_ingest.SCOPES != [
        "https://www.googleapis.com/auth/gmail.readonly"
    ]:
        raise RuntimeError(
            f"INGEST10 FAIL: adapter scopes {gmail_ingest.SCOPES}"
        )

    print("INGEST10 INGESTION HAS NO OUTBOUND CAPABILITY: PASS")


def ingest11_ingested_case_reaches_the_agent_only_after_triage():
    stub = model_module.DeterministicStubModel()

    refused = runner.execute(
        stub, STATE["case_a"], operation_id="ingest-before-triage"
    )

    if refused.result_state != "REFUSED":
        raise RuntimeError(
            f"INGEST11 FAIL: untriaged case ran, state "
            f"{refused.result_state!r}"
        )

    if "no service scope" not in (refused.failure_reason or ""):
        raise RuntimeError(
            f"INGEST11 FAIL: reason {refused.failure_reason!r}"
        )

    with app_conn() as conn:
        core.assign_service(conn, STATE["case_a"], "nri_india_tax_filing")

    result = runner.execute(
        stub, STATE["case_a"], operation_id="ingest-after-triage"
    )

    if result.result_state != "SUCCEEDED":
        raise RuntimeError(
            f"INGEST11 FAIL: {result.result_state} "
            f"{result.failure_reason}"
        )

    # The seeded service holds only SOURCE_RECORDED
    # material, which cannot satisfy the approved-guidance
    # gate, so the honest outcome is a knowledge gap rather
    # than a request to the client.
    if result.decision_state != "MISSING_KNOWLEDGE":
        raise RuntimeError(
            f"INGEST11 FAIL: decision {result.decision_state!r}"
        )

    if result.proposal_id is None:
        raise RuntimeError("INGEST11 FAIL: no proposal persisted")

    STATE["run"] = result

    print(
        "INGEST11 INGESTED CASE REACHES THE AGENT ONLY AFTER TRIAGE: PASS"
    )


def _stranger_attempt(provider_id, subject, references, minutes):
    """Ingest a message from someone with no standing on the case."""
    before = [row for row in message_rows() if row[1] == STATE["case_a"]]

    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [
                message(
                    provider_id,
                    sender="stranger@elsewhere.test",
                    subject=subject,
                    body="Please send me everything on this matter.",
                    rfc=f"{provider_id}@elsewhere.test",
                    references=references,
                    minutes=minutes,
                )
            ],
        )

    outcome = only(report, core.QUARANTINED)

    row = next(row for row in message_rows() if row[0] == provider_id)
    _provider, case_id, status, method = row

    if case_id is not None or status != "AMBIGUOUS" or method is not None:
        raise RuntimeError(f"row is {row!r}")

    after = [row for row in message_rows() if row[1] == STATE["case_a"]]

    if len(after) != len(before):
        raise RuntimeError(
            f"the case gained a message: {len(before)} -> {len(after)}"
        )

    return outcome


def ingest12_subject_reference_from_stranger_is_quarantined():
    outcome = _stranger_attempt(
        "m-stranger-ref",
        f"Re: {STATE['reference_a']} urgent",
        None,
        150,
    )

    print(
        "INGEST12 SUBJECT REFERENCE FROM A STRANGER IS QUARANTINED: "
        f"PASS ({outcome.reason})"
    )


def ingest13_reply_header_from_stranger_is_quarantined():
    outcome = _stranger_attempt(
        "m-stranger-reply",
        "Following up",
        f"<{ROOT_A}>",
        160,
    )

    print(
        "INGEST13 REPLY HEADER FROM A STRANGER IS QUARANTINED: "
        f"PASS ({outcome.reason})"
    )


def ingest14_ingestion_can_refresh_but_never_asks_for_consent():
    """Refreshing a token is not re-consent, and only one of them is here.

    An access token lasts about an hour, so refusing to refresh made
    unattended ingestion impossible while protecting nothing: the
    exchange involves no person. Obtaining consent is the part that must
    stay deliberate, so this module must have no way to do it.
    """
    from app.integrations import gmail_ingest

    source = Path(gmail_ingest.__file__).read_text(encoding="utf-8")

    forbidden = [
        name
        for name in ("InstalledAppFlow", "run_local_server", "run_console")
        if name in source
    ]

    if forbidden:
        raise RuntimeError(
            f"INGEST14 FAIL: ingestion can start a consent flow via "
            f"{forbidden}; that belongs only in the auth flow"
        )

    if "creds.refresh(" not in source:
        raise RuntimeError(
            "INGEST14 FAIL: ingestion cannot refresh an expired access "
            "token, so it cannot run unattended"
        )

    if "RefreshError" not in source:
        raise RuntimeError(
            "INGEST14 FAIL: a refused refresh means consent was "
            "withdrawn and must stop the run, not pass silently"
        )

    print(
        "INGEST14 INGESTION REFRESHES BUT NEVER ASKS FOR CONSENT: PASS"
    )


CHECKS = (
    ingest01_new_message_creates_untriaged_case,
    ingest02_duplicate_provider_message_is_absorbed,
    ingest03_reply_joins_the_same_case,
    ingest04_self_generated_mail_is_not_ingested,
    ingest05_ambiguous_correlation_is_quarantined,
    ingest06_cursor_is_not_advanced_by_ingest,
    ingest07_replay_after_interruption_is_absorbed,
    ingest08_cursor_advances_only_when_asked,
    ingest09_case_reference_correlates,
    ingest10_ingestion_has_no_outbound_capability,
    ingest12_subject_reference_from_stranger_is_quarantined,
    ingest13_reply_header_from_stranger_is_quarantined,
    ingest14_ingestion_can_refresh_but_never_asks_for_consent,
    ingest11_ingested_case_reaches_the_agent_only_after_triage,
)


def phase1():
    seed()

    for check in CHECKS:
        check()

    result = STATE["run"]

    print("\n--- ingested enquiry to persisted proposal ---")
    for line in result.summary_lines():
        print(line)

    print("\nINGESTION PHASE 1: PASS")


def _guard():
    """Refuse to run unless this database is a marked disposable target.

    Runs before any fixture is written and before any cleanup deletes
    anything, so a misconfigured environment cannot write to, or delete
    from, a real instance.
    """
    with admin_conn() as conn:
        token = testguard.assert_disposable(conn)

    testguard.acquire_single_run_lock()

    print(f"TEST TARGET: disposable, token {token}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: ingestion_smoke.py phase1|cleanup")

    mode = sys.argv[1]

    # Only the modes that write fixtures need the target guard and the
    # single-run lock. Read-only modes stay callable from a child
    # process while the parent run holds the lock.
    if mode in ("phase1", "cleanup"):
        _guard()

    if mode == "phase1":
        phase1()
    elif mode == "cleanup":
        cleanup()
    else:
        raise SystemExit(f"Unknown mode: {mode}")
