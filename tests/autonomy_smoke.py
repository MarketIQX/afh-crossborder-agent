"""AUTO01-AUTO09. The loop may start work; it may never finish it.

Autonomy is the part of this system most able to do harm, because it
acts with nobody watching. These checks pin the two properties that make
it safe to leave running: it routes only on a dominant signal, and it
physically cannot approve or send.

The routing checks use a private vocabulary seeded by this suite. The
first version used real client prose against the real corridor and
failed on a clean slate, correctly: a shared fixture seeds two more
services carrying the same topics, so three services tied and no
dominant signal existed. A test whose result depends on which other
suites have run is not a test, so this one owns its own data and
asserts the decision rule itself.

Usage:

    python tests/autonomy_smoke.py phase1
    python tests/autonomy_smoke.py cleanup
"""

import ast
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.autonomy import router  # noqa: E402
from app.db import testguard  # noqa: E402

SLOT = "autonomy-smoke"

# A private vocabulary. Real keywords would collide with the seeded
# corridor and with other suites' fixture services, and then this suite
# would be asserting on whatever else had run rather than on the rule.
SERVICE_A = "90000000-0000-0000-0000-0000000000a1"
SERVICE_B = "90000000-0000-0000-0000-0000000000b1"

TOPIC_A = "autosmoke_alpha"
TOPIC_B = "autosmoke_beta"

WORDS_A = ("zzalfa", "zzbravo", "zzcharlie")
WORDS_B = ("zzdelta", "zzecho")

# Three matches for A, none for B: a clear leader.
DOMINANT = "We need zzalfa and zzbravo guidance, plus zzcharlie."

# Two for A, two for B: a genuine straddle that must reach a person.
STRADDLE = "Questions on zzalfa, zzbravo, zzdelta and zzecho together."

# Nothing in either vocabulary.
JUNK = "Cloud Recording is now available. Your cloud recording is ready."

STATE = {}


def admin():
    return psycopg.connect(
        **config.database_settings().admin_kwargs(), autocommit=True
    )


def runtime():
    return psycopg.connect(
        **config.database_settings().app_kwargs(), autocommit=True
    )


def _seed_case(cur, mailbox_id, body, offset):
    case_id = str(uuid.uuid4())
    reference = f"AUTO-{uuid.uuid4().hex[:8].upper()}"

    cur.execute(
        "INSERT INTO app.cases (id, reference) VALUES (%s, %s)",
        (case_id, reference),
    )
    cur.execute(
        """
        INSERT INTO app.inbound_messages
            (id, mailbox_id, provider_message_id, sender_address,
             recipient_addresses, subject, body_text, received_at,
             case_id, correlation_status, correlation_method)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                'MATCHED', 'NEW_CASE')
        """,
        (
            str(uuid.uuid4()),
            mailbox_id,
            f"auto-{uuid.uuid4().hex[:12]}",
            "client@example.test",
            Jsonb([f"{SLOT}@example.test"]),
            "Enquiry",
            body,
            datetime.now(timezone.utc) - timedelta(minutes=offset),
            case_id,
        ),
    )

    return case_id


def setup():
    with admin() as conn:
        testguard.assert_disposable(conn)

        with conn.cursor() as cur:
            mailbox_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO app.mailboxes (id, provider, address) "
                "VALUES (%s, 'gmail', %s)",
                (mailbox_id, f"{SLOT}@example.test"),
            )

            for service_id, key, topic, words in (
                (SERVICE_A, "autosmoke_a", TOPIC_A, WORDS_A),
                (SERVICE_B, "autosmoke_b", TOPIC_B, WORDS_B),
            ):
                cur.execute(
                    "INSERT INTO app.services (id, service_key, name) "
                    "VALUES (%s, %s, %s)",
                    (service_id, key, f"Autonomy smoke {key}"),
                )
                cur.execute(
                    "INSERT INTO app.service_topics (service_id, topic) "
                    "VALUES (%s, %s)",
                    (service_id, topic),
                )

                for word in words:
                    cur.execute(
                        "INSERT INTO app.topic_keywords "
                        "(service_id, topic, keyword) VALUES (%s, %s, %s)",
                        (service_id, topic, word),
                    )

            STATE["mailbox"] = mailbox_id
            STATE["residency"] = _seed_case(cur, mailbox_id, DOMINANT, 30)
            STATE["straddle"] = _seed_case(cur, mailbox_id, STRADDLE, 20)
            STATE["junk"] = _seed_case(cur, mailbox_id, JUNK, 10)


def auto01_dominant_signal_routes():
    """An enquiry that is obviously about one service is routed."""
    with runtime() as conn:
        outcome, detail = router.triage(conn, STATE["residency"])

    if outcome != router.ROUTED:
        raise RuntimeError(f"AUTO01 FAIL: {outcome} {detail}")

    print("AUTO01 DOMINANT SIGNAL ROUTES: PASS")


def auto02_routing_is_attributed():
    """A routed case records that a machine did it, and when."""
    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT service_id::text = %s, triage_method, "
                "triaged_at IS NOT NULL FROM app.cases WHERE id = %s",
                (SERVICE_A, STATE["residency"]),
            )
            scoped, method, stamped = cur.fetchone()

    if not (scoped and stamped and method == router.KEYWORD_ROUTER):
        raise RuntimeError(
            f"AUTO02 FAIL: scoped={scoped} method={method} stamped={stamped}"
        )

    print("AUTO02 MACHINE ROUTING IS ATTRIBUTED: PASS")


def auto03_genuine_straddle_is_refused():
    """Two services with comparable evidence must reach a human."""
    with runtime() as conn:
        outcome, detail = router.triage(conn, STATE["straddle"])

    if outcome != router.AMBIGUOUS:
        raise RuntimeError(f"AUTO03 FAIL: {outcome} {detail}")

    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT service_id FROM app.cases WHERE id = %s",
                (STATE["straddle"],),
            )

            if cur.fetchone()[0] is not None:
                raise RuntimeError("AUTO03 FAIL: ambiguous case was scoped")

    print("AUTO03 GENUINE STRADDLE IS REFUSED: PASS")


def auto04_unrelated_mail_is_refused():
    """Mail with nothing to do with the service is never routed."""
    with runtime() as conn:
        outcome, detail = router.triage(conn, STATE["junk"])

    if outcome != router.NO_MATCH:
        raise RuntimeError(f"AUTO04 FAIL: {outcome} {detail}")

    print("AUTO04 UNRELATED MAIL IS REFUSED: PASS")


def auto05_every_attempt_is_recorded():
    """A refusal is a fact a reviewer needs, so it is written down."""
    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome FROM app.triage_attempts "
                "WHERE case_id = ANY(%s)",
                ([STATE["residency"], STATE["straddle"], STATE["junk"]],),
            )
            outcomes = sorted(row[0] for row in cur.fetchall())

    expected = sorted([router.AMBIGUOUS, router.NO_MATCH, router.ROUTED])

    if outcomes != expected:
        raise RuntimeError(f"AUTO05 FAIL: recorded {outcomes}")

    print("AUTO05 EVERY TRIAGE ATTEMPT IS RECORDED: PASS")


def auto06_scope_cannot_be_acquired_anonymously():
    """The database refuses a service scope with no triage record."""
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM app.services LIMIT 1")
            service_id = cur.fetchone()[0]

            try:
                cur.execute(
                    "UPDATE app.cases SET service_id = %s, "
                    "triage_method = NULL, triaged_at = NULL WHERE id = %s",
                    (service_id, STATE["junk"]),
                )
            except psycopg.errors.CheckViolation:
                print(
                    "AUTO06 SCOPE CANNOT BE ACQUIRED ANONYMOUSLY: PASS"
                )
                return

    raise RuntimeError("AUTO06 FAIL: anonymous scope assignment accepted")


def _imported_modules(path):
    """Every module name this file imports, however it imports it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)

            for alias in node.names:
                names.add(f"{base}.{alias.name}" if base else alias.name)

    return names


def auto07_loop_cannot_approve_or_dispatch():
    """Structural: the supervisor has no import path to either power.

    This reads the import graph rather than the source text. An earlier
    version grepped for the word "approval" and failed on the line that
    prints "Approval and dispatch are never automatic", which is a
    sentence worth keeping. Naming a capability is not holding it;
    importing it is.
    """
    imported = _imported_modules(REPO_ROOT / "app" / "autonomy" / "loop.py")

    forbidden = sorted(
        name
        for name in imported
        if "approval" in name.lower()
        or "dispatch" in name.lower()
        or name.endswith("Provider")
    )

    if forbidden:
        raise RuntimeError(f"AUTO07 FAIL: loop imports {forbidden}")

    print("AUTO07 LOOP CANNOT APPROVE OR DISPATCH: PASS")


def auto08_router_uses_no_language_model():
    """Structural: routing is decided without inference of any kind."""
    source = (REPO_ROOT / "app" / "autonomy" / "router.py").read_text(
        encoding="utf-8"
    )

    forbidden = [
        name
        for name in ("bedrock", "strands", "Agent(", "invoke_model", "openai")
        if name in source
    ]

    if forbidden:
        raise RuntimeError(f"AUTO08 FAIL: router references {forbidden}")

    print("AUTO08 ROUTER USES NO LANGUAGE MODEL: PASS")


def auto09_triaged_case_is_not_rerouted():
    """Routing is idempotent, so a repeated cycle cannot re-scope a case."""
    with runtime() as conn:
        before = router.triage(conn, STATE["residency"])
        again = router.triage(conn, STATE["residency"])

    if again[1] != "already triaged":
        raise RuntimeError(f"AUTO09 FAIL: {before} then {again}")

    print("AUTO09 TRIAGED CASE IS NOT REROUTED: PASS")


def _seed_run(cur, case_id):
    """A run for the revision to point at.

    `proposal_revisions.run_id` is NOT NULL: a proposal that came from
    nowhere is the thing most of these constraints exist to refuse, so
    the fixture provides provenance rather than avoiding it.
    """
    run_id = str(uuid.uuid4())

    cur.execute(
        """
        INSERT INTO app.agent_runs (
            id, case_id, operation_id, runner, model_id,
            prompt_version, prompt_digest, tool_schema_version,
            context_builder_version, result_state, ended_at
        ) VALUES (
            %s, %s, %s, 'DETERMINISTIC_STUB', 'auto10-fixture',
            'fixture', 'fixture', 'fixture', 'fixture', 'SUCCEEDED',
            now()
        )
        """,
        (run_id, case_id, f"auto10-{uuid.uuid4().hex[:12]}"),
    )

    return run_id


def _seed_proposal(cur, case_id, state, predicates):
    """A proposal and one revision in the given decision state."""
    proposal_id = str(uuid.uuid4())
    revision_id = str(uuid.uuid4())
    run_id = _seed_run(cur, case_id)

    cur.execute(
        "INSERT INTO app.action_proposals (id, case_id) VALUES (%s, %s)",
        (proposal_id, case_id),
    )
    cur.execute(
        """
        INSERT INTO app.proposal_revisions (
            id, proposal_id, revision, run_id, decision_state, summary,
            payload, missing_predicates
        ) VALUES (%s, %s, 1, %s, %s, %s, %s, %s)
        """,
        (
            revision_id,
            proposal_id,
            run_id,
            state,
            f"AUTO10 fixture revision in {state}",
            Jsonb({}),
            Jsonb(predicates),
        ),
    )

    return revision_id


def auto10_loop_drafts_only_client_facing_decisions():
    """A client-facing decision is drafted; an internal one is not.

    Both halves matter. Asserting only the first would pass for a loop
    that drafts everything, and only the second for one that drafts
    nothing.
    """
    from app.autonomy import loop

    with admin() as conn:
        testguard.assert_disposable(conn)

        with conn.cursor() as cur:
            mailbox_id = STATE["mailbox"]

            facts_case = _seed_case(cur, mailbox_id, DOMINANT, 9)
            gap_case = _seed_case(cur, mailbox_id, DOMINANT, 8)

            cur.execute(
                "UPDATE app.cases SET service_id = %s, "
                "triage_method = 'HUMAN', triaged_at = now() "
                "WHERE id = ANY(%s)",
                (SERVICE_A, [facts_case, gap_case]),
            )

            # Required facts need client-facing wording, or drafting
            # refuses by design. That refusal has its own check.
            cur.execute(
                "INSERT INTO app.service_required_facts "
                "(service_id, predicate, prompt_hint) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (
                    SERVICE_A,
                    "autosmoke_fact",
                    "Which year does the enquiry concern?",
                ),
            )

            _seed_proposal(
                cur, facts_case, "MISSING_FACTS", ["autosmoke_fact"]
            )
            _seed_proposal(cur, gap_case, "MISSING_KNOWLEDGE", [])

    class Report:
        def __init__(self):
            self.drafted = 0
            self.errors = []

    report = Report()

    # The drafting stage is global by design: it drafts every
    # client-facing revision without a draft. Calling it here would
    # otherwise write drafts onto other suites' fixtures and leave their
    # cleanup unable to delete its own revisions.
    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text FROM app.draft_messages")
            before = {row[0] for row in cur.fetchall()}

        loop.draft_letters(conn, report)

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id::text
                FROM app.draft_messages d
                JOIN app.proposal_revisions r
                    ON r.id = d.proposal_revision_id
                JOIN app.action_proposals p ON p.id = r.proposal_id
                WHERE p.case_id <> ALL(%s)
                """,
                ([facts_case, gap_case],),
            )
            strangers = [
                row[0] for row in cur.fetchall() if row[0] not in before
            ]

            if strangers:
                cur.execute(
                    "DELETE FROM app.draft_messages WHERE id = ANY(%s)",
                    (strangers,),
                )

    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id::text, count(d.id)
                FROM app.cases c
                JOIN app.action_proposals p ON p.case_id = c.id
                JOIN app.proposal_revisions r ON r.proposal_id = p.id
                LEFT JOIN app.draft_messages d
                    ON d.proposal_revision_id = r.id
                WHERE c.id = ANY(%s)
                GROUP BY c.id
                """,
                ([facts_case, gap_case],),
            )
            counts = dict(cur.fetchall())

    if counts.get(facts_case, 0) != 1:
        raise RuntimeError(
            f"AUTO10 FAIL: a client-facing decision was not drafted "
            f"(drafts={counts.get(facts_case)}, errors={report.errors})"
        )

    if counts.get(gap_case, 0) != 0:
        raise RuntimeError(
            "AUTO10 FAIL: an internal-only decision was drafted to a "
            "client"
        )

    print(
        "AUTO10 LOOP DRAFTS ONLY CLIENT FACING DECISIONS: PASS "
        f"(drafted {report.drafted})"
    )


CHECKS = (
    auto01_dominant_signal_routes,
    auto02_routing_is_attributed,
    auto03_genuine_straddle_is_refused,
    auto04_unrelated_mail_is_refused,
    auto05_every_attempt_is_recorded,
    auto06_scope_cannot_be_acquired_anonymously,
    auto07_loop_cannot_approve_or_dispatch,
    auto08_router_uses_no_language_model,
    auto09_triaged_case_is_not_rerouted,
    auto10_loop_drafts_only_client_facing_decisions,
)


def phase1():
    setup()

    for check in CHECKS:
        check()

    print(f"\nAUTONOMY: {len(CHECKS)} checks PASS")


def cleanup():
    with admin() as conn:
        testguard.assert_disposable(conn)

        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM app.draft_messages
                WHERE proposal_revision_id IN (
                    SELECT r.id
                    FROM app.proposal_revisions r
                    JOIN app.action_proposals p ON p.id = r.proposal_id
                    JOIN app.cases c ON c.id = p.case_id
                    WHERE c.reference LIKE 'AUTO-%'
                )
                """
            )
            cur.execute(
                """
                DELETE FROM app.proposal_revisions
                WHERE proposal_id IN (
                    SELECT p.id
                    FROM app.action_proposals p
                    JOIN app.cases c ON c.id = p.case_id
                    WHERE c.reference LIKE 'AUTO-%'
                )
                """
            )
            cur.execute(
                "DELETE FROM app.action_proposals WHERE case_id IN ("
                "SELECT id FROM app.cases WHERE reference LIKE 'AUTO-%')"
            )
            cur.execute(
                "DELETE FROM app.agent_tool_calls WHERE run_id IN ("
                "SELECT id FROM app.agent_runs WHERE case_id IN ("
                "SELECT id FROM app.cases WHERE reference LIKE 'AUTO-%'))"
            )
            cur.execute(
                "DELETE FROM app.agent_runs WHERE case_id IN ("
                "SELECT id FROM app.cases WHERE reference LIKE 'AUTO-%')"
            )
            cur.execute(
                "DELETE FROM app.service_required_facts "
                "WHERE service_id = ANY(%s)",
                ([SERVICE_A, SERVICE_B],),
            )
            cur.execute(
                "DELETE FROM app.triage_attempts WHERE case_id IN ("
                "SELECT id FROM app.cases WHERE reference LIKE 'AUTO-%')"
            )
            cur.execute(
                "DELETE FROM app.inbound_messages WHERE mailbox_id IN ("
                "SELECT id FROM app.mailboxes WHERE address = %s)",
                (f"{SLOT}@example.test",),
            )
            cur.execute(
                "DELETE FROM app.cases WHERE reference LIKE 'AUTO-%'"
            )
            cur.execute(
                "DELETE FROM app.mailboxes WHERE address = %s",
                (f"{SLOT}@example.test",),
            )
            cur.execute(
                "DELETE FROM app.topic_keywords WHERE service_id = ANY(%s)",
                ([SERVICE_A, SERVICE_B],),
            )
            cur.execute(
                "DELETE FROM app.service_topics WHERE service_id = ANY(%s)",
                ([SERVICE_A, SERVICE_B],),
            )
            cur.execute(
                "DELETE FROM app.services WHERE id = ANY(%s)",
                ([SERVICE_A, SERVICE_B],),
            )

    print("AUTONOMY FIXTURE CLEANUP: PASS")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode not in ("phase1", "cleanup"):
        raise SystemExit("Usage: autonomy_smoke.py phase1|cleanup")

    testguard.acquire_single_run_lock()

    if mode == "phase1":
        phase1()
    else:
        cleanup()
