BEGIN;

-- The teaching loop needs the professional to publish, and only the
-- administrator could.
--
-- That was right while knowledge arrived by seed script. It is wrong
-- now: the entire point is that a qualified person reads a candidate
-- against its source passage and signs it off, and asking them to hand
-- the work to an operator with a shell would put a second person, with
-- no professional standing, between the judgment and the record.
--
-- So the reviewer may publish. Three things keep that honest.
--
-- First, a unit must say who verified it. PROFESSIONALLY_VERIFIED is a
-- claim about a named person's professional judgment, and until now the
-- schema recorded the claim without the person.
--
-- Second, a reviewer may only sign as themselves, enforced the same way
-- draft authorship is, because attribution a writer chooses is not
-- attribution.
--
-- Third, the runtime role gains nothing here. It still cannot publish,
-- cannot activate a release, and cannot verify. AUTH05 and AUTH06 hold
-- unchanged.

ALTER TABLE app.knowledge_units
    ADD COLUMN verified_by uuid REFERENCES app.reviewers(id),
    ADD COLUMN verified_at timestamptz,
    ADD COLUMN from_candidate_id uuid;

-- A claim of professional verification must name the professional.
ALTER TABLE app.knowledge_units
    ADD CONSTRAINT knowledge_units_professional_claim_is_attributed
        CHECK (
            verification_status <> 'PROFESSIONALLY_VERIFIED'
            OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)
        );

CREATE INDEX knowledge_units_verified_by_idx
    ON app.knowledge_units (verified_by)
    WHERE verified_by IS NOT NULL;

-- A reviewer may only sign in their own name.
CREATE OR REPLACE FUNCTION app.verification_is_signed_by_the_writer()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF session_user = 'agents_app' AND NEW.verified_by IS NOT NULL THEN
        RAISE EXCEPTION
            'the runtime role may not record a professional verification';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER verification_is_signed_by_the_writer
    BEFORE INSERT OR UPDATE ON app.knowledge_units
    FOR EACH ROW
    EXECUTE FUNCTION app.verification_is_signed_by_the_writer();

-- The professional publishes.
GRANT SELECT, INSERT ON app.knowledge_units TO agents_reviewer;
GRANT INSERT, UPDATE ON app.knowledge_releases TO agents_reviewer;

COMMIT;
