BEGIN;

-- M9 correction: ask for a fact when a rule needs it, not always.
--
-- 019 registered residency_status and citizenship_status as material
-- facts. That was the wrong instrument, and it was wrong in a way worth
-- recording rather than quietly editing out of 019.
--
-- Marking them material demands both on every case before anything can
-- be answered. Two things go wrong.
--
-- First, it makes the three-valued logic decorative. MISSING_FACTS
-- would fire whether or not any retrieved rule turned on residency, so
-- the UNKNOWN branch would never be the reason for anything, and the
-- applicability gate would look like it was working while contributing
-- nothing. A check that cannot fail is not a check.
--
-- Second, it is wrong to the client. Someone asking which ITR form to
-- file would be asked for their residential status before receiving an
-- answer that does not depend on it. "Always ask" is not caution, it is
-- a refusal to reason about what the question needs.
--
-- The demand belongs where the uncertainty is. `applicability.assess`
-- raises the predicate only when a professionally verified rule's
-- applicability actually turns on it, and only for verified rules, so
-- unapproved material can never drive client contact either. Everything
-- else about these two rows stays: the prompt hint is what lets the
-- question be phrased at all, and drafting refuses rather than
-- inventing wording for a predicate it has no hint for.
--
-- Residency status being both a required fact and a conclusion the
-- engagement produces is not circular. It is the honest sequence: until
-- a professional has established it, rules that turn on it have UNKNOWN
-- applicability, and the system says so instead of picking a side.

UPDATE app.service_required_facts
SET is_material = false
WHERE predicate IN ('residency_status', 'citizenship_status')
  AND service_id IN (
      SELECT id
      FROM app.services
      WHERE service_key = 'nri_india_tax_filing'
  );

COMMIT;
