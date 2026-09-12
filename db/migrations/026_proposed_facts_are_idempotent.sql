BEGIN;

-- D7: the same proposed fact, recorded once.
--
-- Contract section 7 requires `record_proposed_facts` to record
-- idempotently, and it did not. Each call minted a fresh uuid and
-- `app.case_facts` carried only a primary key, so every re-run of a case
-- re-proposed everything it had proposed before:
-- `country_of_residence` reached 7 rows across 7 runs for 3 cases.
--
-- This was recorded as a reliability item for some days, which
-- understated it. It became a product defect the moment the teaching
-- draft was rendered: the letter a professional receives lists the
-- facts on the case, and every one of them appeared twice. A request
-- for help that cannot count its own evidence is not a request anybody
-- should act on.
--
-- Scoped deliberately narrowly.
--
-- Only PROPOSED rows are constrained. A reviewer confirms a fact by
-- appending a CONFIRMED row beside the proposal rather than editing it
-- -- that append is the whole mechanism of migration 021 -- so a
-- constraint spanning every status would block confirmation itself.
--
-- The value is part of the key. The same predicate proposed with a
-- *different* value is genuinely new information and possibly a
-- contradiction a person should see, so it gets its own row. Only an
-- identical restatement is absorbed.
--
-- NULLS NOT DISTINCT because a fact proposed with no value is still one
-- fact. PostgreSQL's default would treat every null-valued proposal as
-- unique and leave exactly the duplication this closes.
--
-- The run is deliberately NOT part of the key. Including it would make
-- the index satisfiable by every run and change nothing: the
-- duplication was across runs, which is precisely what section 7 means
-- by idempotent.

-- Existing duplicates must go before the index can exist. The oldest
-- row of each identical group is kept, so the first time a fact was
-- proposed remains the record of it, and the later restatements -- which
-- carry no information the first does not -- are removed. This is a
-- one-off correction of rows written by a defect, performed by the
-- schema owner; neither production role holds DELETE on this or any
-- other table.
DELETE FROM app.case_facts f
WHERE f.status = 'PROPOSED'
  AND EXISTS (
      SELECT 1
      FROM app.case_facts keep
      WHERE keep.status = 'PROPOSED'
        AND keep.case_id = f.case_id
        AND keep.predicate = f.predicate
        AND keep.value_text IS NOT DISTINCT FROM f.value_text
        AND (
            keep.created_at < f.created_at
            OR (keep.created_at = f.created_at AND keep.id < f.id)
        )
  );

CREATE UNIQUE INDEX case_facts_one_proposal_per_value
    ON app.case_facts (case_id, predicate, value_text)
    NULLS NOT DISTINCT
    WHERE status = 'PROPOSED';

COMMIT;
