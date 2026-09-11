"""Approval and dispatch: every way a send must be refused.

A message reaching a client is the only irreversible thing this system
does. These checks enumerate the ways that must not happen, and each one
additionally asserts that the provider was never touched, because a
refusal that still sent something is not a refusal.

Usage:

    python tests/approval_dispatch_smoke.py phase1
    python tests/approval_dispatch_smoke.py cleanup
"""

import sys
import uuid
from pathlib import Path

import psycopg

from psycopg.errors import InsufficientPrivilege

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402
from app.dispatch import dispatcher, providers  # noqa: E402
from app.domain import approval as approval_domain  # noqa: E402

SETTINGS = config.database_settings()

SERVICE_ID = "10000000-0000-0000-0000-000000000001"
RELEASE_ID = "20000000-0000-0000-0000-000000000001"

CASE_ID = "9b000000-0000-0000-0000-000000000001"
OTHER_CASE_ID = "9b000000-0000-0000-0000-000000000002"

RUN_ID = "9b100000-0000-0000-0000-000000000001"
PROPOSAL_ID = "9b200000-0000-0000-0000-000000000001"
REVISION_ID = "9b300000-0000-0000-0000-000000000001"
SECOND_REVISION_ID = "9b300000-0000-0000-0000-000000000002"
THIRD_REVISION_ID = "9b300000-0000-0000-0000-000000000003"

MAILBOX_ID = "9b500000-0000-0000-0000-000000000001"
INBOUND_ID = "9b600000-0000-0000-0000-000000000001"

GRANTED_REVIEWER = "9b400000-0000-0000-0000-000000000001"
UNGRANTED_REVIEWER = "9b400000-0000-0000-0000-000000000002"
INACTIVE_REVIEWER = "9b400000-0000-0000-0000-000000000003"

RECIPIENT = "client@example.test"
SUBJECT = "About your residency position"
BODY = (
    "Dear client,\n\n"
    "To determine your residential status we still need your day count "
    "for the year.\n\n"
    "Regards"
)

STATE = {}


def admin_conn():
    return psycopg.connect(**SETTINGS.admin_kwargs())


def app_conn():
    return psycopg.connect(**SETTINGS.app_kwargs(), autocommit=True)


def reviewer_conn():
    return psycopg.connect(**SETTINGS.reviewer_kwargs(), autocommit=True)


def cleanup():
    with admin_conn() as conn:
        testguard.assert_disposable(conn)

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.dispatches WHERE approval_id IN "
                "(SELECT a.id FROM app.approvals a "
                " JOIN app.draft_messages d ON d.id = a.draft_message_id "
                " JOIN app.proposal_revisions r "
                "   ON r.id = d.proposal_revision_id "
                " JOIN app.action_proposals p ON p.id = r.proposal_id "
                " WHERE p.case_id = ANY(%s))",
                ([CASE_ID, OTHER_CASE_ID],),
            )
            cur.execute(
                "DELETE FROM app.approvals WHERE draft_message_id IN "
                "(SELECT d.id FROM app.draft_messages d "
                " JOIN app.proposal_revisions r "
                "   ON r.id = d.proposal_revision_id "
                " JOIN app.action_proposals p ON p.id = r.proposal_id "
                " WHERE p.case_id = ANY(%s))",
                ([CASE_ID, OTHER_CASE_ID],),
            )
            cur.execute(
                "DELETE FROM app.draft_messages WHERE proposal_revision_id "
                "IN (SELECT r.id FROM app.proposal_revisions r "
                " JOIN app.action_proposals p ON p.id = r.proposal_id "
                " WHERE p.case_id = ANY(%s))",
                ([CASE_ID, OTHER_CASE_ID],),
            )
            cur.execute(
                "DELETE FROM app.inbound_messages WHERE mailbox_id = %s",
                (MAILBOX_ID,),
            )
            cur.execute(
                "DELETE FROM app.mailboxes WHERE id = %s",
                (MAILBOX_ID,),
            )
            cur.execute(
                "DELETE FROM app.reviewer_case_grants WHERE case_id = ANY(%s)",
                ([CASE_ID, OTHER_CASE_ID],),
            )
            cur.execute(
                "DELETE FROM app.proposal_revisions WHERE proposal_id IN "
                "(SELECT id FROM app.action_proposals "
                " WHERE case_id = ANY(%s))",
                ([CASE_ID, OTHER_CASE_ID],),
            )
            cur.execute(
                "DELETE FROM app.action_proposals WHERE case_id = ANY(%s)",
                ([CASE_ID, OTHER_CASE_ID],),
            )
            cur.execute(
                "DELETE FROM app.agent_runs WHERE case_id = ANY(%s)",
                ([CASE_ID, OTHER_CASE_ID],),
            )
            cur.execute(
                "DELETE FROM app.reviewers WHERE id = ANY(%s)",
                (
                    [
                        GRANTED_REVIEWER,
                        UNGRANTED_REVIEWER,
                        INACTIVE_REVIEWER,
                    ],
                ),
            )
            cur.execute(
                "DELETE FROM app.cases WHERE id = ANY(%s)",
                ([CASE_ID, OTHER_CASE_ID],),
            )
        conn.commit()

    print("APPROVE FIXTURE CLEANUP: PASS")


def seed():
    """Two cases, three reviewers, one proposal revision to draft from."""
    cleanup()

    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.mailboxes (id, provider, address) "
                "VALUES (%s, 'gmail', 'firm@example.test')",
                (MAILBOX_ID,),
            )

            for case_id, reference in (
                (CASE_ID, "APPROVE-FIXTURE-001"),
                (OTHER_CASE_ID, "APPROVE-FIXTURE-002"),
            ):
                cur.execute(
                    "INSERT INTO app.cases (id, service_id, reference) "
                    "VALUES (%s, %s, %s)",
                    (case_id, SERVICE_ID, reference),
                )

            cur.execute(
                """
                INSERT INTO app.inbound_messages (
                    id, mailbox_id, provider_message_id,
                    sender_address, recipient_addresses, subject,
                    body_text, received_at, case_id,
                    correlation_status, correlation_method
                ) VALUES (
                    %s, %s, 'approve-fixture-msg', %s,
                    '["firm@example.test"]', 'Residency question',
                    'I moved abroad last year.',
                    '2026-06-01 09:00:00+00', %s,
                    'MATCHED', 'NEW_CASE'
                )
                """,
                (INBOUND_ID, MAILBOX_ID, RECIPIENT, CASE_ID),
            )

            cur.execute(
                """
                INSERT INTO app.agent_runs (
                    id, case_id, operation_id, runner, model_id,
                    prompt_version, prompt_digest, tool_schema_version,
                    context_builder_version, knowledge_release_id,
                    result_state, ended_at
                ) VALUES (
                    %s, %s, 'approve-fixture-run', 'DETERMINISTIC_STUB',
                    'fixture-model', 'v0', 'digest', 'v1', 'v1', %s,
                    'SUCCEEDED', now()
                )
                """,
                (RUN_ID, CASE_ID, RELEASE_ID),
            )

            cur.execute(
                "INSERT INTO app.action_proposals "
                "(id, case_id, current_revision) VALUES (%s, %s, 3)",
                (PROPOSAL_ID, CASE_ID),
            )

            for revision_id, revision in (
                (REVISION_ID, 1),
                (SECOND_REVISION_ID, 2),
                (THIRD_REVISION_ID, 3),
            ):
                cur.execute(
                    """
                    INSERT INTO app.proposal_revisions (
                        id, proposal_id, revision, run_id, decision_state,
                        summary, missing_predicates
                    ) VALUES (
                        %s, %s, %s, %s, 'MISSING_FACTS',
                        'Ask the client for the day count.',
                        '["days_present_in_india_current_year"]'
                    )
                    """,
                    (revision_id, PROPOSAL_ID, revision, RUN_ID),
                )

            for reviewer_id, email, name, active in (
                (GRANTED_REVIEWER, "granted@example.test", "Granted", True),
                (
                    UNGRANTED_REVIEWER,
                    "ungranted@example.test",
                    "Ungranted",
                    True,
                ),
                (
                    INACTIVE_REVIEWER,
                    "inactive@example.test",
                    "Inactive",
                    False,
                ),
            ):
                cur.execute(
                    "INSERT INTO app.reviewers "
                    "(id, email, display_name, is_active) "
                    "VALUES (%s, %s, %s, %s)",
                    (reviewer_id, email, name, active),
                )

            # The granted reviewer holds this case. The ungranted one
            # holds a different case, which must not carry over.
            cur.execute(
                "INSERT INTO app.reviewer_case_grants "
                "(reviewer_id, case_id) VALUES (%s, %s)",
                (GRANTED_REVIEWER, CASE_ID),
            )
            cur.execute(
                "INSERT INTO app.reviewer_case_grants "
                "(reviewer_id, case_id) VALUES (%s, %s)",
                (UNGRANTED_REVIEWER, OTHER_CASE_ID),
            )
            cur.execute(
                "INSERT INTO app.reviewer_case_grants "
                "(reviewer_id, case_id) VALUES (%s, %s)",
                (INACTIVE_REVIEWER, CASE_ID),
            )
        conn.commit()

    with app_conn() as conn:
        draft = approval_domain.create_draft(
            conn, REVISION_ID, RECIPIENT, SUBJECT, BODY
        )

    STATE["draft_id"] = draft["draft_id"]
    STATE["digest"] = draft["content_digest"]

    print("APPROVE FIXTURE SEEDED: PASS")


def expect_refusal(label, expected, action, provider=None):
    """Assert an action is refused, and that nothing was sent."""
    try:
        action()
    except expected as exc:
        if provider is not None and provider.sent:
            raise RuntimeError(
                f"{label} FAIL: refused but the provider was still called "
                f"{len(provider.sent)} time(s)"
            )

        print(f"{label}: PASS ({str(exc).split('.')[0][:72]})")
        return
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"{label} FAIL: wrong failure {exc.__class__.__name__}: {exc}"
        )

    raise RuntimeError(f"{label} FAIL: the action was permitted")


def tamper(column, value):
    """Change a stored draft after approval. Administrator only."""
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE app.draft_messages SET {column} = %s WHERE id = %s",
                (value, STATE["draft_id"]),
            )
        conn.commit()


def approve01_runtime_cannot_approve():
    def action():
        with app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app.approvals (id, draft_message_id, "
                    "reviewer_id, decision, approved_digest) "
                    "VALUES (%s, %s, %s, 'APPROVED', %s)",
                    (
                        str(uuid.uuid4()),
                        STATE["draft_id"],
                        GRANTED_REVIEWER,
                        STATE["digest"],
                    ),
                )

    expect_refusal(
        "APPROVE01 THE RUNTIME ROLE CANNOT APPROVE",
        InsufficientPrivilege,
        action,
    )


def approve02_reviewer_cannot_dispatch():
    def action():
        with reviewer_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app.dispatches (id, approval_id, "
                    "operation_id) VALUES (%s, %s, 'reviewer-attempt')",
                    (str(uuid.uuid4()), str(uuid.uuid4())),
                )

    expect_refusal(
        "APPROVE02 THE REVIEWER ROLE CANNOT DISPATCH",
        InsufficientPrivilege,
        action,
    )


def approve03_reviewer_cannot_rewrite_the_draft():
    def action():
        with reviewer_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app.draft_messages (id, "
                    "proposal_revision_id, recipient, subject, body_text, "
                    "content_digest) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        SECOND_REVISION_ID,
                        "elsewhere@example.test",
                        "Rewritten",
                        "Rewritten body",
                        "x",
                    ),
                )

    expect_refusal(
        "APPROVE03 THE REVIEWER ROLE CANNOT WRITE A DRAFT",
        InsufficientPrivilege,
        action,
    )


def approve04_reviewer_without_a_grant_is_refused():
    def action():
        with reviewer_conn() as conn:
            approval_domain.record_decision(
                conn,
                STATE["draft_id"],
                UNGRANTED_REVIEWER,
                "APPROVED",
                STATE["digest"],
            )

    expect_refusal(
        "APPROVE04 A REVIEWER GRANTED A DIFFERENT CASE IS REFUSED",
        approval_domain.ApprovalRefused,
        action,
    )


def approve05_inactive_reviewer_is_refused():
    def action():
        with reviewer_conn() as conn:
            approval_domain.record_decision(
                conn,
                STATE["draft_id"],
                INACTIVE_REVIEWER,
                "APPROVED",
                STATE["digest"],
            )

    expect_refusal(
        "APPROVE05 AN INACTIVE REVIEWER IS REFUSED",
        approval_domain.ApprovalRefused,
        action,
    )


def approve06_stale_render_is_refused():
    def action():
        with reviewer_conn() as conn:
            approval_domain.record_decision(
                conn,
                STATE["draft_id"],
                GRANTED_REVIEWER,
                "APPROVED",
                "a digest from a page rendered earlier",
            )

    expect_refusal(
        "APPROVE06 APPROVING A DIGEST THE REVIEWER DID NOT SEE IS REFUSED",
        approval_domain.ApprovalRefused,
        action,
    )


def approve07_valid_approval_is_recorded():
    with reviewer_conn() as conn:
        result = approval_domain.record_decision(
            conn,
            STATE["draft_id"],
            GRANTED_REVIEWER,
            "APPROVED",
            STATE["digest"],
            note="Checked the day-count request reads clearly.",
        )

    if result["approved_digest"] != STATE["digest"]:
        raise RuntimeError("APPROVE07 FAIL: digest not bound")

    STATE["approval_id"] = result["approval_id"]

    print("APPROVE07 A GRANTED REVIEWER'S APPROVAL BINDS THE DIGEST: PASS")


def approve08_second_live_approval_is_refused():
    def action():
        with reviewer_conn() as conn:
            approval_domain.record_decision(
                conn,
                STATE["draft_id"],
                GRANTED_REVIEWER,
                "APPROVED",
                STATE["digest"],
            )

    expect_refusal(
        "APPROVE08 A SECOND LIVE APPROVAL ON ONE DRAFT IS REFUSED",
        approval_domain.ApprovalRefused,
        action,
    )


def approve09_changed_body_is_refused():
    tamper("body_text", BODY + "\n\nPS: please wire the funds today.")
    provider = providers.SimulatedProvider()

    def action():
        with app_conn() as conn:
            dispatcher.dispatch(conn, STATE["approval_id"], provider)

    expect_refusal(
        "APPROVE09 A CHANGED BODY INVALIDATES THE APPROVAL",
        dispatcher.DispatchRefused,
        action,
        provider,
    )

    tamper("body_text", BODY)


def approve10_changed_recipient_is_refused():
    tamper("recipient", "someone-else@example.test")
    provider = providers.SimulatedProvider()

    def action():
        with app_conn() as conn:
            dispatcher.dispatch(conn, STATE["approval_id"], provider)

    expect_refusal(
        "APPROVE10 A CHANGED RECIPIENT INVALIDATES THE APPROVAL",
        dispatcher.DispatchRefused,
        action,
        provider,
    )

    tamper("recipient", RECIPIENT)


def approve11_revoked_approval_is_refused():
    with reviewer_conn() as conn:
        approval_domain.revoke(
            conn,
            STATE["approval_id"],
            GRANTED_REVIEWER,
            "withdrawn for testing",
        )

    provider = providers.SimulatedProvider()

    def action():
        with app_conn() as conn:
            dispatcher.dispatch(conn, STATE["approval_id"], provider)

    expect_refusal(
        "APPROVE11 A REVOKED APPROVAL CANNOT DISPATCH",
        dispatcher.DispatchRefused,
        action,
        provider,
    )

    # A fresh approval, now that the first is revoked.
    with reviewer_conn() as conn:
        result = approval_domain.record_decision(
            conn,
            STATE["draft_id"],
            GRANTED_REVIEWER,
            "APPROVED",
            STATE["digest"],
        )

    STATE["approval_id"] = result["approval_id"]


def approve12_rejected_decision_cannot_dispatch():
    with app_conn() as conn:
        draft = approval_domain.create_draft(
            conn,
            SECOND_REVISION_ID,
            RECIPIENT,
            "A second message",
            "Body of the second message.",
        )

    with reviewer_conn() as conn:
        rejected = approval_domain.record_decision(
            conn,
            draft["draft_id"],
            GRANTED_REVIEWER,
            "REJECTED",
            draft["content_digest"],
            note="Tone is wrong.",
        )

    provider = providers.SimulatedProvider()

    def action():
        with app_conn() as conn:
            dispatcher.dispatch(conn, rejected["approval_id"], provider)

    expect_refusal(
        "APPROVE12 A REJECTION DOES NOT AUTHORISE A SEND",
        dispatcher.DispatchRefused,
        action,
        provider,
    )


def approve13_approved_content_is_what_reaches_the_provider():
    provider = providers.SimulatedProvider()

    with app_conn() as conn:
        result = dispatcher.dispatch(
            conn, STATE["approval_id"], provider, operation_id="send-once"
        )

    if result["state"] != providers.SENT:
        raise RuntimeError(f"APPROVE13 FAIL: {result}")

    if len(provider.sent) != 1:
        raise RuntimeError(
            f"APPROVE13 FAIL: provider called {len(provider.sent)} times"
        )

    delivered = provider.sent[0]

    if (
        delivered["recipient"] != RECIPIENT
        or delivered["subject"] != SUBJECT
        or delivered["body_text"] != BODY
    ):
        raise RuntimeError(
            "APPROVE13 FAIL: the provider received different content from "
            "what was approved"
        )

    STATE["sent_dispatch"] = result["dispatch_id"]

    print(
        "APPROVE13 EXACTLY THE APPROVED CONTENT REACHES THE PROVIDER: "
        f"PASS ({result['provider_message_id']})"
    )


def approve14_duplicate_dispatch_after_send_is_refused():
    provider = providers.SimulatedProvider()

    def action():
        with app_conn() as conn:
            dispatcher.dispatch(conn, STATE["approval_id"], provider)

    expect_refusal(
        "APPROVE14 A SECOND DISPATCH AFTER A SEND IS REFUSED",
        dispatcher.DispatchRefused,
        action,
        provider,
    )


def approve15_duplicate_operation_id_is_refused():
    with app_conn() as conn:
        draft = approval_domain.create_draft(
            conn,
            THIRD_REVISION_ID,
            RECIPIENT,
            "Third message",
            "Body of the third message.",
        )

    provider = providers.SimulatedProvider()

    def action():
        with app_conn() as conn:
            dispatcher.dispatch(
                conn, STATE["approval_id"], provider, operation_id="send-once"
            )

    expect_refusal(
        "APPROVE15 A REPLAYED OPERATION ID IS REFUSED",
        dispatcher.DispatchRefused,
        action,
        provider,
    )

    STATE["spare_draft"] = draft


def approve16_send_unknown_blocks_retry():
    """An undetermined outcome must not be retried."""
    draft = STATE["spare_draft"]

    with reviewer_conn() as conn:
        approved = approval_domain.record_decision(
            conn,
            draft["draft_id"],
            GRANTED_REVIEWER,
            "APPROVED",
            draft["content_digest"],
        )

    unknown = providers.SimulatedProvider(
        outcome=providers.SEND_UNKNOWN,
        failure_reason="timeout after the request left",
    )

    with app_conn() as conn:
        result = dispatcher.dispatch(conn, approved["approval_id"], unknown)

    if result["state"] != providers.SEND_UNKNOWN:
        raise RuntimeError(f"APPROVE16 FAIL: {result}")

    retry = providers.SimulatedProvider()

    def action():
        with app_conn() as conn:
            dispatcher.dispatch(conn, approved["approval_id"], retry)

    expect_refusal(
        "APPROVE16 AN UNDETERMINED SEND BLOCKS A RETRY",
        dispatcher.DispatchRefused,
        action,
        retry,
    )

    STATE["unknown_approval"] = approved["approval_id"]


def approve17_definite_failure_permits_retry():
    """A send that definitively did not happen may be attempted again."""
    # Reuse the case's first draft: revoke its approval and approve it
    # again, so the retry is exercised on a draft that already exists.
    with reviewer_conn() as conn:
        approval_domain.revoke(
            conn,
            STATE["approval_id"],
            GRANTED_REVIEWER,
            "re-testing retry behaviour",
        )
        reapproved = approval_domain.record_decision(
            conn,
            STATE["draft_id"],
            GRANTED_REVIEWER,
            "APPROVED",
            STATE["digest"],
        )

    failing = providers.SimulatedProvider(
        outcome=providers.FAILED, failure_reason="rejected by provider"
    )

    with app_conn() as conn:
        first = dispatcher.dispatch(conn, reapproved["approval_id"], failing)

    if first["state"] != providers.FAILED:
        raise RuntimeError(f"APPROVE17 FAIL: {first}")

    succeeding = providers.SimulatedProvider()

    with app_conn() as conn:
        second = dispatcher.dispatch(
            conn, reapproved["approval_id"], succeeding
        )

    if second["state"] != providers.SENT:
        raise RuntimeError(f"APPROVE17 FAIL on retry: {second}")

    print("APPROVE17 A DEFINITE FAILURE PERMITS ONE RETRY: PASS")


def approve18_agent_has_no_send_or_approve_tool():
    from app.agent import tools as tools_module

    surface = set(tools_module.TOOL_NAMES)
    forbidden = {
        name
        for name in surface
        if any(
            word in name.lower()
            for word in ("send", "approve", "dispatch", "email")
        )
    }

    if forbidden:
        raise RuntimeError(f"APPROVE18 FAIL: agent exposes {forbidden}")

    import inspect

    # Callable members only. `schema_version` is a version constant, not
    # a capability, and comparing every public name would flag it.
    public = {
        name
        for name, _value in inspect.getmembers(
            tools_module.AgentTools, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    }

    if public != surface:
        raise RuntimeError(
            f"APPROVE18 FAIL: tool object exposes {public - surface}"
        )

    print(
        "APPROVE18 THE AGENT HAS NO SEND OR APPROVE CAPABILITY: PASS "
        f"({', '.join(sorted(surface))})"
    )


def approve19_draft_to_a_stranger_is_refused():
    """A recipient with no standing on the case cannot be drafted to."""

    def action():
        with app_conn() as conn:
            approval_domain.create_draft(
                conn,
                THIRD_REVISION_ID,
                "attacker@elsewhere.test",
                "Your case documents",
                "Please find everything attached.",
            )

    expect_refusal(
        "APPROVE19 A DRAFT TO A NON-CORRESPONDENT IS REFUSED",
        approval_domain.DraftRefused,
        action,
    )


def approve20_revoking_without_a_grant_is_refused():
    """Withdrawing an approval is authority over the case."""

    def action():
        with reviewer_conn() as conn:
            approval_domain.revoke(
                conn,
                STATE["approval_id"],
                UNGRANTED_REVIEWER,
                "not my case",
            )

    expect_refusal(
        "APPROVE20 REVOKING WITHOUT A GRANT ON THE CASE IS REFUSED",
        approval_domain.ApprovalRefused,
        action,
    )


CHECKS = (
    approve01_runtime_cannot_approve,
    approve02_reviewer_cannot_dispatch,
    approve03_reviewer_cannot_rewrite_the_draft,
    approve04_reviewer_without_a_grant_is_refused,
    approve05_inactive_reviewer_is_refused,
    approve06_stale_render_is_refused,
    approve07_valid_approval_is_recorded,
    approve08_second_live_approval_is_refused,
    approve09_changed_body_is_refused,
    approve10_changed_recipient_is_refused,
    approve11_revoked_approval_is_refused,
    approve12_rejected_decision_cannot_dispatch,
    approve13_approved_content_is_what_reaches_the_provider,
    approve14_duplicate_dispatch_after_send_is_refused,
    approve15_duplicate_operation_id_is_refused,
    approve16_send_unknown_blocks_retry,
    approve17_definite_failure_permits_retry,
    approve18_agent_has_no_send_or_approve_tool,
    approve19_draft_to_a_stranger_is_refused,
    approve20_revoking_without_a_grant_is_refused,
)


def _guard():
    """Refuse to run unless this database is a marked disposable target."""
    with admin_conn() as conn:
        token = testguard.assert_disposable(conn)

    testguard.acquire_single_run_lock()

    print(f"TEST TARGET: disposable, token {token}")


def phase1():
    seed()

    for check in CHECKS:
        check()

    print("\nAPPROVAL AND DISPATCH PHASE 1: PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: approval_dispatch_smoke.py phase1|cleanup"
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
