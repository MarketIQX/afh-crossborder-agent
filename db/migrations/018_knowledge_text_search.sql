BEGIN;

-- M8: let retrieval find a rule the client did not name.
--
-- A real enquiry arrived asking, in substance, whether an Indian
-- national working in a zero-tax country still owes tax in India. Five
-- professionally verified units covering exactly that had just been
-- signed off. The agent retrieved none of them and correctly refused to
-- answer, because retrieval was topic-scoped and topics came from a
-- 17-word keyword table, and the client used none of those 17 words. He
-- wrote "do I still owe anything in India" and "there is no income tax
-- here". The keyword table knows "resident", "residency", "NRI",
-- "return", "file", "ITR", "RNOR".
--
-- So the loop was blocked by routing, not by knowledge. The units were
-- retrievable the moment a topic was supplied.
--
-- The engineering instruction for this project already required the fix
-- and it was not implemented: "search by meaning or approved rule
-- identity, not exact wording alone. Start with explicit manifest
-- lookup and bounded text search over the small corpus."
--
-- This adds the bounded text search. It is deliberately Postgres full
-- text rather than embeddings: the same instruction forbids introducing
-- a vector database for this loop, the corpus is small enough that
-- lexical search is measurable, and one transactional system remains
-- inspectable by anyone reviewing this.
--
-- What it does NOT change is what follows retrieval. Verification
-- status, effective dates, declared conflicts and the coverage gate all
-- still run afterwards. Search widens the candidate pool. It decides
-- nothing.

ALTER TABLE app.knowledge_units
    ADD COLUMN searchable tsvector
        GENERATED ALWAYS AS (
            -- The statement is what a rule says, so it carries the most
            -- weight. Topic and locator help an enquiry that names the
            -- subject or the authority rather than describing it.
            setweight(to_tsvector('english', coalesce(statement, '')), 'A')
            || setweight(
                   to_tsvector('english', coalesce(topic, '')), 'B'
               )
            || setweight(
                   to_tsvector('english', coalesce(source_locator, '')), 'C'
               )
            || setweight(
                   to_tsvector('english', coalesce(captured_passage, '')), 'D'
               )
        ) STORED;

CREATE INDEX knowledge_units_searchable
    ON app.knowledge_units USING gin (searchable);

-- Retrieval reads through the view, which must expose the new column or
-- the runtime role cannot search at all. Recreated rather than altered
-- because a generated column cannot be added to an existing view.
DROP VIEW IF EXISTS app.active_knowledge_units;

CREATE VIEW app.active_knowledge_units AS
SELECT
    u.id,
    u.release_id,
    u.unit_key,
    u.topic,
    u.statement,
    u.source_locator,
    u.verification_status,
    u.effective_from,
    u.effective_to,
    u.scope_tags,
    u.created_at,
    u.source_version,
    u.captured_passage,
    u.captured_at,
    u.passage_digest,
    u.verified_by,
    u.verified_at,
    u.from_candidate_id,
    u.searchable,
    r.service_id
FROM app.knowledge_units u
JOIN app.knowledge_releases r ON r.id = u.release_id
WHERE r.status = 'ACTIVE';

GRANT SELECT ON app.active_knowledge_units TO agents_app;
GRANT SELECT ON app.active_knowledge_units TO agents_reviewer;

COMMIT;
