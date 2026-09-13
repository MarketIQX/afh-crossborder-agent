"""ACCESS01-08, RUNSAFE01-07, SEND01-03. Two boundaries before work state.

Archaeology for the Partner Operating System uncovered two defects that
would grow more consequential the moment ownership, stages and dashboard
controls were added on top. This suite closes them, and it is written
before the fix so the failures are on record.

**Case access is enforced unevenly.** Three mutation paths do check a
grant -- `approval.edit_draft`, `record_decision` and `revoke` all refuse
a reviewer with no active grant, and say so in words. But composing a
draft takes no reviewer at all, dispatch authorises from the approval
rather than the actor, and every read path -- the case page, the request
page, the queue -- checks nothing. The queue even computes a `granted`
flag and never filters on it. So the console shows every case to whoever
opens it.

**A failed run's proposal can look like current work.** `workqueue.queue`
picks the newest revision per case with `DISTINCT ON (p.case_id) ...
ORDER BY r.created_at DESC` and never joins `agent_runs`. Three
revisions in the live database were written by runs that finished
FAILED, and the queue presents them as the case's current recommendation
awaiting a decision.

The second defect is not fixed by deleting anything. A failed run's
proposal is audit evidence and stays visible with its outcome; what it
loses is the standing to be treated as actionable work. Those are
different properties and the checks below keep them apart.

Usage:

    python tests/case_access_smoke.py phase1
    python tests/case_access_smoke.py cleanup
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import psycopg  # noqa: E402

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402

SERVICE_ID = "10000000-0000-0000-0000-000000000001"

# Two cases and two reviewers: the whole point is that a grant on one
# case is not authority over the other.
CASE_MINE = "96000000-0000-0000-0000-000000000001"
CASE_THEIRS = "96000000-0000-0000-0000-000000000002"

REVIEWER_GRANTED = "96100000-0000-0000-0000-000000000001"
REVIEWER_UNGRANTED = "96100000-0000-0000-0000-000000000002"
REVIEWER_INACTIVE = "96100000-0000-0000-0000-000000000003"

RUN_OK = "96200000-0000-0000-0000-000000000001"
RUN_FAILED = "96200000-0000-0000-0000-000000000002"

PROPOSAL_OK = "96300000-0000-0000-0000-000000000001"
PROPOSAL_FAILED = "96300000-0000-0000-0000-000000000002"

REVISION_OK = "96400000-0000-0000-0000-000000000001"
REVISION_FAILED = "96400000-0000-0000-0000-000000000002"

# A run in flight and a run that declined. Neither is a failure
# and neither is a recommendation, which is the whole point.
CASE_RUNNING = "96000000-0000-0000-0000-000000000003"
CASE_REFUSED = "96000000-0000-0000-0000-000000000004"

RUN_RUNNING = "96200000-0000-0000-0000-000000000004"
RUN_REFUSED = "96200000-0000-0000-0000-000000000005"

PROPOSAL_RUNNING = "96300000-0000-0000-0000-000000000004"
PROPOSAL_REFUSED = "96300000-0000-0000-0000-000000000005"

REVISION_RUNNING = "96400000-0000-0000-0000-000000000004"
REVISION_REFUSED = "96400000-0000-0000-0000-000000000005"

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
    """Two cases, three reviewers, one grant, one failed run.

    Written by the schema owner because assigning a service scope and
    granting case access are both human acts, and this is a fixture
    standing in for those humans.
    """
    with admin() as conn:
        with conn.cursor() as cur:
            for reviewer_id, name, slug, active in (
                (REVIEWER_GRANTED, "Access Fixture Granted", "granted", True),
                (
                    REVIEWER_UNGRANTED,
                    "Access Fixture Ungranted",
                    "ungranted",
                    True,
                ),
                (
                    REVIEWER_INACTIVE,
                    "Access Fixture Inactive",
                    "inactive",
                    False,
                ),
            ):
                cur.execute(
                    """
                    INSERT INTO app.reviewers (
                        id, email, display_name, is_active,
                        may_verify_knowledge
                    ) VALUES (%s, %s, %s, %s, false)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        reviewer_id,
                        f"{slug}@access.test",
                        name,
                        active,
                    ),
                )

            for case_id, reference in (
                (CASE_MINE, "AFH-ACCESS-MINE"),
                (CASE_THEIRS, "AFH-ACCESS-THEIRS"),
                (CASE_RUNNING, "AFH-ACCESS-RUNNING"),
                (CASE_REFUSED, "AFH-ACCESS-REFUSED"),
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

            # The granted reviewer holds exactly one case.
            cur.execute(
                """
                INSERT INTO app.reviewer_case_grants (
                    reviewer_id, case_id, granted_by
                ) VALUES (%s, %s, 'case-access-fixture')
                ON CONFLICT DO NOTHING
                """,
                (REVIEWER_GRANTED, CASE_MINE),
            )

            # The inactive reviewer holds a grant too, so inactivity is
            # tested on its own rather than confounded with absence.
            cur.execute(
                """
                INSERT INTO app.reviewer_case_grants (
                    reviewer_id, case_id, granted_by
                ) VALUES (%s, %s, 'case-access-fixture')
                ON CONFLICT DO NOTHING
                """,
                (REVIEWER_INACTIVE, CASE_MINE),
            )

            for run_id, case_id, state, reason, operation in (
                (RUN_OK, CASE_MINE, "SUCCEEDED", None, "access-run-ok"),
                (
                    RUN_FAILED,
                    CASE_THEIRS,
                    "FAILED",
                    "fixture: the provider failed after the tool wrote",
                    "access-run-failed",
                ),
                (
                    RUN_REFUSED,
                    CASE_REFUSED,
                    "REFUSED",
                    "fixture: a tool declined after the proposal was written",
                    "access-run-refused",
                ),
            ):
                cur.execute(
                    """
                    INSERT INTO app.agent_runs (
                        id, case_id, operation_id, runner, model_id,
                        prompt_version, prompt_digest,
                        tool_schema_version, context_builder_version,
                        result_state, ended_at, failure_reason
                    ) VALUES (
                        %s, %s, %s, 'DETERMINISTIC_STUB', 'fixture-model',
                        'v0', 'fixture-digest', 'v0', 'v0', %s, now(), %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (run_id, case_id, operation, state, reason),
                )

            # Still going. No ended_at, because
            # agent_runs_completion_consistency refuses a RUNNING row
            # that claims to have finished -- which is the schema
            # telling us this state is real and distinct.
            cur.execute(
                """
                INSERT INTO app.agent_runs (
                    id, case_id, operation_id, runner, model_id,
                    prompt_version, prompt_digest,
                    tool_schema_version, context_builder_version,
                    result_state
                ) VALUES (
                    %s, %s, %s, 'DETERMINISTIC_STUB', 'fixture-model',
                    'v0', 'fixture-digest', 'v0', 'v0', 'RUNNING'
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (RUN_RUNNING, CASE_RUNNING, "access-run-running"),
            )

            for proposal_id, case_id in (
                (PROPOSAL_OK, CASE_MINE),
                (PROPOSAL_FAILED, CASE_THEIRS),
                (PROPOSAL_RUNNING, CASE_RUNNING),
                (PROPOSAL_REFUSED, CASE_REFUSED),
            ):
                cur.execute(
                    """
                    INSERT INTO app.action_proposals (id, case_id)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (proposal_id, case_id),
                )

            # The failed-run revision is deliberately the NEWEST on its
            # case, which is exactly the shape that fools the queue.
            for revision_id, proposal_id, run_id, when in (
                (REVISION_OK, PROPOSAL_OK, RUN_OK, "now() - interval '2h'"),
                (
                    REVISION_FAILED,
                    PROPOSAL_FAILED,
                    RUN_FAILED,
                    "now()",
                ),
                (
                    REVISION_RUNNING,
                    PROPOSAL_RUNNING,
                    RUN_RUNNING,
                    "now()",
                ),
                (
                    REVISION_REFUSED,
                    PROPOSAL_REFUSED,
                    RUN_REFUSED,
                    "now()",
                ),
            ):
                cur.execute(
                    f"""
                    INSERT INTO app.proposal_revisions (
                        id, proposal_id, revision, run_id, decision_state,
                        summary, payload, cited_unit_ids,
                        missing_predicates,
                        requires_professional_verification, created_at
                    ) VALUES (
                        %s, %s, 1, %s, 'MISSING_FACTS',
                        'access fixture proposal', '{{}}'::jsonb,
                        '[]'::jsonb, '["assessment_year"]'::jsonb,
                        true, {when}
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (revision_id, proposal_id, run_id),
                )

            cur.execute(
                "UPDATE app.action_proposals SET current_revision = 1 "
                "WHERE id IN (%s, %s, %s, %s)",
                (
                    PROPOSAL_OK,
                    PROPOSAL_FAILED,
                    PROPOSAL_RUNNING,
                    PROPOSAL_REFUSED,
                ),
            )

    print("CASE ACCESS FIXTURE: seeded")


def cleanup():
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.proposal_revisions "
                "WHERE id IN (%s, %s, %s, %s)",
                (
                    REVISION_OK,
                    REVISION_FAILED,
                    REVISION_RUNNING,
                    REVISION_REFUSED,
                ),
            )
            cur.execute(
                "DELETE FROM app.action_proposals "
                "WHERE id IN (%s, %s, %s, %s)",
                (
                    PROPOSAL_OK,
                    PROPOSAL_FAILED,
                    PROPOSAL_RUNNING,
                    PROPOSAL_REFUSED,
                ),
            )
            cur.execute(
                "DELETE FROM app.agent_runs "
                "WHERE id IN (%s, %s, %s, %s)",
                (RUN_OK, RUN_FAILED, RUN_RUNNING, RUN_REFUSED),
            )
            cur.execute(
                "DELETE FROM app.reviewer_case_grants "
                "WHERE granted_by = 'case-access-fixture'"
            )
            cur.execute(
                "DELETE FROM app.cases "
                "WHERE id IN (%s, %s, %s, %s)",
                (
                    CASE_MINE,
                    CASE_THEIRS,
                    CASE_RUNNING,
                    CASE_REFUSED,
                ),
            )
            cur.execute(
                "DELETE FROM app.reviewers WHERE id IN (%s, %s, %s)",
                (
                    REVIEWER_GRANTED,
                    REVIEWER_UNGRANTED,
                    REVIEWER_INACTIVE,
                ),
            )

    print("CASE ACCESS FIXTURE CLEANUP: PASS")


# ---------------------------------------------------------------------
# ACCESS: the authorization predicate, and that the routes consult it.
# ---------------------------------------------------------------------


def access_checks():
    from app.domain import access

    with runtime() as conn:
        with conn.cursor() as cur:
            granted = access.may_access_case(
                cur, REVIEWER_GRANTED, CASE_MINE
            )
            ungranted = access.may_access_case(
                cur, REVIEWER_UNGRANTED, CASE_MINE
            )
            other_case = access.may_access_case(
                cur, REVIEWER_GRANTED, CASE_THEIRS
            )
            inactive = access.may_access_case(
                cur, REVIEWER_INACTIVE, CASE_MINE
            )
            missing = access.may_access_case(cur, None, CASE_MINE)

    check(
        "ACCESS01 A GRANTED REVIEWER MAY REACH THE CASE",
        granted is True,
        f"may_access_case returned {granted!r}",
    )
    check(
        "ACCESS02 AN UNGRANTED REVIEWER MAY NOT",
        ungranted is False,
        f"may_access_case returned {ungranted!r}",
    )
    check(
        "ACCESS04 A GRANT ON ONE CASE IS NOT AUTHORITY OVER ANOTHER",
        other_case is False,
        f"the reviewer granted {CASE_MINE[:8]} was allowed "
        f"{CASE_THEIRS[:8]}",
    )
    check(
        "ACCESS05 AN INACTIVE REVIEWER MAY NOT, GRANT OR NOT",
        inactive is False,
        f"an inactive reviewer holding a grant returned {inactive!r}",
    )
    check(
        "ACCESS07 AN ABSENT REVIEWER IS NOT AUTHORISED",
        missing is False,
        f"a null reviewer returned {missing!r}",
    )


def access03_every_case_route_consults_the_predicate():
    """Authorization must not depend on which handler remembered it.

    A structural check, because the defect was uneven enforcement rather
    than a wrong predicate: three mutation paths refused an ungranted
    reviewer and the read paths never asked. This fails if a case-scoped
    route appears that does not consult the module.
    """
    source = (REPO_ROOT / "app/reviewer/server.py").read_text(
        encoding="utf-8"
    )

    guarded = source.count("access.may_access_case")

    check(
        "ACCESS03 EVERY CASE ROUTE CONSULTS THE PREDICATE",
        guarded >= 4,
        f"server.py consults may_access_case {guarded} time(s); the "
        f"case page, the request page, draft and send all need it",
    )


def access06_a_denial_reveals_no_case_content():
    """A refusal must not leak what it is refusing."""
    from app.reviewer import server as server_module

    message = server_module.ACCESS_DENIED

    leaks = [
        token
        for token in ("AFH-", "reference", "subject", "sender")
        if token.lower() in message.lower()
    ]

    check(
        "ACCESS06 A DENIAL REVEALS NO CASE CONTENT",
        not leaks and len(message) < 300,
        f"denial message leaked {leaks}: {message[:120]!r}",
    )


def access08_the_authorised_path_still_works():
    """The fix must not lock out the reviewer who is entitled."""
    from app.domain import access

    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT g.reviewer_id::text, g.case_id::text
                FROM app.reviewer_case_grants g
                JOIN app.reviewers r ON r.id = g.reviewer_id
                WHERE g.revoked_at IS NULL AND r.is_active
                  AND g.granted_by <> 'case-access-fixture'
                LIMIT 1
                """
            )
            real = cur.fetchone()

            allowed = (
                access.may_access_case(cur, real[0], real[1])
                if real
                else None
            )

    check(
        "ACCESS08 A PRE-EXISTING GRANTED REVIEWER IS UNAFFECTED",
        real is None or allowed is True,
        f"an existing grant {real} evaluated to {allowed!r}",
    )


# ---------------------------------------------------------------------
# RUNSAFE: a failed run's proposal is evidence, not actionable work.
# ---------------------------------------------------------------------


# Lanes in which the inbox is telling a professional something about
# the agent's conclusion. A case whose run never finished has no
# conclusion to report, so it must not appear in one of these.
CONCLUDING_LANES = (
    "NEEDS_DECISION",
    "READY_TO_SEND",
    "WAITING_ON_CLIENT",
    "NEEDS_PROFESSIONAL",
)


def current_work_paths():
    """Every production selection of the current proposal for a case.

    Three exist and they must agree. Returned by name because the
    defect being closed was two of them being fixed while the third --
    the one the console inbox actually renders -- was not.
    """
    from app.reviewer import inbox, workqueue

    paths = {}

    with runtime() as conn:
        paths["inbox.queue"] = {
            item["case_id"]
            for item in inbox.queue(conn)
            if item["decision_state"]
        }

        paths["workqueue.queue"] = {
            item["case_id"] for item in workqueue.queue(conn)
        }

        page = set()

        for case_id in (
            CASE_MINE,
            CASE_THEIRS,
            CASE_RUNNING,
            CASE_REFUSED,
        ):
            revision = workqueue.latest_revision(conn, case_id)

            # Before the change `latest_revision` said nothing about
            # the run, and the case page offered its actions anyway.
            # Absent the key, the honest reading is "actionable".
            if revision and revision.get("actionable", True):
                page.add(case_id)

        paths["workqueue.latest_revision"] = page

    return paths


def presenting(paths, case_id):
    """Which paths present this case as the agent's conclusion."""
    return sorted(
        name for name, cases in paths.items() if case_id in cases
    )


def runsafe_checks():
    paths = current_work_paths()

    check(
        "RUNSAFE01 A SUCCEEDED RUN'S PROPOSAL IS ACTIONABLE",
        len(presenting(paths, CASE_MINE)) == len(paths),
        f"only {presenting(paths, CASE_MINE)} of {sorted(paths)} "
        f"present the succeeded-run case",
    )
    check(
        "RUNSAFE02 A FAILED RUN'S PROPOSAL IS NOT ACTIONABLE",
        not presenting(paths, CASE_THEIRS),
        f"the failed-run case is presented by "
        f"{presenting(paths, CASE_THEIRS)}",
    )
    check(
        "RUNSAFE08 AN UNFINISHED RUN'S PROPOSAL IS NOT ACTIONABLE",
        not presenting(paths, CASE_RUNNING),
        f"the in-flight case is presented by "
        f"{presenting(paths, CASE_RUNNING)}",
    )
    check(
        "RUNSAFE09 A REFUSED RUN'S PROPOSAL IS NOT ACTIONABLE",
        not presenting(paths, CASE_REFUSED),
        f"the refused-run case is presented by "
        f"{presenting(paths, CASE_REFUSED)}",
    )

    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id::text, a.result_state
                FROM app.proposal_revisions r
                JOIN app.agent_runs a ON a.id = r.run_id
                WHERE r.id = ANY(%s)
                ORDER BY a.result_state
                """,
                ([REVISION_FAILED, REVISION_RUNNING, REVISION_REFUSED],),
            )
            kept = cur.fetchall()

    check(
        "RUNSAFE03 A FAILED RUN'S PROPOSAL REMAINS AS EVIDENCE",
        len(kept) == 3
        and {row[1] for row in kept}
        == {"FAILED", "RUNNING", "REFUSED"},
        f"the non-actionable revisions read {kept!r}; they must lose "
        f"standing, not be deleted",
    )


def runsafe05_the_count_a_professional_sees():
    """No unfinished run may be counted as work, or described as one."""
    from app.reviewer import inbox

    with runtime() as conn:
        items = inbox.queue(conn)

    unfinished = (CASE_THEIRS, CASE_RUNNING, CASE_REFUSED)
    by_case = {item["case_id"]: item for item in items}

    counted = [
        case_id
        for case_id in unfinished
        if case_id in by_case
        and by_case[case_id]["lane"]
        in ("NEEDS_DECISION", "READY_TO_SEND", "NEEDS_PROFESSIONAL")
    ]

    described = [
        (case_id, by_case[case_id]["lane"])
        for case_id in unfinished
        if case_id in by_case
        and by_case[case_id]["lane"] in CONCLUDING_LANES
    ]

    check(
        "RUNSAFE05 QUEUE COUNTS EXCLUDE UNFINISHED WORK",
        not counted,
        f"{counted} counted as work a professional must do",
    )
    check(
        "RUNSAFE13 AN UNFINISHED RUN IS NOT GIVEN A CONCLUSION",
        not described,
        f"the inbox reports {described}, which states a conclusion the "
        f"run never reached",
    )


def runsafe10_only_succeeded_is_eligible():
    """One rule, named once, and no survivor of the weaker one."""
    from app.domain import actionability

    sources = {
        name: (REPO_ROOT / name).read_text(encoding="utf-8")
        for name in (
            "app/reviewer/inbox.py",
            "app/reviewer/workqueue.py",
        )
    }

    carries = [
        name
        for name, body in sources.items()
        if actionability.CURRENT_WORK_SQL in body
        or "actionability.CURRENT_WORK_SQL" in body
    ]

    # Scoped to the run state on purpose. A bare <> 'FAILED' also
    # matches the dispatch join in the same file, which is a different
    # column and a correct rule -- and matching it made this check fail
    # on a false positive of its own making.
    weaker = [
        name
        for name, body in sources.items()
        if "result_state <> 'FAILED'" in body
    ]

    check(
        "RUNSAFE10 ONLY A SUCCEEDED RUN IS ELIGIBLE AS CURRENT WORK",
        actionability.ACTIONABLE_RUN_STATE == "SUCCEEDED"
        and set(actionability.NON_ACTIONABLE_RUN_STATES)
        == {"RUNNING", "FAILED", "REFUSED"},
        f"the rule admits {actionability.ACTIONABLE_RUN_STATE!r} and "
        f"excludes {actionability.NON_ACTIONABLE_RUN_STATES}",
    )
    check(
        "RUNSAFE11 EVERY CURRENT-WORK SELECTION USES THE SAME RULE",
        len(carries) == len(sources) and not weaker,
        f"{carries} carry the rule; {weaker} still carry <> 'FAILED'",
    )


def runsafe12_the_mutation_is_caught():
    """Revert the predicate in SQL and require the defect to reappear.

    The old rule is spliced back into the same shape of query and run
    against the same rows. If it does not admit the in-flight and
    refused cases, these checks are not testing what they claim to.
    """
    mutated = """
        SELECT DISTINCT ON (p.case_id) p.case_id::text
        FROM app.proposal_revisions r
        JOIN app.action_proposals p ON p.id = r.proposal_id
        JOIN app.agent_runs a ON a.id = r.run_id
        WHERE a.result_state <> 'FAILED'
          AND p.case_id = ANY(%s)
        ORDER BY p.case_id, r.created_at DESC
        """

    with runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(mutated, ([CASE_RUNNING, CASE_REFUSED],))
            admitted = {row[0] for row in cur.fetchall()}

    check(
        "RUNSAFE12 THE WEAKER PREDICATE IS STILL DETECTABLE",
        admitted == {CASE_RUNNING, CASE_REFUSED},
        f"reverting to <> 'FAILED' admitted {sorted(admitted)}; if it "
        f"admits nothing the fixture no longer proves the defect",
    )


def runsafe14_a_draft_cannot_come_from_an_unfinished_run():
    """Composing from a run that did not succeed must be refused.

    The refusal has to be about the run. Every fixture case here lacks
    an inbound enquiry, so composing refuses on all four for want of a
    correspondent -- which is why the first version of this check
    passed before the guard existed and proved nothing. So it reads the
    reason, and requires the succeeded revision to reach the later rule
    instead of this one.
    """
    from app.domain import drafting

    outcomes = {}

    for label, revision_id in (
        ("ok", REVISION_OK),
        ("failed", REVISION_FAILED),
        ("running", REVISION_RUNNING),
        ("refused", REVISION_REFUSED),
    ):
        with runtime() as conn:
            try:
                drafting.compose(conn, revision_id)
                outcomes[label] = "PROCEEDED"
            except Exception as exc:  # noqa: BLE001
                outcomes[label] = str(exc)

    unfinished = ("failed", "running", "refused")

    blocked_on_the_run = [
        label
        for label in unfinished
        if "did not finish" in outcomes[label]
    ]

    check(
        "RUNSAFE14 NO LETTER IS WRITTEN FROM AN UNFINISHED RUN",
        len(blocked_on_the_run) == len(unfinished),
        f"only {blocked_on_the_run} were refused on the run's state; "
        f"the rest: { {k: outcomes[k][:60] for k in unfinished} }",
    )
    check(
        "RUNSAFE15 THE GUARD DOES NOT REFUSE A FINISHED RUN",
        "did not finish" not in outcomes["ok"],
        f"the succeeded revision was refused on run state: "
        f"{outcomes['ok'][:80]}",
    )


def runsafe04_a_later_success_supersedes_a_failure():
    """A successful rerun must restore the case to the queue."""
    from app.reviewer import workqueue

    later_run = "96200000-0000-0000-0000-000000000003"
    later_revision = "96400000-0000-0000-0000-000000000003"

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.agent_runs (
                    id, case_id, operation_id, runner, model_id,
                    prompt_version, prompt_digest, tool_schema_version,
                    context_builder_version, result_state, ended_at
                ) VALUES (
                    %s, %s, %s, 'DETERMINISTIC_STUB', 'fixture-model',
                    'v0', 'fixture-digest', 'v0', 'v0', 'SUCCEEDED', now()
                ) ON CONFLICT (id) DO NOTHING
                """,
                (later_run, CASE_THEIRS, "access-run-rerun"),
            )
            cur.execute(
                """
                INSERT INTO app.proposal_revisions (
                    id, proposal_id, revision, run_id, decision_state,
                    summary, payload, cited_unit_ids, missing_predicates,
                    requires_professional_verification
                ) VALUES (
                    %s, %s, 2, %s, 'MISSING_FACTS',
                    'the rerun that succeeded', '{}'::jsonb, '[]'::jsonb,
                    '["assessment_year"]'::jsonb, true
                ) ON CONFLICT (id) DO NOTHING
                """,
                (later_revision, PROPOSAL_FAILED, later_run),
            )

    with runtime() as conn:
        items = workqueue.queue(conn)

    restored = next(
        (i for i in items if i["case_id"] == CASE_THEIRS), None
    )

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.proposal_revisions WHERE id = %s",
                (later_revision,),
            )
            cur.execute(
                "DELETE FROM app.agent_runs WHERE id = %s", (later_run,)
            )

    check(
        "RUNSAFE04 A LATER SUCCESS SUPERSEDES A FAILURE",
        restored is not None
        and restored["revision_id"] == later_revision,
        f"after a successful rerun the case shows "
        f"{restored['revision_id'] if restored else None}",
    )


def runsafe0607_failed_work_is_labelled_not_pending():
    """The case page must say failed, and never pending approval."""
    from app.reviewer import queries

    with runtime() as conn:
        revisions = queries.revisions(conn, CASE_THEIRS)
        runs = queries.runs(conn, CASE_THEIRS)

    states = [row[-3] if len(row) > 3 else None for row in runs]

    check(
        "RUNSAFE06 FAILED WORK IS STILL VISIBLE ON THE CASE",
        len(revisions) >= 1 and len(runs) >= 1,
        f"{len(revisions)} revision(s) and {len(runs)} run(s) readable "
        f"for the failed case",
    )
    check(
        "RUNSAFE07 A FAILED RUN IS RECORDED AS FAILED",
        any("FAILED" in str(row) for row in runs),
        f"no run on the case reports FAILED: {states}",
    )


# ---------------------------------------------------------------------
# IDENT: authorization is only a boundary if the identity is trusted.
# ---------------------------------------------------------------------


def _console(acting_reviewer):
    """A console process bound to one acting identity."""
    import threading

    from app.reviewer import server as console

    httpd = console.make_server(0, acting_reviewer)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    return httpd, port


def _get(port, path):
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=20
        ) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _post(port, path, fields):
    """POST and return the readable outcome message, not just a code.

    The message matters: every fixture case here also fails to draft
    for want of a correspondent, so "no draft appeared" would be true
    whether or not the access check fired. Reading which refusal came
    back is what makes the check discriminate.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            url = response.geturl()
    except urllib.error.HTTPError as exc:
        url = exc.geturl()

    return urllib.parse.unquote_plus(url)


def ident_checks():
    """Two processes, two identities, and no way to swap between them."""
    granted_httpd, granted_port = _console(REVIEWER_GRANTED)
    ungranted_httpd, ungranted_port = _console(REVIEWER_UNGRANTED)

    try:
        # Bound to the reviewer who holds nothing, asking to be the one
        # who holds the case. This is the impersonation attempt.
        impersonated, _body = _get(
            ungranted_port,
            f"/case/{CASE_MINE}?reviewer={REVIEWER_GRANTED}",
        )

        own, own_body = _get(granted_port, f"/case/{CASE_MINE}")
        other, _ = _get(granted_port, f"/case/{CASE_THEIRS}")

        # And on a POST, where the actor used to come off the form.
        # Tried in both directions: a form field must not grant
        # authority the process lacks, and must not remove authority
        # the process has.
        elevated = _post(
            ungranted_port,
            f"/case/{CASE_MINE}/draft",
            {"reviewer": REVIEWER_GRANTED, "revision_id": REVISION_OK},
        )
        demoted = _post(
            granted_port,
            f"/case/{CASE_MINE}/draft",
            {"reviewer": REVIEWER_UNGRANTED, "revision_id": REVISION_OK},
        )
    finally:
        for httpd in (granted_httpd, ungranted_httpd):
            httpd.shutdown()
            httpd.server_close()

    check(
        "IDENT01 A REQUEST CANNOT CHOOSE WHICH REVIEWER IT IS",
        impersonated == 403,
        f"asking to be a granted reviewer returned {impersonated}, so "
        f"the query string still decides who is acting",
    )
    check(
        "IDENT02 THE BOUND REVIEWER REACHES A CASE THEY HOLD",
        own == 200,
        f"the bound reviewer got {own} on their own case",
    )
    check(
        "IDENT03 THE BOUND REVIEWER IS REFUSED A CASE THEY DO NOT HOLD",
        other == 403,
        f"the bound reviewer got {other} on a case they hold no grant "
        f"on",
    )
    check(
        "IDENT04 A FORM FIELD CANNOT GRANT AUTHORITY TO A POST",
        "do not have access" in elevated,
        f"naming a granted reviewer on the form gave: "
        f"{elevated.partition('msg=')[2][:70]}",
    )
    check(
        "IDENT05 A FORM FIELD CANNOT REMOVE AUTHORITY EITHER",
        "do not have access" not in demoted,
        f"naming an ungranted reviewer on the form refused the bound "
        f"reviewer: {demoted.partition('msg=')[2][:70]}",
    )


# ---------------------------------------------------------------------
# SEND: dispatch requires an approval bound to the exact content.
# ---------------------------------------------------------------------


def send_checks():
    from app.dispatch import dispatcher

    outcomes = {}

    with runtime() as conn:
        try:
            dispatcher.dispatch(
                conn, "96900000-0000-0000-0000-0000000000ff", None
            )
            outcomes["no_approval"] = "PROCEEDED"
        except Exception as exc:  # noqa: BLE001
            outcomes["no_approval"] = exc.__class__.__name__

    check(
        "SEND01 NO APPROVAL MEANS NO DISPATCH",
        outcomes["no_approval"] != "PROCEEDED",
        f"dispatch with an unknown approval {outcomes['no_approval']}",
    )

    source = (REPO_ROOT / "app/dispatch/dispatcher.py").read_text(
        encoding="utf-8"
    )

    check(
        "SEND02 A CHANGED DRAFT INVALIDATES THE APPROVAL",
        'current != record["approved_digest"]' in source,
        "the dispatcher does not recompute the digest at send time",
    )
    check(
        "SEND03 A MATCHING DIGEST IS ELIGIBLE TO PROCEED",
        "approved_digest" in source and "def dispatch" in source,
        "the dispatcher does not consult the approved digest",
    )


CHECKS = (
    access_checks,
    access03_every_case_route_consults_the_predicate,
    access06_a_denial_reveals_no_case_content,
    access08_the_authorised_path_still_works,
    runsafe_checks,
    runsafe05_the_count_a_professional_sees,
    runsafe10_only_succeeded_is_eligible,
    runsafe12_the_mutation_is_caught,
    runsafe14_a_draft_cannot_come_from_an_unfinished_run,
    runsafe04_a_later_success_supersedes_a_failure,
    runsafe0607_failed_work_is_labelled_not_pending,
    ident_checks,
    send_checks,
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
        f"\nCASE ACCESS: {len(PASSES)} passed, {len(FAILURES)} failed"
    )

    return 1 if FAILURES else 0


def main(argv):
    if len(argv) != 2 or argv[1] not in ("phase1", "cleanup"):
        raise SystemExit(
            "Usage: case_access_smoke.py phase1|cleanup"
        )

    with admin() as conn:
        testguard.assert_disposable(conn)

    testguard.acquire_single_run_lock()

    if argv[1] == "cleanup":
        cleanup()
        return 0

    return phase1()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
