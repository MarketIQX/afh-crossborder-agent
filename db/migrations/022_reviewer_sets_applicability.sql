BEGIN;

-- A reviewer may narrow who a rule is for. Nothing else about it.
--
-- Migration 019 gave knowledge units an applicability restriction and
-- 021 gave a reviewer the ability to confirm the facts it compares
-- against. Both halves now exist and the gate provably excludes on real
-- rows (AGENT29). It still changes no real answer, for one remaining
-- reason: every unit in the corpus carries NULL on both dimensions, so
-- restricts_on() returns empty and every rule applies to everyone.
--
-- Nothing could set them. agents_reviewer held SELECT and INSERT on
-- app.knowledge_units and no UPDATE at all, so applicability could only
-- ever be decided at the moment of publication -- and the five units
-- already signed were published before the columns existed.
--
-- This grant is deliberately two columns wide.
--
--   applies_to_residency
--   applies_to_citizenship
--
-- A reviewer may therefore say "this rule is about non-residents" after
-- signing it, which is exactly the judgement a professional makes while
-- reading. A reviewer may NOT change:
--
--   statement            -- what the rule says
--   topic                -- where it is retrieved from
--   source_locator       -- where it came from
--   captured_passage     -- the evidence it rests on
--   passage_digest       -- the integrity of that evidence
--   verification_status  -- the rung it sits on
--   verified_by          -- whose name is on it
--   verified_at          -- when they signed
--   effective_from / to  -- when it is in force
--
-- That is the boundary worth having. The narrowest useful edit is
-- allowed and the record of what was signed, by whom, on what evidence,
-- stays immutable. A reviewer who wants to change what a rule SAYS must
-- publish a new unit and supersede the old one, which leaves both
-- visible -- consistent with append-only revisions and with no role
-- holding DELETE anywhere in this schema.
--
-- The column CHECK constraints from 019 still police the values, so a
-- reviewer cannot invent a residency class: the database refuses
-- anything outside RESIDENT / NON_RESIDENT / RNOR and
-- INDIAN / PIO / FOREIGN. Validation is not left to application code.
--
-- The runtime role is untouched. agents_app holds no privilege of any
-- kind on app.knowledge_units and reads app.active_knowledge_units,
-- which AUTH20 asserts and this migration does not weaken.

GRANT UPDATE (applies_to_residency, applies_to_citizenship)
    ON app.knowledge_units TO agents_reviewer;

COMMIT;
