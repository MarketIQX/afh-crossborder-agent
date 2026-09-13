BEGIN;

-- Correcting migration 026, which keyed idempotency on the wrong axis.
--
-- 026 enforced UNIQUE (case_id, predicate, value_text) for PROPOSED
-- rows, so a fact could occur at most once per case whatever asserted
-- it. That is deduplication of business facts, not idempotency, and the
-- two are different requirements.
--
-- Idempotency is: retrying the same logical operation must not create
-- the same effect twice. It is not: this fact may only ever be asserted
-- once. An email in September and a signed document in October can both
-- establish country_of_residence = UAE, and those are two evidence
-- events that a professional may need to see separately.
--
-- The surviving rows demonstrate how badly the axis was chosen:
--
--     'UAE (Dubai)'                      run=3ca16f9f  evidence=[]
--     'UAE'                              run=4c82b6fb  evidence=[]
--     'United Arab Emirates'             run=d4303b89  evidence=['enquiry body']
--     'United Arab Emirates (Abu Dhabi)' run=f033a7f0  evidence=[]
--
-- Four assertions of one fact survived because their wording differs,
-- while genuinely distinct evidence events with identical wording were
-- deleted. The key preserved noise and destroyed provenance.
--
-- 026 also removed 11 rows. All eleven belonged to cases whose inbound
-- sender was under the reserved .test domain, so none was genuine
-- participant data, established from app.agent_tool_calls.result_summary,
-- which records the fact_id of every row each call wrote.
--
-- An earlier draft of this comment called them unrecoverable. That was
-- too strong. Their ids, predicates, values, evidence, run and case all
-- survive in those tool-call records; only the original created_at does
-- not, and tool-call time is an approximation of it rather than the
-- value.
--
-- They are deliberately not restored. Reconstructing a row would mean
-- inventing its created_at, and all eleven were synthetic, so the cost
-- of inventing a timestamp exceeds the value of the row. The decision is
-- recorded here rather than left implicit.
--
-- A DELETE should not have been applied without review whatever it
-- turned out to hit. That is the part of 026 that was wrong
-- independently of the index.
--
-- What the operation identity should have been is already in this
-- schema and predates all of this:
--
--     CREATE UNIQUE INDEX agent_runs_operation_unique
--         ON app.agent_runs (operation_id)
--
-- with `runner._insert_run` raising DuplicateOperation on conflict.
--
-- That primitive is real but narrower than it looks, and an earlier
-- draft of this comment overstated it. It rejects a second run for an
-- operation_id already used; it does not give a caller a stable id, and
-- it does not return the earlier result on replay -- it raises.
--
-- Neither production caller supplies one. scripts/run_case.py and
-- app/autonomy/loop.py both call runner.execute without an
-- operation_id, so the runner mints a fresh UUID and a retry becomes a
-- new operation, a new run and new rows. All twelve runs recorded so far
-- carry distinct generated ids.
--
-- So this migration does not establish retry idempotency, and nothing
-- in it should be read as claiming so. Stable operation identity and
-- replay recovery are open work.
--
-- What remains worth constraining is narrow and real: within one
-- operation, the same assertion supported by the same evidence should
-- land once, so a repeated tool call inside a single run cannot inflate
-- the record. Evidence is part of the key because the same value from
-- different evidence is different provenance and must survive.
--
-- No rows are deleted by this migration. If any existing row would
-- violate the new index, the CREATE fails and we look at it rather than
-- silently removing the evidence again.

DROP INDEX app.case_facts_one_proposal_per_value;

CREATE UNIQUE INDEX case_facts_one_assertion_per_operation
    ON app.case_facts (run_id, predicate, value_text, evidence_refs)
    NULLS NOT DISTINCT
    WHERE status = 'PROPOSED' AND run_id IS NOT NULL;

COMMIT;
