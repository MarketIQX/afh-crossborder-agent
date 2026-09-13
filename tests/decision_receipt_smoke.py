"""RECEIPT01-13. One decision, accounted for from the record alone.

A receipt is only worth having if it cannot say more than the database
knows. So these checks are mostly adversarial: they compare the receipt
against direct queries, try to make it claim a tool that never ran,
check that a second case's evidence cannot leak into the first, and
require that the same rows always produce the same digest.

Two of them exist because of what went wrong elsewhere in this slice.
RECEIPT08 requires a receipt to remain available for a run that failed,
refused, or never finished -- the point being that removing standing is
not the same as removing the record. RECEIPT10 fails if any field
appears that purports to hold the model's private reasoning, because
avoiding that by intention is weaker than avoiding it by test.

    python tests/decision_receipt_smoke.py phase1
    python tests/decision_receipt_smoke.py cleanup
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import psycopg  # noqa: E402

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402

SERVICE_ID = "10000000-0000-0000-0000-000000000001"

CASE_ONE = "97000000-0000-0000-0000-000000000001"
CASE_TWO = "97000000-0000-0000-0000-000000000002"

RUN_ONE = "97200000-0000-0000-0000-000000000001"
RUN_TWO = "97200000-0000-0000-0000-000000000002"
RUN_BROKEN = "97200000-0000-0000-0000-000000000003"

PROPOSAL_ONE = "97300000-0000-0000-0000-000000000001"
PROPOSAL_TWO = "97300000-0000-0000-0000-000000000002"
PROPOSAL_BROKEN = "97300000-0000-0000-0000-000000000003"

REVISION_ONE = "97400000-0000-0000-0000-000000000001"
REVISION_TWO = "97400000-0000-0000-0000-000000000002"
REVISION_BROKEN = "97400000-0000-0000-0000-000000000003"

TOOL_ONE = "97500000-0000-0000-0000-000000000001"
TOOL_TWO = "97500000-0000-0000-0000-000000000002"

FACT_ONE = "97600000-0000-0000-0000-000000000001"
FACT_TWO = "97600000-0000-0000-0000-000000000002"
FACT_OTHER = "97600000-0000-0000-0000-000000000003"

# Deliberately distinctive, so a leak between cases is unmistakable.
SECRET_OF_CASE_TWO = "case-two-private-value-9d41"

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
    """Two cases with a run each, and one run that broke.

    Case two carries a distinctive fact value and its own tool call, so
    a receipt that reaches across cases is caught by name rather than by
    a count.
    """
    with admin() as conn:
        with conn.cursor() as cur:
            for case_id, reference in (
                (CASE_ONE, "AFH-RECEIPT-ONE"),
                (CASE_TWO, "AFH-RECEIPT-TWO"),
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

            for run_id, case_id, operation, state, reason in (
                (RUN_ONE, CASE_ONE, "receipt-run-one", "SUCCEEDED", None),
                (RUN_TWO, CASE_TWO, "receipt-run-two", "SUCCEEDED", None),
                (
                    RUN_BROKEN,
                    CASE_ONE,
                    "receipt-run-broken",
                    "FAILED",
                    "fixture: the provider dropped the connection",
                ),
            ):
                cur.execute(
                    """
                    INSERT INTO app.agent_runs (
                        id, case_id, operation_id, runner, model_id,
                        model_config, prompt_version, prompt_digest,
                        tool_schema_version, context_builder_version,
                        result_state, ended_at, failure_reason,
                        latency_ms, input_tokens, output_tokens,
                        tool_call_count
                    ) VALUES (
                        %s, %s, %s, 'DETERMINISTIC_STUB', 'fixture-model',
                        '{"temperature": 0}'::jsonb, 'prompt-v1',
                        'prompt-digest-abc', 'tools-v1', 'context-v1',
                        %s, now(), %s, 120, 900, 80, 2
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (run_id, case_id, operation, state, reason),
                )

            for proposal_id, case_id in (
                (PROPOSAL_ONE, CASE_ONE),
                (PROPOSAL_TWO, CASE_TWO),
                (PROPOSAL_BROKEN, CASE_ONE),
            ):
                cur.execute(
                    """
                    INSERT INTO app.action_proposals (id, case_id)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (proposal_id, case_id),
                )

            for revision_id, proposal_id, run_id, missing in (
                (REVISION_ONE, PROPOSAL_ONE, RUN_ONE, "purchase_date"),
                (REVISION_TWO, PROPOSAL_TWO, RUN_TWO, "assessment_year"),
                (
                    REVISION_BROKEN,
                    PROPOSAL_BROKEN,
                    RUN_BROKEN,
                    "purchase_date",
                ),
            ):
                cur.execute(
                    """
                    INSERT INTO app.proposal_revisions (
                        id, proposal_id, revision, run_id, decision_state,
                        summary, payload, cited_unit_ids,
                        missing_predicates,
                        requires_professional_verification
                    ) VALUES (
                        %s, %s, 1, %s, 'MISSING_FACTS',
                        'receipt fixture proposal', '{}'::jsonb,
                        '[]'::jsonb, %s, true
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        revision_id,
                        proposal_id,
                        run_id,
                        psycopg.types.json.Jsonb([missing]),
                    ),
                )

            for tool_id, run_id, sequence, name in (
                (TOOL_ONE, RUN_ONE, 1, "get_case_context"),
                (TOOL_TWO, RUN_TWO, 1, "get_service_knowledge"),
            ):
                cur.execute(
                    """
                    INSERT INTO app.agent_tool_calls (
                        id, run_id, sequence, tool_name, arguments,
                        result_summary, duration_ms
                    ) VALUES (
                        %s, %s, %s, %s, '{}'::jsonb,
                        '{"units": 0, "topics": 1}'::jsonb, 12
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (tool_id, run_id, sequence, name),
                )

            for fact_id, case_id, predicate, value, status, run_id in (
                (
                    FACT_ONE,
                    CASE_ONE,
                    "country_of_residence",
                    "UAE",
                    "CONFIRMED",
                    None,
                ),
                (
                    FACT_TWO,
                    CASE_ONE,
                    "property_type",
                    "residential",
                    "PROPOSED",
                    RUN_ONE,
                ),
                (
                    FACT_OTHER,
                    CASE_TWO,
                    "country_of_residence",
                    SECRET_OF_CASE_TWO,
                    "CONFIRMED",
                    None,
                ),
            ):
                cur.execute(
                    """
                    INSERT INTO app.case_facts (
                        id, case_id, predicate, value_text, status,
                        origin, evidence_refs, run_id
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, '[]'::jsonb, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        fact_id,
                        case_id,
                        predicate,
                        value,
                        status,
                        "AGENT_PROPOSED" if run_id else "REVIEWER",
                        run_id,
                    ),
                )

            cur.execute(
                "UPDATE app.action_proposals SET current_revision = 1 "
                "WHERE id IN (%s, %s, %s)",
                (PROPOSAL_ONE, PROPOSAL_TWO, PROPOSAL_BROKEN),
            )

    print("RECEIPT FIXTURE: seeded")


def cleanup():
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.case_facts WHERE id IN (%s, %s, %s)",
                (FACT_ONE, FACT_TWO, FACT_OTHER),
            )
            cur.execute(
                "DELETE FROM app.agent_tool_calls WHERE id IN (%s, %s)",
                (TOOL_ONE, TOOL_TWO),
            )
            cur.execute(
                "DELETE FROM app.proposal_revisions "
                "WHERE id IN (%s, %s, %s)",
                (REVISION_ONE, REVISION_TWO, REVISION_BROKEN),
            )
            cur.execute(
                "DELETE FROM app.action_proposals "
                "WHERE id IN (%s, %s, %s)",
                (PROPOSAL_ONE, PROPOSAL_TWO, PROPOSAL_BROKEN),
            )
            cur.execute(
                "DELETE FROM app.agent_runs WHERE id IN (%s, %s, %s)",
                (RUN_ONE, RUN_TWO, RUN_BROKEN),
            )
            cur.execute(
                "DELETE FROM app.cases WHERE id IN (%s, %s)",
                (CASE_ONE, CASE_TWO),
            )

    print("RECEIPT FIXTURE CLEANUP: PASS")


# ---------------------------------------------------------------------
# What the receipt must say, and must not.
# ---------------------------------------------------------------------


def identity_checks():
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            one = receipt_module.build(cur, REVISION_ONE)

    check(
        "RECEIPT01 THE RECEIPT NAMES ITS EXACT CASE, RUN AND REVISION",
        one["case"]["case_id"] == CASE_ONE
        and one["run"]["run_id"] == RUN_ONE
        and one["decision"]["revision_id"] == REVISION_ONE
        and one["run"]["operation_id"] == "receipt-run-one",
        f"case={one['case']['case_id']} run={one['run']['run_id']} "
        f"revision={one['decision']['revision_id']}",
    )
    check(
        "RECEIPT02 THE RECEIPT RECORDS THE RUN'S EXACT RESULT STATE",
        one["run"]["result_state"] == "SUCCEEDED"
        and one["actionable"] is True,
        f"result_state={one['run']['result_state']!r} "
        f"actionable={one['actionable']!r}",
    )
    check(
        "RECEIPT04 THE RECEIPT CARRIES THE TOOL SEQUENCE AND OUTCOMES",
        [(t["sequence"], t["tool"], t["outcome"]) for t in one["tools"]]
        == [(1, "get_case_context", "OK")],
        f"the trajectory reads {one['tools']!r}",
    )
    check(
        "RECEIPT05 ESTABLISHED AND MERELY ASSERTED ARE NOT MIXED",
        [f["predicate"] for f in one["facts"]["established"]]
        == ["country_of_residence"]
        and [
            f["predicate"] for f in one["facts"]["proposed_by_this_run"]
        ]
        == ["property_type"],
        f"established={one['facts']['established']!r} "
        f"proposed={one['facts']['proposed_by_this_run']!r}",
    )
    check(
        "RECEIPT06 WHAT REMAINED UNKNOWN IS PRESERVED",
        one["facts"]["unknown"] == ["purchase_date"],
        f"unknown reads {one['facts']['unknown']!r}",
    )
    check(
        "RECEIPT03 THE RECEIPT NAMES THE RELEASE AND THE CITED UNITS",
        "release_relied_on" in one["knowledge"]
        and "release_in_force" in one["knowledge"]
        and one["knowledge"]["cited_unit_ids"] == [],
        f"knowledge reads {one['knowledge']!r}",
    )
    check(
        "RECEIPT13 THE RECEIPT DECLARES WHAT IT CANNOT RECONSTRUCT",
        any(
            "status-transition" in line
            for line in one["reconstruction_limits"]
        ),
        f"reconstruction_limits reads {one['reconstruction_limits']!r}",
    )


def digest_checks():
    """The same rows must always produce the same receipt."""
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            first = receipt_module.build(cur, REVISION_ONE)
            second = receipt_module.build(cur, REVISION_ONE)

    altered = dict(second)
    altered["decision"] = dict(second["decision"])
    altered["decision"]["summary"] = "something else entirely"

    check(
        "RECEIPT07 THE SAME STATE PRODUCES THE SAME DIGEST",
        first["receipt_digest"] == second["receipt_digest"]
        and len(first["receipt_digest"]) == 64,
        f"{first['receipt_digest'][:16]} vs "
        f"{second['receipt_digest'][:16]}",
    )
    check(
        "RECEIPT08 A CHANGED DECISION PRODUCES A DIFFERENT DIGEST",
        receipt_module.digest(altered) != first["receipt_digest"],
        "editing the summary left the digest unchanged, so the digest "
        "does not cover the decision",
    )


def failed_run_checks():
    """Losing standing is not the same as losing the record."""
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            broken = receipt_module.build(cur, REVISION_BROKEN)

            try:
                receipt_module.build(
                    cur, "97400000-0000-0000-0000-0000000000ff"
                )
                unknown = "PROCEEDED"
            except Exception as exc:  # noqa: BLE001
                unknown = exc.__class__.__name__

    check(
        "RECEIPT09 A FAILED RUN STILL HAS A RECEIPT, MARKED UNACTIONABLE",
        broken["run"]["result_state"] == "FAILED"
        and broken["actionable"] is False
        and broken["run"]["failure_reason"],
        f"result_state={broken['run']['result_state']!r} "
        f"actionable={broken['actionable']!r} "
        f"reason={broken['run']['failure_reason']!r}",
    )
    check(
        "RECEIPT10 A RECEIPT CANNOT BE BUILT FOR A DECISION THAT IS ABSENT",
        unknown == "ReceiptUnavailable",
        f"building a receipt for an unknown revision gave {unknown}",
    )


def no_hidden_reasoning_checks():
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            one = receipt_module.build(cur, REVISION_ONE)

    present = receipt_module.field_names(one)
    offending = sorted(present & receipt_module.FORBIDDEN_FIELDS)

    check(
        "RECEIPT11 NO FIELD PURPORTS TO HOLD HIDDEN REASONING",
        not offending,
        f"the receipt carries {offending}, which claims to be the "
        f"model's private deliberation rather than observable evidence",
    )


def isolation_checks():
    """Case one's receipt must know nothing of case two."""
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            one = receipt_module.build(cur, REVISION_ONE)

    rendered = receipt_module.canonical(one).decode("utf-8")

    check(
        "RECEIPT12 ONE CASE'S RECEIPT CARRIES NO OTHER CASE'S EVIDENCE",
        SECRET_OF_CASE_TWO not in rendered
        and CASE_TWO not in rendered
        and RUN_TWO not in rendered
        and "get_service_knowledge" not in rendered,
        "case two's value, id, run or tool call appears in case one's "
        "receipt",
    )


def no_invention_checks():
    """Every tool and fact in the receipt must exist in the tables."""
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            one = receipt_module.build(cur, REVISION_ONE)

            cur.execute(
                "SELECT sequence, tool_name FROM app.agent_tool_calls "
                "WHERE run_id = %s ORDER BY sequence",
                (RUN_ONE,),
            )
            real_tools = [tuple(row) for row in cur.fetchall()]

            cur.execute(
                "SELECT predicate, value_text FROM app.case_facts "
                "WHERE case_id = %s AND status = 'CONFIRMED' "
                "ORDER BY predicate",
                (CASE_ONE,),
            )
            real_facts = [tuple(row) for row in cur.fetchall()]

    claimed_tools = [(t["sequence"], t["tool"]) for t in one["tools"]]
    claimed_facts = [
        (f["predicate"], f["value"])
        for f in one["facts"]["established"]
    ]

    check(
        "RECEIPT14 THE RECEIPT CLAIMS NO TOOL THE RUN DID NOT CALL",
        claimed_tools == real_tools,
        f"receipt says {claimed_tools}, the table says {real_tools}",
    )
    check(
        "RECEIPT15 THE RECEIPT CLAIMS NO FACT THE CASE DOES NOT HOLD",
        claimed_facts == real_facts,
        f"receipt says {claimed_facts}, the table says {real_facts}",
    )


CHECKS = (
    identity_checks,
    digest_checks,
    failed_run_checks,
    no_hidden_reasoning_checks,
    isolation_checks,
    no_invention_checks,
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

    print(f"\nDECISION RECEIPT: {len(PASSES)} passed, {len(FAILURES)} failed")

    return 1 if FAILURES else 0


def main(argv):
    if len(argv) != 2 or argv[1] not in ("phase1", "cleanup"):
        raise SystemExit("Usage: decision_receipt_smoke.py phase1|cleanup")

    with admin() as conn:
        testguard.assert_disposable(conn)

    testguard.acquire_single_run_lock()

    if argv[1] == "cleanup":
        cleanup()
        return 0

    return phase1()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
