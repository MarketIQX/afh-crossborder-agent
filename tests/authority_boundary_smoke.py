"""Authority boundary checks for the agent workflow schema.

Every check here answers the same question: can the runtime role that
the model's tools execute as manufacture authority it was never given?

The point is that these are not prompt instructions, tool descriptions
or application if-statements. They are database privileges and
constraints, so they hold even if the model is manipulated, the prompt
is bypassed, or the application code is wrong.

Usage:

    python tests/authority_boundary_smoke.py phase1
    python tests/authority_boundary_smoke.py cleanup
"""

import sys
from pathlib import Path

import psycopg

from psycopg.errors import (
    CheckViolation,
    InsufficientPrivilege,
    UniqueViolation,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402

SETTINGS = config.database_settings()

SERVICE_ID = "10000000-0000-0000-0000-000000000001"
RELEASE_ID = "20000000-0000-0000-0000-000000000001"
UNIT_ID = "30000000-0000-0000-0000-000000000001"

CASE_ID = "95000000-0000-0000-0000-000000000001"
RUN_ID = "95100000-0000-0000-0000-000000000001"
PROPOSAL_ID = "95200000-0000-0000-0000-000000000001"
REVISION_ID = "95300000-0000-0000-0000-000000000001"

OPERATION_ID = "auth-boundary-operation-001"


def admin_conn():
    return psycopg.connect(**SETTINGS.admin_kwargs())


def app_conn():
    return psycopg.connect(**SETTINGS.app_kwargs())


def reviewer_conn():
    return psycopg.connect(**SETTINGS.reviewer_kwargs())


def cleanup():
    """Remove only this suite's fixtures, in foreign key safe order."""
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.proposal_revisions WHERE proposal_id = %s",
                (PROPOSAL_ID,),
            )
            cur.execute(
                "DELETE FROM app.action_proposals WHERE case_id = %s",
                (CASE_ID,),
            )
            cur.execute(
                "DELETE FROM app.agent_tool_calls WHERE run_id IN "
                "(SELECT id FROM app.agent_runs WHERE case_id = %s)",
                (CASE_ID,),
            )
            cur.execute(
                "DELETE FROM app.case_facts WHERE case_id = %s",
                (CASE_ID,),
            )
            cur.execute(
                "DELETE FROM app.agent_runs WHERE case_id = %s",
                (CASE_ID,),
            )
            cur.execute(
                "DELETE FROM app.knowledge_releases "
                "WHERE service_id = %s AND version = 99",
                (SERVICE_ID,),
            )
            cur.execute(
                "DELETE FROM app.cases WHERE id = %s",
                (CASE_ID,),
            )
        conn.commit()

    print("AUTH FIXTURE CLEANUP: PASS")


def seed():
    """Create the scoped case, run and proposal the checks operate on.

    The case and the run are created deliberately: the case by an
    administrator, because assigning a service scope is a human triage
    decision, and the run by the runtime role, because recording its
    own run is legitimately its job.
    """
    cleanup()

    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.cases (id, service_id, reference, "
                "triage_method, triaged_at) "
                "VALUES (%s, %s, %s, 'HUMAN', now())",
                (CASE_ID, SERVICE_ID, "AUTH-FIXTURE-001"),
            )
        conn.commit()

    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.agent_runs (
                    id, case_id, operation_id, runner, model_id,
                    prompt_version, prompt_digest, tool_schema_version,
                    context_builder_version, knowledge_release_id
                ) VALUES (
                    %s, %s, %s, 'DETERMINISTIC_STUB', 'fixture-model',
                    'v0', 'fixture-digest', 'v1', 'v1', %s
                )
                """,
                (RUN_ID, CASE_ID, OPERATION_ID, RELEASE_ID),
            )

            cur.execute(
                "INSERT INTO app.action_proposals (id, case_id) "
                "VALUES (%s, %s)",
                (PROPOSAL_ID, CASE_ID),
            )

            cur.execute(
                """
                INSERT INTO app.proposal_revisions (
                    id, proposal_id, revision, run_id, decision_state,
                    summary, cited_unit_ids, knowledge_release_id
                ) VALUES (
                    %s, %s, 1, %s, 'SUPPORTED_WITHIN_POLICY',
                    'Fixture revision', %s, %s
                )
                """,
                (
                    REVISION_ID,
                    PROPOSAL_ID,
                    RUN_ID,
                    f'["{UNIT_ID}"]',
                    RELEASE_ID,
                ),
            )
        conn.commit()

    print("AUTH FIXTURE SEEDED: PASS")


# The constraint each check must be rejected by. A check with no entry
# here is one where PostgreSQL reports no constraint name, such as a
# privilege failure, and only the SQLSTATE is recorded.
EXPECTED_CONSTRAINTS = {
    "AUTH03": "case_facts_reviewer_requires_confirmed",
    "AUTH07": "agent_tool_calls_name_check",
    "AUTH10": "proposal_revisions_supported_requires_citation",
    "AUTH11": "proposal_revisions_missing_facts_requires_predicates",
    "AUTH12": "proposal_revisions_conflict_requires_two_sources",
    "AUTH13": "knowledge_releases_single_active",
    "AUTH14": "agent_runs_operation_unique",
    "AUTH15": "agent_tool_calls_run_sequence_unique",
    "AUTH16": "agent_runs_completion_consistency",
    "AUTH17": "case_facts_single_confirmed",
    "AUTH21": "knowledge_units_verification_needs_evidence",
}


def expect_rejection(label, expected, statement, params=None, admin=False):
    """Assert a statement is rejected, and rejected for the right reason.

    A wrong-reason rejection is reported as a failure. A check that
    passes because of a typo would be worse than no check at all.
    """
    factory = admin_conn if admin else app_conn

    try:
        with factory() as conn:
            with conn.cursor() as cur:
                cur.execute(statement, params)
            conn.commit()
    except expected as exc:
        sqlstate = getattr(exc, "sqlstate", None)
        diagnostic = getattr(exc, "diag", None)
        actual = getattr(diagnostic, "constraint_name", None)

        wanted = EXPECTED_CONSTRAINTS.get(label.split()[0])

        if wanted is not None and actual != wanted:
            raise RuntimeError(
                f"{label} FAIL: rejected by constraint {actual!r}, but "
                f"the check exists to prove {wanted!r} fires. Same "
                f"exception class, different rule."
            )

        detail = f"{exc.__class__.__name__} sqlstate={sqlstate}"

        if actual:
            detail += f" constraint={actual}"

        print(f"{label}: PASS ({detail})")
        return
    except psycopg.Error as exc:
        raise RuntimeError(
            f"{label} FAIL: rejected for the wrong reason: "
            f"{exc.__class__.__name__}: {exc}"
        )

    raise RuntimeError(f"{label} FAIL: the statement was accepted")


FACT_ID = "95400000-0000-0000-0000-000000000001"
CONFIRMED_FACT_ID = "95400000-0000-0000-0000-000000000002"
SECOND_CONFIRMED_ID = "95400000-0000-0000-0000-000000000003"
RIVAL_RELEASE_ID = "95500000-0000-0000-0000-000000000001"
RIVAL_RUN_ID = "95100000-0000-0000-0000-000000000002"
TOOL_CALL_ID = "95600000-0000-0000-0000-000000000001"
DUPLICATE_TOOL_ID = "95600000-0000-0000-0000-000000000002"


def auth01_cannot_set_fact_status():
    expect_rejection(
        "AUTH01 RUNTIME ROLE CANNOT WRITE FACT STATUS",
        InsufficientPrivilege,
        "INSERT INTO app.case_facts "
        "(id, case_id, predicate, origin, status) "
        "VALUES (%s, %s, 'assessment_year', 'AGENT_PROPOSED', 'CONFIRMED')",
        (FACT_ID, CASE_ID),
    )


def auth02_agent_fact_defaults_to_proposed():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.case_facts "
                "(id, case_id, predicate, value_text, origin, run_id) "
                "VALUES (%s, %s, 'country_of_residence', 'UAE', "
                "'AGENT_PROPOSED', %s)",
                (FACT_ID, CASE_ID, RUN_ID),
            )
            cur.execute(
                "SELECT status, origin FROM app.case_facts WHERE id = %s",
                (FACT_ID,),
            )
            row = cur.fetchone()
        conn.commit()

    if row != ("PROPOSED", "AGENT_PROPOSED"):
        raise RuntimeError(f"AUTH02 FAIL: unexpected state {row!r}")

    print("AUTH02 AGENT FACT DEFAULTS TO PROPOSED: PASS")


def auth03_cannot_attribute_fact_to_reviewer():
    expect_rejection(
        "AUTH03 RUNTIME ROLE CANNOT ATTRIBUTE A FACT TO A REVIEWER",
        CheckViolation,
        "INSERT INTO app.case_facts (id, case_id, predicate, origin) "
        "VALUES (%s, %s, 'assessment_year', 'REVIEWER')",
        (CONFIRMED_FACT_ID, CASE_ID),
    )


def auth04_cannot_confirm_own_fact():
    expect_rejection(
        "AUTH04 RUNTIME ROLE CANNOT CONFIRM ITS OWN PROPOSED FACT",
        InsufficientPrivilege,
        "UPDATE app.case_facts SET status = 'CONFIRMED' WHERE id = %s",
        (FACT_ID,),
    )


def auth05_cannot_insert_knowledge():
    expect_rejection(
        "AUTH05 RUNTIME ROLE CANNOT PUBLISH KNOWLEDGE",
        InsufficientPrivilege,
        "INSERT INTO app.knowledge_units "
        "(id, release_id, unit_key, topic, statement, source_locator, "
        "effective_from) VALUES (%s, %s, 'injected', 'tax_residency', "
        "'Injected statement', 'none', DATE '2020-04-01')",
        ("95700000-0000-0000-0000-000000000001", RELEASE_ID),
    )


def auth06_cannot_activate_release():
    expect_rejection(
        "AUTH06 RUNTIME ROLE CANNOT ACTIVATE A KNOWLEDGE RELEASE",
        InsufficientPrivilege,
        "UPDATE app.knowledge_releases SET status = 'ACTIVE' WHERE id = %s",
        (RELEASE_ID,),
    )


def auth07_tool_surface_is_closed():
    expect_rejection(
        "AUTH07 OUT OF SURFACE TOOL CALL REJECTED",
        CheckViolation,
        "INSERT INTO app.agent_tool_calls "
        "(id, run_id, sequence, tool_name) "
        "VALUES (%s, %s, 1, 'send_email')",
        (TOOL_CALL_ID, RUN_ID),
    )


def auth08_revision_not_updatable():
    expect_rejection(
        "AUTH08 PROPOSAL REVISION IS NOT UPDATABLE",
        InsufficientPrivilege,
        "UPDATE app.proposal_revisions SET summary = 'rewritten' "
        "WHERE id = %s",
        (REVISION_ID,),
    )


def auth09_revision_not_deletable():
    expect_rejection(
        "AUTH09 PROPOSAL REVISION IS NOT DELETABLE",
        InsufficientPrivilege,
        "DELETE FROM app.proposal_revisions WHERE id = %s",
        (REVISION_ID,),
    )


def auth10_supported_requires_citation():
    expect_rejection(
        "AUTH10 SUPPORTED DECISION REQUIRES A CITATION",
        CheckViolation,
        "INSERT INTO app.proposal_revisions "
        "(id, proposal_id, revision, run_id, decision_state, summary) "
        "VALUES (%s, %s, 2, %s, 'SUPPORTED_WITHIN_POLICY', 'No sources')",
        ("95300000-0000-0000-0000-000000000002", PROPOSAL_ID, RUN_ID),
    )


def auth11_missing_facts_requires_predicates():
    expect_rejection(
        "AUTH11 MISSING FACTS DECISION MUST NAME THE FACTS",
        CheckViolation,
        "INSERT INTO app.proposal_revisions "
        "(id, proposal_id, revision, run_id, decision_state, summary) "
        "VALUES (%s, %s, 3, %s, 'MISSING_FACTS', 'Something is missing')",
        ("95300000-0000-0000-0000-000000000003", PROPOSAL_ID, RUN_ID),
    )


def auth12_conflict_requires_two_sources():
    expect_rejection(
        "AUTH12 SOURCE CONFLICT REQUIRES TWO SOURCES",
        CheckViolation,
        "INSERT INTO app.proposal_revisions "
        "(id, proposal_id, revision, run_id, decision_state, summary, "
        "cited_unit_ids) VALUES (%s, %s, 4, %s, 'SOURCE_CONFLICT', "
        "'Only one source', %s)",
        (
            "95300000-0000-0000-0000-000000000004",
            PROPOSAL_ID,
            RUN_ID,
            f'["{UNIT_ID}"]',
        ),
    )


def auth13_single_active_release_per_service():
    expect_rejection(
        "AUTH13 ONLY ONE ACTIVE RELEASE PER SERVICE",
        UniqueViolation,
        "INSERT INTO app.knowledge_releases "
        "(id, service_id, version, status, activated_at) "
        "VALUES (%s, %s, 99, 'ACTIVE', now())",
        (RIVAL_RELEASE_ID, SERVICE_ID),
        admin=True,
    )


def auth14_duplicate_operation_rejected():
    expect_rejection(
        "AUTH14 DUPLICATE OPERATION ID REJECTED",
        UniqueViolation,
        """
        INSERT INTO app.agent_runs (
            id, case_id, operation_id, runner, model_id, prompt_version,
            prompt_digest, tool_schema_version, context_builder_version
        ) VALUES (
            %s, %s, %s, 'DETERMINISTIC_STUB', 'fixture-model', 'v0',
            'fixture-digest', 'v1', 'v1'
        )
        """,
        (RIVAL_RUN_ID, CASE_ID, OPERATION_ID),
    )


def auth15_duplicate_tool_sequence_rejected():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.agent_tool_calls "
                "(id, run_id, sequence, tool_name) "
                "VALUES (%s, %s, 1, 'get_case_context')",
                (TOOL_CALL_ID, RUN_ID),
            )
        conn.commit()

    expect_rejection(
        "AUTH15 DUPLICATE TOOL CALL SEQUENCE REJECTED",
        UniqueViolation,
        "INSERT INTO app.agent_tool_calls "
        "(id, run_id, sequence, tool_name) "
        "VALUES (%s, %s, 1, 'get_service_knowledge')",
        (DUPLICATE_TOOL_ID, RUN_ID),
    )


def auth16_completed_run_needs_end_time():
    expect_rejection(
        "AUTH16 COMPLETED RUN MUST RECORD AN END TIME",
        CheckViolation,
        "UPDATE app.agent_runs SET result_state = 'SUCCEEDED' "
        "WHERE id = %s",
        (RUN_ID,),
    )


def auth17_single_confirmed_value_per_predicate():
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.case_facts "
                "(id, case_id, predicate, value_text, status, origin) "
                "VALUES (%s, %s, 'assessment_year', 'AY 2024-25', "
                "'CONFIRMED', 'REVIEWER')",
                (CONFIRMED_FACT_ID, CASE_ID),
            )
        conn.commit()

    expect_rejection(
        "AUTH17 ONLY ONE CONFIRMED VALUE PER PREDICATE",
        UniqueViolation,
        "INSERT INTO app.case_facts "
        "(id, case_id, predicate, value_text, status, origin) "
        "VALUES (%s, %s, 'assessment_year', 'AY 2025-26', "
        "'CONFIRMED', 'REVIEWER')",
        (SECOND_CONFIRMED_ID, CASE_ID),
        admin=True,
    )


def auth18_cannot_widen_service_scope():
    expect_rejection(
        "AUTH18 RUNTIME ROLE CANNOT CREATE A SERVICE SCOPE",
        InsufficientPrivilege,
        "INSERT INTO app.services (id, service_key, name) "
        "VALUES (%s, 'invented_service', 'Invented service')",
        ("95800000-0000-0000-0000-000000000001", ),
    )


def auth19_valid_revision_keeps_provenance():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    r.decision_state,
                    r.knowledge_release_id::text,
                    r.requires_professional_verification,
                    jsonb_array_length(r.cited_unit_ids),
                    a.model_id,
                    a.prompt_version
                FROM app.proposal_revisions r
                JOIN app.agent_runs a ON a.id = r.run_id
                WHERE r.id = %s
                """,
                (REVISION_ID,),
            )
            row = cur.fetchone()

    expected = (
        "SUPPORTED_WITHIN_POLICY",
        RELEASE_ID,
        True,
        1,
        "fixture-model",
        "v0",
    )

    if row != expected:
        raise RuntimeError(f"AUTH19 FAIL: provenance mismatch {row!r}")

    print("AUTH19 VALID REVISION KEEPS ITS PROVENANCE: PASS")


def auth20_base_knowledge_table_is_unreachable():
    expect_rejection(
        "AUTH20 RUNTIME ROLE CANNOT READ THE KNOWLEDGE BASE TABLE",
        InsufficientPrivilege,
        "SELECT count(*) FROM app.knowledge_units",
    )


def auth21_source_verification_requires_evidence():
    expect_rejection(
        "AUTH21 SOURCE VERIFICATION REQUIRES CAPTURED EVIDENCE",
        CheckViolation,
        "INSERT INTO app.knowledge_units ("
        "id, release_id, unit_key, topic, statement, source_locator, "
        "verification_status, effective_from) VALUES ("
        "%s, %s, 'claim_without_passage', 'tax_residency', "
        "'A claim with no captured source passage.', "
        "'Some Act, section 1', 'SOURCE_VERIFIED', DATE '2020-04-01')",
        ("95900000-0000-0000-0000-000000000001", RELEASE_ID),
        admin=True,
    )


def auth22_reviewer_can_append_a_confirmed_fact():
    """The positive control for migration 021.

    Confirmation is an append, not an edit: the reviewer writes a new
    row and the agent's PROPOSED row is left untouched. Both must be
    present afterwards, because the proposal and the confirmation are
    two acts by two actors and destroying either loses provenance.
    """
    fact_id = "95700000-0000-0000-0000-000000000001"

    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.case_facts WHERE id = %s", (fact_id,)
            )
        conn.commit()

    try:
        with reviewer_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app.case_facts "
                    "(id, case_id, predicate, value_text, status, origin) "
                    "VALUES (%s, %s, 'citizenship_status', 'indian', "
                    "'CONFIRMED', 'REVIEWER')",
                    (fact_id, CASE_ID),
                )
            conn.commit()

        with admin_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, origin FROM app.case_facts "
                    "WHERE id = %s",
                    (fact_id,),
                )
                row = cur.fetchone()

        if row != ("CONFIRMED", "REVIEWER"):
            raise RuntimeError(
                f"AUTH22 FAIL: reviewer fact stored as {row!r}"
            )

        # The reviewer must not be able to rewrite it afterwards.
        rewrote = False

        try:
            with reviewer_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE app.case_facts SET value_text = 'edited' "
                        "WHERE id = %s",
                        (fact_id,),
                    )
                conn.commit()
                rewrote = True
        except InsufficientPrivilege:
            pass

        if rewrote:
            raise RuntimeError(
                "AUTH22 FAIL: reviewer rewrote a confirmed fact; "
                "confirmation must be an append, not an edit"
            )
    finally:
        with admin_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM app.case_facts WHERE id = %s", (fact_id,)
                )
            conn.commit()

    print("AUTH22 REVIEWER CAN APPEND A CONFIRMED FACT: PASS")


def auth23_no_role_can_delete_anything():
    """The claim published in docs/ARCHITECTURE.md, made executable.

    Neither serving role holds DELETE on any table in the schema, and
    neither holds TRUNCATE, which would achieve the same thing. Read
    from the catalogue so a future GRANT cannot pass unnoticed.
    """
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT grantee, table_name, privilege_type
                FROM information_schema.table_privileges
                WHERE table_schema = 'app'
                  AND privilege_type IN ('DELETE', 'TRUNCATE')
                  AND grantee IN ('agents_app', 'agents_reviewer')
                ORDER BY grantee, table_name
                """
            )
            offenders = cur.fetchall()

    if offenders:
        detail = ", ".join(
            f"{g}:{t}:{p}" for g, t, p in offenders[:6]
        )
        raise RuntimeError(
            f"AUTH23 FAIL: {len(offenders)} destructive grant(s) exist "
            f"where the architecture document claims none: {detail}"
        )

    print("AUTH23 NO SERVING ROLE CAN DELETE OR TRUNCATE: PASS")


def auth24_reviewer_narrows_a_rule_and_nothing_else():
    """Applicability is editable by a reviewer. The rule itself is not."""
    with reviewer_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.knowledge_units "
                "SET applies_to_residency = 'NON_RESIDENT' WHERE id = %s",
                (UNIT_ID,),
            )
        conn.commit()

    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT applies_to_residency, statement, "
                "verification_status FROM app.knowledge_units "
                "WHERE id = %s",
                (UNIT_ID,),
            )
            residency, statement_before, status_before = cur.fetchone()

    if residency != "NON_RESIDENT":
        raise RuntimeError(
            f"AUTH24 FAIL: reviewer could not record applicability; "
            f"column reads {residency!r}"
        )

    # 2 and 3: the grant must not reach anything else on the row.
    for column, value in (
        ("statement", "'Rewritten by a reviewer'"),
        ("verification_status", "'PROFESSIONALLY_VERIFIED'"),
    ):
        widened = False

        try:
            with reviewer_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE app.knowledge_units SET {column} = "
                        f"{value} WHERE id = %s",
                        (UNIT_ID,),
                    )
                conn.commit()
                widened = True
        except InsufficientPrivilege:
            pass

        if widened:
            raise RuntimeError(
                f"AUTH24 FAIL: a reviewer rewrote {column}. The "
                f"applicability grant has been widened beyond two "
                f"columns and the signed record is no longer immutable."
            )

    # 4: the value vocabulary is enforced by the database, not by
    #    store.set_applicability, so go around the application.
    refused = False

    try:
        with reviewer_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.knowledge_units "
                    "SET applies_to_residency = 'MARTIAN' WHERE id = %s",
                    (UNIT_ID,),
                )
            conn.commit()
    except CheckViolation:
        refused = True

    if not refused:
        raise RuntimeError(
            "AUTH24 FAIL: the database accepted an invalid residency "
            "class. Validation lives only in Python and a reviewer can "
            "invent a class of person."
        )

    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT statement, verification_status "
                "FROM app.knowledge_units WHERE id = %s",
                (UNIT_ID,),
            )
            statement_after, status_after = cur.fetchone()

            cur.execute(
                "UPDATE app.knowledge_units "
                "SET applies_to_residency = NULL WHERE id = %s",
                (UNIT_ID,),
            )
        conn.commit()

    if (statement_after, status_after) != (
        statement_before, status_before
    ):
        raise RuntimeError(
            "AUTH24 FAIL: the rule changed underneath the applicability "
            "edit"
        )

    print(
        "AUTH24 REVIEWER NARROWS A RULE AND NOTHING ELSE: PASS "
        "(2 columns writable, statement and status refused, "
        "invalid class refused by the database)"
    )


CHECKS = (
    auth01_cannot_set_fact_status,
    auth02_agent_fact_defaults_to_proposed,
    auth03_cannot_attribute_fact_to_reviewer,
    auth04_cannot_confirm_own_fact,
    auth05_cannot_insert_knowledge,
    auth06_cannot_activate_release,
    auth07_tool_surface_is_closed,
    auth08_revision_not_updatable,
    auth09_revision_not_deletable,
    auth10_supported_requires_citation,
    auth11_missing_facts_requires_predicates,
    auth12_conflict_requires_two_sources,
    auth13_single_active_release_per_service,
    auth14_duplicate_operation_rejected,
    auth15_duplicate_tool_sequence_rejected,
    auth16_completed_run_needs_end_time,
    auth17_single_confirmed_value_per_predicate,
    auth18_cannot_widen_service_scope,
    auth19_valid_revision_keeps_provenance,
    auth20_base_knowledge_table_is_unreachable,
    auth21_source_verification_requires_evidence,
    auth22_reviewer_can_append_a_confirmed_fact,
    auth23_no_role_can_delete_anything,
    auth24_reviewer_narrows_a_rule_and_nothing_else,
)


def phase1():
    seed()

    for check in CHECKS:
        check()

    print("AUTHORITY BOUNDARY PHASE 1: PASS")


def persist():
    """Verify the fixtures survived an externally performed restart."""
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT decision_state FROM app.proposal_revisions "
                "WHERE id = %s",
                (REVISION_ID,),
            )
            row = cur.fetchone()

    if row != ("SUPPORTED_WITHIN_POLICY",):
        raise RuntimeError(f"PERSIST FAIL: {row!r}")

    print("AUTH PERSISTENCE ACROSS RESTART: PASS")


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
        raise SystemExit(
            "Usage: authority_boundary_smoke.py phase1|persist|cleanup"
        )

    mode = sys.argv[1]

    # Only the modes that write fixtures need the target guard and the
    # single-run lock. Read-only modes stay callable from a child
    # process while the parent run holds the lock.
    if mode in ("phase1", "cleanup"):
        _guard()

    if mode == "phase1":
        phase1()
    elif mode == "persist":
        persist()
    elif mode == "cleanup":
        cleanup()
    else:
        raise SystemExit(f"Unknown mode: {mode}")
