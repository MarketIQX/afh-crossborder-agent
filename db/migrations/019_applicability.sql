BEGIN;

-- M9: whether a rule applies to this client, not merely to this subject.
--
-- Proven as a defect rather than assumed. A test drove the decision
-- layer directly, with no model involved: a verified, on-topic,
-- in-date rule about RESIDENT individuals, a client who is a
-- NON-RESIDENT, every unrelated gate deliberately passing. The server
-- permitted SUPPORTED_WITHIN_POLICY, cited that rule as the basis, and
-- recorded requires_professional_verification = false with the
-- rationale "in scope, material facts confirmed, effective guidance
-- retrieved, no declared conflict". Every clause of that sentence was
-- true and the conclusion was wrong.
--
-- It was not a missing check. `evaluate` read five context fields and
-- none of them was a case attribute, so there was no field in which
-- applicability could be expressed. This adds the field.
--
-- Three-valued, and the third value is the point.
--
--   TRUE     the rule's conditions match what we know -> admissible
--   FALSE    they contradict what we know             -> excluded
--   UNKNOWN  the rule restricts on something we have
--            not established                          -> MISSING_FACTS
--
-- The UNKNOWN branch is why this is not a boolean. If residency status
-- is unestablished, silently dropping every resident-rule would hide
-- the fact that residency is precisely what needs establishing. Routing
-- it to MISSING_FACTS makes the system ask the question instead of
-- quietly narrowing its own evidence. That is fail-closed in the
-- direction a professional wants: the answer becomes "I cannot tell you
-- whether this applies until we know X", which is both true and useful.
--
-- Deliberately small. Two dimensions, both drawn from the corridor this
-- build demonstrates, both expressible as a single confirmed fact. This
-- is not an ontology and must not grow into one without evals.

-- NULL means the rule does not restrict on that dimension. It is the
-- default so every existing unit keeps exactly its current behaviour:
-- unrestricted, therefore applicable.
ALTER TABLE app.knowledge_units
    ADD COLUMN applies_to_residency text,
    ADD COLUMN applies_to_citizenship text;

ALTER TABLE app.knowledge_units
    ADD CONSTRAINT knowledge_units_residency_value_known
        CHECK (
            applies_to_residency IS NULL
            OR applies_to_residency IN
               ('RESIDENT', 'NON_RESIDENT', 'RNOR')
        );

ALTER TABLE app.knowledge_units
    ADD CONSTRAINT knowledge_units_citizenship_value_known
        CHECK (
            applies_to_citizenship IS NULL
            OR applies_to_citizenship IN ('INDIAN', 'PIO', 'FOREIGN')
        );

-- The view is what the runtime reads, so the columns must reach it.
DROP VIEW IF EXISTS app.active_knowledge_units;

CREATE VIEW app.active_knowledge_units AS
SELECT
    u.id,
    u.release_id,
    u.unit_key,
    u.topic,
    u.statement,
    u.source_locator,
    u.verification_status,
    u.effective_from,
    u.effective_to,
    u.scope_tags,
    u.created_at,
    u.source_version,
    u.captured_passage,
    u.captured_at,
    u.passage_digest,
    u.verified_by,
    u.verified_at,
    u.from_candidate_id,
    u.searchable,
    u.applies_to_residency,
    u.applies_to_citizenship,
    r.service_id
FROM app.knowledge_units u
JOIN app.knowledge_releases r ON r.id = u.release_id
WHERE r.status = 'ACTIVE';

GRANT SELECT ON app.active_knowledge_units TO agents_app;
GRANT SELECT ON app.active_knowledge_units TO agents_reviewer;


-- The case attributes that resolve applicability.
--
-- These are required facts like any other, which means they are subject
-- to the rule that already governs every fact: the agent may propose
-- one and only a reviewer may confirm it. So an applicability decision
-- can never rest on something the model asserted about the client.
--
-- Residency status is deliberately among them even though it is also a
-- conclusion the engagement produces. That is not circular, it is the
-- honest sequence: until a professional has established it, rules that
-- turn on it have UNKNOWN applicability, and the system says so.
INSERT INTO app.service_required_facts
    (service_id, predicate, prompt_hint, is_material)
SELECT
    s.id,
    v.predicate,
    v.hint,
    true
FROM app.services s
CROSS JOIN (
    VALUES
        (
            'residency_status',
            'Residential status for the year, once established: '
            || 'resident, non-resident, or resident but not ordinarily '
            || 'resident.'
        ),
        (
            'citizenship_status',
            'Whether the client is an Indian citizen, a person of '
            || 'Indian origin, or neither.'
        )
) AS v(predicate, hint)
WHERE s.service_key = 'nri_india_tax_filing'
ON CONFLICT DO NOTHING;

COMMIT;
