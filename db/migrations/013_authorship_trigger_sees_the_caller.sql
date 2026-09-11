BEGIN;

-- Migration 012 added a trigger to stop a reviewer writing a draft
-- labelled as the agent's, and the trigger never fired.
--
-- It was declared SECURITY DEFINER, which makes `current_user` inside
-- the function the role that *owns* it, not the role that called it. So
-- every insert looked like it came from agents_admin, matched neither
-- branch, and passed. The guard read its own name and let everything
-- through.
--
-- Caught by APPROVE03, which was rewritten in the same change to test
-- the new property and immediately reported that the property did not
-- hold. Without that check this would have shipped as a control that
-- exists, is documented, and does nothing.
--
-- The fix is to drop SECURITY DEFINER: the function needs no elevated
-- rights, only the truth about who is calling. `session_user` is used
-- as well, because it is the role that authenticated and cannot be
-- changed by SET ROLE partway through a transaction.

CREATE OR REPLACE FUNCTION app.draft_authorship_matches_writer()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    writer text := session_user;
BEGIN
    IF writer = 'agents_reviewer' AND NEW.authored_by <> 'REVIEWER' THEN
        RAISE EXCEPTION
            'the reviewer role may only write drafts attributed to a '
            'reviewer; it may not author as the agent';
    END IF;

    IF writer = 'agents_app' AND NEW.authored_by <> 'AGENT' THEN
        RAISE EXCEPTION
            'the runtime role may only write drafts attributed to the '
            'agent; it may not author as a reviewer';
    END IF;

    RETURN NEW;
END;
$$;

COMMIT;
