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
TOOL_THREE = "97500000-0000-0000-0000-000000000003"
TOOL_FOUR = "97500000-0000-0000-0000-000000000004"

FACT_ONE = "97600000-0000-0000-0000-000000000001"
FACT_TWO = "97600000-0000-0000-0000-000000000002"
FACT_OTHER = "97600000-0000-0000-0000-000000000003"

# One citable unit, below the top rung, cited from REVISION_ONE.
UNIT_UNVERIFIED = "97700000-0000-0000-0000-000000000001"
UNIT_RETURNED_TWO = "97700000-0000-0000-0000-000000000002"
UNIT_PREFLIGHT_ONLY = "97700000-0000-0000-0000-000000000003"
# The release migration 003 already seeded and activated for this
# service. Only one ACTIVE release per service is allowed
# (knowledge_releases_single_active), so the fixture cites this
# one rather than manufacture a competing one.
SEEDED_RELEASE = "20000000-0000-0000-0000-000000000001"

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

            for tool_id, run_id, sequence, name, summary in (
                (
                    TOOL_ONE,
                    RUN_ONE,
                    1,
                    "get_case_context",
                    {"units": 0, "topics": 1,
                     "returned_evidence": [
                         {"unit_id": "not-a-knowledge-tool-result"}
                     ]},
                ),
                (TOOL_TWO, RUN_TWO, 1, "get_service_knowledge",
                 {"units": 0, "topics": 1}),
            ):
                cur.execute(
                    """
                    INSERT INTO app.agent_tool_calls (
                        id, run_id, sequence, tool_name, arguments,
                        result_summary, duration_ms
                    ) VALUES (
                        %s, %s, %s, %s, '{}'::jsonb,
                        %s, 12
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (tool_id, run_id, sequence, name,
                     psycopg.types.json.Jsonb(summary)),
                )

            # A call that found one usable unit and consulted, without
            # using, two more it would not vouch for.
            cur.execute(
                """
                INSERT INTO app.agent_tool_calls (
                    id, run_id, sequence, tool_name, arguments,
                    result_summary, duration_ms
                ) VALUES (
                    %s, %s, 2, 'get_service_knowledge', '{}'::jsonb,
                    '{"units": 1, "consulted_unverified": 2,
                      "applicability_unknown": 0,
                      "not_applicable": 0,
                      "returned_evidence": [
                        {"unit_id": "97700000-0000-0000-0000-000000000001",
                         "knowledge_release_id": "20000000-0000-0000-0000-000000000001",
                         "verification_status_at_return": "SOURCE_RECORDED",
                         "source_locator": "fixture://locator",
                         "provenance": "FIRM_KNOWLEDGE",
                         "authority": "UNVERIFIED_SOURCE"},
                        {"unit_id": "97700000-0000-0000-0000-000000000002",
                         "knowledge_release_id": "20000000-0000-0000-0000-000000000001",
                         "verification_status_at_return": "VERIFIED",
                         "source_locator": "fixture://returned-two",
                         "provenance": "FIRM_KNOWLEDGE",
                         "authority": "PROFESSIONALLY_VERIFIED"}
                      ]}'::jsonb, 15
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (TOOL_THREE, RUN_ONE),
            )

            # A call that did not complete. No trust signal belongs
            # here: it found nothing, trustworthy or otherwise.
            cur.execute(
                """
                INSERT INTO app.agent_tool_calls (
                    id, run_id, sequence, tool_name, arguments,
                    error, duration_ms
                ) VALUES (
                    %s, %s, 3, 'get_service_knowledge', '{}'::jsonb,
                    'fixture: the retrieval timed out', 8
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (TOOL_FOUR, RUN_ONE),
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

            cur.execute(
                """
                INSERT INTO app.knowledge_units (
                    id, release_id, unit_key, topic, statement,
                    source_locator, verification_status, effective_from
                ) VALUES (
                    %s, %s, 'receipt-fixture-unit', 'fixture_topic',
                    'fixture statement, not professional content',
                    'fixture://locator', 'SOURCE_RECORDED', '2026-01-01'
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (UNIT_UNVERIFIED, SEEDED_RELEASE),
            )
            cur.execute(
                "UPDATE app.proposal_revisions "
                "SET cited_unit_ids = %s, knowledge_release_id = %s "
                "WHERE id = %s",
                (
                    psycopg.types.json.Jsonb([UNIT_UNVERIFIED]),
                    SEEDED_RELEASE,
                    REVISION_ONE,
                ),
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
                "DELETE FROM app.agent_tool_calls "
                "WHERE id IN (%s, %s, %s, %s)",
                (TOOL_ONE, TOOL_TWO, TOOL_THREE, TOOL_FOUR),
            )
            cur.execute(
                "DELETE FROM app.proposal_revisions "
                "WHERE id IN (%s, %s, %s)",
                (REVISION_ONE, REVISION_TWO, REVISION_BROKEN),
            )
            cur.execute(
                "DELETE FROM app.knowledge_units WHERE id = %s",
                (UNIT_UNVERIFIED,),
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
        == [
            (1, "get_case_context", "OK"),
            (2, "get_service_knowledge", "OK"),
            (3, "get_service_knowledge", "ERROR"),
        ],
        f"the trajectory reads {one['tools']!r}",
    )
    check(
        "RECEIPT05 ESTABLISHED AND MERELY ASSERTED ARE NOT MIXED",
        [f["predicate"] for f in one["facts"]["established"]]
        == ["country_of_residence"]
        and [
            f["predicate"] for f in one["facts"]["written_by_this_run"]
        ]
        == ["property_type"],
        f"established={one['facts']['established']!r} "
        f"written={one['facts']['written_by_this_run']!r}",
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
        and one["knowledge"]["cited_unit_ids"] == [UNIT_UNVERIFIED],
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
        and RUN_TWO not in rendered,
        "case two's value, case id or run id appears in case one's "
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


# ---------------------------------------------------------------------
# VER: a receipt read later must be interpretable against its contract.
# ---------------------------------------------------------------------


def version_checks():
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            one = receipt_module.build(cur, REVISION_ONE)
            baseline = one["receipt_digest"]

    # Same state, recomputed: the digest is a function of the content,
    # so it must not drift between two reads.
    with runtime() as conn:
        with conn.cursor() as cur:
            again = receipt_module.build(cur, REVISION_ONE)

    # A version bump is a content change and must move the digest; a
    # display rename is not and must not. The second half is what HID01
    # proves in the other suite, so here it is the first.
    altered = dict(one)
    altered["receipt_version"] = "agent-decision-receipt-v99"

    check(
        "VER01 THE RECEIPT DECLARES ITS OWN SCHEMA VERSIONS",
        one["receipt_version"] == receipt_module.RECEIPT_VERSION
        and one["reasoning_receipt_version"]
        == receipt_module.REASONING_RECEIPT_VERSION
        and "context_manifest_version" in one,
        f"versions read {one.get('receipt_version')!r} / "
        f"{one.get('reasoning_receipt_version')!r} / "
        f"{one.get('context_manifest_version')!r}",
    )
    check(
        "VER02 THE MANIFEST VERSION IS REPORTED, PRESENT OR NOT",
        one["context_manifest_version"]
        and one["reasoning"]["system_preflight_basis"]["manifest_version"]
        == one["context_manifest_version"],
        f"the receipt and its reasoning section disagree: "
        f"{one['context_manifest_version']!r} vs "
        f"{one['reasoning']['system_preflight_basis']['manifest_version']!r}",
    )
    check(
        "VER03 A VERSION CHANGE MOVES THE DIGEST, A RE-READ DOES NOT",
        again["receipt_digest"] == baseline
        and receipt_module.digest(altered) != baseline,
        f"re-read {again['receipt_digest'][:12]} vs {baseline[:12]}; "
        f"version-bumped {receipt_module.digest(altered)[:12]}",
    )


# ---------------------------------------------------------------------
# PRIV: three audiences, and only one of them is a stranger.
# ---------------------------------------------------------------------


def redaction_checks():
    import json

    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            one = receipt_module.build(cur, REVISION_ONE)
            two = receipt_module.build(cur, REVISION_TWO)

    before = json.dumps(one, sort_keys=True, default=str)

    demo = receipt_module.view(one, receipt_module.DEMO_RECEIPT)
    partner = receipt_module.view(one, receipt_module.PARTNER_RECEIPT)
    engineering = receipt_module.view(
        one, receipt_module.ENGINEERING_TRACE
    )

    after = json.dumps(one, sort_keys=True, default=str)

    demo_text = json.dumps(demo, sort_keys=True, default=str)
    partner_text = json.dumps(partner, sort_keys=True, default=str)
    two_text = json.dumps(
        receipt_module.view(two, receipt_module.PARTNER_RECEIPT),
        sort_keys=True,
        default=str,
    )

    check(
        "PRIV01 THE DEMO VIEW CARRIES NO CLIENT CONTENT",
        "UAE" not in demo_text
        and "residential" not in demo_text
        and "AFH-RECEIPT-ONE" not in demo_text
        and one["correlation_id"] in demo_text,
        "a confirmed fact value, or the case reference, survived into "
        "the view meant for people outside the case",
    )
    check(
        "PRIV02 RENDERING A VIEW DOES NOT ALTER THE RECORD",
        before == after
        and engineering["receipt_digest"] == one["receipt_digest"]
        and demo["receipt_digest"] == one["receipt_digest"],
        "rendering changed the authoritative receipt, so the audit "
        "record would depend on who last read it",
    )
    check(
        "PRIV03 ONE CASE'S SENSITIVE EVIDENCE STAYS OUT OF ANOTHER",
        SECRET_OF_CASE_TWO not in partner_text
        and "UAE" not in two_text,
        "a value belonging to one case appeared in the other's view",
    )
    check(
        "PRIV04 THE PARTNER VIEW KEEPS WHAT THE PARTNER NEEDS",
        "UAE" in partner_text
        and partner["rendered_for"] == receipt_module.PARTNER_RECEIPT,
        "the Partner's own case content was withheld from the Partner, "
        "which is redaction applied to the wrong audience",
    )




# ---------------------------------------------------------------------
# PROV: where a claim came from, and how much it is trusted, are two
# different questions with two different answers.
# ---------------------------------------------------------------------


def provenance_checks():
    from app.domain import provenance as prov_module
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            one = receipt_module.build(cur, REVISION_ONE)

    by_predicate = {
        f["predicate"]: f for f in one["facts"]["established"]
    }
    written = {
        f["predicate"]: f for f in one["facts"]["written_by_this_run"]
    }

    confirmed_by_reviewer = by_predicate.get("country_of_residence")
    proposed_by_agent = written.get("property_type")

    cited = {u["unit_id"]: u for u in one["knowledge"]["cited_units"]}
    unverified = cited.get(UNIT_UNVERIFIED)

    check(
        "PROV01 PROVENANCE AND AUTHORITY VARY INDEPENDENTLY",
        confirmed_by_reviewer is not None
        and confirmed_by_reviewer["provenance"]
        == prov_module.PROVENANCE_HUMAN
        and confirmed_by_reviewer["authority"]
        == prov_module.AUTHORITY_CONFIRMED_FACT
        and unverified is not None
        and unverified["provenance"] == prov_module.PROVENANCE_SOURCE
        and unverified["authority"]
        == prov_module.AUTHORITY_UNVERIFIED_SOURCE,
        f"a human-stated confirmed fact reads "
        f"{confirmed_by_reviewer!r}; a cited unit reads "
        f"{unverified!r} -- two different provenances with two "
        f"different authorities, or the axes are not independent",
    )
    check(
        "PROV02 AN UNVERIFIED SOURCE CANNOT RENDER AS VERIFIED",
        unverified is not None
        and unverified["verification_status"] == "SOURCE_RECORDED"
        and unverified["authority"]
        == prov_module.AUTHORITY_UNVERIFIED_SOURCE
        and unverified["authority"]
        != prov_module.AUTHORITY_VERIFIED_KNOWLEDGE,
        f"a unit recorded SOURCE_RECORDED reports authority "
        f"{unverified['authority'] if unverified else None!r}; being "
        f"cited must not itself confer verification",
    )
    check(
        "PROV03 A SUCCESSFUL EXTRACTION IS NOT A CONFIRMATION",
        proposed_by_agent is not None
        and proposed_by_agent["provenance"] == prov_module.PROVENANCE_MODEL
        and proposed_by_agent["authority"]
        == prov_module.AUTHORITY_PROPOSED_FACT,
        f"an agent-proposed, still-PROPOSED fact reads "
        f"{proposed_by_agent!r}; resolving where a claim came from "
        f"must not leak into how much it is trusted",
    )
    check(
        "PROV04 SYSTEM-DERIVED ACTIVITY IS NOT SOURCE AUTHORITY",
        one["gates"]["asserted_by"] == "APPLICATION"
        and "authority" not in one["gates"]
        and unverified is not None
        and unverified["authority"]
        in prov_module.AUTHORITY_CLASSES,
        f"the deterministic gates read {one['gates']!r}; a cited "
        f"unit's authority must remain the source's own property, not "
        f"something the act of computing a gate result confers",
    )


# ---------------------------------------------------------------------
# TOOLTRUST: a tool call succeeding is not the same claim as trusted.
# ---------------------------------------------------------------------


def tooltrust_checks():
    from app.domain import receipt as receipt_module

    with runtime() as conn:
        with conn.cursor() as cur:
            one = receipt_module.build(cur, REVISION_ONE)

    by_sequence = {t["sequence"]: t for t in one["tools"]}
    consulted_call = by_sequence.get(2)
    failed_call = by_sequence.get(3)

    check(
        "TOOLTRUST01 UNVERIFIED MATERIAL CONSULTED IS REPORTED, NOT USED",
        consulted_call is not None
        and consulted_call["trust_signals"] is not None
        and consulted_call["trust_signals"]["consulted_unverified"] == 2
        and consulted_call["trust_signals"]["units"] == 1,
        f"the call that consulted unverified material reads "
        f"{consulted_call!r}; the receipt must say it found material "
        f"it would not vouch for, not merely that the call succeeded",
    )
    check(
        "TOOLTRUST02 A SUCCESSFUL CALL PRESERVES WHAT IT COULD VOUCH FOR",
        consulted_call["outcome"] == "OK"
        and consulted_call["trust_signals"]["units"] == 1
        and consulted_call["trust_signals"]["applicability_unknown"] == 0,
        f"the usable count must survive alongside the declined one, "
        f"not be reduced to a bare OK: {consulted_call!r}",
    )
    check(
        "TOOLTRUST03 A FAILED CALL CARRIES NO TRUST SIGNAL AT ALL",
        failed_call is not None
        and failed_call["outcome"] == "ERROR"
        and failed_call["trust_signals"] is None,
        f"a call that did not complete reads {failed_call!r}; it found "
        f"nothing, and must not be read as having found nothing "
        f"trustworthy specifically",
    )


# ---------------------------------------------------------------------
# KGHIST: a cited unit's history survives its release being superseded.
# ---------------------------------------------------------------------


def _seed_manifest_for_one():
    """A real manifest for REVISION_ONE's run, citing UNIT_UNVERIFIED."""
    from datetime import date

    from app.domain import context_manifest
    from app.domain.context import CaseContext
    from app.domain.knowledge import KnowledgeUnit

    ctx = CaseContext(
        case_id=CASE_ONE,
        service_id=SERVICE_ID,
        service_key="nri_tax",
        service_name="Cross-border compliance",
        reference="AFH-RECEIPT-ONE",
        enquiry=None,
        material_date=date(2026, 4, 1),
        facts=(),
        confirmed_predicates=frozenset(),
        material_predicates=(),
        missing_material_predicates=(),
        fact_prompts={},
        topic_matches=(),
        knowledge_release_id=SEEDED_RELEASE,
    )

    units = (
        KnowledgeUnit(
            unit_id=UNIT_UNVERIFIED,
            unit_key="receipt-fixture-unit",
            topic="fixture_topic",
            statement="fixture statement, not professional content",
            source_locator="fixture://locator",
            verification_status="SOURCE_RECORDED",
            effective_from=date(2026, 1, 1),
            effective_to=None,
            scope_tags=(),
        ),
        KnowledgeUnit(
            unit_id=UNIT_RETURNED_TWO,
            unit_key="receipt-returned-two",
            topic="fixture_topic",
            statement="fixture returned two, not professional content",
            source_locator="fixture://returned-two",
            verification_status="VERIFIED",
            effective_from=date(2026, 1, 1),
            effective_to=None,
            scope_tags=(),
        ),
        KnowledgeUnit(
            unit_id=UNIT_PREFLIGHT_ONLY,
            unit_key="receipt-preflight-only",
            topic="fixture_topic",
            statement="fixture preflight only, not professional content",
            source_locator="fixture://preflight-only",
            verification_status="VERIFIED",
            effective_from=date(2026, 1, 1),
            effective_to=None,
            scope_tags=(),
        ),
    )

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.run_context_manifests "
                "WHERE run_id = %s",
                (RUN_ONE,),
            )

    with runtime() as conn:
        with conn.cursor() as cur:
            context_manifest.record(
                cur,
                RUN_ONE,
                context_manifest.describe(ctx, None, None, units),
            )


def kghist_checks():
    from app.domain import receipt as receipt_module

    _seed_manifest_for_one()

    with runtime() as conn:
        with conn.cursor() as cur:
            before = receipt_module.build(cur, REVISION_ONE)

    before_unit = {
        u["unit_id"]: u for u in before["knowledge"]["cited_units"]
    }[UNIT_UNVERIFIED]

    # Supersede the release this unit belongs to. No production role
    # can do this -- nothing in the codebase transitions a release out
    # of ACTIVE -- so it is done as the schema owner, the state a
    # future publication pipeline will eventually reach on its own.
    later_release = "97900000-0000-0000-0000-000000000002"

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.knowledge_releases SET status = 'SUPERSEDED' "
                "WHERE id = %s",
                (SEEDED_RELEASE,),
            )
            cur.execute(
                "INSERT INTO app.knowledge_releases (id, service_id, "
                "version, status, notes, activated_at) VALUES "
                "(%s, %s, 999, 'ACTIVE', 'kghist fixture', now()) "
                "ON CONFLICT (id) DO NOTHING",
                (later_release, SERVICE_ID),
            )

    try:
        with runtime() as conn:
            with conn.cursor() as cur:
                after = receipt_module.build(cur, REVISION_ONE)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM app.active_knowledge_units "
                    "WHERE id = %s",
                    (UNIT_UNVERIFIED,),
                )
                still_current = cur.fetchone()[0] > 0

    finally:
        with admin() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM app.knowledge_releases WHERE id = %s",
                    (later_release,),
                )
                cur.execute(
                    "UPDATE app.knowledge_releases SET status = 'ACTIVE' "
                    "WHERE id = %s",
                    (SEEDED_RELEASE,),
                )

    after_unit = {
        u["unit_id"]: u for u in after["knowledge"]["cited_units"]
    }[UNIT_UNVERIFIED]

    check(
        "KGHIST01 SUPERSESSION DOES NOT ERASE THE HISTORICAL CITATION",
        after_unit["verification_status"] == "SOURCE_RECORDED"
        and after_unit["authority"] == "UNVERIFIED_SOURCE",
        f"after superseding the release, the citation reads "
        f"{after_unit!r}; the run's own manifest must still answer "
        f"this regardless of what is currently active",
    )
    check(
        "KGHIST02 SUPERSESSION DOES NOT MOVE THE CANONICAL DIGEST",
        after["receipt_digest"] == before["receipt_digest"],
        f"{before['receipt_digest'][:12]} became "
        f"{after['receipt_digest'][:12]} purely because current "
        f"release membership changed",
    )
    check(
        "KGHIST03 CURRENT RETRIEVAL DOES NOT REUSE THE SUPERSEDED UNIT",
        still_current is False,
        "the superseded unit is still visible through "
        "active_knowledge_units, so current retrieval could reuse it",
    )
    check(
        "KGHIST04 THE RECEIPT NAMES WHICH ANSWER IT GAVE",
        after["knowledge"]["system_support_evidence_as_of"] == "RUN"
        and before["knowledge"]["system_support_evidence_as_of"] == "RUN",
        f"as_of reads {before['knowledge']['system_support_evidence_as_of']!r} / "
        f"{after['knowledge']['system_support_evidence_as_of']!r}",
    )
    from app.domain import receipt as receipt_module

    unresolved_id = "97700000-0000-0000-0000-0000000000ff"

    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.proposal_revisions SET cited_unit_ids = %s "
                "WHERE id = %s",
                (
                    psycopg.types.json.Jsonb(
                        [UNIT_UNVERIFIED, unresolved_id]
                    ),
                    REVISION_ONE,
                ),
            )

    try:
        with runtime() as conn:
            with conn.cursor() as cur:
                with_unresolved = receipt_module.build(cur, REVISION_ONE)
    finally:
        with admin() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app.proposal_revisions SET cited_unit_ids = %s "
                    "WHERE id = %s",
                    (
                        psycopg.types.json.Jsonb([UNIT_UNVERIFIED]),
                        REVISION_ONE,
                    ),
                )

    unresolved_entry = {
        u["unit_id"]: u
        for u in with_unresolved["knowledge"]["cited_units"]
    }[unresolved_id]

    check(
        "KGHIST05 AN UNRESOLVED CITATION DOES NOT MASQUERADE AS VERIFIED",
        unresolved_entry["authority"] == "UNVERIFIED_SOURCE"
        and unresolved_entry["verification_status"] is None,
        f"a unit id cited but never observed by this run's own "
        f"manifest reads {unresolved_entry!r}",
    )


# ---------------------------------------------------------------------
# TOOLOBS: the safe evidence a run's own knowledge retrieval left behind.
# ---------------------------------------------------------------------


def toolobs_checks():
    from app.domain import context_manifest

    with runtime() as conn:
        with conn.cursor() as cur:
            one_manifest = context_manifest.for_run(cur, RUN_ONE)
            two_manifest = context_manifest.for_run(cur, RUN_TWO)

    one_ids = {
        o["unit_id"] for o in one_manifest["deterministic_knowledge_snapshot"]
    }

    check(
        "TOOLOBS01 THE OBSERVED UNIT IS RECORDED WITH ITS SAFE REFERENCE",
        UNIT_UNVERIFIED in one_ids
        and all(
            {"unit_id", "unit_key", "topic", "source_locator",
             "verification_status", "provenance", "authority"}
            <= set(entry)
            for entry in one_manifest["deterministic_knowledge_snapshot"]
        ),
        f"run one's manifest observed {one_ids!r}",
    )
    check(
        "TOOLOBS02 ANOTHER RUN'S OBSERVATION DOES NOT APPEAR HERE",
        UNIT_UNVERIFIED
        not in {
            o["unit_id"]
            for o in (two_manifest or {}).get(
                "deterministic_knowledge_snapshot", []
            )
        },
        "run two's manifest also carries run one's observed unit",
    )
    check(
        "TOOLOBS03 NO STATEMENT TEXT IS DUPLICATED INTO THE OBSERVATION",
        all(
            "statement" not in entry
            for entry in one_manifest["deterministic_knowledge_snapshot"]
        ),
        "the manifest carries the unit's professional text, which is "
        "more than a safe reference",
    )
    check(
        "TOOLOBS04 A LEGACY RUN WITH NO MANIFEST FALLS BACK HONESTLY",
        two_manifest is None
        or "deterministic_knowledge_snapshot" in two_manifest,
        "a run with no manifest produced no clear signal to fall back "
        "on",
    )


# ---------------------------------------------------------------------
# TOOLRET: preflight availability is not a knowledge-tool return.
# ---------------------------------------------------------------------


def toolret_checks():
    """The receipt must preserve the tool's own historical projection."""
    from app.domain import context_manifest
    from app.domain import receipt as receipt_module

    _seed_manifest_for_one()

    with runtime() as conn:
        with conn.cursor() as cur:
            before = receipt_module.build(cur, REVISION_ONE)
            manifest = context_manifest.for_run(cur, RUN_ONE)
            other_run = receipt_module.build(cur, REVISION_TWO)

    calls = before["reasoning"]["actual_tool_trajectory"]["calls"]
    knowledge_calls = [
        call for call in calls if call["tool"] == "get_service_knowledge"
    ]
    completed = next(
        call for call in knowledge_calls if call["outcome"] == "OK"
    )
    failed = next(
        call for call in knowledge_calls if call["outcome"] == "ERROR"
    )
    returned_ids = {
        item["unit_id"] for item in completed["tool_returned_evidence"]
    }
    preflight_ids = {
        item["unit_id"]
        for item in manifest["deterministic_knowledge_snapshot"]
    }
    non_knowledge = next(
        call for call in calls if call["tool"] == "get_case_context"
    )

    check(
        "TOOLRET01 PREFLIGHT AND TOOL RETURNS HAVE THEIR OWN SETS",
        preflight_ids
        == {UNIT_UNVERIFIED, UNIT_RETURNED_TWO, UNIT_PREFLIGHT_ONLY}
        and returned_ids == {UNIT_UNVERIFIED, UNIT_RETURNED_TWO},
        f"preflight={preflight_ids!r}; returned={returned_ids!r}",
    )
    check(
        "TOOLRET02 PREFLIGHT-ONLY KNOWLEDGE IS NOT TOOL-RETURNED",
        UNIT_PREFLIGHT_ONLY not in returned_ids,
        f"preflight-only unit appeared in returned evidence: {returned_ids!r}",
    )
    check(
        "TOOLRET03 ANOTHER TOOL CANNOT CONTAMINATE KNOWLEDGE RETURNS",
        non_knowledge["tool_returned_evidence"] == [],
        f"non-knowledge tool contributed {non_knowledge['tool_returned_evidence']!r}",
    )
    check(
        "TOOLRET04 ANOTHER RUN OR CASE CANNOT CONTAMINATE RETURNS",
        all(
            not call["tool_returned_evidence"]
            for call in other_run["reasoning"]["actual_tool_trajectory"]["calls"]
        ),
        "run two's receipt inherited run one's returned evidence",
    )
    check(
        "TOOLRET07 FAILED KNOWLEDGE CALL HAS NO RETURNED EVIDENCE",
        failed["tool_returned_evidence"] == [],
        f"failed call reads {failed['tool_returned_evidence']!r}",
    )
    check(
        "TOOLRET08 RETURNED EVIDENCE HAS NO STATEMENT TEXT",
        all("statement" not in item for item in completed["tool_returned_evidence"]),
        f"returned evidence duplicated statement text: {completed!r}",
    )

    later_release = "97900000-0000-0000-0000-000000000003"
    with admin() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.knowledge_releases SET status = 'SUPERSEDED' "
                "WHERE id = %s",
                (SEEDED_RELEASE,),
            )
            cur.execute(
                "INSERT INTO app.knowledge_releases (id, service_id, "
                "version, status, notes, activated_at) VALUES "
                "(%s, %s, 1000, 'ACTIVE', 'toolret fixture', now()) "
                "ON CONFLICT (id) DO NOTHING",
                (later_release, SERVICE_ID),
            )
    try:
        with runtime() as conn:
            with conn.cursor() as cur:
                after = receipt_module.build(cur, REVISION_ONE)
                cur.execute(
                    "SELECT count(*) FROM app.active_knowledge_units "
                    "WHERE id = %s",
                    (UNIT_UNVERIFIED,),
                )
                still_current = cur.fetchone()[0] > 0
    finally:
        with admin() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM app.knowledge_releases WHERE id = %s",
                    (later_release,),
                )
                cur.execute(
                    "UPDATE app.knowledge_releases SET status = 'ACTIVE' "
                    "WHERE id = %s",
                    (SEEDED_RELEASE,),
                )

    after_completed = next(
        call
        for call in after["reasoning"]["actual_tool_trajectory"]["calls"]
        if call["tool"] == "get_service_knowledge" and call["outcome"] == "OK"
    )
    check(
        "TOOLRET05 SUPERSESSION DOES NOT REWRITE HISTORICAL TOOL RETURNS",
        after_completed["tool_returned_evidence"]
        == completed["tool_returned_evidence"],
        "historical returned evidence changed after the release was superseded",
    )
    check(
        "TOOLRET06 CURRENT RETRIEVAL EXCLUDES SUPERSEDED KNOWLEDGE",
        still_current is False,
        "the superseded unit remains current retrieval material",
    )


CHECKS = (
    identity_checks,
    provenance_checks,
    version_checks,
    redaction_checks,
    digest_checks,
    failed_run_checks,
    no_hidden_reasoning_checks,
    isolation_checks,
    no_invention_checks,
    tooltrust_checks,
    kghist_checks,
    toolobs_checks,
    toolret_checks,
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
