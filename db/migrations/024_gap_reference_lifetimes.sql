BEGIN;

-- S1.2 follow-up: what should happen to a gap when its evidence goes.
--
-- Migration 023 referenced the revision and the case with plain foreign
-- keys, which was wrong in a way the test suite found immediately: the
-- console's fixture cleanup deletes its own proposal revisions, and the
-- new reference refused to let it, so a suite that had passed for the
-- whole build failed on tidying up after itself.
--
-- The tempting fix was to teach the cleanup to delete gaps first. That
-- would be wrong twice: every future cleanup would have to remember the
-- same thing, and it would treat the ordering as an accident of test
-- code rather than a property of the data.
--
-- The real statement is about meaning. A gap is a record of what could
-- not be answered, and everything it rests on -- the release consulted,
-- the retrieved units, the missing predicates -- lives on the revision
-- it points at. A gap whose revision is gone is not a historical
-- record, it is a question with no evidence behind it and no way to
-- reconstruct why it was asked. Section 3 requires the gap to carry its
-- evidence references; a dangling reference does not satisfy that, so
-- the gap should not outlive the revision.
--
-- The same holds for the case, more obviously.
--
-- This does not weaken the no-DELETE rule. Neither agents_app nor
-- agents_reviewer holds DELETE on any table here, so neither can
-- trigger a cascade; only the schema owner can, which is the identity
-- that runs migrations and disposable fixtures.

ALTER TABLE app.knowledge_gaps
    DROP CONSTRAINT knowledge_gaps_revision_id_fkey;

ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_revision_id_fkey
        FOREIGN KEY (revision_id)
        REFERENCES app.proposal_revisions (id)
        ON DELETE CASCADE;

ALTER TABLE app.knowledge_gaps
    DROP CONSTRAINT knowledge_gaps_case_id_fkey;

ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_case_id_fkey
        FOREIGN KEY (case_id)
        REFERENCES app.cases (id)
        ON DELETE CASCADE;

-- The assignee is different, and deliberately so. Who was asked is not
-- what was asked. If a reviewer leaves the firm the request for help
-- still happened and still needs answering, so the gap survives and
-- becomes honestly unassigned rather than disappearing with them.
ALTER TABLE app.knowledge_gaps
    DROP CONSTRAINT knowledge_gaps_assigned_reviewer_id_fkey;

ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_assigned_reviewer_id_fkey
        FOREIGN KEY (assigned_reviewer_id)
        REFERENCES app.reviewers (id)
        ON DELETE SET NULL;

COMMIT;
