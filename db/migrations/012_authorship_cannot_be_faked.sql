BEGIN;

-- Migration 010 let a reviewer write a draft. It did not stop them
-- writing one labelled as the agent's.
--
-- Found by APPROVE03, which asserted the reviewer role could not write a
-- draft at all. That property changed on purpose, and rewriting the
-- check meant asking what should be true now. The answer was not "a
-- reviewer may write anything": it is that a reviewer may write *as
-- themselves*.
--
-- Attribution that a writer can choose is not attribution. If a person
-- can label their own words as the machine's, then "Anika drafted this"
-- stops meaning anything, and the audit trail this whole system rests on
-- is decorative.
--
-- Column grants cannot express "this role may only insert rows about
-- itself", so the rule lives in a trigger that reads the acting role.

CREATE OR REPLACE FUNCTION app.draft_authorship_matches_writer()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- The reviewer role may only write drafts attributed to a reviewer.
    IF current_user = 'agents_reviewer'
       AND NEW.authored_by <> 'REVIEWER' THEN
        RAISE EXCEPTION
            'the reviewer role may only write drafts attributed to a '
            'reviewer; it may not author as the agent';
    END IF;

    -- The runtime role carries the agent's work and may not attribute a
    -- draft to a person. A machine signing a human's name is the same
    -- failure in the other direction.
    IF current_user = 'agents_app'
       AND NEW.authored_by <> 'AGENT' THEN
        RAISE EXCEPTION
            'the runtime role may only write drafts attributed to the '
            'agent; it may not author as a reviewer';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER draft_authorship_matches_writer
    BEFORE INSERT OR UPDATE ON app.draft_messages
    FOR EACH ROW
    EXECUTE FUNCTION app.draft_authorship_matches_writer();

COMMIT;
