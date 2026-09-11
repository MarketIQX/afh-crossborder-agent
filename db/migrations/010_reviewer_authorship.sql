BEGIN;

-- M5: let a reviewer correct the words before they go, and record that
-- they did.
--
-- Until now a reviewer could approve a letter or return it, and nothing
-- else. That is not how a professional works. A partner reads a draft,
-- changes a sentence that reads badly, and sends it. Forcing a return
-- for every small correction makes the tool slower than writing the
-- letter by hand, which is the surest way to have it abandoned.
--
-- The separation that matters is not who writes. It is that the process
-- which sends cannot authorise, and the account which authorises cannot
-- send. Both remain exactly as they were: the reviewer role still holds
-- no privilege on dispatches, and the runtime role still holds none on
-- approvals.
--
-- What changes is that a draft now says who wrote it. An edit is a new
-- draft attributed to the reviewer who made it, with its own digest, and
-- the approval binds that digest the same way it always did. Nothing
-- about the binding is loosened: a reviewer approves exactly the words
-- they are looking at, whoever composed them.

ALTER TABLE app.draft_messages
    ADD COLUMN authored_by text NOT NULL DEFAULT 'AGENT',
    ADD COLUMN authored_by_reviewer_id uuid
        REFERENCES app.reviewers(id),
    ADD COLUMN supersedes_draft_id uuid
        REFERENCES app.draft_messages(id);

ALTER TABLE app.draft_messages
    ADD CONSTRAINT draft_messages_author_known
        CHECK (authored_by IN ('AGENT', 'REVIEWER'));

-- A reviewer-authored draft must name the reviewer. An agent-authored
-- one must not, because attributing machine text to a person is the
-- kind of quiet falsehood this schema exists to prevent.
ALTER TABLE app.draft_messages
    ADD CONSTRAINT draft_messages_authorship_is_attributed
        CHECK (
            (authored_by = 'REVIEWER') = (authored_by_reviewer_id IS NOT NULL)
        );

-- An edit must say what it replaced, so the trail from the agent's
-- words to the words actually sent is never broken.
ALTER TABLE app.draft_messages
    ADD CONSTRAINT draft_messages_edit_names_its_original
        CHECK (
            authored_by = 'AGENT' OR supersedes_draft_id IS NOT NULL
        );

CREATE INDEX draft_messages_supersedes_idx
    ON app.draft_messages (supersedes_draft_id)
    WHERE supersedes_draft_id IS NOT NULL;

-- The reviewer may compose. Note what is NOT granted: nothing on
-- app.dispatches, so the account that can now write a letter still
-- cannot put it on the wire.
GRANT INSERT, SELECT ON app.draft_messages TO agents_reviewer;

-- The runtime role reads these columns when it dispatches, so that a
-- sent message can report who wrote it.
GRANT SELECT (authored_by, authored_by_reviewer_id, supersedes_draft_id)
    ON app.draft_messages TO agents_app;

-- Feedback on a returned draft is worth keeping as a training signal:
-- it is a professional saying, in their own words, what the agent got
-- wrong. `approvals.note` already holds it; this makes it reportable.
CREATE INDEX approvals_rejected_idx
    ON app.approvals (created_at DESC)
    WHERE decision = 'REJECTED';

COMMIT;
