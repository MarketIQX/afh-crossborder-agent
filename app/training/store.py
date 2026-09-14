"""Persist documents, run extraction over them, and publish what a
professional signs off.

The boundaries here are the whole point, so they are stated rather than
implied.

Uploading is a reviewer act: it says this document is fit to teach from.
Chunking and extraction are runtime work, because they are machine work.
Accepting a candidate and publishing it as knowledge are reviewer acts,
and no runtime connection appears anywhere near them.

Publication reads the passage out of `document_chunks` and writes it
into the unit's `captured_passage`. Nobody types it and no model
produces it, which is what makes `SOURCE_VERIFIED` mean something.
"""

import json
import uuid

from psycopg.types.json import Jsonb

from app.domain import applicability
from app.training import documents, extract


class TrainingRefused(Exception):
    """This document or candidate cannot be accepted."""


# ---- upload -------------------------------------------------------


def save_document(conn, service_id, filename, content, reviewer_id):
    """Store the file and its passages. Reviewer connection for the file,
    runtime for the chunks, because chunking is machine work.
    """
    prepared = documents.prepare(content, filename)

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, filename FROM app.source_documents "
                "WHERE service_id = %s AND content_sha256 = %s",
                (service_id, prepared["content_sha256"]),
            )
            existing = cur.fetchone()

            if existing:
                raise TrainingRefused(
                    f"this file is already here as {existing[1]!r}. The "
                    f"same document is the same knowledge, not new "
                    f"knowledge."
                )

            cur.execute(
                """
                INSERT INTO app.source_documents (
                    id, service_id, filename, media_type, byte_count,
                    content, content_sha256, uploaded_by,
                    page_count, extracted_characters
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    prepared["document_id"],
                    service_id,
                    filename,
                    prepared["media_type"],
                    prepared["byte_count"],
                    content,
                    prepared["content_sha256"],
                    reviewer_id,
                    prepared["page_count"],
                    prepared["extracted_characters"],
                ),
            )

    return prepared


def save_chunks(conn, document_id, chunks):
    """Runtime connection. Passages stored verbatim, never edited."""
    with conn.transaction():
        with conn.cursor() as cur:
            for chunk in chunks:
                cur.execute(
                    """
                    INSERT INTO app.document_chunks (
                        id, document_id, ordinal, page_from, page_to,
                        passage, passage_sha256, context_note
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        document_id,
                        chunk["ordinal"],
                        chunk["page_from"],
                        chunk["page_to"],
                        chunk["passage"],
                        chunk["passage_sha256"],
                        chunk.get("context_note", ""),
                    ),
                )


# ---- reading ------------------------------------------------------


def documents_for(conn, service_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                d.id::text,
                d.filename,
                d.media_type,
                d.byte_count,
                d.page_count,
                d.uploaded_at,
                r.display_name,
                (SELECT count(*) FROM app.document_chunks c
                  WHERE c.document_id = d.id),
                (SELECT count(*) FROM app.knowledge_candidates k
                  WHERE k.document_id = d.id),
                (SELECT count(*) FROM app.knowledge_candidates k
                  WHERE k.document_id = d.id AND k.review_state = 'PENDING'),
                (SELECT count(*) FROM app.knowledge_candidates k
                  WHERE k.document_id = d.id AND k.review_state = 'ACCEPTED')
            FROM app.source_documents d
            JOIN app.reviewers r ON r.id = d.uploaded_by
            WHERE d.service_id = %s
            ORDER BY d.uploaded_at DESC
            """,
            (service_id,),
        )

        return [
            {
                "document_id": row[0],
                "filename": row[1],
                "media_type": row[2],
                "byte_count": row[3],
                "page_count": row[4],
                "uploaded_at": row[5],
                "uploaded_by": row[6],
                "chunks": row[7],
                "candidates": row[8],
                "pending": row[9],
                "accepted": row[10],
            }
            for row in cur.fetchall()
        ]


def chunks_for(conn, document_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, ordinal, page_from, page_to, passage "
            "FROM app.document_chunks WHERE document_id = %s "
            "ORDER BY ordinal",
            (document_id,),
        )

        return [
            {
                "chunk_id": row[0],
                "ordinal": row[1],
                "page_from": row[2],
                "page_to": row[3],
                "passage": row[4],
            }
            for row in cur.fetchall()
        ]


def candidates_for(conn, document_id=None, state="PENDING"):
    """Candidates with the real text of every passage they cite.

    The passage is joined in here rather than trusted from anywhere
    else, so what a reviewer reads is what the document says.
    """
    clauses = ["k.review_state = %s"]
    params = [state]

    if document_id:
        clauses.append("k.document_id = %s")
        params.append(document_id)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                k.id::text, k.topic, k.guidance, k.supporting_chunk_ids,
                k.verifier_verdict, k.verifier_note, k.review_state,
                k.reviewer_note, d.filename, k.document_id::text,
                k.created_at
            FROM app.knowledge_candidates k
            JOIN app.source_documents d ON d.id = k.document_id
            WHERE {' AND '.join(clauses)}
            ORDER BY k.created_at DESC
            """,
            params,
        )
        rows = cur.fetchall()

        out = []

        for row in rows:
            ordinals = list(row[3] or [])

            cur.execute(
                "SELECT ordinal, page_from, page_to, passage "
                "FROM app.document_chunks "
                "WHERE document_id = %s AND ordinal = ANY(%s) "
                "ORDER BY ordinal",
                (row[9], ordinals),
            )
            passages = [
                {
                    "ordinal": p[0],
                    "page_from": p[1],
                    "page_to": p[2],
                    "passage": p[3],
                }
                for p in cur.fetchall()
            ]

            out.append(
                {
                    "candidate_id": row[0],
                    "topic": row[1],
                    "guidance": row[2],
                    "cited_ordinals": ordinals,
                    "verifier_verdict": row[4],
                    "verifier_note": row[5],
                    "review_state": row[6],
                    "reviewer_note": row[7],
                    "filename": row[8],
                    "document_id": row[9],
                    "created_at": row[10],
                    "passages": passages,
                }
            )

        return out


# ---- extraction ---------------------------------------------------


def record_run(conn, document_id, runner, model_id, prompt_digest):
    run_id = str(uuid.uuid4())

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app.extraction_runs "
            "(id, document_id, runner, model_id, prompt_digest) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run_id, document_id, runner, model_id, prompt_digest),
        )

    return run_id


def save_candidates(conn, run_id, document_id, service_id, proposed,
                    verdicts):
    """Runtime connection. Proposes only; cannot decide."""
    with conn.transaction():
        with conn.cursor() as cur:
            for unit in proposed:
                verdict, note = verdicts.get(
                    unit["candidate_id"], (extract.UNCHECKED, "")
                )

                cur.execute(
                    """
                    INSERT INTO app.knowledge_candidates (
                        id, extraction_run_id, document_id, service_id,
                        topic, guidance, supporting_chunk_ids,
                        verifier_verdict, verifier_note
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        unit["candidate_id"],
                        run_id,
                        document_id,
                        service_id,
                        unit["topic"],
                        unit["statement"],
                        Jsonb(unit["cited_ordinals"]),
                        verdict,
                        note[:900],
                    ),
                )


def finish_run(conn, run_id, chunk_count, candidate_count, rejected,
               failure=None):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app.extraction_runs SET ended_at = now(), "
            "chunk_count = %s, candidate_count = %s, "
            "rejected_by_verifier = %s, failure_reason = %s "
            "WHERE id = %s",
            (chunk_count, candidate_count, rejected, failure, run_id),
        )


# ---- the professional's decision -----------------------------------


def reject(conn, candidate_id, reviewer_id, note):
    """Reviewer connection. A rejection needs a reason to be useful."""
    if not (note or "").strip():
        raise TrainingRefused(
            "say what is wrong with it. A rejection with no reason "
            "teaches nobody anything, and the reason is the most useful "
            "thing this produces."
        )

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app.knowledge_candidates SET review_state = "
                "'REJECTED', reviewed_by = %s, reviewed_at = now(), "
                "reviewer_note = %s WHERE id = %s AND review_state = "
                "'PENDING'",
                (reviewer_id, note.strip()[:900], candidate_id),
            )

            if cur.rowcount != 1:
                raise TrainingRefused(
                    "that candidate is not pending; it has already been "
                    "decided"
                )



def applicability_values():
    """The canonical value per dimension, taken from one source.

    applicability.DIMENSIONS maps a client's wording onto a canonical
    value; the canonical values are what the column accepts, and the
    CHECK constraints from migration 019 police the same set in the
    database. Deriving them here keeps a third copy from existing.
    """
    return {
        dimension["name"]: sorted(set(dimension["values"].values()))
        for dimension in applicability.DIMENSIONS
    }


def set_applicability(conn, unit_id, residency=None, citizenship=None):
    """Record which class of person a signed rule is about.

    Reviewer connection. Migration 022 grants UPDATE on exactly these
    two columns, so this cannot alter what the rule says, the evidence
    under it, or whose name is on it.

    Pass None to leave a dimension alone and the empty string to clear
    it. Clearing means "this rule does not narrow itself here", which is
    the default and is not the same as an unknown value.

    The database refuses an invalid value regardless of what this
    function does; the check here exists so a reviewer gets a useful
    message rather than a constraint violation.
    """
    allowed = applicability_values()
    updates = {}

    for name, column, raw in (
        ("residency", "applies_to_residency", residency),
        ("citizenship", "applies_to_citizenship", citizenship),
    ):
        if raw is None:
            continue

        value = str(raw).strip().upper()

        if not value:
            updates[column] = None
            continue

        if value not in allowed[name]:
            raise TrainingRefused(
                f"{value!r} is not a {name} class this vocabulary "
                f"knows. Accepted: {', '.join(allowed[name])}. Nothing "
                f"was changed."
            )

        updates[column] = value

    if not updates:
        raise TrainingRefused(
            "no dimension was given, so there is nothing to record"
        )

    assignments = ", ".join(f"{col} = %s" for col in updates)

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE app.knowledge_units SET {assignments} "
                f"WHERE id = %s",
                (*updates.values(), unit_id),
            )

            if cur.rowcount != 1:
                raise TrainingRefused(
                    f"no knowledge unit {unit_id}, so nothing was "
                    f"recorded"
                )

            cur.execute(
                "SELECT topic, applies_to_residency, "
                "applies_to_citizenship FROM app.knowledge_units "
                "WHERE id = %s",
                (unit_id,),
            )
            topic, res, cit = cur.fetchone()

    return {
        "unit_id": unit_id,
        "topic": topic,
        "applies_to_residency": res,
        "applies_to_citizenship": cit,
    }

def accept_and_publish(conn, candidate_id, reviewer_id, service_id,
                       source_locator, note=""):
    """Turn a candidate into knowledge the agent may actually use.

    Reviewer connection throughout. The captured passage is read out of
    `document_chunks` here: nobody types it and no model writes it,
    which is the only reason SOURCE_VERIFIED can mean anything.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic, guidance, supporting_chunk_ids, "
                "document_id::text, review_state "
                "FROM app.knowledge_candidates WHERE id = %s",
                (candidate_id,),
            )
            row = cur.fetchone()

            if row is None:
                raise TrainingRefused(f"no candidate {candidate_id}")

            topic, guidance, ordinals, document_id, state = row

            if state != "PENDING":
                raise TrainingRefused(
                    f"this candidate is already {state.lower()}"
                )

            cur.execute(
                "SELECT string_agg(passage, E'\\n\\n' ORDER BY ordinal) "
                "FROM app.document_chunks "
                "WHERE document_id = %s AND ordinal = ANY(%s)",
                (document_id, list(ordinals or [])),
            )
            passage = (cur.fetchone() or [None])[0]

            if not passage:
                raise TrainingRefused(
                    "the cited passages could not be found, so there is "
                    "nothing to capture as evidence"
                )

            cur.execute(
                "SELECT filename FROM app.source_documents WHERE id = %s",
                (document_id,),
            )
            filename = (cur.fetchone() or ["unknown"])[0]

            release_id = _active_or_new_release(cur, service_id, reviewer_id)

            unit_id = str(uuid.uuid4())
            locator = (source_locator or "").strip() or (
                f"{filename}, passages {sorted(ordinals or [])}"
            )

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
                    unit_id,
                    release_id,
                    f"{topic}_{unit_id[:8]}",
                    topic,
                    guidance,
                    locator,
                    passage,
                    documents.sha256(passage),
                    reviewer_id,
                    candidate_id,
                ),
            )

            cur.execute(
                "UPDATE app.knowledge_candidates SET review_state = "
                "'ACCEPTED', reviewed_by = %s, reviewed_at = now(), "
                "reviewer_note = %s, published_unit_id = %s "
                "WHERE id = %s AND review_state = 'PENDING'",
                (reviewer_id, (note or "").strip()[:900], unit_id,
                 candidate_id),
            )

            if cur.rowcount != 1:
                raise TrainingRefused("the candidate changed while deciding")

    return {"unit_id": unit_id, "release_id": release_id}


def _active_or_new_release(cur, service_id, reviewer_id):
    """The release new knowledge goes into, creating one if needed."""
    cur.execute(
        "SELECT id::text FROM app.knowledge_releases "
        "WHERE service_id = %s AND status = 'ACTIVE'",
        (service_id,),
    )
    row = cur.fetchone()

    if row:
        return row[0]

    cur.execute(
        "SELECT coalesce(max(version), 0) + 1 "
        "FROM app.knowledge_releases WHERE service_id = %s",
        (service_id,),
    )
    version = cur.fetchone()[0]

    release_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO app.knowledge_releases "
        "(id, service_id, version, status, notes, activated_at) "
        "VALUES (%s, %s, %s, 'ACTIVE', %s, now())",
        (
            release_id,
            service_id,
            version,
            "Published from documents reviewed in the console.",
        ),
    )

    return release_id


# ---- what has been learned ------------------------------------------


def learning_summary(conn, service_id):
    """The curve: what Nicole knows, and whether it is usable."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM app.source_documents
                  WHERE service_id = %(s)s),
                (SELECT count(*) FROM app.knowledge_candidates
                  WHERE service_id = %(s)s),
                (SELECT count(*) FROM app.knowledge_candidates
                  WHERE service_id = %(s)s AND review_state = 'ACCEPTED'),
                (SELECT count(*) FROM app.knowledge_candidates
                  WHERE service_id = %(s)s AND review_state = 'REJECTED'),
                (SELECT count(*) FROM app.knowledge_candidates
                  WHERE service_id = %(s)s AND review_state = 'PENDING'),
                (SELECT count(*) FROM app.service_topics
                  WHERE service_id = %(s)s)
            """,
            {"s": service_id},
        )
        docs, proposed, accepted, rejected, pending, topics = cur.fetchone()

        cur.execute(
            """
            SELECT u.topic, count(*)
            FROM app.knowledge_units u
            JOIN app.knowledge_releases r ON r.id = u.release_id
            WHERE r.service_id = %s
              AND u.verification_status = 'PROFESSIONALLY_VERIFIED'
            GROUP BY u.topic
            ORDER BY u.topic
            """,
            (service_id,),
        )
        covered = {row[0]: row[1] for row in cur.fetchall()}

        cur.execute(
            "SELECT topic FROM app.service_topics WHERE service_id = %s "
            "ORDER BY topic",
            (service_id,),
        )
        all_topics = [row[0] for row in cur.fetchall()]

    return {
        "documents": docs,
        "proposed": proposed,
        "accepted": accepted,
        "rejected": rejected,
        "pending": pending,
        "topic_count": topics,
        "topics": all_topics,
        "covered": covered,
        "covered_count": len(covered),
        "acceptance_rate": (
            round(100 * accepted / (accepted + rejected))
            if (accepted + rejected)
            else None
        ),
    }


def taught_units(conn, service_id, limit=50):
    """Every unit a professional signed, newest first, with its source."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                u.topic, u.statement, u.source_locator, u.verified_at,
                p.display_name, u.captured_passage,
                u.verification_status
            FROM app.knowledge_units u
            JOIN app.knowledge_releases r ON r.id = u.release_id
            LEFT JOIN app.reviewers p ON p.id = u.verified_by
            WHERE r.service_id = %s
            ORDER BY u.verified_at DESC NULLS LAST, u.created_at DESC
            LIMIT %s
            """,
            (service_id, limit),
        )

        return [
            {
                "topic": row[0],
                "statement": row[1],
                "source_locator": row[2],
                "verified_at": row[3],
                "verified_by": row[4],
                "captured_passage": row[5],
                "verification_status": row[6],
            }
            for row in cur.fetchall()
        ]
