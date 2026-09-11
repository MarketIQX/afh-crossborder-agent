"""Checks for the reviewer console, driven over HTTP.

The previous suite asserted that the view was read-only: POST returned
405 and no control was rendered. Both were true of the inspection page
and are deliberately false now, so those checks encoded a claim the
system has moved past. They are replaced rather than deleted, and what
replaces them is harder: that the three powers stay separable, that a
stale draft cannot be approved, and that an ungranted reviewer cannot
authorise anything through the interface.

Two cases are built through the real path so both branches appear: one
where a letter is legitimate, and one where the decision is internal
and no letter should exist at all.

Usage:

    python tests/reviewer_console_smoke.py phase1
    python tests/reviewer_console_smoke.py cleanup
"""

import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.agent import model as model_module  # noqa: E402
from app.agent import runner  # noqa: E402
from app.db import testguard  # noqa: E402
from app.ingestion import core  # noqa: E402
from app.reviewer import server as console  # noqa: E402

import simulated_approved_fixture as simulated  # noqa: E402

SETTINGS = config.database_settings()

SEEDED_SERVICE = "nri_india_tax_filing"

MAILBOX_ID = "9c000000-0000-0000-0000-000000000001"
MAILBOX_ADDRESS = "console-fixture@example.test"

CLIENT = "console-client@example.test"
OTHER_CLIENT = "console-other@example.test"

GRANTED = "9c100000-0000-0000-0000-000000000001"
UNGRANTED = "9c100000-0000-0000-0000-000000000002"

BASE_TIME = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)

LETTER_SUBJECT = "Residency and filing for the year"
LETTER_BODY = (
    "Hello,\n"
    "I moved abroad and want to know my residency position and whether "
    "I must file a return.\n"
    "Regards"
)

GAP_SUBJECT = "Sale of a flat in Pune"
GAP_BODY = "I sold a property last year. What are the implications?"

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
        testguard.assert_disposable(conn)

        with conn.cursor() as cur:
            cases = _fixture_cases(cur)

            if cases:
                cur.execute(
                    "DELETE FROM app.dispatches WHERE approval_id IN "
                    "(SELECT a.id FROM app.approvals a "
                    " JOIN app.draft_messages d ON d.id = a.draft_message_id "
                    " JOIN app.proposal_revisions r "
                    "   ON r.id = d.proposal_revision_id "
                    " JOIN app.action_proposals p ON p.id = r.proposal_id "
                    " WHERE p.case_id = ANY(%s))",
                    (cases,),
                )
                cur.execute(
                    "DELETE FROM app.approvals WHERE draft_message_id IN "
                    "(SELECT d.id FROM app.draft_messages d "
                    " JOIN app.proposal_revisions r "
                    "   ON r.id = d.proposal_revision_id "
                    " JOIN app.action_proposals p ON p.id = r.proposal_id "
                    " WHERE p.case_id = ANY(%s))",
                    (cases,),
                )
                cur.execute(
                    "DELETE FROM app.draft_messages WHERE "
                    "proposal_revision_id IN (SELECT r.id "
                    " FROM app.proposal_revisions r "
                    " JOIN app.action_proposals p ON p.id = r.proposal_id "
                    " WHERE p.case_id = ANY(%s))",
                    (cases,),
                )
                cur.execute(
                    "DELETE FROM app.proposal_revisions WHERE proposal_id IN "
                    "(SELECT id FROM app.action_proposals "
                    " WHERE case_id = ANY(%s))",
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
                    " WHERE case_id = ANY(%s))",
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
                    "DELETE FROM app.reviewer_case_grants "
                    "WHERE case_id = ANY(%s)",
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
                "DELETE FROM app.reviewers WHERE id = ANY(%s)",
                ([GRANTED, UNGRANTED],),
            )
            cur.execute(
                "DELETE FROM app.mailboxes WHERE id = %s", (MAILBOX_ID,)
            )

            simulated.REVIEWER.drop(cur)
        conn.commit()

    print("CONSOLE FIXTURE CLEANUP: PASS")


def _message(provider_id, sender, subject, body, minutes):
    return core.ProviderMessage(
        provider_message_id=provider_id,
        sender_address=sender,
        recipient_addresses=(MAILBOX_ADDRESS,),
        received_at=BASE_TIME + timedelta(minutes=minutes),
        rfc_message_id=f"{provider_id}@example.test",
        subject=subject,
        body_text=body,
    )


def seed():
    """Two matters: one that warrants a letter, one that does not."""
    cleanup()

    with admin_conn() as conn:
        with conn.cursor() as cur:
            simulated.REVIEWER.create(cur)

            cur.execute(
                "INSERT INTO app.mailboxes (id, provider, address) "
                "VALUES (%s, 'gmail', %s)",
                (MAILBOX_ID, MAILBOX_ADDRESS),
            )

            for reviewer_id, email, name in (
                (GRANTED, "granted-console@example.test", "Granted Reviewer"),
                (
                    UNGRANTED,
                    "ungranted-console@example.test",
                    "Ungranted Reviewer",
                ),
            ):
                cur.execute(
                    "INSERT INTO app.reviewers (id, email, display_name) "
                    "VALUES (%s, %s, %s)",
                    (reviewer_id, email, name),
                )
        conn.commit()

    with app_conn() as conn:
        report = core.ingest(
            conn,
            MAILBOX_ID,
            [
                _message("c1", CLIENT, LETTER_SUBJECT, LETTER_BODY, 0),
                _message("c2", OTHER_CLIENT, GAP_SUBJECT, GAP_BODY, 10),
            ],
        )

        letter_case = report.outcomes[0].case_id
        gap_case = report.outcomes[1].case_id

        # The letter case runs against verified knowledge, so a client
        # letter is legitimate. The gap case runs against the seeded
        # corpus, which is only SOURCE_RECORDED, so it must not produce
        # one.
        core.assign_service(conn, letter_case, simulated.REVIEWER.service_key)
        core.assign_service(conn, gap_case, SEEDED_SERVICE)

    stub = model_module.DeterministicStubModel()

    letter_run = runner.execute(
        stub, letter_case, operation_id=f"console-letter-{uuid.uuid4().hex[:8]}"
    )
    gap_run = runner.execute(
        stub, gap_case, operation_id=f"console-gap-{uuid.uuid4().hex[:8]}"
    )

    if letter_run.decision_state != "MISSING_FACTS":
        raise RuntimeError(
            f"CONSOLE SEED FAIL: letter case is "
            f"{letter_run.decision_state}"
        )

    if gap_run.decision_state != "MISSING_KNOWLEDGE":
        raise RuntimeError(
            f"CONSOLE SEED FAIL: gap case is {gap_run.decision_state}"
        )

    with admin_conn() as conn:
        with conn.cursor() as cur:
            for case_id in (letter_case, gap_case):
                cur.execute(
                    "INSERT INTO app.reviewer_case_grants "
                    "(reviewer_id, case_id) VALUES (%s, %s)",
                    (GRANTED, case_id),
                )
        conn.commit()

    STATE["letter_case"] = letter_case
    STATE["gap_case"] = gap_case

    print("CONSOLE FIXTURE SEEDED: PASS")


def start():
    httpd = console.make_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    return httpd, port


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as r:
        return r.status, r.read().decode("utf-8"), r.geturl()


def post(port, path, fields):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=urllib.parse.urlencode(fields).encode(),
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as r:
        return r.status, r.read().decode("utf-8"), r.geturl()


def hidden(body, name):
    match = re.search(rf'name="{name}"\s+value="([^"]*)"', body)
    return match.group(1) if match else None


def flash_of(url):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return (query.get("msg") or [""])[0]


def require(label, condition, detail=""):
    if not condition:
        raise RuntimeError(f"{label} FAIL: {detail}")

    print(f"{label}: PASS {detail}".rstrip())


def view01_console_opens_on_the_decision(port):
    status, body, _ = get(port, f"/case/{STATE['letter_case']}")

    require(
        "VIEW01 THE PAGE LEADS WITH THE DECISION",
        status == 200
        and "The client needs to tell us something first." in body,
        "",
    )


def view02_the_clients_words_are_shown(port):
    _s, body, _ = get(port, f"/case/{STATE['letter_case']}")

    require(
        "VIEW02 THE CLIENT'S OWN WORDS ARE SHOWN",
        "must file a return" in body and CLIENT in body,
    )


def view03_no_authentication_is_claimed(port):
    _s, body, _ = get(port, f"/case/{STATE['letter_case']}")

    require(
        "VIEW03 THE PAGE STATES THERE IS NO SIGN-IN",
        "selected, not verified" in body,
        "authorisation without authentication, said plainly",
    )


def view04_page_makes_no_external_requests(port):
    _s, body, _ = get(port, f"/case/{STATE['letter_case']}")

    external = set(re.findall(r"https?://[^\"'<> ]+", body))

    require(
        "VIEW04 THE CONSOLE MAKES NO EXTERNAL REQUESTS",
        not external,
        "a reviewer opening a client matter phones nobody",
    )


def view05_internal_decisions_offer_no_letter(port):
    _s, body, _ = get(port, f"/case/{STATE['gap_case']}")

    require(
        "VIEW05 AN INTERNAL DECISION OFFERS NO LETTER TO THE CLIENT",
        "needs a colleague" in body
        and "/draft" not in body
        and "Approve this letter" not in body,
        "MISSING_KNOWLEDGE cannot be drafted to a client",
    )


def view06_drafting_produces_the_letter(port):
    _s, body, _ = get(port, f"/case/{STATE['letter_case']}")
    revision_id = hidden(body, "revision_id")

    _s, body, url = post(
        port,
        f"/case/{STATE['letter_case']}/draft",
        {"reviewer": GRANTED, "revision_id": revision_id},
    )

    STATE["draft_id"] = hidden(body, "draft_id")
    STATE["digest"] = hidden(body, "seen_digest")

    # Assert the control, not its wording. A button's label is copy and
    # will change; what must hold is that a control exists which
    # approves this draft, and one which returns it.
    approves = 'name="decision" value="APPROVED"' in body
    returns = 'name="decision" value="REJECTED"' in body
    edits = f"/case/{STATE['letter_case']}/edit" in body

    require(
        "VIEW06 DRAFTING PRODUCES A LETTER AND THREE ACTIONS",
        STATE["draft_id"] is not None
        and CLIENT in body
        and approves
        and returns
        and edits,
        f"draft={STATE['draft_id'] is not None} approve={approves} "
        f"return={returns} edit={edits} {flash_of(url)[:40]}",
    )


def view07_a_stale_digest_is_refused(port):
    _s, _body, url = post(
        port,
        f"/case/{STATE['letter_case']}/decide",
        {
            "reviewer": GRANTED,
            "draft_id": STATE["draft_id"],
            "seen_digest": "a digest from a page rendered earlier",
            "decision": "APPROVED",
        },
    )

    require(
        "VIEW07 A STALE RENDER CANNOT BE APPROVED THROUGH THE FORM",
        "changed since it was shown" in flash_of(url),
        "",
    )


def view08_an_ungranted_reviewer_is_refused(port):
    _s, _body, url = post(
        port,
        f"/case/{STATE['letter_case']}/decide",
        {
            "reviewer": UNGRANTED,
            "draft_id": STATE["draft_id"],
            "seen_digest": STATE["digest"],
            "decision": "APPROVED",
        },
    )

    require(
        "VIEW08 AN UNGRANTED REVIEWER CANNOT APPROVE THROUGH THE FORM",
        "no active grant" in flash_of(url),
        "",
    )


def view09_approval_does_not_send(port):
    _s, body, url = post(
        port,
        f"/case/{STATE['letter_case']}/decide",
        {
            "reviewer": GRANTED,
            "draft_id": STATE["draft_id"],
            "seen_digest": STATE["digest"],
            "decision": "APPROVED",
            "note": "Asks only what we need.",
        },
    )

    STATE["approval_id"] = hidden(body, "approval_id")

    require(
        "VIEW09 APPROVING DOES NOT SEND, AND THE PAGE SAYS SO",
        "Approved, not yet sent" in body
        and "cannot send one" in body
        and STATE["approval_id"] is not None,
        "",
    )


def view10_sending_reports_the_outcome_honestly(port):
    _s, body, url = post(
        port,
        f"/case/{STATE['letter_case']}/send",
        {"reviewer": GRANTED, "approval_id": STATE["approval_id"]},
    )

    require(
        "VIEW10 SENDING REPORTS PROVIDER ACCEPTANCE, NOT DELIVERY",
        "SENT" in body and "not proof of delivery" in body,
        flash_of(url)[:44],
    )


def view11_a_second_send_is_refused(port):
    _s, _body, url = post(
        port,
        f"/case/{STATE['letter_case']}/send",
        {"reviewer": GRANTED, "approval_id": STATE["approval_id"]},
    )

    require(
        "VIEW11 A SECOND SEND THROUGH THE FORM IS REFUSED",
        "must not be retried" in flash_of(url)
        or "already has a dispatch" in flash_of(url),
        "",
    )


def view12_unknown_routes_behave(port):
    try:
        get(port, "/case/99999999-9999-9999-9999-999999999999")
        missing = 200
    except urllib.error.HTTPError as exc:
        missing = exc.code

    try:
        post(port, f"/case/{STATE['letter_case']}/destroy", {})
        action = 200
        allow = None
    except urllib.error.HTTPError as exc:
        action = exc.code
        allow = exc.headers.get("Allow")

    require(
        "VIEW12 UNKNOWN MATTER IS 404 AND UNKNOWN ACTION IS 405",
        missing == 404 and action == 405 and allow == "GET, POST",
        f"{missing}/{action}",
    )


CHECKS = (
    view01_console_opens_on_the_decision,
    view02_the_clients_words_are_shown,
    view03_no_authentication_is_claimed,
    view04_page_makes_no_external_requests,
    view05_internal_decisions_offer_no_letter,
    view06_drafting_produces_the_letter,
    view07_a_stale_digest_is_refused,
    view08_an_ungranted_reviewer_is_refused,
    view09_approval_does_not_send,
    view10_sending_reports_the_outcome_honestly,
    view11_a_second_send_is_refused,
    view12_unknown_routes_behave,
)


def _guard():
    with admin_conn() as conn:
        token = testguard.assert_disposable(conn)

    testguard.acquire_single_run_lock()

    print(f"TEST TARGET: disposable, token {token}")


def phase1():
    seed()

    httpd, port = start()
    print(f"CONSOLE: http://127.0.0.1:{port}/")

    try:
        for check in CHECKS:
            check(port)
    finally:
        httpd.shutdown()
        httpd.server_close()

    print("\nREVIEWER CONSOLE PHASE 1: PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: reviewer_console_smoke.py phase1|cleanup"
        )

    mode = sys.argv[1]

    if mode in ("phase1", "cleanup"):
        _guard()

    if mode == "phase1":
        phase1()
    elif mode == "cleanup":
        cleanup()
    else:
        raise SystemExit(f"Unknown mode: {mode}")
