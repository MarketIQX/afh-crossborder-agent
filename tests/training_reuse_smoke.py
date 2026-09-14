"""Direct proof that governed teaching becomes reusable knowledge.

This is deliberately one semantic scenario, not another architecture layer:
a reviewer-owned source exists first, two independent candidates cite it, one
is accepted and one rejected, and a fresh runtime connection can retrieve only
the accepted lesson from the active release.

Run with --mutation to inject the rejected lesson into the active
release. The same assertions must then fail, proving this gate discriminates
the property it claims to test.
"""

import sys
from datetime import date
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.db import testguard  # noqa: E402
from app.domain import knowledge  # noqa: E402
from app.training import documents, extract, store  # noqa: E402

SETTINGS = config.database_settings()
SERVICE_KEY = "nri_india_tax_filing"
TOPIC = "tax_residency"
REVIEWER_ID = "9e100000-0000-0000-0000-000000000001"
ACCEPTED_CANDIDATE = "9e200000-0000-0000-0000-000000000001"
REJECTED_CANDIDATE = "9e200000-0000-0000-0000-000000000002"
MUTATION_UNIT = "9e300000-0000-0000-0000-000000000001"
DOCUMENT_NAME = "training-reuse-fixture.txt"

ACCEPTED_STATEMENT = (
    "TEACHING_REUSE_ACCEPTED_FIXTURE is the synthetic lesson that may be "
    "reused after a professional accepts it in this disposable test."
)
REJECTED_STATEMENT = (
    "TEACHING_REUSE_REJECTED_FIXTURE is approved for runtime reuse even "
    "though the professional rejected it."
)
SOURCE_TEXT = (
    "This is synthetic verification text used only in the disposable test "
    "database. The TEACHING_REUSE_ACCEPTED_FIXTURE sentence is intended to "
    "be accepted after review. The TEACHING_REUSE_REJECTED_FIXTURE proposal "
    "is deliberately an unsafe overgeneralisation and must remain rejected. "
    "Both proposed lessons cite this exact same stored passage so the test "
    "can distinguish shared provenance from independent professional review."
)


def admin_conn():
    return psycopg.connect(**SETTINGS.admin_kwargs())


def app_conn():
    return psycopg.connect(**SETTINGS.app_kwargs(), autocommit=True)


def reviewer_conn():
    return psycopg.connect(**SETTINGS.reviewer_kwargs(), autocommit=True)


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail or 'condition was false'}")
    print(f"PASS  {label}")


def fixture_service_id(cur):
    cur.execute(
        "SELECT id::text FROM app.services WHERE service_key = %s",
        (SERVICE_KEY,),
    )
    row = cur.fetchone()
    if row is None:
        raise AssertionError(f"seed service {SERVICE_KEY!r} is missing")
    return row[0]


def cleanup():
    """Delete only rows attributable to this dedicated fixture reviewer."""
    with admin_conn() as conn:
        testguard.assert_disposable(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.source_documents WHERE uploaded_by = %s",
                (REVIEWER_ID,),
            )
            cur.execute(
                "DELETE FROM app.knowledge_units WHERE verified_by = %s",
                (REVIEWER_ID,),
            )
            cur.execute(
                "DELETE FROM app.reviewers WHERE id = %s",
                (REVIEWER_ID,),
            )
        conn.commit()


def seed_reviewer():
    with admin_conn() as conn:
        testguard.assert_disposable(conn)
        with conn.cursor() as cur:
            service_id = fixture_service_id(cur)
            cur.execute(
                """
                INSERT INTO app.reviewers (
                    id, email, display_name, is_active,
                    professional_qualification, may_verify_knowledge
                ) VALUES (%s, %s, %s, true, %s, true)
                """,
                (
                    REVIEWER_ID,
                    "training-reuse-reviewer@example.test",
                    "Training Reuse Reviewer",
                    "Synthetic test professional",
                ),
            )
        conn.commit()
    return service_id


def save_source_before_candidates(service_id):
    content = SOURCE_TEXT.encode("utf-8")
    with reviewer_conn() as conn:
        prepared = store.save_document(
            conn, service_id, DOCUMENT_NAME, content, REVIEWER_ID
        )
    with app_conn() as conn:
        store.save_chunks(conn, prepared["document_id"], prepared["chunks"])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM app.knowledge_candidates "
                "WHERE document_id = %s",
                (prepared["document_id"],),
            )
            count = cur.fetchone()[0]

    check(
        "source exists before any candidate",
        count == 0,
        f"found {count} candidate(s) before extraction",
    )
    return prepared


def propose_two_candidates(service_id, prepared):
    proposed = [
        {
            "candidate_id": ACCEPTED_CANDIDATE,
            "topic": TOPIC,
            "statement": ACCEPTED_STATEMENT,
            "cited_ordinals": [1],
        },
        {
            "candidate_id": REJECTED_CANDIDATE,
            "topic": TOPIC,
            "statement": REJECTED_STATEMENT,
            "cited_ordinals": [1],
        },
    ]
    verdicts = {
        ACCEPTED_CANDIDATE: (extract.SUPPORTED, "fixture support check"),
        REJECTED_CANDIDATE: (
            extract.NOT_SUPPORTED,
            "fixture deliberately overclaims the shared passage",
        ),
    }
    with app_conn() as conn:
        run_id = store.record_run(
            conn,
            prepared["document_id"],
            "DETERMINISTIC_STUB",
            "training-reuse-fixture",
            documents.sha256("training-reuse-fixture-prompt"),
        )
        store.save_candidates(
            conn,
            run_id,
            prepared["document_id"],
            service_id,
            proposed,
            verdicts,
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, document_id::text, supporting_chunk_ids "
                "FROM app.knowledge_candidates WHERE id = ANY(%s) "
                "ORDER BY id",
                ([ACCEPTED_CANDIDATE, REJECTED_CANDIDATE],),
            )
            rows = cur.fetchall()

    check("two candidates exist independently", len(rows) == 2)
    check(
        "both candidates reference the same stored source",
        len({row[1] for row in rows}) == 1
        and rows[0][1] == prepared["document_id"],
    )
    check(
        "both candidates independently cite passage 1",
        all(list(row[2]) == [1] for row in rows),
    )


def decide_candidates(service_id):
    with reviewer_conn() as conn:
        published = store.accept_and_publish(
            conn,
            ACCEPTED_CANDIDATE,
            REVIEWER_ID,
            service_id,
            "training-reuse-fixture: shared passage 1",
            "accepted by dedicated fixture reviewer",
        )
        store.reject(
            conn,
            REJECTED_CANDIDATE,
            REVIEWER_ID,
            "rejected: the candidate overclaims what the shared passage says",
        )

    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, review_state, reviewed_by::text,
                       published_unit_id::text, document_id::text
                FROM app.knowledge_candidates
                WHERE id = ANY(%s)
                ORDER BY id
                """,
                ([ACCEPTED_CANDIDATE, REJECTED_CANDIDATE],),
            )
            rows = {row[0]: row[1:] for row in cur.fetchall()}

            cur.execute(
                """
                SELECT from_candidate_id::text, verified_by::text,
                       source_locator, captured_passage, passage_digest
                FROM app.knowledge_units WHERE id = %s
                """,
                (published["unit_id"],),
            )
            unit = cur.fetchone()

    accepted = rows[ACCEPTED_CANDIDATE]
    rejected = rows[REJECTED_CANDIDATE]
    check("accepted candidate is attributed", accepted[0] == "ACCEPTED" and accepted[1] == REVIEWER_ID)
    check("rejected candidate is attributed", rejected[0] == "REJECTED" and rejected[1] == REVIEWER_ID)
    check("rejected candidate has no published unit", rejected[2] is None)
    check("accepted unit points back to accepted candidate", unit[0] == ACCEPTED_CANDIDATE)
    check("accepted unit is signed by the reviewer", unit[1] == REVIEWER_ID)
    check("published provenance names the fixture source", "training-reuse-fixture" in unit[2])
    check("captured evidence digest matches the stored passage", unit[4] == documents.sha256(unit[3]))
    return published


def inject_rejected_mutation(service_id, prepared):
    if "--mutation" not in sys.argv[1:]:
        return

    print("MUTATION ACTIVE: injecting rejected lesson into active release")
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM app.knowledge_releases "
                "WHERE service_id = %s AND status = 'ACTIVE'",
                (service_id,),
            )
            release_id = cur.fetchone()[0]
            cur.execute(
                "SELECT passage FROM app.document_chunks "
                "WHERE document_id = %s AND ordinal = 1",
                (prepared["document_id"],),
            )
            passage = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO app.knowledge_units (
                    id, release_id, unit_key, topic, statement,
                    source_locator, verification_status, effective_from,
                    captured_passage, passage_digest, captured_at,
                    verified_by, verified_at, from_candidate_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'PROFESSIONALLY_VERIFIED',
                    current_date, %s, %s, now(), %s, now(), %s
                )
                """,
                (
                    MUTATION_UNIT,
                    release_id,
                    "training_reuse_rejected_mutation",
                    TOPIC,
                    REJECTED_STATEMENT,
                    "training-reuse-fixture: unsafe mutation",
                    passage,
                    documents.sha256(passage),
                    REVIEWER_ID,
                    REJECTED_CANDIDATE,
                ),
            )
        conn.commit()


def prove_fresh_runtime_reuse(service_id):
    """Open a brand-new runtime DB session after the review decisions."""
    with app_conn() as conn:
        with conn.cursor() as cur:
            release_id = knowledge.active_release_id(cur, service_id)
            units = knowledge.retrieve(cur, release_id, [TOPIC], date.today())

    statements = {unit.statement for unit in units}
    accepted_units = [u for u in units if u.statement == ACCEPTED_STATEMENT]
    check(
        "fresh runtime retrieves the accepted lesson",
        len(accepted_units) == 1,
        f"accepted lesson count was {len(accepted_units)}",
    )
    check(
        "fresh runtime cannot retrieve the rejected lesson",
        REJECTED_STATEMENT not in statements,
        "rejected lesson leaked into active runtime knowledge",
    )
    check(
        "retrieved accepted lesson remains professionally verified",
        accepted_units[0].professionally_verified,
    )


def main():
    with admin_conn() as conn:
        token = testguard.assert_disposable(conn)
    testguard.acquire_single_run_lock()
    print(f"TEST TARGET: disposable, token {token}")

    cleanup()
    try:
        service_id = seed_reviewer()
        prepared = save_source_before_candidates(service_id)
        propose_two_candidates(service_id, prepared)
        decide_candidates(service_id)
        inject_rejected_mutation(service_id, prepared)
        prove_fresh_runtime_reuse(service_id)
        print("TRAINING REUSE GATE: PASS")
        return 0
    finally:
        cleanup()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # keep one-line failure visible in run_all
        print(f"TRAINING REUSE GATE: FAIL - {exc}")
        raise
