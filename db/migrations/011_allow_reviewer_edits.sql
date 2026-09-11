BEGIN;

-- Migration 010 gave drafts an author and a supersedes link, and then
-- could not use either: `draft_messages_one_per_revision` allows exactly
-- one draft per proposal revision, so a reviewer's edit had nowhere to
-- go.
--
-- The constraint was right for what existed when it was written. Its
-- purpose was that the agent may not quietly replace a letter a reviewer
-- is looking at. That purpose is unchanged and still enforced; what
-- changes is that it now says so precisely.
--
-- One AGENT draft per revision. As many REVIEWER drafts as the reviewer
-- needs, each one naming the draft it replaces, so the chain from the
-- agent's words to the words actually sent is unbroken and readable.

ALTER TABLE app.draft_messages
    DROP CONSTRAINT draft_messages_one_per_revision;

CREATE UNIQUE INDEX draft_messages_one_agent_draft_per_revision
    ON app.draft_messages (proposal_revision_id)
    WHERE authored_by = 'AGENT';

-- A reviewer draft may not supersede itself, and may not claim to
-- replace a draft belonging to a different revision. Without this, an
-- edit could be attached to one matter while replacing another's letter.
CREATE OR REPLACE FUNCTION app.draft_supersedes_same_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_revision uuid;
BEGIN
    IF NEW.supersedes_draft_id IS NULL THEN
        RETURN NEW;
    END IF;

    IF NEW.supersedes_draft_id = NEW.id THEN
        RAISE EXCEPTION 'a draft cannot supersede itself';
    END IF;

    SELECT proposal_revision_id INTO parent_revision
    FROM app.draft_messages
    WHERE id = NEW.supersedes_draft_id;

    IF parent_revision IS NULL THEN
        RAISE EXCEPTION 'superseded draft % does not exist',
            NEW.supersedes_draft_id;
    END IF;

    IF parent_revision <> NEW.proposal_revision_id THEN
        RAISE EXCEPTION
            'a draft may only supersede one on the same proposal revision';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER draft_supersedes_same_revision
    BEFORE INSERT OR UPDATE ON app.draft_messages
    FOR EACH ROW
    EXECUTE FUNCTION app.draft_supersedes_same_revision();

COMMIT;
