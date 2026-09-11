"""End to end checks for the agent slice.

These run the real context assembler, the real tool surface, the real
database and the real validation path. The model is the deterministic
stub, so no network or AWS access is involved and every run is stamped
DETERMINISTIC_STUB in the run record. A stub run is not evidence that
Bedrock works. It is evidence that everything except the model call is
correct and that the model's authority is genuinely bounded.

Usage:

    python tests/agent_slice_smoke.py phase1
    python tests/agent_slice_smoke.py verify_persisted <run_id>
    python tests/agent_slice_smoke.py cleanup
"""

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402

import simulated_approved_fixture as simulated  # noqa: E402
from app.agent import model as model_module  # noqa: E402
from app.agent import runner  # noqa: E402
from app.agent import tools as tools_module  # noqa: E402
from app.domain import knowledge  # noqa: E402

SETTINGS = config.database_settings()

SERVICE_TAX = "10000000-0000-0000-0000-000000000001"
SERVICE_FEMA = "10000000-0000-0000-0000-000000000002"
ACTIVE_RELEASE = "20000000-0000-0000-0000-000000000001"

UNIT_BASIC = "30000000-0000-0000-0000-000000000001"
UNIT_DEEMED = "30000000-0000-0000-0000-000000000002"

MAILBOX_ID = "96000000-0000-0000-0000-000000000001"

CASE_SUPPORTED = "96100000-0000-0000-0000-000000000001"
CASE_MISSING_FACTS = "96100000-0000-0000-0000-000000000002"
CASE_KNOWLEDGE_GAP = "96100000-0000-0000-0000-000000000003"
CASE_MIXED_SCOPE = "96100000-0000-0000-0000-000000000004"
CASE_CONFLICT = "96100000-0000-0000-0000-000000000005"
CASE_NO_SERVICE = "96100000-0000-0000-0000-000000000006"
CASE_UNROUTABLE = "96100000-0000-0000-0000-000000000007"

ALL_CASES = (
    CASE_SUPPORTED,
    CASE_MISSING_FACTS,
    CASE_KNOWLEDGE_GAP,
    CASE_MIXED_SCOPE,
    CASE_CONFLICT,
    CASE_NO_SERVICE,
    CASE_UNROUTABLE,
)

DRAFT_RELEASE = "96200000-0000-0000-0000-000000000001"
DRAFT_UNIT = "96300000-0000-0000-0000-000000000001"

RECEIVED_AT = "2026-06-15 10:00:00+00"

MATERIAL_FACTS = (
    ("assessment_year", "AY 2026-27"),
    ("days_present_in_india_current_year", "41"),
    ("days_present_in_india_preceding_four_years", "180"),
    ("india_sourced_income_present", "yes, rental income"),
    ("country_of_residence", "United Arab Emirates"),
)

ENQUIRIES = {
    CASE_SUPPORTED: (
        "NRI residency and return filing question",
        "Please confirm my residential status and whether I must file a "
        "return in India for the year.",
    ),
    CASE_MISSING_FACTS: (
        "Question about my residency",
        "Hello,\n"
        "I moved abroad and want to understand my residency position.\n"
        "Country of residence: United Arab Emirates\n"
        "Regards",
    ),
    CASE_KNOWLEDGE_GAP: (
        "Sale of property in India",
        "I sold a flat in Pune last year. What are the implications for "
        "me?",
    ),
    CASE_MIXED_SCOPE: (
        "Residency and investment advice",
        "Please confirm my residency status, and also suggest where I "
        "should invest the proceeds.",
    ),
    CASE_CONFLICT: (
        "Residency status query",
        "Please determine my residential status for the year.",
    ),
    CASE_NO_SERVICE: (
        "Untriaged enquiry",
        "I have a question about my residency.",
    ),
    CASE_UNROUTABLE: (
        "Hello",
        "Can you help me with a general question about my situation?",
    ),
}

CONFIRMED_FACT_CASES = (
    CASE_SUPPORTED,
    CASE_KNOWLEDGE_GAP,
    CASE_MIXED_SCOPE,
    CASE_CONFLICT,
)


def admin_conn():
    return psycopg.connect(**SETTINGS.admin_kwargs())


def app_conn():
    return psycopg.connect(**SETTINGS.app_kwargs())


def cleanup():
    """Remove only this suite's fixtures, in foreign key safe order."""
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.knowledge_unit_conflicts "
                "WHERE unit_id = ANY(%s)",
                ([UNIT_BASIC, UNIT_DEEMED],),
            )
            cur.execute(
                "DELETE FROM app.proposal_revisions WHERE proposal_id IN "
                "(SELECT id FROM app.action_proposals "
                " WHERE case_id = ANY(%s))",
                (list(ALL_CASES),),
            )
            cur.execute(
                "DELETE FROM app.action_proposals WHERE case_id = ANY(%s)",
                (list(ALL_CASES),),
            )
            cur.execute(
                "DELETE FROM app.agent_tool_calls WHERE run_id IN "
                "(SELECT id FROM app.agent_runs WHERE case_id = ANY(%s))",
                (list(ALL_CASES),),
            )
            cur.execute(
                "DELETE FROM app.case_facts WHERE case_id = ANY(%s)",
                (list(ALL_CASES),),
            )
            cur.execute(
                "DELETE FROM app.agent_runs WHERE case_id = ANY(%s)",
                (list(ALL_CASES),),
            )
            cur.execute(
                "DELETE FROM app.inbound_messages WHERE case_id = ANY(%s)",
                (list(ALL_CASES),),
            )
            cur.execute(
                "DELETE FROM app.inbound_messages WHERE mailbox_id = %s",
                (MAILBOX_ID,),
            )
            cur.execute(
                "DELETE FROM app.knowledge_units WHERE release_id = %s",
                (DRAFT_RELEASE,),
            )
            cur.execute(
                "DELETE FROM app.knowledge_releases WHERE id = %s",
                (DRAFT_RELEASE,),
            )
            cur.execute(
                "DELETE FROM app.cases WHERE id = ANY(%s)",
                (list(ALL_CASES),),
            )
            cur.execute(
                "DELETE FROM app.mailboxes WHERE id = %s",
                (MAILBOX_ID,),
            )

            simulated.AGENT.drop(cur)
        conn.commit()

    print("AGENT FIXTURE CLEANUP: PASS")


def seed():
    """Create seven cases covering every decision path."""
    cleanup()

    with admin_conn() as conn:
        with conn.cursor() as cur:
            simulated.AGENT.create(cur)

            cur.execute(
                "INSERT INTO app.mailboxes (id, provider, address) "
                "VALUES (%s, 'gmail', 'agent-slice-fixture@example.test')",
                (MAILBOX_ID,),
            )

            for index, case_id in enumerate(ALL_CASES, start=1):
                if case_id == CASE_NO_SERVICE:
                    service = None
                elif case_id in (CASE_SUPPORTED, CASE_MISSING_FACTS):
                    # Both need verified knowledge present: one to reach
                    # support, the other to isolate a missing client fact
                    # from a knowledge gap. The seeded service cannot
                    # provide that, by design.
                    service = simulated.AGENT.service_id
                else:
                    service = SERVICE_TAX

                cur.execute(
                    "INSERT INTO app.cases (id, service_id, reference, "
                    "triage_method, triaged_at) "
                    "VALUES (%s, %s, %s, 'HUMAN', now())",
                    (case_id, service, f"AGENT-FIXTURE-{index:03d}"),
                )

                subject, body = ENQUIRIES[case_id]

                cur.execute(
                    """
                    INSERT INTO app.inbound_messages (
                        id, mailbox_id, provider_message_id,
                        sender_address, recipient_addresses, subject,
                        body_text, received_at, case_id,
                        correlation_status, correlation_method
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'MATCHED', 'NEW_CASE'
                    )
                    """,
                    (
                        f"96400000-0000-0000-0000-{index:012d}",
                        MAILBOX_ID,
                        f"agent-fixture-message-{index:03d}",
                        "client@example.test",
                        '["agent-slice-fixture@example.test"]',
                        subject,
                        body,
                        RECEIVED_AT,
                        case_id,
                    ),
                )

            for case_id in CONFIRMED_FACT_CASES:
                for offset, (predicate, value) in enumerate(MATERIAL_FACTS):
                    cur.execute(
                        """
                        INSERT INTO app.case_facts (
                            id, case_id, predicate, value_text, status,
                            origin
                        ) VALUES (%s, %s, %s, %s, 'CONFIRMED', 'REVIEWER')
                        """,
                        (
                            f"96500000-0000-0000-{case_id[-4:]}-"
                            f"{offset:012d}",
                            case_id,
                            predicate,
                            value,
                        ),
                    )

            # An unapproved candidate release. Nothing in the retrieval
            # path may ever surface this.
            cur.execute(
                "INSERT INTO app.knowledge_releases "
                "(id, service_id, version, status, notes) "
                "VALUES (%s, %s, 2, 'DRAFT', 'Unapproved candidate')",
                (DRAFT_RELEASE, SERVICE_TAX),
            )
            cur.execute(
                """
                INSERT INTO app.knowledge_units (
                    id, release_id, unit_key, topic, statement,
                    source_locator, verification_status, effective_from
                ) VALUES (
                    %s, %s, 'candidate_rule', 'tax_residency',
                    'UNAPPROVED CANDIDATE that must never be retrieved.',
                    'pending review', 'UNVERIFIED', DATE '2020-04-01'
                )
                """,
                (DRAFT_UNIT, DRAFT_RELEASE),
            )
        conn.commit()

    print("AGENT FIXTURE SEEDED: PASS")


def run_case(case_id, forced_state=None, operation_id=None):
    stub = model_module.DeterministicStubModel(forced_state=forced_state)
    return runner.execute(stub, case_id, operation_id=operation_id)


def expect_state(label, result, expected_decision, expected_result_state):
    if result.decision_state != expected_decision:
        raise RuntimeError(
            f"{label} FAIL: decision {result.decision_state!r}, "
            f"expected {expected_decision!r}. "
            f"permitted={result.permitted_states} "
            f"reason={result.failure_reason}"
        )

    if result.result_state != expected_result_state:
        raise RuntimeError(
            f"{label} FAIL: run state {result.result_state!r}, "
            f"expected {expected_result_state!r}"
        )

    print(f"{label}: PASS ({expected_decision})")


import inspect  # noqa: E402
import subprocess  # noqa: E402
from datetime import date  # noqa: E402

from app.domain import context as context_module  # noqa: E402


class CrossServiceModel:
    """A model that tries to read another service's knowledge."""

    runner = "DETERMINISTIC_STUB"
    model_id = "cross-service-probe"
    prompt_version = model_module.PROMPT_VERSION

    def prompt_digest(self):
        return model_module.prompt_digest()

    def run(self, tools):
        tools.get_case_context()
        tools.get_service_knowledge("residency", service_id=SERVICE_FEMA)
        return {}


def fetch_revision(run_id):
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    decision_state,
                    knowledge_release_id::text,
                    requires_professional_verification,
                    cited_unit_ids,
                    missing_predicates,
                    revision
                FROM app.proposal_revisions
                WHERE run_id = %s
                ORDER BY revision DESC
                LIMIT 1
                """,
                (run_id,),
            )
            return cur.fetchone()


def revision_count(case_id):
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM app.proposal_revisions r "
                "JOIN app.action_proposals p ON p.id = r.proposal_id "
                "WHERE p.case_id = %s",
                (case_id,),
            )
            return cur.fetchone()[0]


def tool_names_for_run(run_id):
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tool_name, error FROM app.agent_tool_calls "
                "WHERE run_id = %s ORDER BY sequence",
                (run_id,),
            )
            return cur.fetchall()


def agent01_enquiry_is_read():
    with app_conn() as conn:
        ctx = context_module.assemble(conn, CASE_SUPPORTED)

    if ctx.enquiry is None:
        raise RuntimeError("AGENT01 FAIL: no enquiry loaded")

    if ctx.enquiry.subject != ENQUIRIES[CASE_SUPPORTED][0]:
        raise RuntimeError(
            f"AGENT01 FAIL: subject {ctx.enquiry.subject!r}"
        )

    if ctx.material_date != date(2026, 6, 15):
        raise RuntimeError(
            f"AGENT01 FAIL: material date {ctx.material_date}"
        )

    if "file a return" not in (ctx.enquiry.body_text or ""):
        raise RuntimeError("AGENT01 FAIL: body not loaded")

    print("AGENT01 PERSISTED ENQUIRY IS READ CORRECTLY: PASS")


def agent02_scope_cannot_be_redirected():
    signature = inspect.signature(tools_module.AgentTools.get_case_context)

    if list(signature.parameters) != ["self"]:
        raise RuntimeError(
            "AGENT02 FAIL: get_case_context accepts arguments, so a model "
            "could try to name a case"
        )

    result = runner.execute(
        CrossServiceModel(),
        CASE_SUPPORTED,
        operation_id="agent-cross-service-probe",
    )

    if result.result_state != "REFUSED":
        raise RuntimeError(
            f"AGENT02 FAIL: run state {result.result_state!r}"
        )

    if "does not match the scope" not in (result.failure_reason or ""):
        raise RuntimeError(
            f"AGENT02 FAIL: reason {result.failure_reason!r}"
        )

    recorded = tool_names_for_run(result.run_id)
    refusals = [name for name, error in recorded if error]

    if "get_service_knowledge" not in refusals:
        raise RuntimeError(
            "AGENT02 FAIL: the refused call was not recorded in the trace"
        )

    print("AGENT02 SCOPE CANNOT BE REDIRECTED BY MODEL ARGUMENTS: PASS")


def agent03_active_release_used(result):
    if result.knowledge_release_id != simulated.AGENT.release_id:
        raise RuntimeError(
            f"AGENT03 FAIL: release {result.knowledge_release_id!r}"
        )

    print("AGENT03 APPROVED ACTIVE KNOWLEDGE RELEASE IS USED: PASS")


def agent04_draft_release_is_invisible(result):
    with app_conn() as conn:
        with conn.cursor() as cur:
            units = knowledge.retrieve(
                cur, DRAFT_RELEASE, ("tax_residency",), date(2026, 6, 15)
            )

    if units:
        raise RuntimeError(
            f"AGENT04 FAIL: draft release returned {len(units)} unit(s)"
        )

    revision = fetch_revision(result.run_id)
    cited = revision[3]

    if DRAFT_UNIT in cited:
        raise RuntimeError("AGENT04 FAIL: candidate unit was cited")

    print("AGENT04 UNAPPROVED CANDIDATE CANNOT BE RETRIEVED: PASS")


def agent05_missing_fact_is_not_a_knowledge_gap(result):
    expect_state(
        "AGENT05 MISSING CLIENT FACT IS NOT A KNOWLEDGE GAP",
        result,
        "MISSING_FACTS",
        "SUCCEEDED",
    )

    revision = fetch_revision(result.run_id)

    if not revision[4]:
        raise RuntimeError(
            "AGENT05 FAIL: no missing predicates were named"
        )


def agent06_system_failure_is_not_a_knowledge_gap():
    original = knowledge.retrieve

    def exploding_retrieve(*args, **kwargs):
        raise knowledge.KnowledgeSystemFailure(
            "simulated retrieval outage"
        )

    knowledge.retrieve = exploding_retrieve

    try:
        result = run_case(
            CASE_SUPPORTED, operation_id="agent-simulated-outage"
        )
    finally:
        knowledge.retrieve = original

    expect_state(
        "AGENT06 SYSTEM FAILURE IS NOT A KNOWLEDGE GAP",
        result,
        "SYSTEM_FAILURE",
        "FAILED",
    )

    if "simulated retrieval outage" not in (result.failure_reason or ""):
        raise RuntimeError(
            f"AGENT06 FAIL: reason {result.failure_reason!r}"
        )


def agent07_uncovered_topic_is_a_knowledge_gap(result):
    expect_state(
        "AGENT07 IN SCOPE TOPIC WITH NO GUIDANCE IS A KNOWLEDGE GAP",
        result,
        "MISSING_KNOWLEDGE",
        "SUCCEEDED",
    )

    revision = fetch_revision(result.run_id)

    if revision[2] is not True:
        raise RuntimeError(
            "AGENT07 FAIL: a gap proposal must be flagged as "
            "requiring professional verification"
        )


def agent08_mixed_request_is_not_blanket_supported(result):
    expect_state(
        "AGENT08 MIXED REQUEST DOES NOT GET BLANKET SUPPORT",
        result,
        "OUT_OF_SCOPE",
        "SUCCEEDED",
    )

    if "SUPPORTED_WITHIN_POLICY" in result.permitted_states:
        raise RuntimeError(
            "AGENT08 FAIL: support was permitted despite a declined topic"
        )


def agent09_declared_conflict_blocks_support():
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.knowledge_unit_conflicts "
                "(unit_id, conflicting_unit_id, note) "
                "VALUES (%s, %s, 'Fixture conflict for testing')",
                (UNIT_BASIC, UNIT_DEEMED),
            )
        conn.commit()

    try:
        result = run_case(
            CASE_CONFLICT, operation_id="agent-conflict-case"
        )

        expect_state(
            "AGENT09 DECLARED SOURCE CONFLICT BLOCKS SUPPORT",
            result,
            "SOURCE_CONFLICT",
            "SUCCEEDED",
        )

        revision = fetch_revision(result.run_id)

        if len(revision[3]) < 2:
            raise RuntimeError(
                "AGENT09 FAIL: conflict cited fewer than two sources"
            )
    finally:
        with admin_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM app.knowledge_unit_conflicts "
                    "WHERE unit_id = %s AND conflicting_unit_id = %s",
                    (UNIT_BASIC, UNIT_DEEMED),
                )
            conn.commit()


def agent10_supported_is_cited_and_flagged(result):
    expect_state(
        "AGENT10 SUPPORTED DECISION IS CITED AND FLAGGED",
        result,
        "SUPPORTED_WITHIN_POLICY",
        "SUCCEEDED",
    )

    revision = fetch_revision(result.run_id)
    state, release, requires_verification, cited, _missing, _rev = revision

    if not cited:
        raise RuntimeError("AGENT10 FAIL: supported without citations")

    if release != simulated.AGENT.release_id:
        raise RuntimeError(f"AGENT10 FAIL: release {release!r}")

    if requires_verification is not False:
        raise RuntimeError(
            "AGENT10 FAIL: every cited unit is professionally "
            "verified, so the knowledge-verification flag should "
            "be clear. Reviewer approval of the action is a "
            "separate gate that does not exist yet."
        )


def agent11_dishonest_support_is_refused():
    before = revision_count(CASE_MISSING_FACTS)

    result = run_case(
        CASE_MISSING_FACTS,
        forced_state="SUPPORTED_WITHIN_POLICY",
        operation_id="agent-dishonest-support",
    )

    if result.result_state != "REFUSED":
        raise RuntimeError(
            f"AGENT11 FAIL: run state {result.result_state!r}"
        )

    if result.proposal_id is not None:
        raise RuntimeError(
            "AGENT11 FAIL: a proposal was persisted for a refused claim"
        )

    after = revision_count(CASE_MISSING_FACTS)

    if after != before:
        raise RuntimeError(
            f"AGENT11 FAIL: revisions changed from {before} to {after}"
        )

    if "not supported by the evidence" not in (result.failure_reason or ""):
        raise RuntimeError(
            f"AGENT11 FAIL: reason {result.failure_reason!r}"
        )

    print("AGENT11 UNSUPPORTED CLAIM IS REFUSED AND NOT PERSISTED: PASS")


def agent12_proposal_survives_restart(result):
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "verify_persisted",
            result.run_id,
        ],
        capture_output=True,
        text=True,
    )

    if completed.returncode != 0:
        raise RuntimeError(
            f"AGENT12 FAIL: {completed.stdout}{completed.stderr}"
        )

    print("AGENT12 PROPOSAL SURVIVES A PROCESS RESTART: PASS")


def agent13_untriaged_case_is_refused():
    result = run_case(
        CASE_NO_SERVICE, operation_id="agent-untriaged-case"
    )

    if result.result_state != "REFUSED":
        raise RuntimeError(
            f"AGENT13 FAIL: run state {result.result_state!r}"
        )

    if "no service scope" not in (result.failure_reason or ""):
        raise RuntimeError(
            f"AGENT13 FAIL: reason {result.failure_reason!r}"
        )

    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT result_state FROM app.agent_runs WHERE id = %s",
                (result.run_id,),
            )
            row = cur.fetchone()

    if row != ("REFUSED",):
        raise RuntimeError(f"AGENT13 FAIL: run not recorded {row!r}")

    print("AGENT13 UNTRIAGED CASE IS REFUSED AND RECORDED: PASS")


def agent14_duplicate_operation_is_rejected():
    operation = "agent-idempotency-probe"
    run_case(CASE_SUPPORTED, operation_id=operation)

    try:
        run_case(CASE_SUPPORTED, operation_id=operation)
    except runner.DuplicateOperation:
        print("AGENT14 DUPLICATE OPERATION ID IS REJECTED: PASS")
        return

    raise RuntimeError("AGENT14 FAIL: the duplicate run was accepted")


def agent15_tool_surface_is_bounded(result):
    recorded = tool_names_for_run(result.run_id)
    names = [name for name, _error in recorded]

    unexpected = set(names) - set(tools_module.TOOL_NAMES)

    if unexpected:
        raise RuntimeError(f"AGENT15 FAIL: unexpected tools {unexpected}")

    if names[0] != "get_case_context":
        raise RuntimeError(f"AGENT15 FAIL: first call {names[0]!r}")

    if names[-1] != "propose_next_action":
        raise RuntimeError(f"AGENT15 FAIL: last call {names[-1]!r}")

    print(f"AGENT15 TOOL SURFACE IS BOUNDED: PASS ({len(names)} calls)")


def agent16_agent_fact_is_proposed_and_attributed():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, origin, value_text FROM app.case_facts "
                "WHERE case_id = %s AND predicate = 'country_of_residence' "
                "AND origin = 'AGENT_PROPOSED'",
                (CASE_MISSING_FACTS,),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "AGENT16 FAIL: the agent recorded no fact from the enquiry"
        )

    if row[0] != "PROPOSED" or row[1] != "AGENT_PROPOSED":
        raise RuntimeError(f"AGENT16 FAIL: {row!r}")

    print("AGENT16 AGENT FACT IS PROPOSED AND ATTRIBUTED: PASS")


def verify_persisted(run_id):
    """Run in a fresh process to prove durability, not memory."""
    revision = fetch_revision(run_id)

    if revision is None:
        raise SystemExit("PERSIST FAIL: proposal not found")

    if revision[0] != "SUPPORTED_WITHIN_POLICY":
        raise SystemExit(f"PERSIST FAIL: {revision[0]!r}")

    print(f"PERSIST OK: {run_id} revision {revision[5]}")


def agent18_tool_object_exposes_exactly_four_tools():
    """Assert the tool object's public surface.

    This is deliberately a narrow claim. It shows the object handed to
    the model exposes exactly the four agreed tools and nothing else.
    It does NOT show that a Strands Agent, once built, registers only
    these four. That must be proven separately against the real SDK
    construction when the Bedrock adapter lands.
    """
    public = tuple(
        sorted(
            name
            for name, value in inspect.getmembers(
                tools_module.AgentTools, predicate=inspect.isfunction
            )
            if not name.startswith("_")
        )
    )

    expected = tuple(sorted(tools_module.TOOL_NAMES))

    if public != expected:
        raise RuntimeError(
            f"AGENT18 FAIL: public tool surface {public}, "
            f"expected {expected}"
        )

    print(
        "AGENT18 TOOL OBJECT EXPOSES EXACTLY FOUR TOOLS: PASS "
        "(object surface only, not SDK registration)"
    )


def agent19_strands_registers_exactly_four_tools():
    """Prove the tool surface at SDK registration, not just on our object.

    AGENT18 asserts what our own object exposes. This asserts what the
    Strands agent will actually offer the model, which is the claim that
    matters and the one a database constraint could never support. It
    needs no AWS credentials, so it holds while invocation is blocked.
    """
    from app.agent import bedrock

    agent = bedrock.BedrockStrandsModel().build_agent(None)

    registered = tuple(sorted(agent.tool_names))
    expected = tuple(sorted(tools_module.TOOL_NAMES))

    if registered != expected:
        raise RuntimeError(
            f"AGENT19 FAIL: registered {registered}, expected {expected}"
        )

    print(
        "AGENT19 STRANDS REGISTERS EXACTLY FOUR TOOLS: PASS "
        f"({', '.join(registered)})"
    )


def agent20_fixture_digests_match_their_passages():
    """A stored digest must match its captured passage.

    Positive control: every simulated fixture unit verifies. Negative
    control: tampering with the passage is detected. Without the
    negative case, a digest column proves only that a string was
    written.
    """
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT unit_key, captured_passage, passage_digest "
                "FROM app.knowledge_units WHERE release_id = %s "
                "ORDER BY unit_key",
                (simulated.AGENT.release_id,),
            )
            rows = cur.fetchall()

    if not rows:
        raise RuntimeError("AGENT20 FAIL: no fixture units found")

    for unit_key, passage, stored in rows:
        if simulated.digest_of(passage) != stored:
            raise RuntimeError(
                f"AGENT20 FAIL: {unit_key} digest does not match its "
                f"captured passage"
            )

    _key, passage, stored = rows[0]
    tampered = passage + " and one extra clause nobody approved"

    if simulated.digest_of(tampered) == stored:
        raise RuntimeError(
            "AGENT20 FAIL: a tampered passage produced the stored "
            "digest, so the digest detects nothing"
        )

    print(
        f"AGENT20 FIXTURE DIGESTS MATCH THEIR PASSAGES: PASS "
        f"({len(rows)} verified, tampering detected)"
    )


def agent21_simulated_fixture_cannot_reach_the_application():
    """No shipped code may import the simulated fixture.

    Its units claim PROFESSIONALLY_VERIFIED without any verification
    having happened. If application bootstrap or deployment seeding
    could import it, that claim would escape the test suite.
    """
    root = Path(__file__).resolve().parent.parent
    offenders = []

    for directory in ("app", "scripts"):
        for path in (root / directory).rglob("*.py"):
            if "simulated_approved_fixture" in path.read_text(
                encoding="utf-8", errors="replace"
            ):
                offenders.append(str(path.relative_to(root)))

    if offenders:
        raise RuntimeError(
            f"AGENT21 FAIL: simulated fixture reachable from {offenders}"
        )

    print(
        "AGENT21 SIMULATED FIXTURE CANNOT REACH THE APPLICATION: PASS"
    )


class _ExplodingSession:
    """Any AWS contact from this session is a failure of the gate."""

    def get_credentials(self):
        raise AssertionError(
            "the gate contacted AWS before checking its configuration"
        )

    def client(self, name):
        raise AssertionError(
            f"the gate built a {name} client before checking its "
            f"configuration"
        )


class _FakeSession:
    """A session that reports a chosen identity, with no network."""

    def __init__(self, arn, account):
        self._arn = arn
        self._account = account

    def get_credentials(self):
        import types

        return types.SimpleNamespace(method="fixture-provider")

    def client(self, name):
        if name != "sts":
            raise AssertionError(f"unexpected client {name}")

        arn, account = self._arn, self._account

        class _Sts:
            def get_caller_identity(self):
                return {"Arn": arn, "Account": account}

        return _Sts()


def agent22_identity_gate_cannot_be_bypassed():
    """No identity, root, wrong account or wrong principal may invoke.

    Runs entirely offline. The gate is a precondition of invocation, so
    it must be provable without credentials, and it must refuse before
    it ever reaches AWS when it has nothing to check against.
    """
    import inspect
    import os

    from app.agent import bedrock

    signature = inspect.signature(bedrock.enforce_identity)

    if list(signature.parameters) != ["session"]:
        raise RuntimeError(
            f"AGENT22 FAIL: enforce_identity takes "
            f"{list(signature.parameters)}; an override parameter would "
            f"be a bypass"
        )

    expected_account = "111122223333"
    expected_principal = (
        f"arn:aws:iam::{expected_account}:user/afh-claude-deployer"
    )

    saved = {
        key: os.environ.get(key)
        for key in (
            bedrock.EXPECTED_ACCOUNT_KEY,
            bedrock.EXPECTED_PRINCIPAL_KEY,
        )
    }

    for key in saved:
        os.environ.pop(key, None)

    cases = []

    try:
        # Unconfigured: must refuse without touching AWS at all.
        try:
            bedrock.enforce_identity(_ExplodingSession())
            raise RuntimeError(
                "AGENT22 FAIL: unconfigured gate permitted invocation"
            )
        except bedrock.IdentityRefused as exc:
            cases.append(("unconfigured", str(exc)[:60]))

        os.environ[bedrock.EXPECTED_ACCOUNT_KEY] = expected_account
        os.environ[bedrock.EXPECTED_PRINCIPAL_KEY] = expected_principal

        refusals = (
            (
                "root",
                f"arn:aws:iam::{expected_account}:root",
                expected_account,
            ),
            (
                "wrong account",
                "arn:aws:iam::999988887777:user/afh-claude-deployer",
                "999988887777",
            ),
            (
                "wrong principal",
                f"arn:aws:iam::{expected_account}:user/someone-else",
                expected_account,
            ),
        )

        for label, arn, account in refusals:
            try:
                bedrock.enforce_identity(_FakeSession(arn, account))
                raise RuntimeError(
                    f"AGENT22 FAIL: {label} was permitted to invoke"
                )
            except bedrock.IdentityRefused as exc:
                cases.append((label, str(exc)[:60]))

        # Positive control: the intended principal must be accepted.
        identity = bedrock.enforce_identity(
            _FakeSession(expected_principal, expected_account)
        )

        if identity["arn"] != expected_principal:
            raise RuntimeError("AGENT22 FAIL: positive control rejected")

        cases.append(("expected principal", "accepted"))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print(
        f"AGENT22 IDENTITY GATE CANNOT BE BYPASSED: PASS "
        f"({len(cases)} cases: "
        f"{', '.join(label for label, _ in cases)})"
    )


def phase1():
    seed()

    supported = run_case(CASE_SUPPORTED, operation_id="agent-supported")
    missing = run_case(CASE_MISSING_FACTS, operation_id="agent-missing")
    gap = run_case(CASE_KNOWLEDGE_GAP, operation_id="agent-gap")
    mixed = run_case(CASE_MIXED_SCOPE, operation_id="agent-mixed")
    unroutable = run_case(CASE_UNROUTABLE, operation_id="agent-unroutable")

    agent01_enquiry_is_read()
    agent02_scope_cannot_be_redirected()
    agent03_active_release_used(supported)
    agent04_draft_release_is_invisible(supported)
    agent05_missing_fact_is_not_a_knowledge_gap(missing)
    agent06_system_failure_is_not_a_knowledge_gap()
    agent07_uncovered_topic_is_a_knowledge_gap(gap)
    agent08_mixed_request_is_not_blanket_supported(mixed)
    agent09_declared_conflict_blocks_support()
    agent10_supported_is_cited_and_flagged(supported)
    agent11_dishonest_support_is_refused()
    agent12_proposal_survives_restart(supported)
    agent13_untriaged_case_is_refused()
    agent14_duplicate_operation_is_rejected()
    agent15_tool_surface_is_bounded(supported)
    agent16_agent_fact_is_proposed_and_attributed()
    agent18_tool_object_exposes_exactly_four_tools()
    agent19_strands_registers_exactly_four_tools()
    agent20_fixture_digests_match_their_passages()
    agent21_simulated_fixture_cannot_reach_the_application()
    agent22_identity_gate_cannot_be_bypassed()

    expect_state(
        "AGENT17 UNROUTABLE ENQUIRY IS NOT ANSWERED",
        unroutable,
        "OUT_OF_SCOPE",
        "SUCCEEDED",
    )

    print("\n--- evidence for the supported run ---")
    for line in supported.summary_lines():
        print(line)

    print("\nAGENT SLICE PHASE 1: PASS")


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
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: agent_slice_smoke.py phase1|verify_persisted|cleanup"
        )

    mode = sys.argv[1]

    # Only the modes that write fixtures need the target guard and the
    # single-run lock. Read-only modes stay callable from a child
    # process while the parent run holds the lock.
    if mode in ("phase1", "cleanup"):
        _guard()

    if mode == "phase1":
        phase1()
    elif mode == "verify_persisted":
        verify_persisted(sys.argv[2])
    elif mode == "cleanup":
        cleanup()
    else:
        raise SystemExit(f"Unknown mode: {mode}")
