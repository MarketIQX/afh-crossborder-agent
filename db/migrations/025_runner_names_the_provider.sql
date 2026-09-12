BEGIN;

-- S0.4: a run record must be able to name the provider that answered it.
--
-- Three tables restricted `runner` to BEDROCK_STRANDS or
-- DETERMINISTIC_STUB, which was exactly right when those were the only
-- two things that could produce a run. With inference now supplied by a
-- configured provider, the constraint would refuse to record a Groq run
-- at all -- and the database is behaving correctly in doing so. It was
-- told two runners exist; a third is a fact it has not been given.
--
-- The alternative was to keep stamping BEDROCK_STRANDS whatever
-- answered. That is worse than a refused insert: every eval score and
-- every agent run would be filed against a provider that did not
-- produce it, and the evidence would be false while looking complete.
-- The same reason `app/dispatch/providers.py` refuses to substitute a
-- simulated send for an approved one.
--
-- The existing eight BEDROCK_STRANDS rows remain valid. This widens the
-- permitted set; it does not reinterpret anything already recorded, and
-- the two historical eval runs keep meaning what they meant.
--
-- Named per provider rather than as a free text column on purpose. A
-- column that accepts anything accepts a typo, and a run labelled
-- GROK_STRANDS or GROQ_STRAND would silently become a fourth provider
-- in every later count.

ALTER TABLE app.agent_runs
    DROP CONSTRAINT agent_runs_runner_check;

ALTER TABLE app.agent_runs
    ADD CONSTRAINT agent_runs_runner_check
        CHECK (
            runner IN (
                'BEDROCK_STRANDS',
                'ANTHROPIC_STRANDS',
                'GROQ_STRANDS',
                'DETERMINISTIC_STUB'
            )
        );

ALTER TABLE app.extraction_runs
    DROP CONSTRAINT extraction_runs_runner_known;

ALTER TABLE app.extraction_runs
    ADD CONSTRAINT extraction_runs_runner_known
        CHECK (
            runner IN (
                'BEDROCK_STRANDS',
                'ANTHROPIC_STRANDS',
                'GROQ_STRANDS',
                'DETERMINISTIC_STUB'
            )
        );

ALTER TABLE app.eval_runs
    DROP CONSTRAINT eval_runs_runner_known;

ALTER TABLE app.eval_runs
    ADD CONSTRAINT eval_runs_runner_known
        CHECK (
            runner IN (
                'BEDROCK_STRANDS',
                'ANTHROPIC_STRANDS',
                'GROQ_STRANDS',
                'DETERMINISTIC_STUB'
            )
        );

COMMIT;
