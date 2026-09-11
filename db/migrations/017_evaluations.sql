BEGIN;

-- M7: scores, kept over time.
--
-- There are 120 checks in this repository and not one of them scores an
-- answer. They prove the system refuses what it should refuse, which is
-- the harder half and not the whole job: a system that correctly refuses
-- everything would pass all 120.
--
-- An eval is the other half. A golden case is an enquiry with the
-- outcome a professional would expect, and a run scores the agent
-- against every one of them. The number matters less than its
-- direction: a prompt change that lifts one case and drops two is a
-- regression, and without history that is invisible.
--
-- Results are stored rather than printed for exactly that reason. A
-- score in a terminal is a feeling. A score next to the last four is
-- evidence.
--
-- Note on honesty: a failing eval is recorded, not hidden. The point of
-- keeping history is that the bad runs stay in it.

CREATE TABLE app.eval_runs (
    id uuid PRIMARY KEY,

    -- What was being scored: the agent's decision, or the verifier's
    -- verdict on a claim. Different suites, same history.
    suite text NOT NULL,

    -- Everything needed to explain a change in score.
    runner text NOT NULL,
    model_id text NOT NULL,
    prompt_version text NOT NULL DEFAULT '',
    prompt_digest text NOT NULL DEFAULT '',
    git_commit text NOT NULL DEFAULT '',

    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,

    total integer NOT NULL DEFAULT 0,
    passed integer NOT NULL DEFAULT 0,
    failed integer NOT NULL DEFAULT 0,
    errored integer NOT NULL DEFAULT 0,

    notes text NOT NULL DEFAULT '',

    CONSTRAINT eval_runs_suite_known
        CHECK (suite IN ('DECISION_STATE', 'CLAIM_VERIFICATION')),

    CONSTRAINT eval_runs_runner_known
        CHECK (runner IN ('BEDROCK_STRANDS', 'DETERMINISTIC_STUB')),

    CONSTRAINT eval_runs_counts_add_up
        CHECK (passed + failed + errored <= total)
);

CREATE INDEX eval_runs_history
    ON app.eval_runs (suite, started_at DESC);


CREATE TABLE app.eval_results (
    id uuid PRIMARY KEY,

    eval_run_id uuid NOT NULL
        REFERENCES app.eval_runs(id) ON DELETE CASCADE,

    case_key text NOT NULL,

    expected text NOT NULL,
    observed text NOT NULL DEFAULT '',

    outcome text NOT NULL,

    -- Why the case exists, carried alongside the score so a failure can
    -- be understood without opening the dataset.
    rationale text NOT NULL DEFAULT '',

    -- What it cost, per case, because an eval that gets slower is also
    -- a regression.
    latency_ms integer,
    input_tokens integer,
    output_tokens integer,

    detail text NOT NULL DEFAULT '',

    CONSTRAINT eval_results_outcome_known
        CHECK (outcome IN ('PASS', 'FAIL', 'ERROR'))
);

CREATE UNIQUE INDEX eval_results_one_per_case
    ON app.eval_results (eval_run_id, case_key);

CREATE INDEX eval_results_failures
    ON app.eval_results (eval_run_id)
    WHERE outcome <> 'PASS';


-- Scoring is machine work: the runtime role writes it.
GRANT SELECT, INSERT, UPDATE ON app.eval_runs TO agents_app;
GRANT SELECT, INSERT ON app.eval_results TO agents_app;

-- The professional reads scores and cannot write them. A score a
-- reviewer could edit is not a measurement.
GRANT SELECT ON app.eval_runs TO agents_reviewer;
GRANT SELECT ON app.eval_results TO agents_reviewer;

COMMIT;
