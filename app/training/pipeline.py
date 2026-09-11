"""One call from an uploaded file to candidates awaiting a professional.

Kept apart from the console so it can be run from a script, tested
without HTTP, and read in one screen. The console calls `ingest` and
renders whatever comes back.

Two connections, deliberately. The reviewer's connection stores the file,
because uploading is a professional act that says this document is fit to
teach from. The runtime connection does the chunking, the extraction and
the verification, because that is machine work and because the runtime
role holds no privilege to decide anything it produces.
"""

import traceback

from app import config
from app.training import documents, extract, store

DEFAULT_BATCHES = 3


def build_agent_factory(stub=None):
    """Return a function that makes a fresh agent for a system prompt."""
    if stub is not None:
        return stub

    from strands import Agent
    from strands.models import BedrockModel

    from app.agent import bedrock

    region = config.get("AWS_REGION", bedrock.DEFAULT_REGION)
    session = bedrock.build_session(region)
    bedrock.enforce_identity(session)

    model_id = config.get("BEDROCK_MODEL_ID", bedrock.DEFAULT_MODEL_ID)

    def factory(system_prompt):
        return Agent(
            model=BedrockModel(
                model_id=model_id,
                boto_session=bedrock.with_region(session, region),
                max_tokens=2048,
                temperature=0.0,
                streaming=False,
            ),
            system_prompt=system_prompt,
        )

    factory.model_id = model_id
    factory.runner = "BEDROCK_STRANDS"

    return factory


def topics_for(conn, service_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT topic FROM app.service_topics WHERE service_id = %s "
            "ORDER BY topic",
            (service_id,),
        )

        return [row[0] for row in cur.fetchall()]


def ingest(reviewer_conn, runtime_conn, service_id, filename, content,
           reviewer_id, factory, max_batches=DEFAULT_BATCHES):
    """Store, chunk, extract, verify. Returns what happened.

    Never raises for a model failure: a document that could not be read
    from is a fact to report, not a crash. It does raise when the file
    itself is unacceptable, because that is the caller's mistake.
    """
    prepared = store.save_document(
        reviewer_conn, service_id, filename, content, reviewer_id
    )

    document_id = prepared["document_id"]
    chunks = prepared["chunks"]

    store.save_chunks(runtime_conn, document_id, chunks)

    topics = topics_for(runtime_conn, service_id)

    run_id = store.record_run(
        runtime_conn,
        document_id,
        getattr(factory, "runner", "DETERMINISTIC_STUB"),
        getattr(factory, "model_id", "stub"),
        extract.EXTRACTOR_PROMPT[:16] + "...",
    )

    report = {
        "document_id": document_id,
        "filename": filename,
        "chunks": len(chunks),
        "pages": prepared["page_count"],
        "proposed": 0,
        "discarded": 0,
        "not_supported": 0,
        "failure": None,
    }

    # Extraction is bounded so one large document cannot spend an
    # unattended afternoon of model calls.
    considered = chunks[: max_batches * extract.MAX_CHUNKS_PER_PASS]

    try:
        proposed, discarded = extract.extract(
            factory, considered, topics, filename
        )
    except Exception as exc:  # noqa: BLE001
        store.finish_run(
            runtime_conn, run_id, len(chunks), 0, 0,
            failure=f"{exc.__class__.__name__}: {exc}"[:400],
        )
        report["failure"] = (
            f"reading this document failed: {exc.__class__.__name__}"
        )
        traceback.print_exc()

        return report

    by_ordinal = {c["ordinal"]: c for c in considered}
    verdicts = {}
    not_supported = 0

    for unit in proposed:
        try:
            verdict, reason = extract.verify(factory, unit, by_ordinal)
        except Exception as exc:  # noqa: BLE001
            verdict, reason = (
                extract.UNCLEAR,
                f"the check could not be run: {exc.__class__.__name__}",
            )

        verdicts[unit["candidate_id"]] = (verdict, reason)

        if verdict == extract.NOT_SUPPORTED:
            not_supported += 1

    store.save_candidates(
        runtime_conn, run_id, document_id, service_id, proposed, verdicts
    )
    store.finish_run(
        runtime_conn, run_id, len(chunks), len(proposed), not_supported
    )

    report.update(
        {
            "proposed": len(proposed),
            "discarded": len(discarded),
            "not_supported": not_supported,
        }
    )

    return report
