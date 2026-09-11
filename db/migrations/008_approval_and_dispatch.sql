BEGIN;

-- M2: human approval and controlled dispatch.
--
-- The central idea is that approving and sending are different powers
-- held by different database identities. The runtime role can send and
-- cannot approve. The reviewer role can approve and cannot send.
-- Neither identity on its own can put a message in front of a client,
-- and that is a privilege fact rather than a convention in code.
--
-- The second idea is that an approval is bound to exact content. A
-- reviewer approves a specific recipient, subject, body and attachment
-- set, recorded as a digest at the moment of approval. Dispatch
-- recomputes the digest and refuses if it differs, so an approval
-- cannot be transferred onto different content.

CREATE TABLE app.reviewers (
    id uuid PRIMARY KEY,

    email text NOT NULL,
    display_name text NOT NULL,

    is_active boolean NOT NULL DEFAULT true,

    -- Recorded for knowledge verification in M3. Approving a client
    -- reply is not the same authority as approving a reusable
    -- professional rule, so the two are separated here.
    professional_qualification text,
    may_verify_knowledge boolean NOT NULL DEFAULT false,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT reviewers_email_lowercase
        CHECK (email = lower(email) AND btrim(email) <> ''),

    CONSTRAINT reviewers_email_unique
        UNIQUE (email),

    CONSTRAINT reviewers_display_name_nonempty
        CHECK (btrim(display_name) <> ''),

    -- Claiming verification authority requires a recorded qualification.
    CONSTRAINT reviewers_verification_needs_qualification
        CHECK (
            NOT may_verify_knowledge
            OR (
                professional_qualification IS NOT NULL
                AND btrim(professional_qualification) <> ''
            )
        )
);


-- A reviewer may act only on cases explicitly granted to them. Holding
-- a reviewer login is not authority over every client's case.

CREATE TABLE app.reviewer_case_grants (
    reviewer_id uuid NOT NULL
        REFERENCES app.reviewers(id),

    case_id uuid NOT NULL
        REFERENCES app.cases(id),

    granted_at timestamptz NOT NULL DEFAULT now(),
    granted_by text NOT NULL DEFAULT current_user,

    revoked_at timestamptz,
    revoked_reason text,

    CONSTRAINT reviewer_case_grants_pk
        PRIMARY KEY (reviewer_id, case_id),

    CONSTRAINT reviewer_case_grants_revocation_consistency
        CHECK (revoked_reason IS NULL OR revoked_at IS NOT NULL)
);


CREATE INDEX reviewer_case_grants_active_idx
    ON app.reviewer_case_grants (case_id)
    WHERE revoked_at IS NULL;


-- The concrete thing a reviewer approves. A proposal revision says what
-- the agent decided; a draft message is the exact text that would leave
-- the building. Immutable: no UPDATE is granted to anyone but the
-- administrator, and a changed draft is a new draft.

CREATE TABLE app.draft_messages (
    id uuid PRIMARY KEY,

    proposal_revision_id uuid NOT NULL
        REFERENCES app.proposal_revisions(id),

    channel text NOT NULL DEFAULT 'EMAIL',

    recipient text NOT NULL,
    subject text NOT NULL,
    body_text text NOT NULL,
    attachments jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Computed by the application over the four fields above. Stored for
    -- display and audit; the binding that matters is the digest recorded
    -- on the approval.
    content_digest text NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_messages_channel_check
        CHECK (channel IN ('EMAIL')),

    CONSTRAINT draft_messages_recipient_nonempty
        CHECK (btrim(recipient) <> ''),

    CONSTRAINT draft_messages_subject_nonempty
        CHECK (btrim(subject) <> ''),

    CONSTRAINT draft_messages_body_nonempty
        CHECK (btrim(body_text) <> ''),

    CONSTRAINT draft_messages_attachments_array
        CHECK (jsonb_typeof(attachments) = 'array'),

    CONSTRAINT draft_messages_digest_nonempty
        CHECK (btrim(content_digest) <> ''),

    -- One draft per proposal revision. A different message means a
    -- different revision, so approval history stays unambiguous.
    CONSTRAINT draft_messages_one_per_revision
        UNIQUE (proposal_revision_id)
);

-- The approval itself. `approved_digest` is the content the reviewer
-- actually saw, captured at the moment of approval, so the approval
-- cannot later be applied to different text.

CREATE TABLE app.approvals (
    id uuid PRIMARY KEY,

    draft_message_id uuid NOT NULL
        REFERENCES app.draft_messages(id),

    reviewer_id uuid NOT NULL
        REFERENCES app.reviewers(id),

    decision text NOT NULL,

    approved_digest text NOT NULL,
    note text,

    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL DEFAULT current_user,

    revoked_at timestamptz,
    revoked_reason text,

    CONSTRAINT approvals_decision_check
        CHECK (decision IN ('APPROVED', 'REJECTED')),

    CONSTRAINT approvals_digest_nonempty
        CHECK (btrim(approved_digest) <> ''),

    CONSTRAINT approvals_revocation_consistency
        CHECK (revoked_reason IS NULL OR revoked_at IS NOT NULL)
);


-- At most one live approval per draft. A second approval requires the
-- first to be revoked, so "who authorised this" always has one answer.

CREATE UNIQUE INDEX approvals_one_live_per_draft
    ON app.approvals (draft_message_id)
    WHERE decision = 'APPROVED' AND revoked_at IS NULL;


CREATE INDEX approvals_reviewer_idx
    ON app.approvals (reviewer_id, created_at DESC);


-- Dispatch. SEND_UNKNOWN exists because a provider can fail in a way
-- that leaves delivery genuinely undetermined. Recording it as FAILED
-- would invite a retry that double-sends; recording it as SENT would
-- claim something we do not know.

CREATE TABLE app.dispatches (
    id uuid PRIMARY KEY,

    approval_id uuid NOT NULL
        REFERENCES app.approvals(id),

    operation_id text NOT NULL,

    state text NOT NULL DEFAULT 'PENDING',

    provider_message_id text,
    provider_response jsonb,
    failure_reason text,

    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,

    CONSTRAINT dispatches_operation_unique
        UNIQUE (operation_id),

    CONSTRAINT dispatches_operation_nonempty
        CHECK (btrim(operation_id) <> ''),

    CONSTRAINT dispatches_state_check
        CHECK (
            state IN ('PENDING', 'SENT', 'FAILED', 'SEND_UNKNOWN')
        ),

    CONSTRAINT dispatches_completion_consistency
        CHECK (
            (state = 'PENDING' AND completed_at IS NULL)
            OR (state <> 'PENDING' AND completed_at IS NOT NULL)
        ),

    CONSTRAINT dispatches_sent_needs_provider_id
        CHECK (
            state <> 'SENT'
            OR (
                provider_message_id IS NOT NULL
                AND btrim(provider_message_id) <> ''
            )
        )
);


-- One attempt per approval unless the previous attempt definitively
-- failed. SENT and SEND_UNKNOWN both block a retry: the first because
-- it worked, the second because we cannot show it did not.

CREATE UNIQUE INDEX dispatches_one_live_per_approval
    ON app.dispatches (approval_id)
    WHERE state <> 'FAILED';


-- Runtime role. It may read who the reviewers are and what they
-- approved, may write drafts, and may carry out a dispatch. It is
-- granted nothing at all on app.approvals beyond SELECT, so the process
-- that sends cannot author the authority to send.

GRANT SELECT ON app.reviewers TO agents_app;
GRANT SELECT ON app.reviewer_case_grants TO agents_app;
GRANT SELECT ON app.draft_messages TO agents_app;
GRANT SELECT ON app.approvals TO agents_app;
GRANT SELECT ON app.dispatches TO agents_app;

GRANT INSERT (
    id,
    proposal_revision_id,
    channel,
    recipient,
    subject,
    body_text,
    attachments,
    content_digest
) ON app.draft_messages TO agents_app;

GRANT INSERT (
    id,
    approval_id,
    operation_id
) ON app.dispatches TO agents_app;

GRANT UPDATE (
    state,
    provider_message_id,
    provider_response,
    failure_reason,
    completed_at
) ON app.dispatches TO agents_app;


-- Reviewer role. It may read the case material it needs in order to
-- decide, and may record a decision. It is granted nothing on
-- app.dispatches, so approving is not sending, and nothing on
-- app.draft_messages, so it cannot rewrite what it is approving.

GRANT SELECT ON app.services TO agents_reviewer;
GRANT SELECT ON app.service_required_facts TO agents_reviewer;
GRANT SELECT ON app.cases TO agents_reviewer;
GRANT SELECT ON app.inbound_messages TO agents_reviewer;
GRANT SELECT ON app.case_facts TO agents_reviewer;
GRANT SELECT ON app.active_knowledge_units TO agents_reviewer;
GRANT SELECT ON app.knowledge_releases TO agents_reviewer;
GRANT SELECT ON app.agent_runs TO agents_reviewer;
GRANT SELECT ON app.agent_tool_calls TO agents_reviewer;
GRANT SELECT ON app.action_proposals TO agents_reviewer;
GRANT SELECT ON app.proposal_revisions TO agents_reviewer;
GRANT SELECT ON app.reviewers TO agents_reviewer;
GRANT SELECT ON app.reviewer_case_grants TO agents_reviewer;
GRANT SELECT ON app.draft_messages TO agents_reviewer;
GRANT SELECT ON app.approvals TO agents_reviewer;
GRANT SELECT ON app.dispatches TO agents_reviewer;

GRANT INSERT (
    id,
    draft_message_id,
    reviewer_id,
    decision,
    approved_digest,
    note
) ON app.approvals TO agents_reviewer;

GRANT UPDATE (
    revoked_at,
    revoked_reason
) ON app.approvals TO agents_reviewer;

COMMIT;
