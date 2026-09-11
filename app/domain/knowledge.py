"""Retrieval of approved professional knowledge.

Two properties matter more than retrieval quality here.

First, the runtime role cannot reach unapproved knowledge at all. It
holds no privilege on app.knowledge_units and reads
app.active_knowledge_units, a view restricted to ACTIVE releases. That
is a privilege boundary, so it holds for any query the runtime makes,
not only for the function below. Migration 005 made this true; before
it, the ACTIVE filter here was the only thing standing between the
runtime and a draft row, which is a filter rather than a boundary.

Second, retrieval either returns results or raises. It never returns an
empty list to signal a failure, because "we found no guidance" and "the
lookup broke" must never collapse into the same answer.
"""

from dataclasses import dataclass
from datetime import date

import psycopg

RETRIEVAL_VERSION = "knowledge-retrieval-v1"


class KnowledgeSystemFailure(Exception):
    """Retrieval could not be performed. This is not a knowledge gap."""


@dataclass(frozen=True)
class KnowledgeUnit:
    unit_id: str
    unit_key: str
    topic: str
    statement: str
    source_locator: str
    verification_status: str
    effective_from: date
    effective_to: date | None
    scope_tags: tuple

    @property
    def professionally_verified(self):
        return self.verification_status == "PROFESSIONALLY_VERIFIED"

    def citation(self):
        """What a reviewer needs in order to check the claim."""
        return {
            "unit_id": self.unit_id,
            "unit_key": self.unit_key,
            "topic": self.topic,
            "source_locator": self.source_locator,
            "verification_status": self.verification_status,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": (
                self.effective_to.isoformat() if self.effective_to else None
            ),
        }


def active_release_id(cur, service_id):
    """Return the one ACTIVE release for a service, or None."""
    try:
        cur.execute(
            "SELECT id::text FROM app.knowledge_releases "
            "WHERE service_id = %s AND status = 'ACTIVE'",
            (service_id,),
        )
        row = cur.fetchone()
    except psycopg.Error as exc:
        raise KnowledgeSystemFailure(
            f"active release lookup failed: {exc.__class__.__name__}"
        ) from exc

    return row[0] if row else None


def retrieve(cur, release_id, topics, material_date):
    """Return effective units for the given topics from an ACTIVE release.

    Reads the ACTIVE-only view, so passing a DRAFT release id returns
    nothing. Note what this does and does not establish: it shows the
    production retrieval path cannot surface a draft unit. The reason
    no other runtime query can either is the revoked privilege on the
    base table, not this statement.
    """
    if not topics:
        return ()

    try:
        cur.execute(
            """
            SELECT
                u.id::text,
                u.unit_key,
                u.topic,
                u.statement,
                u.source_locator,
                u.verification_status,
                u.effective_from,
                u.effective_to,
                u.scope_tags
            FROM app.active_knowledge_units u
            WHERE u.release_id = %s
              AND u.topic = ANY(%s)
              AND u.effective_from <= %s
              AND (u.effective_to IS NULL OR u.effective_to >= %s)
            ORDER BY u.topic, u.unit_key
            """,
            (release_id, list(topics), material_date, material_date),
        )
        rows = cur.fetchall()
    except psycopg.Error as exc:
        raise KnowledgeSystemFailure(
            f"knowledge retrieval failed: {exc.__class__.__name__}"
        ) from exc

    return tuple(
        KnowledgeUnit(
            unit_id=row[0],
            unit_key=row[1],
            topic=row[2],
            statement=row[3],
            source_locator=row[4],
            verification_status=row[5],
            effective_from=row[6],
            effective_to=row[7],
            scope_tags=tuple(row[8] or ()),
        )
        for row in rows
    )


def declared_conflicts(cur, unit_ids):
    """Return curator-declared conflicts among the given units.

    A conflict is never inferred from the text. A human recorded it, so
    a SOURCE_CONFLICT decision can always be traced to that record.
    """
    if len(unit_ids) < 2:
        return ()

    try:
        cur.execute(
            """
            SELECT unit_id::text, conflicting_unit_id::text, note
            FROM app.knowledge_unit_conflicts
            WHERE unit_id = ANY(%s) AND conflicting_unit_id = ANY(%s)
            ORDER BY unit_id, conflicting_unit_id
            """,
            (list(unit_ids), list(unit_ids)),
        )
        rows = cur.fetchall()
    except psycopg.Error as exc:
        raise KnowledgeSystemFailure(
            f"conflict lookup failed: {exc.__class__.__name__}"
        ) from exc

    return tuple(
        {"unit_id": row[0], "conflicting_unit_id": row[1], "note": row[2]}
        for row in rows
    )


def approved(units):
    """Units that clear the approved-guidance bar.

    Being in an ACTIVE release only makes a unit
    retrievable. Supporting an answer needs a qualified
    professional to have verified it.
    """
    return tuple(
        unit for unit in units if unit.professionally_verified
    )


def coverage_gaps(units, topics):
    """Topics that were asked about and returned no effective unit."""
    covered = {unit.topic for unit in units}
    return tuple(topic for topic in topics if topic not in covered)
