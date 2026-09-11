BEGIN;

-- M6: teaching Anika from documents.
--
-- The knowledge base has seven units, none professionally verified, so
-- the agent cannot answer anything and correctly says so. Filling it by
-- typing guidance into a form would work and would be worthless: a unit
-- claiming SOURCE_VERIFIED needs a captured passage, its digest and a
-- timestamp, and a person typing into a box can assert all three about
-- text that exists nowhere.
--
-- So knowledge comes from documents the practice already has, and the
-- passage is never written by anyone. The document is stored whole with
-- its digest, split into chunks that are stored verbatim, and a
-- candidate unit cites chunks *by id*. The model proposes which chunk
-- supports a claim; the server reads that chunk out of this table. A
-- fabricated quotation is therefore not unlikely, it is unavailable.
--
-- The model still writes the guidance, in its own words, and that is
-- the part a human must read. Nothing here reaches the agent until a
-- professional has seen the guidance and the passage side by side and
-- signed it off.

CREATE TABLE app.source_documents (
    id uuid PRIMARY KEY,

    service_id uuid NOT NULL
        REFERENCES app.services(id),

    filename text NOT NULL,
    media_type text NOT NULL,
    byte_count bigint NOT NULL,

    -- The document itself, so a passage can always be checked against
    -- the thing it came from rather than against a copy of a copy.
    content bytea NOT NULL,
    content_sha256 text NOT NULL,

    uploaded_by uuid NOT NULL
        REFERENCES app.reviewers(id),
    uploaded_at timestamptz NOT NULL DEFAULT now(),

    page_count integer,
    extracted_characters integer,

    CONSTRAINT source_documents_filename_nonempty
        CHECK (btrim(filename) <> ''),

    CONSTRAINT source_documents_media_known
        CHECK (media_type IN ('application/pdf',
                              'application/vnd.openxmlformats-officedocument'
                              || '.wordprocessingml.document',
                              'text/plain',
                              'text/markdown')),

    CONSTRAINT source_documents_digest_shaped
        CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),

    CONSTRAINT source_documents_has_bytes
        CHECK (byte_count > 0)
);

-- The same file uploaded twice is the same knowledge, not new knowledge.
CREATE UNIQUE INDEX source_documents_unique_content
    ON app.source_documents (service_id, content_sha256);


CREATE TABLE app.document_chunks (
    id uuid PRIMARY KEY,

    document_id uuid NOT NULL
        REFERENCES app.source_documents(id) ON DELETE CASCADE,

    ordinal integer NOT NULL,

    -- Where in the document, so a reviewer can find it by eye.
    page_from integer,
    page_to integer,

    -- Verbatim. Never written by a model, never edited.
    passage text NOT NULL,
    passage_sha256 text NOT NULL,

    -- Anthropic's contextual retrieval: a sentence situating this chunk
    -- inside the whole document, written at ingest and indexed with the
    -- passage. Retrieval on the passage alone loses the subject of a
    -- paragraph that says "this test applies instead".
    context_note text NOT NULL DEFAULT '',

    searchable tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(context_note, '')), 'A')
            || setweight(to_tsvector('english', passage), 'B')
        ) STORED,

    CONSTRAINT document_chunks_passage_nonempty
        CHECK (btrim(passage) <> ''),

    CONSTRAINT document_chunks_digest_shaped
        CHECK (passage_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE UNIQUE INDEX document_chunks_ordinal
    ON app.document_chunks (document_id, ordinal);

CREATE INDEX document_chunks_search
    ON app.document_chunks USING gin (searchable);


CREATE TABLE app.extraction_runs (
    id uuid PRIMARY KEY,

    document_id uuid NOT NULL
        REFERENCES app.source_documents(id) ON DELETE CASCADE,

    runner text NOT NULL,
    model_id text NOT NULL,
    prompt_digest text NOT NULL,

    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,

    chunk_count integer NOT NULL DEFAULT 0,
    candidate_count integer NOT NULL DEFAULT 0,
    rejected_by_verifier integer NOT NULL DEFAULT 0,

    failure_reason text,

    CONSTRAINT extraction_runs_runner_known
        CHECK (runner IN ('BEDROCK_STRANDS', 'DETERMINISTIC_STUB'))
);


CREATE TABLE app.knowledge_candidates (
    id uuid PRIMARY KEY,

    extraction_run_id uuid NOT NULL
        REFERENCES app.extraction_runs(id) ON DELETE CASCADE,

    document_id uuid NOT NULL
        REFERENCES app.source_documents(id) ON DELETE CASCADE,

    service_id uuid NOT NULL
        REFERENCES app.services(id),

    topic text NOT NULL,

    -- The model's words. The part a human must actually read.
    guidance text NOT NULL,

    -- Chunk ids, not text. The passage is fetched from document_chunks.
    supporting_chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- A second agent reads the guidance against its cited passages and
    -- says whether the passages actually support it.
    verifier_verdict text NOT NULL DEFAULT 'UNCHECKED',
    verifier_note text NOT NULL DEFAULT '',

    -- The professional's decision. Nothing reaches the agent without it.
    review_state text NOT NULL DEFAULT 'PENDING',
    reviewed_by uuid REFERENCES app.reviewers(id),
    reviewed_at timestamptz,
    reviewer_note text NOT NULL DEFAULT '',

    -- Set when this candidate becomes real knowledge.
    published_unit_id uuid REFERENCES app.knowledge_units(id),

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT knowledge_candidates_guidance_nonempty
        CHECK (btrim(guidance) <> ''),

    CONSTRAINT knowledge_candidates_cites_something
        CHECK (jsonb_array_length(supporting_chunk_ids) > 0),

    CONSTRAINT knowledge_candidates_verdict_known
        CHECK (verifier_verdict IN
               ('UNCHECKED', 'SUPPORTED', 'NOT_SUPPORTED', 'UNCLEAR')),

    CONSTRAINT knowledge_candidates_review_state_known
        CHECK (review_state IN ('PENDING', 'ACCEPTED', 'REJECTED')),

    -- A decision must say who made it and when. An accepted candidate
    -- becomes guidance a client relies on; anonymous acceptance is not
    -- acceptance.
    CONSTRAINT knowledge_candidates_decision_is_attributed
        CHECK (
            (review_state = 'PENDING')
            = (reviewed_by IS NULL AND reviewed_at IS NULL)
        ),

    -- Only an accepted candidate may point at a published unit.
    CONSTRAINT knowledge_candidates_published_only_when_accepted
        CHECK (
            published_unit_id IS NULL OR review_state = 'ACCEPTED'
        )
);

CREATE INDEX knowledge_candidates_pending
    ON app.knowledge_candidates (created_at DESC)
    WHERE review_state = 'PENDING';

CREATE INDEX knowledge_candidates_document
    ON app.knowledge_candidates (document_id);


-- Grants.
--
-- Uploading is a professional act: it says this document is fit to teach
-- from. The reviewer does it.
GRANT SELECT, INSERT ON app.source_documents TO agents_reviewer;
GRANT SELECT ON app.source_documents TO agents_app;

-- Chunking and extraction are machine work.
GRANT SELECT, INSERT ON app.document_chunks TO agents_app;
GRANT SELECT ON app.document_chunks TO agents_reviewer;

GRANT SELECT, INSERT, UPDATE ON app.extraction_runs TO agents_app;
GRANT SELECT ON app.extraction_runs TO agents_reviewer;

-- The runtime may propose candidates and record what the verifier said.
-- It may not decide them: no grant on the review columns.
GRANT SELECT, INSERT ON app.knowledge_candidates TO agents_app;
GRANT UPDATE (verifier_verdict, verifier_note)
    ON app.knowledge_candidates TO agents_app;

-- The reviewer decides, and only the reviewer.
GRANT SELECT ON app.knowledge_candidates TO agents_reviewer;
GRANT UPDATE (review_state, reviewed_by, reviewed_at, reviewer_note,
              published_unit_id)
    ON app.knowledge_candidates TO agents_reviewer;

COMMIT;
