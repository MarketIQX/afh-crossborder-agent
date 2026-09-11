"""SIMULATED approved-knowledge fixtures. Test-only.

Read this before believing anything it creates.

The units here are marked PROFESSIONALLY_VERIFIED so that tests can
exercise the branch that depends on approved knowledge. That marking is
simulated. No source was captured from a real publication, no passage
was checked against a statute, and no qualified professional approved
anything. The passages say so in their own text.

This is legitimate test data for driving an approval-dependent code
path. It is not evidence that source capture works, that verification
works, or that publication works. Any demonstration of the real system
must use genuinely captured and genuinely approved knowledge.

Containment:

- `create` and `drop` assert the disposable-target guard themselves, so
  they cannot be used against a real instance even if a caller forgets.
- Nothing under `app/` or `scripts/` may import this module. A check in
  the agent suite enforces that, so it can never reach application
  bootstrap, deployment seeding or a genuine demonstration.

Each suite gets its own slot, with its own service, release and units.
Suites share a target database and leave fixtures in place until their
own cleanup runs, so a shared fixture would let one suite's cleanup
delete knowledge another suite's runs still reference.
"""

import hashlib

from app.db import testguard

MATERIAL_PREDICATES = (
    "assessment_year",
    "days_present_in_india_current_year",
    "days_present_in_india_preceding_four_years",
    "india_sourced_income_present",
    "country_of_residence",
)

TOPICS = (
    ("tax_residency", True),
    ("return_filing", True),
)

KEYWORDS = (
    ("tax_residency", "residency"),
    ("tax_residency", "residential"),
    ("tax_residency", "resident"),
    ("return_filing", "return"),
    ("return_filing", "file"),
    ("return_filing", "filing"),
)

SIMULATED_PASSAGE = (
    "SIMULATED approved-knowledge fixture. This text is not a capture "
    "of any real source and has not been verified by anyone. It exists "
    "only so tests can exercise the approved-knowledge branch."
)


def digest_of(passage):
    """The digest a captured passage must carry.

    Computed from the passage bytes, so a stored digest that does not
    match its passage is detectable rather than decorative.
    """
    return hashlib.sha256(passage.encode("utf-8")).hexdigest()


class SimulatedApprovedService:
    """One isolated service whose approved status is simulated."""

    def __init__(self, slot, service_key):
        self.slot = slot
        self.service_key = service_key

        self.service_id = f"9a000000-0000-0000-0000-{slot:012d}"
        self.release_id = f"9a100000-0000-0000-0000-{slot:012d}"
        self.verifier_id = f"9a300000-0000-0000-0000-{slot:012d}"

        self.units = (
            (
                f"9a200000-0000-0000-0000-{slot * 100 + 1:012d}",
                "simulated_residency_rule",
                "tax_residency",
                "SIMULATED FIXTURE RULE about residential status. Not "
                "real guidance and not for any other use.",
                "SIMULATED FIXTURE SOURCE, not a real citation",
            ),
            (
                f"9a200000-0000-0000-0000-{slot * 100 + 2:012d}",
                "simulated_filing_rule",
                "return_filing",
                "SIMULATED FIXTURE RULE about the obligation to file. "
                "Not real guidance and not for any other use.",
                "SIMULATED FIXTURE SOURCE, not a real citation",
            ),
        )

    def passage_for(self, unit_key):
        """Each unit gets a distinct passage, so digests differ."""
        return f"{SIMULATED_PASSAGE} Unit: {unit_key}, slot {self.slot}."

    def create(self, cur):
        testguard.assert_disposable_cursor(cur)

        cur.execute(
            "INSERT INTO app.services (id, service_key, name) "
            "VALUES (%s, %s, %s)",
            (
                self.service_id,
                self.service_key,
                f"SIMULATED fixture service {self.slot}",
            ),
        )

        for predicate in MATERIAL_PREDICATES:
            cur.execute(
                "INSERT INTO app.service_required_facts "
                "(service_id, predicate, is_material, prompt_hint) "
                "VALUES (%s, %s, true, %s)",
                (self.service_id, predicate, f"What is {predicate}?"),
            )

        for topic, in_scope in TOPICS:
            cur.execute(
                "INSERT INTO app.service_topics "
                "(service_id, topic, is_in_scope) VALUES (%s, %s, %s)",
                (self.service_id, topic, in_scope),
            )

        for topic, keyword in KEYWORDS:
            cur.execute(
                "INSERT INTO app.topic_keywords "
                "(service_id, topic, keyword) VALUES (%s, %s, %s)",
                (self.service_id, topic, keyword),
            )

        cur.execute(
            "INSERT INTO app.knowledge_releases "
            "(id, service_id, version, status, notes, activated_at) "
            "VALUES (%s, %s, 1, 'ACTIVE', %s, now())",
            (
                self.release_id,
                self.service_id,
                f"SIMULATED fixture release {self.slot}. Not approved "
                f"knowledge.",
            ),
        )

        # A unit claiming professional verification must name the
        # professional. Inside a fixture that means creating one, not
        # leaving the strongest claim in the system unattributed.
        cur.execute(
            "INSERT INTO app.reviewers (id, display_name, email) "
            "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (
                self.verifier_id,
                "Fixture Verifier",
                f"verifier-{self.slot}@example.test",
            ),
        )

        for unit_id, unit_key, topic, statement, locator in self.units:
            passage = self.passage_for(unit_key)

            cur.execute(
                """
                INSERT INTO app.knowledge_units (
                    id, release_id, unit_key, topic, statement,
                    source_locator, verification_status, effective_from,
                    captured_passage, passage_digest, captured_at,
                    verified_by, verified_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'PROFESSIONALLY_VERIFIED',
                    DATE '2020-04-01', %s, %s, now(), %s, now()
                )
                """,
                (
                    unit_id,
                    self.release_id,
                    unit_key,
                    topic,
                    statement,
                    locator,
                    passage,
                    digest_of(passage),
                    self.verifier_id,
                ),
            )

    def drop(self, cur):
        """Remove this slot. Cases and runs referencing it must be gone."""
        testguard.assert_disposable_cursor(cur)

        cur.execute(
            "DELETE FROM app.knowledge_units WHERE release_id = %s",
            (self.release_id,),
        )
        cur.execute(
            "DELETE FROM app.knowledge_releases WHERE id = %s",
            (self.release_id,),
        )
        cur.execute(
            "DELETE FROM app.reviewers WHERE id = %s",
            (self.verifier_id,),
        )
        cur.execute(
            "DELETE FROM app.topic_keywords WHERE service_id = %s",
            (self.service_id,),
        )
        cur.execute(
            "DELETE FROM app.service_topics WHERE service_id = %s",
            (self.service_id,),
        )
        cur.execute(
            "DELETE FROM app.service_required_facts WHERE service_id = %s",
            (self.service_id,),
        )
        cur.execute(
            "DELETE FROM app.services WHERE id = %s", (self.service_id,)
        )


AGENT = SimulatedApprovedService(1, "simulated_fixture_agent")
REVIEWER = SimulatedApprovedService(2, "simulated_fixture_reviewer")
