BEGIN;

-- M4: make triage recorded, so the loop can run unattended.
--
-- Until now a case reached the agent only because a person ran a
-- script. That made the system honest but not autonomous, and the whole
-- premise is an agent that works in the background and surfaces only
-- when a human judgment is genuinely required.
--
-- The property that actually matters is not that a human performs
-- triage. It is that the *model* never chooses its own service scope,
-- because that is the path by which it could reach another corridor's
-- knowledge. A deterministic keyword router is not the model: it runs
-- server side, consults a seeded keyword table, and has no language
-- model anywhere in it. Letting it route preserves the real property
-- while removing the manual break.
--
-- What this migration adds is accountability. Every triaged case must
-- now say how it was triaged and when. A reviewer can therefore see at
-- a glance which cases a machine routed and override any of them, and
-- no case can acquire a service scope anonymously.
--
-- Note on privilege: the runtime role already held UPDATE on
-- cases.service_id. The "operator only" character of triage came from
-- there being no agent tool that reached it, not from a grant. That is
-- still true, and is now stated rather than assumed.

ALTER TABLE app.cases
    ADD COLUMN triage_method text,
    ADD COLUMN triaged_at timestamptz;

-- Existing triaged cases were routed by a person running a script.
UPDATE app.cases
SET triage_method = 'HUMAN',
    triaged_at = COALESCE(updated_at, created_at)
WHERE service_id IS NOT NULL;

ALTER TABLE app.cases
    ADD CONSTRAINT cases_triage_method_known
        CHECK (
            triage_method IS NULL
            OR triage_method IN ('HUMAN', 'KEYWORD_ROUTER')
        );

-- A service scope may never be acquired anonymously.
ALTER TABLE app.cases
    ADD CONSTRAINT cases_service_requires_triage_record
        CHECK (
            service_id IS NULL
            OR (triage_method IS NOT NULL AND triaged_at IS NOT NULL)
        );

-- Routing decisions the router declined to make, kept so that an
-- operator can see what the machine found ambiguous rather than having
-- to guess why a case is still waiting.
CREATE TABLE app.triage_attempts (
    id uuid PRIMARY KEY,

    case_id uuid NOT NULL
        REFERENCES app.cases(id),

    attempted_at timestamptz NOT NULL DEFAULT now(),

    -- ROUTED, AMBIGUOUS (more than one service matched), or
    -- NO_MATCH (nothing in the keyword table matched).
    outcome text NOT NULL,

    chosen_service_id uuid
        REFERENCES app.services(id),

    -- What matched, for a human reading the queue.
    detail text NOT NULL DEFAULT '',

    CONSTRAINT triage_attempts_outcome_known
        CHECK (outcome IN ('ROUTED', 'AMBIGUOUS', 'NO_MATCH')),

    CONSTRAINT triage_attempts_routed_names_service
        CHECK (
            (outcome = 'ROUTED') = (chosen_service_id IS NOT NULL)
        )
);

CREATE INDEX triage_attempts_case_idx
    ON app.triage_attempts (case_id, attempted_at DESC);

GRANT SELECT, INSERT ON app.triage_attempts TO agents_app;
GRANT SELECT ON app.triage_attempts TO agents_reviewer;

GRANT SELECT (triage_method, triaged_at) ON app.cases TO agents_app;
GRANT UPDATE (triage_method, triaged_at) ON app.cases TO agents_app;

GRANT SELECT (triage_method, triaged_at) ON app.cases TO agents_reviewer;

-- A reviewer may correct a machine routing decision.
GRANT UPDATE (service_id, triage_method, triaged_at)
    ON app.cases TO agents_reviewer;

COMMIT;
