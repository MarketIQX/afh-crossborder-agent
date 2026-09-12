BEGIN;

-- S1.1: the record of Anika asking for help.
--
-- The forensic audit found that the agent could reach three of its six
-- decision states on real input and that two of those three -- MISSING_
-- KNOWLEDGE and MISSING_FACTS -- had nowhere to go. The decision was
-- computed, recorded on the revision, and then the enquiry simply sat
-- there. No record said what could not be answered, who was being
-- asked, or whether anyone had answered. Contract section 3 requires
-- one, and it is also the product's whole thesis: an agent that reaches
-- a person only when the decision is genuinely theirs has to be able to
-- show the asking.
--
-- This table deliberately does NOT copy the evidence. The revision it
-- points at already carries the release consulted, the retrieved unit
-- ids, the missing predicates and the run that produced them, all under
-- constraints that keep them honest. Section 3 asks for the gap and its
-- "evidence references", so a reference is what this stores. Copying
-- would create a second version of the same facts, free to drift from
-- the first, and the drift would favour whichever one a screen happened
-- to read.
--
-- What it adds is what no existing row holds: the precise unanswered
-- question, who is responsible for answering it, whether it is still
-- open, and the draft that was put in front of them.

CREATE TABLE app.knowledge_gaps (
    id uuid PRIMARY KEY,

    -- Named directly, not only reachable through the revision. A gap is
    -- read per case constantly, and the dedupe guarantee below has to
    -- be enforceable by the database rather than by a digest the
    -- application computes correctly today.
    case_id uuid NOT NULL REFERENCES app.cases (id),

    -- The decision that caused the escalation. Immutable, and the
    -- source of every piece of evidence this gap rests on.
    revision_id uuid NOT NULL
        REFERENCES app.proposal_revisions (id),

    -- Section 3 requires support for several reasons in one request, so
    -- this is an array and not a column. The vocabulary is the decision
    -- layer's own; a second vocabulary would be a second thing to keep
    -- in step.
    reason_codes jsonb NOT NULL,

    -- The precise unanswered question, in the reviewer's language.
    -- Section 3 forbids unreplaced placeholders, which is why this is
    -- NOT NULL and non-empty: a gap that cannot state its question is
    -- not a gap, it is a shrug.
    question text NOT NULL,

    -- Who is being asked. Nullable because an unassigned gap is an
    -- honest state and better than inventing an assignee, but it can
    -- never be silently reassigned: there is no UPDATE grant on it.
    assigned_reviewer_id uuid REFERENCES app.reviewers (id),

    -- Section 3: "Deduplicate retries of the same gap; do not merge
    -- unrelated cases because their wording is similar." Both halves
    -- matter and they pull in opposite directions, so the second is
    -- enforced structurally below rather than trusted to the digest.
    dedupe_key text NOT NULL,

    gap_state text NOT NULL DEFAULT 'OPEN',

    -- The draft, persisted before anything claims it exists. Section 3
    -- is explicit that a draft is not a sent message and that neither
    -- confers authority, so nothing here records a dispatch: that lives
    -- in app.dispatches under its own controls.
    draft_body text,
    draft_rendered_at timestamptz,

    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

-- The dedupe guarantee, made structural.
--
-- A digest over the question text alone would merge two clients asking
-- the same thing, which section 3 forbids outright. Including the case
-- id in the uniqueness constraint means that merge is impossible
-- whatever the application computes, so a future bug in key generation
-- cannot produce a cross-case collision. Within one case, a retry
-- carrying the same key collides and is rejected, which is the
-- deduplication.
ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_one_per_case_and_key
        UNIQUE (case_id, dedupe_key);

ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_dedupe_key_nonempty
        CHECK (btrim(dedupe_key) <> '');

ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_question_nonempty
        CHECK (btrim(question) <> '');

ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_reason_codes_array
        CHECK (jsonb_typeof(reason_codes) = 'array');

-- A gap with no reason is not answerable. The reviewer needs to know
-- which kind of help is being requested, because section 2 gives a
-- different required behaviour for each.
ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_reason_codes_present
        CHECK (jsonb_array_length(reason_codes) > 0);

-- SUPPORTED_WITHIN_POLICY is deliberately not in this list. It is the
-- state in which nothing is missing, so it can never be the reason for
-- a request for help. If it ever appears here the decision layer and
-- this table disagree, and the database should say so rather than store
-- the contradiction.
--
-- Written as jsonb containment rather than a subquery over
-- jsonb_array_elements_text, because PostgreSQL refuses a subquery in a
-- CHECK constraint outright -- it cannot guarantee the result stays
-- true, so it declines to pretend otherwise. Containment asks the same
-- question without that problem: every element on the left must appear
-- on the right, so one unrecognised code fails the whole row.
ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_reason_codes_known
        CHECK (
            reason_codes <@ '[
                "MISSING_FACTS",
                "MISSING_KNOWLEDGE",
                "SOURCE_CONFLICT",
                "OUT_OF_SCOPE",
                "SYSTEM_FAILURE"
            ]'::jsonb
        );

ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_state_known
        CHECK (gap_state IN ('OPEN', 'ANSWERED', 'WITHDRAWN'));

-- A resolved gap must say when, and an open one must not pretend to
-- have been. Section 5 requires states derived from stored events
-- rather than from generated text, which only works if the timestamp
-- and the state cannot disagree.
ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_resolution_has_a_time
        CHECK (
            (gap_state = 'OPEN' AND resolved_at IS NULL)
            OR (gap_state <> 'OPEN' AND resolved_at IS NOT NULL)
        );

-- A draft that exists must say when it was rendered, so "no draft yet"
-- and "drafted" are distinguishable without reading the body.
ALTER TABLE app.knowledge_gaps
    ADD CONSTRAINT knowledge_gaps_draft_has_a_time
        CHECK (
            (draft_body IS NULL) = (draft_rendered_at IS NULL)
        );

CREATE INDEX knowledge_gaps_open_by_case
    ON app.knowledge_gaps (case_id)
    WHERE gap_state = 'OPEN';

CREATE INDEX knowledge_gaps_by_reviewer
    ON app.knowledge_gaps (assigned_reviewer_id, gap_state);


-- Powers, split the way every other table here is split.
--
-- The runtime creates the gap and renders the draft: contract section 7
-- puts that on the application, from a validated proposed action, which
-- is why no fifth model-facing tool is needed to do it.
--
-- It cannot change the question, the reason codes, the case, the
-- revision or the assignee after the fact, because those are the record
-- of what was asked and of whom. It cannot close a gap either -- that
-- is a professional judgement.
GRANT SELECT ON app.knowledge_gaps TO agents_app;

GRANT INSERT (
    id,
    case_id,
    revision_id,
    reason_codes,
    question,
    assigned_reviewer_id,
    dedupe_key,
    gap_state,
    created_at
) ON app.knowledge_gaps TO agents_app;

GRANT UPDATE (draft_body, draft_rendered_at)
    ON app.knowledge_gaps TO agents_app;

-- The reviewer can close a gap and cannot author one. The asymmetry is
-- the point: a request for help is a record of what the system could
-- not do, and a reviewer who could write that record could also write
-- one that never happened.
GRANT SELECT ON app.knowledge_gaps TO agents_reviewer;

GRANT UPDATE (gap_state, resolved_at)
    ON app.knowledge_gaps TO agents_reviewer;

-- No DELETE to anyone, matching every other table in this schema.

COMMIT;
