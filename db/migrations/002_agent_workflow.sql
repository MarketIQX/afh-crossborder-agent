BEGIN;

-- Service scope. A case belongs to exactly one service, and knowledge
-- is released per service, so nothing can be retrieved across scopes.

CREATE TABLE app.services (
    id uuid PRIMARY KEY,

    service_key text NOT NULL,
    name text NOT NULL,

    is_active boolean NOT NULL DEFAULT true,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT services_key_nonempty
        CHECK (btrim(service_key) <> ''),

    CONSTRAINT services_name_nonempty
        CHECK (btrim(name) <> ''),

    CONSTRAINT services_key_unique
        UNIQUE (service_key)
);


-- Cases gain a service scope and a human-facing reference. Nullable
-- because migration 001 already allows correlation-only cases that
-- have not been triaged into a service yet. Context assembly refuses
-- to run for a case with no service, so the agent can never operate
-- on an unscoped case.

ALTER TABLE app.cases
    ADD COLUMN service_id uuid
        REFERENCES app.services(id);

ALTER TABLE app.cases
    ADD COLUMN reference text;

CREATE UNIQUE INDEX cases_reference_unique
    ON app.cases (reference)
    WHERE reference IS NOT NULL;


-- The facts a service materially requires before advice is possible.
-- This is what makes "missing client facts" a deterministic database
-- question rather than a model opinion.

CREATE TABLE app.service_required_facts (
    service_id uuid NOT NULL
        REFERENCES app.services(id),

    predicate text NOT NULL,

    is_material boolean NOT NULL DEFAULT true,

    prompt_hint text,

    CONSTRAINT service_required_facts_pk
        PRIMARY KEY (service_id, predicate),

    CONSTRAINT service_required_facts_predicate_nonempty
        CHECK (btrim(predicate) <> '')
);


-- Versioned knowledge releases. Exactly one release per service may be
-- ACTIVE. Retrieval reads ACTIVE releases only, so DRAFT content is
-- invisible to the agent by construction rather than by filtering
-- discipline in application code.

CREATE TABLE app.knowledge_releases (
    id uuid PRIMARY KEY,

    service_id uuid NOT NULL
        REFERENCES app.services(id),

    version integer NOT NULL,

    status text NOT NULL DEFAULT 'DRAFT',

    notes text,

    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,

    CONSTRAINT knowledge_releases_version_positive
        CHECK (version > 0),

    CONSTRAINT knowledge_releases_status_check
        CHECK (status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')),

    CONSTRAINT knowledge_releases_activation_consistency
        CHECK (
            (status = 'DRAFT' AND activated_at IS NULL)
            OR (status IN ('ACTIVE', 'SUPERSEDED') AND activated_at IS NOT NULL)
        ),

    CONSTRAINT knowledge_releases_service_version_unique
        UNIQUE (service_id, version)
);


CREATE UNIQUE INDEX knowledge_releases_single_active
    ON app.knowledge_releases (service_id)
    WHERE status = 'ACTIVE';


-- Individual knowledge units. verification_status keeps "we can cite a
-- source" separate from "a qualified professional has verified this",
-- because a reviewer approving a client reply is not the same as a
-- reviewer approving a reusable professional rule.

CREATE TABLE app.knowledge_units (
    id uuid PRIMARY KEY,

    release_id uuid NOT NULL
        REFERENCES app.knowledge_releases(id),

    unit_key text NOT NULL,

    topic text NOT NULL,
    statement text NOT NULL,

    source_locator text NOT NULL,

    verification_status text NOT NULL DEFAULT 'UNVERIFIED',

    effective_from date NOT NULL,
    effective_to date,

    scope_tags jsonb NOT NULL DEFAULT '[]'::jsonb,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT knowledge_units_key_nonempty
        CHECK (btrim(unit_key) <> ''),

    CONSTRAINT knowledge_units_topic_nonempty
        CHECK (btrim(topic) <> ''),

    CONSTRAINT knowledge_units_statement_nonempty
        CHECK (btrim(statement) <> ''),

    CONSTRAINT knowledge_units_source_nonempty
        CHECK (btrim(source_locator) <> ''),

    CONSTRAINT knowledge_units_verification_check
        CHECK (
            verification_status IN (
                'UNVERIFIED',
                'SOURCE_VERIFIED',
                'PROFESSIONALLY_VERIFIED'
            )
        ),

    CONSTRAINT knowledge_units_effective_range
        CHECK (effective_to IS NULL OR effective_to >= effective_from),

    CONSTRAINT knowledge_units_scope_tags_array
        CHECK (jsonb_typeof(scope_tags) = 'array'),

    CONSTRAINT knowledge_units_release_key_unique
        UNIQUE (release_id, unit_key)
);


CREATE INDEX knowledge_units_release_topic_idx
    ON app.knowledge_units (release_id, topic);

-- One row per actual model run. Everything needed to reconstruct what
-- produced a proposal lives here, including which knowledge release and
-- which prompt version were in force.

CREATE TABLE app.agent_runs (
    id uuid PRIMARY KEY,

    case_id uuid NOT NULL
        REFERENCES app.cases(id),

    operation_id text NOT NULL,

    runner text NOT NULL,

    model_id text NOT NULL,
    model_config jsonb NOT NULL DEFAULT '{}'::jsonb,

    prompt_version text NOT NULL,
    prompt_digest text NOT NULL,
    tool_schema_version text NOT NULL,
    context_builder_version text NOT NULL,

    knowledge_release_id uuid
        REFERENCES app.knowledge_releases(id),

    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,

    latency_ms integer,
    input_tokens integer,
    output_tokens integer,

    tool_call_count integer NOT NULL DEFAULT 0,

    result_state text NOT NULL DEFAULT 'RUNNING',
    failure_reason text,

    CONSTRAINT agent_runs_operation_unique
        UNIQUE (operation_id),

    CONSTRAINT agent_runs_operation_nonempty
        CHECK (btrim(operation_id) <> ''),

    CONSTRAINT agent_runs_runner_check
        CHECK (runner IN ('BEDROCK_STRANDS', 'DETERMINISTIC_STUB')),

    CONSTRAINT agent_runs_result_state_check
        CHECK (
            result_state IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'REFUSED')
        ),

    CONSTRAINT agent_runs_completion_consistency
        CHECK (
            (result_state = 'RUNNING' AND ended_at IS NULL)
            OR (result_state <> 'RUNNING' AND ended_at IS NOT NULL)
        ),

    CONSTRAINT agent_runs_failure_reason_consistency
        CHECK (
            failure_reason IS NULL
            OR result_state IN ('FAILED', 'REFUSED')
        ),

    CONSTRAINT agent_runs_latency_nonnegative
        CHECK (latency_ms IS NULL OR latency_ms >= 0),

    CONSTRAINT agent_runs_tool_count_nonnegative
        CHECK (tool_call_count >= 0)
);


CREATE INDEX agent_runs_case_idx
    ON app.agent_runs (case_id, started_at DESC);


-- The tool trace. The permitted tool surface is enforced here, so a
-- logged attempt to call anything outside the four bounded tools is
-- rejected by the database, not by prompt instructions.

CREATE TABLE app.agent_tool_calls (
    id uuid PRIMARY KEY,

    run_id uuid NOT NULL
        REFERENCES app.agent_runs(id),

    sequence integer NOT NULL,

    tool_name text NOT NULL,

    arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
    result_summary jsonb,
    error text,

    called_at timestamptz NOT NULL DEFAULT now(),
    duration_ms integer,

    CONSTRAINT agent_tool_calls_sequence_positive
        CHECK (sequence > 0),

    CONSTRAINT agent_tool_calls_run_sequence_unique
        UNIQUE (run_id, sequence),

    CONSTRAINT agent_tool_calls_name_check
        CHECK (
            tool_name IN (
                'get_case_context',
                'get_service_knowledge',
                'record_proposed_facts',
                'propose_next_action'
            )
        ),

    CONSTRAINT agent_tool_calls_duration_nonnegative
        CHECK (duration_ms IS NULL OR duration_ms >= 0)
);


-- Case facts. The runtime role is not granted the status column, so it
-- physically cannot create a CONFIRMED fact: every fact it writes
-- defaults to PROPOSED and waits for a human. A reviewer-attributed
-- fact must be CONFIRMED, which the runtime role cannot achieve, so it
-- cannot forge reviewer authorship either.

CREATE TABLE app.case_facts (
    id uuid PRIMARY KEY,

    case_id uuid NOT NULL
        REFERENCES app.cases(id),

    predicate text NOT NULL,
    value_text text,

    status text NOT NULL DEFAULT 'PROPOSED',
    origin text NOT NULL,

    evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,

    run_id uuid
        REFERENCES app.agent_runs(id),

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT case_facts_predicate_nonempty
        CHECK (btrim(predicate) <> ''),

    CONSTRAINT case_facts_status_check
        CHECK (status IN ('PROPOSED', 'CONFIRMED', 'REJECTED')),

    CONSTRAINT case_facts_origin_check
        CHECK (
            origin IN ('CLIENT_MESSAGE', 'AGENT_PROPOSED', 'REVIEWER')
        ),

    CONSTRAINT case_facts_reviewer_requires_confirmed
        CHECK (origin <> 'REVIEWER' OR status = 'CONFIRMED'),

    CONSTRAINT case_facts_evidence_array
        CHECK (jsonb_typeof(evidence_refs) = 'array')
);


CREATE INDEX case_facts_case_predicate_idx
    ON app.case_facts (case_id, predicate);


CREATE UNIQUE INDEX case_facts_single_confirmed
    ON app.case_facts (case_id, predicate)
    WHERE status = 'CONFIRMED';

-- Proposals are append-only. A revision is never edited: a changed
-- proposal is a new revision, so an approval in M2 can be bound to
-- exact immutable content.

CREATE TABLE app.action_proposals (
    id uuid PRIMARY KEY,

    case_id uuid NOT NULL
        REFERENCES app.cases(id),

    current_revision integer NOT NULL DEFAULT 0,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT action_proposals_revision_nonnegative
        CHECK (current_revision >= 0)
);


CREATE INDEX action_proposals_case_idx
    ON app.action_proposals (case_id);


CREATE TABLE app.proposal_revisions (
    id uuid PRIMARY KEY,

    proposal_id uuid NOT NULL
        REFERENCES app.action_proposals(id),

    revision integer NOT NULL,

    run_id uuid NOT NULL
        REFERENCES app.agent_runs(id),

    decision_state text NOT NULL,

    summary text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,

    cited_unit_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    missing_predicates jsonb NOT NULL DEFAULT '[]'::jsonb,

    knowledge_release_id uuid
        REFERENCES app.knowledge_releases(id),

    requires_professional_verification boolean NOT NULL DEFAULT true,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT proposal_revisions_revision_positive
        CHECK (revision > 0),

    CONSTRAINT proposal_revisions_proposal_revision_unique
        UNIQUE (proposal_id, revision),

    CONSTRAINT proposal_revisions_summary_nonempty
        CHECK (btrim(summary) <> ''),

    CONSTRAINT proposal_revisions_decision_state_check
        CHECK (
            decision_state IN (
                'MISSING_FACTS',
                'MISSING_KNOWLEDGE',
                'SOURCE_CONFLICT',
                'OUT_OF_SCOPE',
                'SUPPORTED_WITHIN_POLICY',
                'SYSTEM_FAILURE'
            )
        ),

    CONSTRAINT proposal_revisions_cited_units_array
        CHECK (jsonb_typeof(cited_unit_ids) = 'array'),

    CONSTRAINT proposal_revisions_missing_predicates_array
        CHECK (jsonb_typeof(missing_predicates) = 'array'),

    -- A supported answer must cite something and must name the release
    -- it relied on. This is what stops a confident unsourced answer.
    CONSTRAINT proposal_revisions_supported_requires_citation
        CHECK (
            decision_state <> 'SUPPORTED_WITHIN_POLICY'
            OR (
                jsonb_array_length(cited_unit_ids) > 0
                AND knowledge_release_id IS NOT NULL
            )
        ),

    -- Claiming facts are missing requires naming which ones.
    CONSTRAINT proposal_revisions_missing_facts_requires_predicates
        CHECK (
            decision_state <> 'MISSING_FACTS'
            OR jsonb_array_length(missing_predicates) > 0
        ),

    -- A conflict needs at least two sources to be in conflict.
    CONSTRAINT proposal_revisions_conflict_requires_two_sources
        CHECK (
            decision_state <> 'SOURCE_CONFLICT'
            OR jsonb_array_length(cited_unit_ids) > 1
        )
);


CREATE INDEX proposal_revisions_proposal_idx
    ON app.proposal_revisions (proposal_id, revision DESC);


-- Runtime privileges.
--
-- The runtime role may read scope and knowledge, and may write runs,
-- tool traces, proposed facts and proposal revisions. It is granted no
-- write privilege of any kind on services, required facts, knowledge
-- releases or knowledge units, so the agent cannot widen its own scope
-- or publish professional knowledge. It is granted no UPDATE or DELETE
-- on the tool trace, on case facts or on proposal revisions, so its
-- own record of what it did is not editable by it.

GRANT SELECT ON app.services TO agents_app;
GRANT SELECT ON app.service_required_facts TO agents_app;
GRANT SELECT ON app.knowledge_releases TO agents_app;
GRANT SELECT ON app.knowledge_units TO agents_app;
GRANT SELECT ON app.case_facts TO agents_app;
GRANT SELECT ON app.agent_runs TO agents_app;
GRANT SELECT ON app.agent_tool_calls TO agents_app;
GRANT SELECT ON app.action_proposals TO agents_app;
GRANT SELECT ON app.proposal_revisions TO agents_app;

GRANT INSERT (
    service_id,
    reference
) ON app.cases TO agents_app;

GRANT UPDATE (
    service_id,
    reference
) ON app.cases TO agents_app;

GRANT INSERT (
    id,
    case_id,
    operation_id,
    runner,
    model_id,
    model_config,
    prompt_version,
    prompt_digest,
    tool_schema_version,
    context_builder_version,
    knowledge_release_id
) ON app.agent_runs TO agents_app;

GRANT UPDATE (
    ended_at,
    latency_ms,
    input_tokens,
    output_tokens,
    tool_call_count,
    result_state,
    failure_reason
) ON app.agent_runs TO agents_app;

GRANT INSERT (
    id,
    run_id,
    sequence,
    tool_name,
    arguments,
    result_summary,
    error,
    duration_ms
) ON app.agent_tool_calls TO agents_app;

GRANT INSERT (
    id,
    case_id,
    predicate,
    value_text,
    origin,
    evidence_refs,
    run_id
) ON app.case_facts TO agents_app;

GRANT INSERT (
    id,
    case_id
) ON app.action_proposals TO agents_app;

GRANT UPDATE (
    current_revision,
    updated_at
) ON app.action_proposals TO agents_app;

GRANT INSERT (
    id,
    proposal_id,
    revision,
    run_id,
    decision_state,
    summary,
    payload,
    cited_unit_ids,
    missing_predicates,
    knowledge_release_id,
    requires_professional_verification
) ON app.proposal_revisions TO agents_app;

COMMIT;
