"""Checks for the read-only reviewer view.

The fixture is built through the real path: a provider message is
ingested, a human triages it into a service, and the agent produces a
proposal. The view is then served on an ephemeral port and fetched over
HTTP, so what is asserted is the page a reviewer would actually see.

Usage:

    python tests/reviewer_view_smoke.py phase1
    python tests/reviewer_view_smoke.py cleanup
"""

import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402

import simulated_approved_fixture as simulated  # noqa: E402
from app.agent import model as model_module  # noqa: E402
from app.agent import runner  # noqa: E402
from app.ingestion import core  # noqa: E402
from app.reviewer import server as server_module  # noqa: E402

SETTINGS = config.database_settings()

MAILBOX_ID = "98000000-0000-0000-0000-000000000001"
MAILBOX_ADDRESS = "reviewer-fixture@example.test"

CLIENT = "reviewer-client@example.test"
OTHER = "reviewer-other@example.test"

ROOT_A = "view-root-a@client.test"
ROOT_B = "view-root-b@client.test"

BASE_TIME = datetime(2026, 6, 25, 8, 0, 0, tzinfo=timezone.utc)

SUBJECT = "Residency and return filing for this year"

BODY = (
    "Hello,\n"
    "I need to know my residential status and whether I must file a "
    "return in India.\n"
    "Country of residence: United Arab Emirates\n"
    "Regards"
)

MATERIAL_FACTS = (
    ("assessment_year", "AY 2026-27"),
    ("days_present_in_india_current_year", "38"),
    ("days_present_in_india_preceding_four_years", "150"),
    ("india_sourced_income_present", "yes, rental income"),
    ("country_of_residence", "United Arab Emirates"),
)

STATE = {}


def admin_conn():
    return psycopg.connect(**SETTINGS.admin_kwargs())


def app_conn():
    return psycopg.connect(**SETTINGS.app_kwargs(), autocommit=True)


def _fixture_cases(cur):
    cur.execute(
        "SELECT DISTINCT case_id::text FROM app.inbound_messages "
        "WHERE mailbox_id = %s AND case_id IS NOT NULL",
        (MAILBOX_ID,),
    )
    return [row[0] for row in cur.fetchall()]


def cleanup():
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cases = _fixture_cases(cur)

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

            simulated.REVIEWER.drop(cur)
        conn.commit()

    print("VIEW FIXTURE CLEANUP: PASS")


def _message(provider_id, sender, subject, body, rfc, references=None, minutes=0):
    return core.ProviderMessage(
        provider_message_id=provider_id,
        sender_address=sender,
        recipient_addresses=(MAILBOX_ADDRESS,),
        received_at=BASE_TIME + timedelta(minutes=minutes),
        rfc_message_id=rfc,
        references_header=references,
        subject=subject,
        body_text=body,
    )


def seed():
    """Build the fixture through ingestion, triage and a real agent run."""
    cleanup()

    with admin_conn() as conn:
        with conn.cursor() as cur:
            simulated.REVIEWER.create(cur)

            cur.execute(
                "INSERT INTO app.mailboxes (id, provider, address) "
                "VALUES (%s, 'gmail', %s)",
                (MAILBOX_ID, MAILBOX_ADDRESS),
            )
        conn.commit()

    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [
                _message("v1", CLIENT, SUBJECT, BODY, ROOT_A, minutes=0),
                _message(
                    "v2",
                    OTHER,
                    "Unrelated filing question",
                    "Do I need to file this year?",
                    ROOT_B,
                    minutes=10,
                ),
                _message(
                    "v3",
                    CLIENT,
                    "Combining both threads",
                    "Please read both of my earlier emails together.",
                    "view-merge@client.test",
                    references=f"<{ROOT_A}> <{ROOT_B}>",
                    minutes=20,
                ),
            ],
        )

    inserted = [o for o in report.outcomes if o.action == core.INSERTED]
    quarantined = [o for o in report.outcomes if o.action == core.QUARANTINED]

    if len(inserted) != 2 or len(quarantined) != 1:
        raise RuntimeError(f"VIEW SEED FAIL: {report.counts()}")

    case_id = inserted[0].case_id
    STATE["case_id"] = case_id

    with app_conn() as conn:
        core.assign_service(
            conn, case_id, simulated.REVIEWER.service_key
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reference FROM app.cases WHERE id = %s", (case_id,)
            )
            STATE["reference"] = cur.fetchone()[0]

    stub = model_module.DeterministicStubModel()

    first = runner.execute(stub, case_id, operation_id="view-first-run")

    if first.decision_state != "MISSING_FACTS":
        raise RuntimeError(
            f"VIEW SEED FAIL: first run {first.decision_state}"
        )

    STATE["missing_run"] = first

    # A reviewer confirms the material facts. Only an elevated identity
    # can do this; the runtime role has no privilege on the status
    # column, which AUTH01 proves.
    with admin_conn() as conn:
        with conn.cursor() as cur:
            for offset, (predicate, value) in enumerate(MATERIAL_FACTS):
                cur.execute(
                    """
                    INSERT INTO app.case_facts (
                        id, case_id, predicate, value_text, status, origin
                    ) VALUES (%s, %s, %s, %s, 'CONFIRMED', 'REVIEWER')
                    """,
                    (
                        f"98500000-0000-0000-0000-{offset:012d}",
                        case_id,
                        predicate,
                        value,
                    ),
                )
        conn.commit()

    second = runner.execute(stub, case_id, operation_id="view-second-run")

    if second.decision_state != "SUPPORTED_WITHIN_POLICY":
        raise RuntimeError(
            f"VIEW SEED FAIL: second run {second.decision_state}"
        )

    STATE["supported_run"] = second

    print("VIEW FIXTURE SEEDED: PASS")


def start_server():
    httpd = server_module.make_server(0)
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    return httpd, port


def get(port, path):
    url = f"http://127.0.0.1:{port}{path}"

    with urllib.request.urlopen(url, timeout=15) as response:
        return response.status, response.read().decode("utf-8")


def send_method(port, path, method):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method, data=b""
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers)


def require(label, condition, detail=""):
    if not condition:
        raise RuntimeError(f"{label} FAIL: {detail}")

    print(f"{label}: PASS")


def view01_index_lists_the_case(port):
    status, body = get(port, "/")

    require(
        "VIEW01 INDEX LISTS THE CASE",
        status == 200 and STATE["reference"] in body,
        f"status {status}",
    )


def view02_case_page_shows_the_enquiry(port):
    _status, body = get(port, f"/case/{STATE['case_id']}")

    require(
        "VIEW02 CASE PAGE SHOWS THE ORIGINAL ENQUIRY",
        SUBJECT in body and "must file a" in body,
        "enquiry subject or body missing",
    )


def view03_case_page_shows_run_provenance(port):
    _status, body = get(port, f"/case/{STATE['case_id']}")

    run_id = STATE["supported_run"].run_id

    require(
        "VIEW03 CASE PAGE SHOWS RUN PROVENANCE",
        run_id in body
        and "DETERMINISTIC_STUB" in body
        and "get_service_knowledge" in body,
        "run id, runner or tool trace missing",
    )


def view04_case_page_shows_source_provenance(port):
    _status, body = get(port, f"/case/{STATE['case_id']}")

    require(
        "VIEW04 CASE PAGE SHOWS SOURCE PROVENANCE",
        "SIMULATED FIXTURE SOURCE" in body
        and "PROFESSIONALLY_VERIFIED" in body
        and "signed off by a qualified professional" in body
        and "SIMULATED approved-knowledge fixture" in body,
        "source locator, verification status or captured passage missing",
    )


def view05_case_page_names_missing_facts(port):
    _status, body = get(port, f"/case/{STATE['case_id']}")

    require(
        "VIEW05 CASE PAGE NAMES THE MISSING FACTS",
        "Missing facts named" in body
        and "days_present_in_india_current_year" in body,
        "missing predicates not shown",
    )


def view06_supported_state_is_labelled_honestly(port):
    _status, body = get(port, f"/case/{STATE['case_id']}")

    require(
        "VIEW06 SUPPORTED STATE IS LABELLED HONESTLY",
        "Covered for review, not yet approved for the client"
        in body,
        "supported state is not qualified in the UI",
    )


def view07_writes_are_refused(port):
    results = {}

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        status, headers = send_method(
            port, f"/case/{STATE['case_id']}", method
        )
        results[method] = (status, headers.get("Allow"))

    require(
        "VIEW07 EVERY WRITE METHOD IS REFUSED",
        all(
            status == 405 and allow == "GET"
            for status, allow in results.values()
        ),
        f"{results}",
    )


def view08_unknown_case_is_not_found(port):
    try:
        get(port, "/case/99999999-9999-9999-9999-999999999999")
        status = 200
    except urllib.error.HTTPError as exc:
        status = exc.code

    require(
        "VIEW08 UNKNOWN CASE IS NOT FOUND",
        status == 404,
        f"status {status}",
    )


def view09_quarantine_is_visible(port):
    _status, body = get(port, "/")

    require(
        "VIEW09 QUARANTINED MESSAGE IS VISIBLE TO A REVIEWER",
        "Quarantined messages" in body
        and "AMBIGUOUS" in body
        and "Combining both threads" in body,
        "quarantine section missing",
    )


def view10_no_write_controls_are_rendered(port):
    _status, index = get(port, "/")
    _status, detail = get(port, f"/case/{STATE['case_id']}")

    offenders = [
        marker
        for marker in ("<form", "<button", "<input", "<textarea")
        if marker in index or marker in detail
    ]

    require(
        "VIEW10 NO WRITE CONTROLS ARE RENDERED",
        not offenders,
        f"found {offenders}",
    )


CHECKS = (
    view01_index_lists_the_case,
    view02_case_page_shows_the_enquiry,
    view03_case_page_shows_run_provenance,
    view04_case_page_shows_source_provenance,
    view05_case_page_names_missing_facts,
    view06_supported_state_is_labelled_honestly,
    view07_writes_are_refused,
    view08_unknown_case_is_not_found,
    view09_quarantine_is_visible,
    view10_no_write_controls_are_rendered,
)


def phase1():
    seed()

    httpd, port = start_server()
    print(f"VIEW SERVER: http://127.0.0.1:{port}/")

    try:
        for check in CHECKS:
            check(port)
    finally:
        httpd.shutdown()
        httpd.server_close()

    print(f"\nREVIEWER CASE URL: /case/{STATE['case_id']}")
    print(f"REVIEWER CASE REFERENCE: {STATE['reference']}")
    print(f"SUPPORTED RUN: {STATE['supported_run'].run_id}")
    print(f"SUPPORTED PROPOSAL: {STATE['supported_run'].proposal_id}")

    print("\nREVIEWER VIEW PHASE 1: PASS")


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
        raise SystemExit("Usage: reviewer_view_smoke.py phase1|cleanup")

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
