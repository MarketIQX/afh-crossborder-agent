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

import re

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


TEXT_MATCH_LIMIT = 12

# Below this rank a match is a coincidence of common words. With an OR
# expression, one shared ordinary word scores far under this and a rule
# genuinely described in other language scores above it.
MIN_TEXT_RANK = 0.01

# Enough terms to describe a rule, few enough that one rambling email
# cannot turn the query into a scan of the whole corpus.
MAX_QUERY_TERMS = 24

# Words that would match almost any unit. The English dictionary already
# strips true stopwords; these are domain-common terms that are not
# stopwords and carry no discriminating signal here.
_UNHELPFUL = frozenset(
    {
        "india", "indian", "tax", "taxed", "taxes", "taxable",
        "year", "years", "please", "hello", "thanks", "regards",
        "would", "could", "should", "anything", "something",
        "really", "quite", "about", "there", "here", "still",
        "help", "know", "think", "said", "told", "asked",
    }
)

_TOKEN = re.compile(r"[a-z0-9]+")


def search_expression(text):
    """Reduce a client's prose to a safe OR expression, or return None.

    Every term is forced to [a-z0-9]+ before it can reach to_tsquery, so
    search syntax written into an email is treated as words rather than
    as syntax.
    """
    seen = []

    for token in _TOKEN.findall((text or "").lower()):
        if len(token) < 4 or token in _UNHELPFUL or token in seen:
            continue

        seen.append(token)

        if len(seen) >= MAX_QUERY_TERMS:
            break

    if not seen:
        return None

    return " | ".join(seen)


def retrieve_by_query(cur, release_id, topics, query, material_date):
    """Units for the given topics, plus units whose text answers the query.

    Returns (units, matched_by) where matched_by maps unit_id to
    'topic', 'text' or 'both', so a reviewer can see why each unit was
    put in front of the model.

    The scope arguments come first and are not negotiable: one release,
    one effective-date window. Search happens inside that, never across
    it.
    """
    by_id = {}
    matched_by = {}

    for unit in retrieve(cur, release_id, topics, material_date):
        by_id[unit.unit_id] = unit
        matched_by[unit.unit_id] = "topic"

    expression = search_expression(query)

    if expression is None:
        return tuple(by_id.values()), matched_by

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
            u.scope_tags,
            ts_rank(u.searchable, to_tsquery('english', %s))
                AS rank
        FROM app.active_knowledge_units u
        WHERE u.release_id = %s
          AND u.effective_from <= %s
          AND (u.effective_to IS NULL OR u.effective_to >= %s)
          AND u.searchable @@ to_tsquery('english', %s)
        ORDER BY rank DESC, u.topic, u.unit_key
        LIMIT %s
        """,
        (expression, release_id, material_date, material_date,
         expression, TEXT_MATCH_LIMIT),
    )

    for row in cur.fetchall():
        rank = row[9]

        if rank is not None and rank < MIN_TEXT_RANK:
            continue

        unit_id = row[0]

        if unit_id in by_id:
            matched_by[unit_id] = "both"
            continue

        by_id[unit_id] = KnowledgeUnit(
            unit_id=unit_id,
            unit_key=row[1],
            topic=row[2],
            statement=row[3],
            source_locator=row[4],
            verification_status=row[5],
            effective_from=row[6],
            effective_to=row[7],
            scope_tags=tuple(row[8] or ()),
        )
        matched_by[unit_id] = "text"

    return tuple(by_id.values()), matched_by


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
