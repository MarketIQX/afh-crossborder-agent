"""AGID01-05, CTX01-10, REASON01-03. Whose agent, and what it saw.

Two claims are on trial here.

The first is that a personal agent is an identity owned by a human and
holds no authority of its own. The interesting checks are the negative
ones: renaming an agent must change nothing about what it may reach,
and an agent whose owner holds no grant must be refused even though the
agent itself is perfectly valid. An agent that could widen its owner's
reach would be a privilege-escalation path wearing a friendly name.

The second is that a run's decision can be read against the state the
run actually had. `app.case_facts` has no status history, so before
this the confirmed facts in a receipt were the case's facts now --
which means confirming a fact this morning silently improved the
apparent basis of last week's answer. CTX02 and CTX03 are the checks
that matter: change a fact after the run and require the manifest and
its digest to be exactly what they were.

CTX09 is the one that keeps the whole thing honest. A run from before
manifests existed must report HISTORICAL_CONTEXT_INCOMPLETE, not a
reconstruction. A guessed manifest is indistinguishable from a captured
one, which makes every captured one worth less.

    python tests/agent_context_smoke.py phase1
    python tests/agent_context_smoke.py cleanup
"""

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import psycopg  # noqa: E402

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402

SERVICE_ID = "10000000-0000-0000-0000-000000000001"

OWNER = "98100000-0000-0000-0000-000000000001"
STRANGER = "98100000-0000-0000-0000-000000000002"
RETIRED = "98100000-0000-0000-0000-000000000003"

NICOLE = "98150000-0000-0000-0000-000000000001"
MAYA = "98150000-0000-0000-0000-000000000002"
ORPHANED = "98150000-0000-0000-0000-000000000003"

CASE_HELD = "98000000-0000-0000-0000-000000000001"
CASE_OTHER = "98000000-0000-0000-0000-000000000002"

RUN_BOUND = "98200000-0000-0000-0000-000000000001"
RUN_LEGACY = "98200000-0000-0000-0000-000000000002"

PROPOSAL_BOUND = "98300000-0000-0000-0000-000000000001"
PROPOSAL_LEGACY = "98300000-0000-0000-0000-000000000002"

REVISION_BOUND = "98400000-0000-0000-0000-000000000001"
REVISION_LEGACY = "98400000-0000-0000-0000-000000000002"

FACT_SEEN = "98600000-0000-0000-0000-000000000001"
FACT_LATER = "98600000-0000-0000-0000-000000000002"
FACT_OTHER_CASE = "98600000-0000-0000-0000-000000000003"

# Written by the bound run itself, so that changing its status
# afterwards has somewhere to show up.
FACT_BY_RUN = "98600000-0000-0000-0000-000000000004"

SECRET_OF_OTHER_CASE = "other-case-private-value-4b7e"

MATERIAL_DATE = date(2026, 4, 1)

FAILURES = []
PASSES = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


def admin():
    return psycopg.connect(
        **config.database_settings().admin_kwargs(), autocommit=True
    )


def runtime():
    return psycopg.connect(
        **config.database_settings().app_kwargs(), autocommit=True
    )


def seed():
    """Three people, three agents, two cases, and one legacy run.

    ORPHANED belongs to RETIRED, who is inactive: a perfectly valid
    agent whose human cannot act, which is the case that proves
    authority is derived rather than held.

    RUN_LEGACY gets no manifest on purpose. It stands for every run
    that happened before this slice existed.
    """
    with admin() as conn:
        with conn.cursor() as cur:
            # `record` refuses a second manifest for a run, correctly:
            # two accounts of what one run saw would be ambiguous. So
            # the fixture clears its own before seeding, which is what
            # makes phase1 repeatable. The module does not learn to
            # overwrite, because overwriting is the thing that must
            # never happen outside a fixture.
            cur.execute(
                "DELETE FROM app.run_context_manifests "
                "WHERE run_id IN (%s, %s)",
                (RUN_BOUND, RUN_LEGACY),
            )

            for reviewer_id, name, slug, active in (
                (OWNER, "Context Fixture Owner", "owner", True),
                (STRANGER, "Context Fixture Stranger", "stranger", True),
                (RETIRED, "Context Fixture Retired", "retired", False),
            ):
                cur.execute(
                    """
                    INSERT INTO app.reviewers (
                        id, email, display_name, is_active,
                        may_verify_knowledge
                    ) VALUES (%s, %s, %s, %s, false)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (reviewer_id, f"{slug}@context.test", name, active),
                )

            for profile_id, owner, display in (
                (NICOLE, OWNER, "Nicole"),
                (MAYA, STRANGER, "Maya"),
                (ORPHANED, RETIRED, "Orphaned"),
            ):
                cur.execute(
                    """
                    INSERT INTO app.agent_profiles (
                        id, owner_reviewer_id, display_name
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (profile_id, owner, display),
                )

            for case_id, reference in (
                (CASE_HELD, "AFH-CTX-HELD"),
                (CASE_OTHER, "AFH-CTX-OTHER"),
            ):
                cur.execute(
                    """
                    INSERT INTO app.cases (
                        id, lifecycle_status, service_id, reference,
                        triage_method, triaged_at
                    ) VALUES (%s, 'OPEN', %s, %s, 'HUMAN', now())
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (case_id, SERVICE_ID, reference),
                )

            # Only the owner, and only on one case.
            cur.execute(
                """
                INSERT INTO app.reviewer_case_grants (
                    reviewer_id, case_id, granted_by
                ) VALUES (%s, %s, 'context-fixture')
                ON CONFLICT DO NOTHING
                """,
                (OWNER, CASE_HELD),
            )

            # The retired reviewer holds a grant too, so inactivity is
            # tested on its own rather than confounded with absence.
            cur.execute(
                """
                INSERT INTO app.reviewer_case_grants (
                    reviewer_id, case_id, granted_by
                ) VALUES (%s, %s, 'context-fixture')
                ON CONFLICT DO NOTHING
                """,
                (RETIRED, CASE_HELD),
            )

            # Operation ids written out. Both run uuids share their
            # first eight characters, and deriving ids from that prefix
            # has collided twice already in this repository's fixtures.
            for run_id, profile, initiator, operation in (
                (RUN_BOUND, NICOLE, OWNER, "context-run-bound"),
                (RUN_LEGACY, None, None, "context-run-legacy"),
            ):
                cur.execute(
                    """
                    INSERT INTO app.agent_runs (
                        id, case_id, operation_id, runner, model_id,
                        prompt_version, prompt_digest,
                        tool_schema_version, context_builder_version,
                        result_state, ended_at,
                        agent_profile_id, initiated_by_reviewer_id
                    ) VALUES (
                        %s, %s, %s, 'DETERMINISTIC_STUB',
                        'fixture-model', 'v0', 'fixture-digest',
                        'v0', 'context-builder-v1', 'SUCCEEDED', now(),
                        %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (run_id, CASE_HELD, operation, profile, initiator),
                )

            # The legacy revision carries no payload, so the absence
            # of model rationale has something to be absent from.
            for proposal_id, revision_id, run_id, has_payload in (
                (PROPOSAL_BOUND, REVISION_BOUND, RUN_BOUND, True),
                (PROPOSAL_LEGACY, REVISION_LEGACY, RUN_LEGACY, False),
            ):
                cur.execute(
                    """
                    INSERT INTO app.action_proposals (id, case_id)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (proposal_id, CASE_HELD),
                )
                cur.execute(
                    """
                    INSERT INTO app.proposal_revisions (
                        id, proposal_id, revision, run_id,
                        decision_state, summary, payload,
                        cited_unit_ids, missing_predicates,
                        requires_professional_verification
                    ) VALUES (
                        %s, %s, 1, %s, 'MISSING_FACTS',
                        'context fixture proposal', %s,
                        '[]'::jsonb, '["purchase_date"]'::jsonb, true
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        revision_id,
                        proposal_id,
                        run_id,
                        psycopg.types.json.Jsonb(
                            {
                                # From the model's tool arguments.
                                "model_payload": {"note": "model said"},
                                "requested_information": [
                                    "purchase_date"
                                ],
                                "reason_codes": [
                                    "MATERIAL_FACT_ABSENT"
                                ],
                                "uncertainties": ["residency period"],
                                # Written by the deterministic
                                # evaluation, despite how `rationale`
                                # reads.
                                "rationale": [
                                    "a material predicate is absent"
                                ],
                                "permitted_states": ["MISSING_FACTS"],
                                "rules_version": "decision-rules-v1",
                                # Claims the model must not be able to
                                # make stick.
                                "decision_state": (
                                    "SUPPORTED_WITHIN_POLICY"
                                ),
                                "actionable": True,
                                "established_facts": ["invented"],
                            }
                            if has_payload
                            else {}
                        ),
                    ),
                )

            # One call per run, with different tool names, so a receipt
            # that reached across runs would be caught by name.
            for tool_id, run_id, sequence, tool_name in (
                (
                    "98500000-0000-0000-0000-000000000001",
                    RUN_BOUND,
                    1,
                    "get_case_context",
                ),
                (
                    "98500000-0000-0000-0000-000000000002",
                    RUN_BOUND,
                    2,
                    "record_proposed_facts",
                ),
                (
                    "98500000-0000-0000-0000-000000000003",
                    RUN_LEGACY,
                    1,
                    "get_service_knowledge",
                ),
            ):
                cur.execute(
                    """
                    INSERT INTO app.agent_tool_calls (
                        id, run_id, sequence, tool_name, arguments,
                        result_summary, duration_ms
                    ) VALUES (
                        %s, %s, %s, %s, '{}'::jsonb,
                        '{"units": 0}'::jsonb, 9
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (tool_id, run_id, sequence, tool_name),
                )

            for fact_id, case_id, predicate, value, status, origin in (
                (
                    FACT_SEEN,
                    CASE_HELD,
                    "country_of_residence",
                    "UAE",
                    "CONFIRMED",
                    "REVIEWER",
                ),
                (
                    FACT_BY_RUN,
                    CASE_HELD,
                    "disposal_year",
                    "2026",
                    "PROPOSED",
                    "AGENT_PROPOSED",
                ),
                (
                    FACT_LATER,
                    CASE_HELD,
                    "property_type",
                    "residential",
                    "PROPOSED",
                    "CLIENT_MESSAGE",
                ),
                (
                    FACT_OTHER_CASE,
                    CASE_OTHER,
                    "country_of_residence",
                    SECRET_OF_OTHER_CASE,
                    "CONFIRMED",
                    "REVIEWER",
                ),
            ):
                cur.execute(
                    """
                    INSERT INTO app.case_facts (
                        id, case_id, predicate, value_text, status,
                        origin, evidence_refs, run_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, '[]'::jsonb, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        fact_id,
                        case_id,
                        predicate,
                        value,
                        status,
                        origin,
                        RUN_BOUND if origin == "AGENT_PROPOSED" else None,
                    ),
                )

            cur.execute(
                "UPDATE app.action_proposals SET current_revision = 1 "
                "WHERE id IN (%s, %s)",
                (PROPOSAL_BOUND, PROPOSAL_LEGACY),
            )

    print("AGENT CONTEXT FIXTURE: seeded")


def cleanup():
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.run_context_manifests "
                "WHERE run_id IN (%s, %s)",
                (RUN_BOUND, RUN_LEGACY),
            )
            # Tool calls do not cascade with their run, so they go
            # first or the run delete is refused.
            cur.execute(
                "DELETE FROM app.agent_tool_calls "
                "WHERE run_id IN (%s, %s)",
                (RUN_BOUND, RUN_LEGACY),
            )
            # The two CTXFAIL attempts create their own runs.
            cur.execute(
                "DELETE FROM app.agent_tool_calls WHERE run_id IN ("
                "SELECT id FROM app.agent_runs "
                "WHERE operation_id LIKE 'ctxfail-%')"
            )
            cur.execute(
                "DELETE FROM app.proposal_revisions WHERE run_id IN ("
                "SELECT id FROM app.agent_runs "
                "WHERE operation_id LIKE 'ctxfail-%')"
            )
            cur.execute(
                "DELETE FROM app.agent_runs "
                "WHERE operation_id LIKE 'ctxfail-%'"
            )
            cur.execute(
                "DELETE FROM app.case_facts "
                "WHERE id IN (%s, %s, %s, %s)",
                (
                    FACT_SEEN,
                    FACT_LATER,
                    FACT_OTHER_CASE,
                    FACT_BY_RUN,
                ),
            )
            cur.execute(
                "DELETE FROM app.proposal_revisions WHERE id IN (%s, %s)",
                (REVISION_BOUND, REVISION_LEGACY),
            )
            cur.execute(
                "DELETE FROM app.action_proposals WHERE id IN (%s, %s)",
                (PROPOSAL_BOUND, PROPOSAL_LEGACY),
            )
            cur.execute(
                "DELETE FROM app.agent_runs WHERE id IN (%s, %s)",
                (RUN_BOUND, RUN_LEGACY),
            )
            cur.execute(
                "DELETE FROM app.reviewer_case_grants "
                "WHERE granted_by = 'context-fixture'"
            )
            cur.execute(
                "DELETE FROM app.agent_profiles WHERE id IN (%s, %s, %s)",
                (NICOLE, MAYA, ORPHANED),
            )
            cur.execute(
                "DELETE FROM app.cases WHERE id IN (%s, %s)",
                (CASE_HELD, CASE_OTHER),
            )
            cur.execute(
                "DELETE FROM app.reviewers WHERE id IN (%s, %s, %s)",
                (OWNER, STRANGER, RETIRED),
            )

    print("AGENT CONTEXT FIXTURE CLEANUP: PASS")


# ---------------------------------------------------------------------
# AGID: an agent is an identity, not an authority.
# ---------------------------------------------------------------------


def agid_checks():
    from app.domain import agent_identity

    with runtime() as conn:
        with conn.cursor() as cur:
            nicole = agent_identity.profile(cur, NICOLE)
            principal = agent_identity.authority_of(cur, NICOLE)

            held = agent_identity.may_agent_access_case(
                cur, NICOLE, CASE_HELD
            )
            not_held = agent_identity.may_agent_access_case(
                cur, NICOLE, CASE_OTHER
            )

            # A valid agent whose human cannot act. The grant exists;
            # the person behind it is inactive.
            orphaned = agent_identity.may_agent_access_case(
                cur, ORPHANED, CASE_HELD
            )
            orphan_authority = agent_identity.authority_of(cur, ORPHANED)

            # Another Partner's agent, on a case it has no business on.
            others = agent_identity.may_agent_access_case(
                cur, MAYA, CASE_HELD
            )

            cur.execute(
                """
                SELECT a.agent_profile_id::text,
                       a.initiated_by_reviewer_id::text,
                       p.display_name,
                       p.owner_reviewer_id::text
                FROM app.agent_runs a
                JOIN app.agent_profiles p ON p.id = a.agent_profile_id
                WHERE a.id = %s
                """,
                (RUN_BOUND,),
            )
            bound = cur.fetchone()

    check(
        "AGID01 A RUN IDENTIFIES THE PERSONAL AGENT THAT ACTED",
        bound is not None
        and bound[0] == NICOLE
        and bound[2] == "Nicole",
        f"the run reports {bound!r}",
    )
    check(
        "AGID04 THE AGENT IS BOUND TO THE INTENDED HUMAN PRINCIPAL",
        bound is not None
        and bound[1] == OWNER
        and bound[3] == OWNER
        and principal == OWNER,
        f"initiated_by={bound[1] if bound else None} "
        f"owner={bound[3] if bound else None} authority={principal}",
    )
    check(
        "AGID05 AN AGENT CANNOT WIDEN ITS PRINCIPAL'S AUTHORITY",
        held is True
        and not_held is False
        and others is False
        and orphaned is False
        and orphan_authority is None,
        f"held={held} other_case={not_held} other_agent={others} "
        f"inactive_owner={orphaned} authority={orphan_authority!r}",
    )

    # Renaming. The display name is presentation; if it reached any
    # decision, this would move.
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.agent_profiles SET display_name = %s "
                "WHERE id = %s",
                ("Nicole the Second", NICOLE),
            )

    try:
        with runtime() as conn:
            with conn.cursor() as cur:
                renamed_access = agent_identity.may_agent_access_case(
                    cur, NICOLE, CASE_HELD
                )
                renamed_other = agent_identity.may_agent_access_case(
                    cur, NICOLE, CASE_OTHER
                )
                renamed_authority = agent_identity.authority_of(
                    cur, NICOLE
                )
                renamed = agent_identity.profile(cur, NICOLE)
    finally:
        with admin() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.agent_profiles SET display_name = %s "
                    "WHERE id = %s",
                    ("Nicole", NICOLE),
                )

    check(
        "AGID02 THE DISPLAY NAME DOES NOT DEFINE AUTHORITY",
        renamed is not None
        and renamed["display_name"] == "Nicole the Second"
        and renamed["owner_reviewer_id"] == OWNER,
        f"after renaming, the profile reads {renamed!r}",
    )
    check(
        "AGID03 RENAMING AN AGENT CHANGES NO PERMISSION",
        renamed_access is True
        and renamed_other is False
        and renamed_authority == OWNER,
        f"after renaming: held={renamed_access} "
        f"other={renamed_other} authority={renamed_authority}",
    )


# ---------------------------------------------------------------------
# CTX: what the run saw, kept as the run saw it.
# ---------------------------------------------------------------------


def _describe_fixture_context():
    """A context object standing in for one the runner assembled.

    The real one comes from `context.assemble`, which needs an enquiry
    and a service corpus. What is under test is the manifest, so this
    supplies the same shape with known values.
    """
    from app.domain.context import CaseContext, Fact

    return CaseContext(
        case_id=CASE_HELD,
        service_id=SERVICE_ID,
        service_key="nri_tax",
        service_name="Cross-border compliance",
        reference="AFH-CTX-HELD",
        enquiry=None,
        material_date=MATERIAL_DATE,
        facts=(
            Fact("country_of_residence", "UAE", "CONFIRMED", "REVIEWER"),
            Fact(
                "property_type",
                "residential",
                "PROPOSED",
                "CLIENT_MESSAGE",
            ),
        ),
        confirmed_predicates=frozenset({"country_of_residence"}),
        material_predicates=("country_of_residence", "purchase_date"),
        missing_material_predicates=("purchase_date",),
        fact_prompts={},
        topic_matches=(),
        knowledge_release_id=None,
    )


def agid_failclosed_checks():
    """No eligible or an ambiguous set of agents means no attribution."""
    from app.domain import acting_agent, agent_identity

    second_profile_id = "98150000-0000-0000-0000-000000000004"
    no_agent_owner = "98100000-0000-0000-0000-000000000004"

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.reviewers (
                    id, email, display_name, is_active,
                    may_verify_knowledge
                ) VALUES (%s, %s, %s, true, false)
                ON CONFLICT (id) DO NOTHING
                """,
                (no_agent_owner, "no-agent@context.test", "No Agent Owner"),
            )
            cur.execute(
                """
                INSERT INTO app.agent_profiles (
                    id, owner_reviewer_id, display_name
                ) VALUES (%s, %s, 'Second Nicole')
                ON CONFLICT (id) DO NOTHING
                """,
                (second_profile_id, OWNER),
            )

    try:
        with runtime() as conn:
            with conn.cursor() as cur:
                ambiguous = agent_identity.profile_for_owner(cur, OWNER)
                none_eligible = agent_identity.profile_for_owner(
                    cur, no_agent_owner
                )

            import unittest.mock as mock

            with mock.patch(
                "app.config.get",
                side_effect=lambda name, default="": (
                    OWNER
                    if name == acting_agent.ACTING_REVIEWER_SETTING
                    else default
                ),
            ):
                with conn.cursor() as cur:
                    ambiguous_resolution = acting_agent.profile_id(cur)

            with mock.patch(
                "app.config.get",
                side_effect=lambda name, default="": (
                    no_agent_owner
                    if name == acting_agent.ACTING_REVIEWER_SETTING
                    else default
                ),
            ):
                with conn.cursor() as cur:
                    none_resolution = acting_agent.profile_id(cur)
    finally:
        with admin() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM app.agent_profiles WHERE id = %s",
                    (second_profile_id,),
                )
                cur.execute(
                    "DELETE FROM app.reviewers WHERE id = %s",
                    (no_agent_owner,),
                )

    check(
        "AGID06 NO ELIGIBLE PROFILE MEANS NO ATTRIBUTED RUN",
        none_eligible is None and none_resolution is None,
        f"an owner with no agent profile resolved to "
        f"{none_eligible!r} / {none_resolution!r}",
    )
    check(
        "AGID07 TWO ELIGIBLE PROFILES ARE NOT SILENTLY RESOLVED",
        ambiguous is None and ambiguous_resolution is None,
        f"an owner with two active profiles resolved to "
        f"{ambiguous!r} / {ambiguous_resolution!r} instead of refusing",
    )


def agid08_09_explicit_profile_resolves_or_refuses():
    """A configured profile id must be validated, not merely trusted."""
    from app.domain import acting_agent

    import unittest.mock as mock

    with runtime() as conn:
        with mock.patch(
            "app.config.get",
            side_effect=lambda name, default="": {
                acting_agent.ACTING_REVIEWER_SETTING: OWNER,
                acting_agent.ACTING_AGENT_SETTING: MAYA,
            }.get(name, default),
        ):
            with conn.cursor() as cur:
                wrong_owner = acting_agent.profile_id(cur)

        with mock.patch(
            "app.config.get",
            side_effect=lambda name, default="": {
                acting_agent.ACTING_REVIEWER_SETTING: OWNER,
                acting_agent.ACTING_AGENT_SETTING: NICOLE,
            }.get(name, default),
        ):
            with conn.cursor() as cur:
                right_owner = acting_agent.profile_id(cur)

    check(
        "AGID08 AN EXPLICIT PROFILE OWNED BY SOMEONE ELSE IS REFUSED",
        wrong_owner is None,
        f"configuring MAYA (owned by STRANGER) while the principal is "
        f"OWNER resolved to {wrong_owner!r}",
    )
    check(
        "AGID09 AN EXPLICIT PROFILE OWNED BY THE PRINCIPAL RESOLVES",
        right_owner == NICOLE,
        f"configuring NICOLE, owned by OWNER, resolved to "
        f"{right_owner!r}",
    )


def ctx_capture_checks():
    from app.domain import context_manifest

    body = context_manifest.describe(
        _describe_fixture_context(), NICOLE, OWNER
    )

    with runtime() as conn:
        with conn.cursor() as cur:
            context_manifest.record(
                cur, RUN_BOUND, body, policy_envelope={"may_dispatch": False}
            )
            stored = context_manifest.for_run(cur, RUN_BOUND)
            absent = context_manifest.for_run(cur, RUN_LEGACY)

    check(
        "CTX05 THE SAME CANONICAL CONTEXT GIVES THE SAME DIGEST",
        context_manifest.describe(
            _describe_fixture_context(), NICOLE, OWNER
        )["context_digest"]
        == body["context_digest"],
        "describing the same context twice gave two digests",
    )
    check(
        "CTX07 THE MANIFEST CARRIES THE MATERIAL DATE",
        stored is not None
        and stored["material_date"] == MATERIAL_DATE.isoformat(),
        f"material_date reads {stored['material_date'] if stored else None!r}",
    )
    check(
        "CTX08 THE MANIFEST CARRIES THE KNOWLEDGE RELEASE FIELD",
        stored is not None and "knowledge_release_id" in stored,
        "the manifest does not record which release was in force",
    )
    check(
        "CTX09 A RUN WITHOUT A MANIFEST IS UNKNOWN, NOT RECONSTRUCTED",
        absent is None,
        f"a run predating manifests produced {absent!r}",
    )


def ctx_immutability_checks():
    """Confirming a fact later must not change what the run saw."""
    from app.domain import context_manifest

    with runtime() as conn:
        with conn.cursor() as cur:
            before = context_manifest.for_run(cur, RUN_BOUND)

    # A fact is confirmed after the run. Under the old arrangement this
    # would have appeared in the run's basis retrospectively.
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.case_facts SET status = 'CONFIRMED', "
                "origin = 'REVIEWER' WHERE id = %s",
                (FACT_LATER,),
            )

    with runtime() as conn:
        with conn.cursor() as cur:
            after_confirm = context_manifest.for_run(cur, RUN_BOUND)

    # And rejected.
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.case_facts SET status = 'REJECTED', "
                "origin = 'AGENT_PROPOSED' WHERE id = %s",
                (FACT_SEEN,),
            )

    with runtime() as conn:
        with conn.cursor() as cur:
            after_reject = context_manifest.for_run(cur, RUN_BOUND)

        # And the runtime must not be able to amend it at all.
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.run_context_manifests "
                    "SET context_digest = %s WHERE run_id = %s",
                    ("0" * 64, RUN_BOUND),
                )
            amended = "PERMITTED"
        except Exception as exc:  # noqa: BLE001
            amended = exc.__class__.__name__

    check(
        "CTX02 A LATER CONFIRMATION DOES NOT REWRITE OLD CONTEXT",
        after_confirm == before,
        "confirming a fact changed the stored manifest",
    )
    check(
        "CTX03 A LATER REJECTION DOES NOT REWRITE OLD CONTEXT",
        after_reject == before
        and after_reject["context_digest"] == before["context_digest"],
        "rejecting a fact changed the stored manifest",
    )
    check(
        "CTX11 THE RUNTIME CANNOT AMEND A MANIFEST",
        amended != "PERMITTED",
        f"updating a manifest as the runtime role {amended}",
    )


def ctx04_a_changed_context_changes_the_digest():
    """A material change must move the digest, or it proves nothing.

    CTX05 requires the same context to give the same digest. On its own
    that is satisfied by a digest which never changes at all, so this
    is its other half: alter one material fact and require a different
    answer. Then alter something immaterial -- the order facts arrive
    in -- and require the same answer, because a digest that moves when
    nothing meaningful changed would make every comparison noise.
    """
    from app.domain import context_manifest
    from app.domain.context import CaseContext, Fact

    base = _describe_fixture_context()
    baseline = context_manifest.describe(base, NICOLE, OWNER)

    def variant(**changes):
        fields = {
            "case_id": base.case_id,
            "service_id": base.service_id,
            "service_key": base.service_key,
            "service_name": base.service_name,
            "reference": base.reference,
            "enquiry": None,
            "material_date": base.material_date,
            "facts": base.facts,
            "confirmed_predicates": base.confirmed_predicates,
            "material_predicates": base.material_predicates,
            "missing_material_predicates": (
                base.missing_material_predicates
            ),
            "fact_prompts": {},
            "topic_matches": (),
            "knowledge_release_id": base.knowledge_release_id,
        }
        fields.update(changes)

        return context_manifest.describe(
            CaseContext(**fields), NICOLE, OWNER
        )

    # A fact the reasoning turns on now has a different value.
    changed_fact = variant(
        facts=(
            Fact(
                "country_of_residence", "Singapore", "CONFIRMED", "REVIEWER"
            ),
            base.facts[1],
        )
    )

    # An unknown becomes known.
    resolved = variant(missing_material_predicates=())

    # The date the question is asked against.
    later = variant(material_date=base.material_date.replace(month=9))

    # Immaterial: the same facts, handed over in the other order.
    reordered = variant(facts=tuple(reversed(base.facts)))

    moved = {
        "changed fact": changed_fact["context_digest"],
        "unknown resolved": resolved["context_digest"],
        "material date": later["context_digest"],
    }

    unmoved = [
        name
        for name, digest in moved.items()
        if digest == baseline["context_digest"]
    ]

    check(
        "CTX04 A MATERIAL CHANGE PRODUCES A DIFFERENT DIGEST",
        not unmoved and len(set(moved.values())) == 3,
        f"{unmoved or 'nothing'} left the digest unchanged; distinct "
        f"digests: {len(set(moved.values()))} of 3",
    )
    check(
        "CTX13 AN IMMATERIAL REORDERING DOES NOT MOVE THE DIGEST",
        reordered["context_digest"] == baseline["context_digest"],
        "the same facts in a different order gave a different digest, "
        "so comparing two manifests would report noise as change",
    )


def ctx_receipt_checks():
    """The receipt reads the manifest, and says so."""
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            bound = receipt_module.build(cur, REVISION_BOUND)
            legacy = receipt_module.build(cur, REVISION_LEGACY)

    established = [f["predicate"] for f in bound["facts"]["established"]]

    check(
        "CTX01 A RECEIPT USES ITS RUN'S BOUND CONTEXT MANIFEST",
        bound["facts"]["as_of"] == "RUN"
        and bound["context"]["context_digest"]
        and established == ["country_of_residence"],
        f"as_of={bound['facts']['as_of']!r} "
        f"established={established} "
        f"digest={bound['context']['context_digest']!r}",
    )
    check(
        "CTX12 A RUN WITH NO MANIFEST REPORTS CURRENT STATE AS CURRENT",
        legacy["facts"]["as_of"] == "CURRENT"
        and legacy["context"]["manifest_version"]
        == "HISTORICAL_CONTEXT_INCOMPLETE"
        and legacy["reconstruction_limits"],
        f"as_of={legacy['facts']['as_of']!r} "
        f"manifest={legacy['context']['manifest_version']!r}",
    )
    check(
        "CTX10 NO FIELD PURPORTS TO HOLD HIDDEN REASONING",
        not (
            receipt_module.field_names(bound)
            & receipt_module.FORBIDDEN_FIELDS
        ),
        "the receipt carries a field claiming to hold private "
        "deliberation",
    )

    rendered = receipt_module.canonical(bound).decode("utf-8")

    check(
        "CTX06 ANOTHER CASE'S EVIDENCE NEVER ENTERS THIS MANIFEST",
        SECRET_OF_OTHER_CASE not in rendered and CASE_OTHER not in rendered,
        "the other case's value or id appears in this receipt",
    )


# ---------------------------------------------------------------------
# REASON: the model's account of itself is not a finding.
# ---------------------------------------------------------------------


def reason_checks():
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            bound = receipt_module.build(cur, REVISION_BOUND)

            cur.execute(
                """
                SELECT a.result_state, r.decision_state
                FROM app.proposal_revisions r
                JOIN app.agent_runs a ON a.id = r.run_id
                WHERE r.id = %s
                """,
                (REVISION_BOUND,),
            )
            rows = cur.fetchone()

    stated = bound["model_stated_rationale"]

    check(
        "REASON01 THE SYSTEM-VERIFIED BASIS MATCHES THE ROWS",
        bound["run"]["result_state"] == rows[0]
        and bound["decision"]["decision_state"] == rows[1]
        and bound["system_verified_basis"]["asserted_by"] == "APPLICATION",
        f"receipt says {bound['run']['result_state']}/"
        f"{bound['decision']['decision_state']}, rows say {rows}",
    )
    check(
        "REASON02 THE MODEL'S RATIONALE IS LABELLED AS THE MODEL'S",
        stated["asserted_by"] == "MODEL"
        and stated["verified"] is False
        and stated["fields"].get("reason_codes")
        == ["MATERIAL_FACT_ABSENT"],
        f"the rationale reads {stated!r}",
    )
    check(
        "REASON03 A MODEL CLAIM CANNOT OVERWRITE A DETERMINISTIC FACT",
        bound["decision"]["decision_state"] == "MISSING_FACTS"
        and bound["actionable"] is True
        and "decision_state" not in stated["fields"]
        and "established_facts" not in stated["fields"],
        f"the payload claimed SUPPORTED_WITHIN_POLICY and invented "
        f"facts; the receipt reports "
        f"{bound['decision']['decision_state']} and "
        f"{sorted(stated['fields'])}",
    )

    gates = bound["gates"]

    check(
        "REASON04 THE APPLICATION'S OWN REASONING IS NOT THE MODEL'S",
        "rationale" in gates["fields"]
        and "rationale" not in stated["fields"]
        and gates["asserted_by"] == "APPLICATION"
        and gates["verified"] is True,
        f"`rationale` is written by the deterministic evaluation. "
        f"gates hold {sorted(gates['fields'])}; the model-stated half "
        f"holds {sorted(stated['fields'])}",
    )
    check(
        "REASON05 THE MODEL-STATED HALF IS NOT VACUOUS",
        "model_payload" in stated["fields"]
        and "requested_information" in stated["fields"],
        f"the model supplied fields that did not reach the receipt: "
        f"{sorted(stated['fields'])}",
    )


# ---------------------------------------------------------------------
# AUTHZ: effective authority is an intersection, not an inheritance.
# ---------------------------------------------------------------------


def authz_checks():
    """Two terms, and the second must bite on its own.

    The principal's authority bounds Nicole from above. The runtime she
    executes inside bounds her again: it holds no INSERT on approvals,
    so no grant held by any human can make her authorise a letter. If
    only the first term were real, the claim would be inheritance.
    """
    from app.domain import agent_identity

    with runtime() as conn:
        with conn.cursor() as cur:
            principal_can = agent_identity.may_agent_access_case(
                cur, NICOLE, CASE_HELD
            )
            principal_cannot = agent_identity.may_agent_access_case(
                cur, NICOLE, CASE_OTHER
            )

        # An operation the principal may perform and the runtime may
        # not. Attempted as the runtime, which is what Nicole is.
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.approvals (
                        id, draft_message_id, reviewer_id, decision,
                        approved_digest
                    ) VALUES (
                        gen_random_uuid(),
                        '98700000-0000-0000-0000-0000000000ff',
                        %s, 'APPROVED', 'forged'
                    )
                    """,
                    (OWNER,),
                )
            approved = "PERMITTED"
        except Exception as exc:  # noqa: BLE001
            approved = exc.__class__.__name__

    check(
        "AUTHZ01 THE AGENT CANNOT REACH WHAT ITS PRINCIPAL CANNOT",
        principal_can is True and principal_cannot is False,
        f"held={principal_can} not_held={principal_cannot}",
    )
    check(
        "AUTHZ02 A PRINCIPAL'S GRANT DOES NOT WIDEN THE RUNTIME",
        approved != "PERMITTED",
        f"inserting an approval as the runtime {approved}; the "
        f"principal's authority must not reach through the agent into "
        f"an operation the runtime does not hold",
    )


# ---------------------------------------------------------------------
# HID: renaming is presentation. History is not.
# ---------------------------------------------------------------------


def hid_checks():
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            before = receipt_module.build(cur, REVISION_BOUND)

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.agent_profiles SET display_name = %s "
                "WHERE id = %s",
                ("Maya", NICOLE),
            )
            cur.execute(
                "UPDATE app.reviewers SET display_name = %s, email = %s "
                "WHERE id = %s",
                ("Renamed Owner", "renamed@context.test", OWNER),
            )

    try:
        with runtime() as conn:
            with conn.cursor() as cur:
                after = receipt_module.build(cur, REVISION_BOUND)
    finally:
        with admin() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.agent_profiles SET display_name = %s "
                    "WHERE id = %s",
                    ("Nicole", NICOLE),
                )
                cur.execute(
                    "UPDATE app.reviewers SET display_name = %s, "
                    "email = %s WHERE id = %s",
                    ("Context Fixture Owner", "owner@context.test", OWNER),
                )

    check(
        "HID01 RENAMING AN AGENT DOES NOT MOVE A HISTORICAL DIGEST",
        after["receipt_digest"] == before["receipt_digest"],
        f"the digest moved from {before['receipt_digest'][:12]} to "
        f"{after['receipt_digest'][:12]} because a display name "
        f"changed, so history can be retitled",
    )
    check(
        "HID02 THE CANONICAL RECEIPT IDENTIFIES BY ID, NOT BY NAME",
        NICOLE in receipt_module.canonical(before).decode("utf-8")
        and "Nicole"
        not in receipt_module.canonical(before).decode("utf-8"),
        "the canonical bytes carry a display name, which is mutable",
    )
    check(
        "HID03 THE CURRENT DISPLAY NAME IS STILL SHOWN, AS CURRENT",
        after["display"]["agent_profile"] == "Maya"
        and before["display"]["agent_profile"] == "Nicole",
        f"display reads {after.get('display')!r}",
    )
    check(
        "HID04 THE IDENTITY IDS SURVIVE THE RENAME UNCHANGED",
        after["identity"]["agent_profile_id"]
        == before["identity"]["agent_profile_id"]
        == NICOLE
        and after["identity"]["human_principal_id"]
        == before["identity"]["human_principal_id"]
        == OWNER,
        f"ids moved: {before['identity']} then {after['identity']}",
    )


# ---------------------------------------------------------------------
# CORR: one identifier for the whole business journey.
# ---------------------------------------------------------------------


def corr_checks():
    """`case_id` is designated the business correlation id.

    Nothing new is introduced for this. The enquiry, every run, every
    manifest, every proposal and every receipt already resolve to the
    case, and adding a second identifier that means the same thing
    would create two answers to one question.
    """
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            first = receipt_module.build(cur, REVISION_BOUND)
            follow_up = receipt_module.build(cur, REVISION_LEGACY)

            cur.execute(
                """
                SELECT DISTINCT p.case_id::text
                FROM app.proposal_revisions r
                JOIN app.action_proposals p ON p.id = r.proposal_id
                JOIN app.agent_runs a ON a.id = r.run_id
                LEFT JOIN app.run_context_manifests m ON m.run_id = a.id
                WHERE r.id = ANY(%s)
                   OR a.case_id = %s
                   OR m.case_id = %s
                """,
                ([REVISION_BOUND, REVISION_LEGACY], CASE_HELD, CASE_HELD),
            )
            journey = {row[0] for row in cur.fetchall()}

    # Attempted, not counted. Counting rows that disagree returns zero
    # whether the constraint exists or not, because nothing has ever
    # tried to write one. This tries, as the only actor with the
    # privilege to try.
    with admin() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.run_context_manifests (
                        run_id, manifest_version, case_id,
                        material_date, context_builder_version,
                        context_digest
                    ) VALUES (
                        %s, 'divergence-attempt', %s,
                        '2026-04-01', 'v0', %s
                    )
                    """,
                    (RUN_LEGACY, CASE_OTHER, "a" * 64),
                )
            refused = "PERMITTED"
        except Exception as exc:  # noqa: BLE001
            refused = exc.__class__.__name__

    check(
        "CORR01 EVERY ARTIFACT RESOLVES TO ONE CORRELATION ID",
        journey == {CASE_HELD}
        and first["correlation_id"] == CASE_HELD
        and first["correlation_id"] == first["case"]["case_id"],
        f"the journey spans {sorted(journey)}; the receipt reports "
        f"{first.get('correlation_id')!r}",
    )
    check(
        "CORR02 A LATER RUN KEEPS THE SAME CORRELATION ID",
        follow_up["correlation_id"] == first["correlation_id"],
        f"the second run reports "
        f"{follow_up.get('correlation_id')!r}, the first "
        f"{first.get('correlation_id')!r}",
    )
    check(
        "CORR03 A MANIFEST CANNOT NAME A CASE ITS RUN DOES NOT",
        refused == "ForeignKeyViolation",
        f"writing a manifest whose case disagrees with its run gave "
        f"{refused}; two answers to one journey must be impossible, "
        f"not merely unattempted",
    )


# ---------------------------------------------------------------------
# REASON06-10: every field says who asserted it.
# ---------------------------------------------------------------------


def origin_checks():
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            bound = receipt_module.build(cur, REVISION_BOUND)
            legacy = receipt_module.build(cur, REVISION_LEGACY)

    reasoning = bound["reasoning"]
    classes = {
        name: entry["origin"] for name, entry in reasoning.items()
    }

    unclassified = [
        name
        for name, origin in classes.items()
        if origin not in receipt_module.ORIGIN_CLASSES
    ]

    system_named = [
        name
        for name, entry in reasoning.items()
        if entry["origin"] == "SYSTEM_VERIFIED"
    ]

    tools = [call["tool"] for call in bound["tools"]]

    check(
        "REASON06 EVERY REASONING FIELD CARRIES AN ORIGIN CLASS",
        reasoning and not unclassified,
        f"{unclassified} carry no recognised origin; classes are "
        f"{receipt_module.ORIGIN_CLASSES}",
    )
    check(
        "REASON07 SYSTEM-DERIVED CONTENT NEVER RENDERS AS MODEL-STATED",
        "deterministic_gates" in system_named
        and classes.get("model_rationale") == "MODEL_STATED",
        f"origins read {classes}",
    )
    check(
        "REASON08 ABSENT MODEL RATIONALE RENDERS UNAVAILABLE",
        legacy["reasoning"]["model_rationale"]["origin"] == "UNAVAILABLE"
        and not legacy["reasoning"]["model_rationale"]["fields"],
        f"a revision whose payload is empty reports "
        f"{legacy['reasoning']['model_rationale']!r}; silence must not "
        f"read as testimony",
    )
    check(
        "REASON09 THE RECEIPT CARRIES THIS RUN'S ORDERED TRAJECTORY",
        [call["sequence"] for call in bound["tools"]]
        == sorted(call["sequence"] for call in bound["tools"]),
        f"the trajectory is out of order: {bound['tools']!r}",
    )
    check(
        "REASON10 NO OTHER RUN'S TOOL OBSERVATION APPEARS",
        tools == ["get_case_context", "record_proposed_facts"],
        f"this run called {tools}; the other run's "
        f"get_service_knowledge must not be among them",
    )


# ---------------------------------------------------------------------
# CTXFAIL: a run that cannot record its context must not reason.
# ---------------------------------------------------------------------


class CountingStubModel:
    """Records whether it was asked to reason, and refuses to.

    If the model is invoked at all after the manifest failed, that is
    the defect: the agent would be reasoning over state nobody can
    reconstruct afterwards.
    """

    runner = "DETERMINISTIC_STUB"
    model_id = "ctxfail-counting-stub"
    prompt_version = "v0"

    def __init__(self):
        self.calls = 0

    def prompt_digest(self):
        return "ctxfail-stub-digest"

    def run(self, agent_tools):
        self.calls += 1

        raise AssertionError(
            "the model was invoked after the context manifest failed"
        )


def ctxfail_checks():
    from app.agent import runner
    from app.domain import context_manifest

    operation = "ctxfail-manifest-unwritable"
    model = CountingStubModel()
    original = context_manifest.record

    def refuse(cur, run_id, body, policy_envelope=None):
        raise RuntimeError("fixture: the manifest could not be written")

    context_manifest.record = refuse

    try:
        with runtime() as conn:
            try:
                runner.execute(
                    model,
                    CASE_HELD,
                    operation_id=operation,
                    conn=conn,
                    agent_profile_id=NICOLE,
                )
                outcome = "RETURNED"
            except Exception as exc:  # noqa: BLE001
                outcome = exc.__class__.__name__
    finally:
        context_manifest.record = original

    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, result_state, failure_reason,
                       ended_at IS NOT NULL
                FROM app.agent_runs
                WHERE operation_id = %s
                """,
                (operation,),
            )
            run_row = cur.fetchone()

            cur.execute(
                """
                SELECT count(*)
                FROM app.proposal_revisions r
                JOIN app.agent_runs a ON a.id = r.run_id
                WHERE a.operation_id = %s
                """,
                (operation,),
            )
            revisions = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM app.run_context_manifests "
                "WHERE run_id = %s",
                (run_row[0],) if run_row else (None,),
            )
            manifests = cur.fetchone()[0]

    check(
        "CTXFAIL01 THE MODEL IS NOT INVOKED",
        model.calls == 0,
        f"the model was called {model.calls} time(s) after the "
        f"manifest failed, so it reasoned over state nobody can "
        f"reconstruct",
    )
    check(
        "CTXFAIL02 THE RUN ENDS FAILED, NOT LEFT RUNNING",
        run_row is not None
        and run_row[1] == "FAILED"
        and run_row[3] is True,
        f"the run reads {run_row!r}; a run left RUNNING with no end "
        f"time is indistinguishable from one still in flight",
    )
    check(
        "CTXFAIL03 NO PROPOSAL SURVIVES THE FAILURE",
        revisions == 0 and manifests == 0,
        f"{revisions} revision(s) and {manifests} manifest(s) exist "
        f"for a run that could not record its context",
    )
    check(
        "CTXFAIL04 THE FAILURE SAYS WHAT WENT WRONG",
        run_row is not None
        and run_row[2]
        and "context" in run_row[2].lower(),
        f"the recorded reason is {run_row[2] if run_row else None!r}",
    )
    check(
        "CTXFAIL05 THE CALLER IS TOLD, NOT LEFT TO ASSUME SUCCESS",
        outcome != "RETURNED",
        f"execute() returned normally ({outcome}) after failing to "
        f"record the context, so a caller would treat it as a run",
    )


def ctxfail06_a_retry_is_a_separate_attempt():
    """The failed attempt stays; the retry is its own run.

    Retrying must not overwrite the record of the attempt that failed,
    and must not be blocked by it either. Two operation ids, two runs,
    one of them failed and still there.
    """
    from app.agent import runner
    from app.domain import context_manifest

    retry_operation = "ctxfail-manifest-retry"
    model = CountingStubModel()

    with runtime() as conn:
        try:
            runner.execute(
                model,
                CASE_HELD,
                operation_id=retry_operation,
                conn=conn,
                agent_profile_id=NICOLE,
            )
        except Exception:  # noqa: BLE001
            # The stub refuses to reason, so this run fails at the
            # model. What matters here is that it got that far: the
            # manifest was written, which the first attempt could not
            # do.
            pass

    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.operation_id, a.result_state,
                       m.run_id IS NOT NULL AS has_manifest
                FROM app.agent_runs a
                LEFT JOIN app.run_context_manifests m
                       ON m.run_id = a.id
                WHERE a.operation_id = ANY(%s)
                ORDER BY a.operation_id
                """,
                (["ctxfail-manifest-retry", "ctxfail-manifest-unwritable"],),
            )
            rows = cur.fetchall()

    by_operation = {row[0]: row for row in rows}

    check(
        "CTXFAIL06 THE RETRY IS A SEPARATE, PROPERLY RECORDED ATTEMPT",
        len(rows) == 2
        and by_operation["ctxfail-manifest-unwritable"][2] is False
        and by_operation["ctxfail-manifest-retry"][2] is True,
        f"the two attempts read {rows!r}; the failed one must remain "
        f"without a manifest and the retry must have its own",
    )


# ---------------------------------------------------------------------
# DRSTABLE: the agent's decision is history; the case moves on.
# ---------------------------------------------------------------------


def drstable_checks():
    """Nothing that happens after the run may move its digest."""
    from app.domain import receipt as receipt_module

    draft_id = "98800000-0000-0000-0000-000000000001"
    approval_id = "98900000-0000-0000-0000-000000000001"

    with runtime() as conn:
        with conn.cursor() as cur:
            before = receipt_module.build(cur, REVISION_BOUND)

    # A letter is written from the decision.
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.draft_messages (
                    id, proposal_revision_id, recipient, subject,
                    body_text, content_digest, authored_by
                ) VALUES (
                    %s, %s, 'client@drstable.test', 'Subject',
                    'Body text', %s, 'AGENT'
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (draft_id, REVISION_BOUND, "d" * 64),
            )

    with runtime() as conn:
        with conn.cursor() as cur:
            after_draft = receipt_module.build(cur, REVISION_BOUND)

    # A human approves it.
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.approvals (
                    id, draft_message_id, reviewer_id, decision,
                    approved_digest
                ) VALUES (%s, %s, %s, 'APPROVED', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (approval_id, draft_id, OWNER, "d" * 64),
            )

    with runtime() as conn:
        with conn.cursor() as cur:
            after_approval = receipt_module.build(cur, REVISION_BOUND)

    # And revokes it.
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.approvals SET revoked_at = now() "
                "WHERE id = %s",
                (approval_id,),
            )

    with runtime() as conn:
        with conn.cursor() as cur:
            after_revoke = receipt_module.build(cur, REVISION_BOUND)

    # A professional confirms the same predicate the run proposed --
    # through a real INSERT, exactly as the reviewer role does. The
    # run's own row is untouched by construction: no role holds UPDATE
    # on app.case_facts, which is what migration 021 is for.
    confirmation_id = "98600000-0000-0000-0000-000000000005"

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.case_facts (
                    id, case_id, predicate, value_text, status, origin
                ) VALUES (%s, %s, 'disposal_year', '2026', 'CONFIRMED',
                          'REVIEWER')
                """,
                (confirmation_id, CASE_HELD),
            )

    with runtime() as conn:
        with conn.cursor() as cur:
            after_confirm = receipt_module.build(cur, REVISION_BOUND)

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.approvals WHERE id = %s", (approval_id,)
            )
            cur.execute(
                "DELETE FROM app.draft_messages WHERE id = %s",
                (draft_id,),
            )
            cur.execute(
                "DELETE FROM app.case_facts WHERE id = %s",
                (confirmation_id,),
            )

    baseline = before["receipt_digest"]

    check(
        "DRSTABLE01 WRITING A LETTER DOES NOT MOVE THE DIGEST",
        after_draft["receipt_digest"] == baseline,
        f"{baseline[:12]} became {after_draft['receipt_digest'][:12]}",
    )
    check(
        "DRSTABLE02 APPROVAL DOES NOT MOVE THE DIGEST",
        after_approval["receipt_digest"] == baseline,
        f"{baseline[:12]} became "
        f"{after_approval['receipt_digest'][:12]}",
    )
    check(
        "DRSTABLE03 REVOCATION DOES NOT MOVE THE DIGEST",
        after_revoke["receipt_digest"] == baseline,
        f"{baseline[:12]} became {after_revoke['receipt_digest'][:12]}",
    )
    check(
        "DRSTABLE04 A LATER CONFIRMATION DOES NOT MOVE THE DIGEST",
        after_confirm["receipt_digest"] == baseline,
        f"{baseline[:12]} became "
        f"{after_confirm['receipt_digest'][:12]}. Confirmation is a "
        f"new row (migration 021); if this moves, the run's own "
        f"written_by_this_run entry is reading something other than "
        f"the row it owns",
    )


def drstable06_written_by_this_run_ignores_status():
    """The invariant itself, independent of how confirmation happens today.

    DRSTABLE04 shows the real confirmation path never touches a run's
    own row. This proves the module does not merely benefit from that
    by accident: a row this run wrote, with any status at all, must
    still appear in written_by_this_run.
    """
    from app.domain import receipt as receipt_module

    hypothetical_id = "98600000-0000-0000-0000-000000000006"

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.case_facts (
                    id, case_id, predicate, value_text, status,
                    origin, evidence_refs, run_id
                ) VALUES (
                    %s, %s, 'hypothetical_confirmed_by_run', 'x',
                    'CONFIRMED', 'AGENT_PROPOSED', '[]'::jsonb, %s
                )
                """,
                (hypothetical_id, CASE_HELD, RUN_BOUND),
            )

    try:
        with runtime() as conn:
            with conn.cursor() as cur:
                built = receipt_module.build(cur, REVISION_BOUND)
    finally:
        with admin() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM app.case_facts WHERE id = %s",
                    (hypothetical_id,),
                )

    written = {
        f["predicate"] for f in built["facts"]["written_by_this_run"]
    }

    check(
        "DRSTABLE06 WHAT A RUN WROTE DOES NOT DEPEND ON ITS STATUS",
        "hypothetical_confirmed_by_run" in written,
        f"a CONFIRMED-status row this run wrote is missing from "
        f"written_by_this_run: {sorted(written)}. If a status filter "
        f"exists here, the invariant depends on today's confirmation "
        f"mechanism rather than being true on its own",
    )


def drstable05_the_receipt_reads_no_lifecycle_table():
    """Structural: the query surface cannot see downstream state.

    The behavioural checks above prove it did not happen. This proves
    it cannot, which is what stops the next person adding a draft
    status to the receipt without noticing what they broke.
    """
    source = (
        REPO_ROOT / "app/domain/receipt.py"
    ).read_text(encoding="utf-8")

    forbidden = [
        table
        for table in (
            "draft_messages",
            "approvals",
            "dispatches",
            "reviewer_case_grants",
        )
        if table in source
    ]

    check(
        "DRSTABLE05 THE RECEIPT QUERIES NO LIFECYCLE TABLE",
        not forbidden,
        f"the receipt reads {forbidden}, which move after the decision "
        f"was taken; a digest over them is not a historical record",
    )


CHECKS = (
    agid_checks,
    agid_failclosed_checks,
    agid08_09_explicit_profile_resolves_or_refuses,
    ctx_capture_checks,
    ctx_immutability_checks,
    ctx04_a_changed_context_changes_the_digest,
    ctx_receipt_checks,
    reason_checks,
    authz_checks,
    hid_checks,
    corr_checks,
    origin_checks,
    ctxfail_checks,
    ctxfail06_a_retry_is_a_separate_attempt,
    drstable_checks,
    drstable05_the_receipt_reads_no_lifecycle_table,
    drstable06_written_by_this_run_ignores_status,
)


def phase1():
    seed()

    for run in CHECKS:
        try:
            run()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(
                f"{run.__name__} RAISED {exc.__class__.__name__}: {exc}"
            )

    for name in sorted(PASSES):
        print(f"PASS  {name}")

    for line in sorted(FAILURES):
        print(f"FAIL  {line}")

    print(
        f"\nAGENT CONTEXT: {len(PASSES)} passed, {len(FAILURES)} failed"
    )

    return 1 if FAILURES else 0


def main(argv):
    if len(argv) != 2 or argv[1] not in ("phase1", "cleanup"):
        raise SystemExit("Usage: agent_context_smoke.py phase1|cleanup")

    with admin() as conn:
        testguard.assert_disposable(conn)

    testguard.acquire_single_run_lock()

    if argv[1] == "cleanup":
        cleanup()
        return 0

    return phase1()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
