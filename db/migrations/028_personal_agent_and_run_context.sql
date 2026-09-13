BEGIN;

-- Two things this schema could not say, and a decision needs both.
--
-- 1. WHOSE AGENT ACTED.
--
-- `app.reviewers` models humans. `app.agent_runs` models a model
-- invocation: a runner, a model id, a prompt version. Between them
-- there was nothing durable representing "Nicole, the personal work
-- agent belonging to this Partner". So a run could say which model
-- answered but not on whose behalf, and a display name is not an
-- identity: it is presentation. The identity has to be a row that
-- authority can be derived from.
--
-- The derivation runs one way only. A profile's authority is its
-- owner's authority, never its own, so naming an agent can never widen
-- what the person behind it may do. Nothing in this table grants
-- anything; it records who a run was acting for.
--
-- 2. WHAT THE RUN ACTUALLY SAW.
--
-- `app.case_facts` records when a fact row was created but not when it
-- became CONFIRMED. So reading a case's confirmed facts today tells you
-- the state now, and gives no way to prove which of them a run read
-- last week. A decision receipt could describe a decision but could
-- not reproduce the ground it stood on, and a later confirmation would
-- silently change the apparent basis of an older decision.
--
-- The fix is to capture, once, at run time, what the run was given.
-- `app.run_context_manifests` is that capture: a serialisation of the
-- context object the runner assembled and handed to the model, with a
-- digest over it.
--
-- It is written once and never amended. No role is granted UPDATE or
-- DELETE on it, which is the same way every other immutable record here
-- is protected: not by intention, by absent privilege. A manifest that
-- could be edited after the fact would be worth less than no manifest,
-- because it would look like history.
--
-- Runs that predate this migration have no manifest, and none is
-- invented for them. Their historical context is incomplete and the
-- receipt says so rather than reconstructing a plausible past.

CREATE TABLE app.agent_profiles (
    id uuid PRIMARY KEY,

    -- The human this agent acts for. Authority derives from here and
    -- from nowhere else.
    owner_reviewer_id uuid NOT NULL
        REFERENCES app.reviewers(id),

    -- Presentation. Two Partners may both call their agent Nicole; the
    -- id is what anything durable refers to.
    display_name text NOT NULL,

    agent_role text NOT NULL DEFAULT 'PERSONAL_WORK_AGENT',

    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT agent_profiles_display_name_nonempty
        CHECK (btrim(display_name) <> ''),

    -- Deliberately narrow. A department agent, a capability agent and
    -- an orchestrator are different things with different authority
    -- models, and none of them is built. Adding a role here should
    -- require deciding what its authority derives from.
    CONSTRAINT agent_profiles_role_check
        CHECK (agent_role IN ('PERSONAL_WORK_AGENT')),

    CONSTRAINT agent_profiles_owner_name_unique
        UNIQUE (owner_reviewer_id, display_name)
);


CREATE INDEX agent_profiles_owner_idx
    ON app.agent_profiles (owner_reviewer_id)
    WHERE is_active;


-- Which agent acted, and which human initiated it. Both nullable,
-- because runs exist that predate the concept and backfilling an
-- identity onto them would be fabricating attribution.
--
-- The initiating reviewer is recorded on the run as well as being
-- reachable through the profile's owner. That is not redundancy: owners
-- can change, and a run must keep saying who initiated it at the time,
-- not who owns the profile now.

ALTER TABLE app.agent_runs
    ADD COLUMN agent_profile_id uuid
        REFERENCES app.agent_profiles(id),
    ADD COLUMN initiated_by_reviewer_id uuid
        REFERENCES app.reviewers(id);


-- So a manifest can be tied to (run, case) as one fact rather than to
-- each separately. `id` is already the primary key; this adds the pair
-- a composite reference needs.

ALTER TABLE app.agent_runs
    ADD CONSTRAINT agent_runs_id_case_unique UNIQUE (id, case_id);


CREATE TABLE app.run_context_manifests (
    -- One manifest per run, so the run id is the key. A second
    -- manifest for the same run would mean the record of what was seen
    -- is ambiguous, which defeats the purpose.
    --
    -- CASCADE because a manifest has no meaning without its run: it is
    -- an account of what that run was given, not an independent
    -- record. Following migration 024, which took the same view of
    -- references whose lifetime is bounded by their parent. This can
    -- only ever fire for the schema owner, since neither production
    -- role holds DELETE on app.agent_runs.
    run_id uuid PRIMARY KEY
        REFERENCES app.agent_runs(id) ON DELETE CASCADE,

    manifest_version text NOT NULL,

    -- Recorded here as well as on the run, because the manifest is
    -- read on its own and a join to discover which case it describes
    -- would invite the answer being assumed. The composite foreign key
    -- below is what stops the two disagreeing: without it, a writer
    -- passing the wrong case id would produce two answers to "which
    -- enquiry is this", and the correlation claim would be a
    -- convention rather than an invariant.
    case_id uuid NOT NULL,
    service_id uuid
        REFERENCES app.services(id),

    agent_profile_id uuid
        REFERENCES app.agent_profiles(id),
    initiated_by_reviewer_id uuid
        REFERENCES app.reviewers(id),

    -- The date the run reasoned as of. A rule effective from April
    -- means nothing without knowing which date the question was asked
    -- against.
    material_date date NOT NULL,

    knowledge_release_id uuid
        REFERENCES app.knowledge_releases(id),

    -- As seen. These are the facts the context builder handed over,
    -- with the status each had at that moment, not a pointer to rows
    -- whose status can move afterwards.
    confirmed_facts jsonb NOT NULL DEFAULT '[]'::jsonb,
    unconfirmed_assertions jsonb NOT NULL DEFAULT '[]'::jsonb,
    missing_material_predicates jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Every knowledge unit deterministic preflight retrieved for this
    -- run, as it stood at that moment: unit id,
    -- release id, verification status, source locator, effective
    -- dates. Not the statement text -- this is a safe reference a
    -- receipt can cite, not an archive of the corpus. Superseding the
    -- release afterwards must not change this preflight snapshot; current
    -- retrieval is a separate question, answered
    -- by app.active_knowledge_units at the time it is asked.
    deterministic_knowledge_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- What the deterministic gates were given. Applicability is decided
    -- by the application, not the model, so its inputs belong in the
    -- record of the decision.
    applicability_inputs jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- What the run was permitted to do. Recorded rather than inferred,
    -- because "it could not have sent anything" is a claim about
    -- configuration at the time.
    policy_envelope jsonb NOT NULL DEFAULT '{}'::jsonb,

    context_builder_version text NOT NULL,
    truncations jsonb NOT NULL DEFAULT '[]'::jsonb,

    context_digest text NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT run_context_manifests_version_nonempty
        CHECK (btrim(manifest_version) <> ''),

    CONSTRAINT run_context_manifests_digest_shape
        CHECK (context_digest ~ '^[0-9a-f]{64}$'),

    CONSTRAINT run_context_manifests_confirmed_array
        CHECK (jsonb_typeof(confirmed_facts) = 'array'),

    CONSTRAINT run_context_manifests_unconfirmed_array
        CHECK (jsonb_typeof(unconfirmed_assertions) = 'array'),

    CONSTRAINT run_context_manifests_missing_array
        CHECK (jsonb_typeof(missing_material_predicates) = 'array'),

    CONSTRAINT run_context_manifests_deterministic_snapshot_array
        CHECK (jsonb_typeof(deterministic_knowledge_snapshot) = 'array'),

    CONSTRAINT run_context_manifests_truncations_array
        CHECK (jsonb_typeof(truncations) = 'array'),

    CONSTRAINT run_context_manifests_applicability_object
        CHECK (jsonb_typeof(applicability_inputs) = 'object'),

    CONSTRAINT run_context_manifests_policy_object
        CHECK (jsonb_typeof(policy_envelope) = 'object'),

    -- The case is not independently assertable. A manifest describes
    -- what one run was given, and that run already belongs to a case;
    -- naming a different one here would mean two answers to which
    -- enquiry a decision belongs to, which is exactly what a business
    -- correlation identifier has to rule out.
    CONSTRAINT run_context_manifests_case_matches_run
        FOREIGN KEY (run_id, case_id)
        REFERENCES app.agent_runs(id, case_id)
        ON DELETE CASCADE
);


CREATE INDEX run_context_manifests_case_idx
    ON app.run_context_manifests (case_id, captured_at DESC);


-- Privileges.
--
-- The runtime writes a manifest and reads it back. It cannot amend one:
-- no UPDATE, no DELETE, deliberately. Immutability here is the absence
-- of a grant, which cannot be forgotten by a code path.
--
-- Agent profiles are administered, not self-registered. The runtime
-- reads them; it cannot create one, because an agent that can create
-- its own identity can name itself into an authority it was not given.

GRANT SELECT ON app.agent_profiles TO agents_app;
GRANT SELECT ON app.agent_profiles TO agents_reviewer;

GRANT SELECT ON app.run_context_manifests TO agents_app;
GRANT SELECT ON app.run_context_manifests TO agents_reviewer;

GRANT INSERT (
    run_id,
    manifest_version,
    case_id,
    service_id,
    agent_profile_id,
    initiated_by_reviewer_id,
    material_date,
    knowledge_release_id,
    confirmed_facts,
    unconfirmed_assertions,
    missing_material_predicates,
    deterministic_knowledge_snapshot,
    applicability_inputs,
    policy_envelope,
    context_builder_version,
    truncations,
    context_digest
) ON app.run_context_manifests TO agents_app;

-- The runner names the acting agent and the initiating human when it
-- creates the run. INSERT only, and deliberately no UPDATE: attribution
-- that can be amended after the fact is not attribution. The existing
-- UPDATE grant on this table covers outcome columns -- ended_at,
-- result_state and the rest -- which do legitimately change once, when
-- the run finishes. Identity does not.

GRANT INSERT (agent_profile_id, initiated_by_reviewer_id)
    ON app.agent_runs TO agents_app;

COMMIT;
