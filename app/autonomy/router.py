"""Decide which service a case belongs to, without a language model.

This is the step that used to require a person, and it is the reason the
system could not run unattended. It is deliberately the dumbest component
in the project.

Why a keyword table rather than the model. The model must never choose
its own service scope: that is precisely the path by which it could reach
another corridor's knowledge, and it has already attempted exactly that
twice in recorded runs. Handing it triage would hand it the boundary it
tried to cross. So routing is done by matching seeded keywords against
the client's own words, entirely server side, with no inference in it.

Why it refuses rather than guesses. Routing a case wrongly is worse than
not routing it, because a wrongly scoped case is answered from the wrong
body of law and still looks confident. It routes only on a dominant
signal: the leading service must carry at least two distinct matches and
at least twice the nearest rival's. Anything short of that, or nothing
matching at all, is left for a human. Every outcome is written to `triage_attempts`, so
a reviewer reading the queue can see what the machine found ambiguous
rather than guessing why a case is still waiting.
"""

import re
import uuid

ROUTED = "ROUTED"
AMBIGUOUS = "AMBIGUOUS"
NO_MATCH = "NO_MATCH"

KEYWORD_ROUTER = "KEYWORD_ROUTER"

# The leading service must carry at least this many distinct keyword
# matches before the router will act on its own.
MIN_MATCHES_TO_ROUTE = 2

# ...and at least this multiple of the nearest rival's matches.
DOMINANCE_FACTOR = 2

# A keyword must appear as a whole word. Without this, "return" matches
# inside "returning" and the FEMA keyword "nre" matches inside
# "generated", which routes a case on a coincidence of spelling.
def _mentions(haystack, keyword):
    pattern = r"\b" + re.escape(keyword.lower()) + r"\b"

    return re.search(pattern, haystack) is not None


def _case_text(cur, case_id):
    """Every word the client wrote on this case, lowercased."""
    cur.execute(
        """
        SELECT coalesce(subject, '') || ' ' || coalesce(body_text, '')
        FROM app.inbound_messages
        WHERE case_id = %s
        ORDER BY received_at
        """,
        (case_id,),
    )

    return " ".join(row[0] for row in cur.fetchall()).lower()


def _keywords(cur):
    """The seeded keyword catalogue, as (service_id, topic, keyword)."""
    cur.execute(
        """
        SELECT service_id::text, topic, keyword
        FROM app.topic_keywords
        """
    )

    return cur.fetchall()


def evaluate(cur, case_id):
    """Decide, without writing anything. Returns (outcome, service, detail)."""
    text = _case_text(cur, case_id)

    if not text.strip():
        return NO_MATCH, None, "the case carries no client text"

    hits = {}

    for service_id, topic, keyword in _keywords(cur):
        if _mentions(text, keyword):
            hits.setdefault(service_id, set()).add(f"{topic}:{keyword}")

    if not hits:
        return NO_MATCH, None, "no seeded keyword appears in the enquiry"

    ranked = sorted(
        hits.items(), key=lambda item: len(item[1]), reverse=True
    )
    service_id, matches = ranked[0]
    runner_up = len(ranked[1][1]) if len(ranked) > 1 else 0

    # Dominance, not uniqueness. One incidental noun should not be able
    # to veto an enquiry that is otherwise unmistakable, and a real
    # straddle should still reach a person.
    dominant = len(matches) >= MIN_MATCHES_TO_ROUTE and (
        len(matches) >= runner_up * DOMINANCE_FACTOR
    )

    if not dominant:
        summary = "; ".join(
            f"{service}={sorted(found)}" for service, found in ranked
        )

        return (
            AMBIGUOUS,
            None,
            f"no dominant service across {len(hits)} candidates, "
            f"a human must choose: {summary}",
        )

    detail = f"matched {sorted(matches)}"

    if runner_up:
        detail += f", ahead of the next service {len(matches)}-{runner_up}"

    return ROUTED, service_id, detail


def triage(conn, case_id):
    """Evaluate and record. Assigns a service only on an unambiguous match.

    Returns the outcome. Writing the attempt is not conditional on the
    outcome: a case nobody routed is a fact a reviewer needs, and the
    reason it was not routed is the useful part.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT service_id FROM app.cases WHERE id = %s",
                (case_id,),
            )
            row = cur.fetchone()

            if row is None:
                raise ValueError(f"no such case {case_id}")

            if row[0] is not None:
                return ROUTED, "already triaged"

            outcome, service_id, detail = evaluate(cur, case_id)

            cur.execute(
                """
                INSERT INTO app.triage_attempts
                    (id, case_id, outcome, chosen_service_id, detail)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    case_id,
                    outcome,
                    service_id,
                    detail[:500],
                ),
            )

            if outcome == ROUTED:
                cur.execute(
                    """
                    UPDATE app.cases
                    SET service_id = %s,
                        triage_method = %s,
                        triaged_at = now(),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (service_id, KEYWORD_ROUTER, case_id),
                )

            return outcome, detail


def untriaged(conn, limit=50):
    """Cases with no service scope, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text
            FROM app.cases
            WHERE service_id IS NULL
            ORDER BY created_at
            LIMIT %s
            """,
            (limit,),
        )

        return [row[0] for row in cur.fetchall()]
