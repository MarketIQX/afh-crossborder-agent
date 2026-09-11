BEGIN;

-- Two corrections to migration 002/003, both raised in review.
--
-- 1. Verification was overstated. The seeded units were recorded as
--    SOURCE_VERIFIED when all that existed was a statutory locator. A
--    locator is not verification: it does not show that any captured
--    passage actually supports the statement. The status ladder is now
--    explicit, and the two stronger states are made unfakeable by
--    requiring captured evidence rather than by convention.
--
--    UNVERIFIED               nothing recorded
--    SOURCE_RECORDED          a locator exists, content not captured
--    SOURCE_VERIFIED          passage captured and digested, and shown
--                             to support the statement
--    PROFESSIONALLY_VERIFIED  a qualified professional signed it off
--
--    Activation and verification are separate axes. A release being
--    ACTIVE means only that it is the one retrievable release for its
--    service. It says nothing about professional approval.
--
-- 2. The runtime role held SELECT on app.knowledge_units, so although
--    the retrieval function filtered to ACTIVE releases, draft rows
--    were still reachable through any other query. The filter was a
--    property of one function, not a boundary. The base table is now
--    revoked from the runtime role, which reads a view restricted to
--    ACTIVE releases instead, so unapproved knowledge is unreachable
--    rather than merely unrequested.

ALTER TABLE app.knowledge_units
    ADD COLUMN source_version text;

ALTER TABLE app.knowledge_units
    ADD COLUMN captured_passage text;

ALTER TABLE app.knowledge_units
    ADD COLUMN captured_at timestamptz;

ALTER TABLE app.knowledge_units
    ADD COLUMN passage_digest text;


ALTER TABLE app.knowledge_units
    DROP CONSTRAINT knowledge_units_verification_check;

ALTER TABLE app.knowledge_units
    ADD CONSTRAINT knowledge_units_verification_check
        CHECK (
            verification_status IN (
                'UNVERIFIED',
                'SOURCE_RECORDED',
                'SOURCE_VERIFIED',
                'PROFESSIONALLY_VERIFIED'
            )
        );


-- Correct the overstated status before the stricter rule is imposed.

UPDATE app.knowledge_units
    SET verification_status = 'SOURCE_RECORDED'
    WHERE verification_status = 'SOURCE_VERIFIED'
      AND captured_passage IS NULL;


-- A claim of source verification now requires the evidence that would
-- let a reviewer check it. This cannot be satisfied by editing a status
-- column alone.

ALTER TABLE app.knowledge_units
    ADD CONSTRAINT knowledge_units_verification_needs_evidence
        CHECK (
            verification_status NOT IN (
                'SOURCE_VERIFIED',
                'PROFESSIONALLY_VERIFIED'
            )
            OR (
                captured_passage IS NOT NULL
                AND btrim(captured_passage) <> ''
                AND passage_digest IS NOT NULL
                AND btrim(passage_digest) <> ''
                AND captured_at IS NOT NULL
            )
        );


CREATE VIEW app.active_knowledge_units AS
    SELECT
        u.id,
        u.release_id,
        r.service_id,
        r.version AS release_version,
        u.unit_key,
        u.topic,
        u.statement,
        u.source_locator,
        u.source_version,
        u.verification_status,
        u.captured_passage,
        u.passage_digest,
        u.captured_at,
        u.effective_from,
        u.effective_to,
        u.scope_tags
    FROM app.knowledge_units u
    JOIN app.knowledge_releases r ON r.id = u.release_id
    WHERE r.status = 'ACTIVE';


-- The runtime role loses the base table and gains only the view.

REVOKE SELECT ON app.knowledge_units FROM agents_app;

GRANT SELECT ON app.active_knowledge_units TO agents_app;

COMMIT;
