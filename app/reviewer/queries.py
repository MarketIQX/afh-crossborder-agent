"""Read-only queries backing the reviewer view.

The reviewer view exists so a human can see exactly what the agent saw
and exactly what it is proposing, before anyone builds an approval path
on top of it.

Every function here reads, but that is a property of these functions,
not of the connection. The runtime role can write elsewhere in the
system, so this module's discipline is what keeps the view read-only,
and it is not backed by a database privilege.
"""

import psycopg

from app import config


def connect():
    return psycopg.connect(**config.database_settings().app_kwargs())


def case_list(conn):
    """Cases with their newest decision, most recently updated first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.id::text,
                c.reference,
                c.lifecycle_status,
                s.service_key,
                (
                    SELECT count(*) FROM app.inbound_messages m
                    WHERE m.case_id = c.id
                ) AS messages,
                (
                    SELECT r.decision_state
                    FROM app.proposal_revisions r
                    JOIN app.action_proposals p ON p.id = r.proposal_id
                    WHERE p.case_id = c.id
                    ORDER BY r.created_at DESC
                    LIMIT 1
                ) AS latest_decision,
                c.updated_at
            FROM app.cases c
            LEFT JOIN app.services s ON s.id = c.service_id
            ORDER BY c.updated_at DESC, c.id
            LIMIT 200
            """
        )
        return cur.fetchall()


def quarantined_messages(conn):
    """Messages no case could safely be chosen for."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                m.id::text,
                m.provider_message_id,
                m.sender_address,
                m.subject,
                m.received_at,
                m.correlation_status
            FROM app.inbound_messages m
            WHERE m.case_id IS NULL
            ORDER BY m.received_at DESC
            LIMIT 100
            """
        )
        return cur.fetchall()


def case_header(conn, case_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.id::text,
                c.reference,
                c.lifecycle_status,
                c.service_id::text,
                s.service_key,
                s.name,
                c.created_at,
                c.updated_at
            FROM app.cases c
            LEFT JOIN app.services s ON s.id = c.service_id
            WHERE c.id = %s
            """,
            (case_id,),
        )
        return cur.fetchone()


def case_messages(conn, case_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                sender_address,
                subject,
                body_text,
                received_at,
                correlation_status,
                correlation_method,
                provider_message_id
            FROM app.inbound_messages
            WHERE case_id = %s
            ORDER BY received_at, id
            """,
            (case_id,),
        )
        return cur.fetchall()


def case_facts(conn, case_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                predicate,
                value_text,
                status,
                origin,
                run_id::text,
                created_at
            FROM app.case_facts
            WHERE case_id = %s
            ORDER BY predicate, created_at
            """,
            (case_id,),
        )
        return cur.fetchall()


def required_facts(conn, service_id):
    if service_id is None:
        return []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT predicate, is_material, prompt_hint
            FROM app.service_required_facts
            WHERE service_id = %s
            ORDER BY predicate
            """,
            (service_id,),
        )
        return cur.fetchall()


def revisions(conn, case_id):
    """Every proposal revision for the case, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.id::text,
                r.proposal_id::text,
                r.revision,
                r.decision_state,
                r.summary,
                r.payload,
                r.cited_unit_ids,
                r.missing_predicates,
                r.knowledge_release_id::text,
                r.requires_professional_verification,
                r.run_id::text,
                r.created_at
            FROM app.proposal_revisions r
            JOIN app.action_proposals p ON p.id = r.proposal_id
            WHERE p.case_id = %s
            ORDER BY r.created_at DESC, r.revision DESC
            """,
            (case_id,),
        )
        return cur.fetchall()


def units_by_id(conn, unit_ids):
    """Resolve cited unit ids to their source passages.

    Reads the ACTIVE-only view, so a citation that can no longer be
    resolved is shown as unresolved rather than silently omitted.
    """
    if not unit_ids:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text,
                unit_key,
                topic,
                statement,
                source_locator,
                source_version,
                verification_status,
                captured_passage,
                effective_from,
                effective_to,
                release_version
            FROM app.active_knowledge_units
            WHERE id = ANY(%s)
            ORDER BY topic, unit_key
            """,
            (list(unit_ids),),
        )
        rows = cur.fetchall()

    return {row[0]: row for row in rows}


def runs(conn, case_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id::text,
                operation_id,
                runner,
                model_id,
                prompt_version,
                knowledge_release_id::text,
                started_at,
                latency_ms,
                tool_call_count,
                result_state,
                failure_reason,
                context_builder_version
            FROM app.agent_runs
            WHERE case_id = %s
            ORDER BY started_at DESC
            """,
            (case_id,),
        )
        return cur.fetchall()


def tool_calls(conn, run_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sequence, tool_name, arguments, result_summary, error
            FROM app.agent_tool_calls
            WHERE run_id = %s
            ORDER BY sequence
            """,
            (run_id,),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------
# Requests for help.
#
# Every field below is stored. Nothing on the screen that says a gap is
# open, assigned or answered is computed at render time from something
# softer -- `gap_state` and `resolved_at` are constrained to agree in the
# database, so a status shown here cannot disagree with the record.
# ---------------------------------------------------------------------


def open_requests(conn):
    """What Nicole could not answer, oldest first.

    Oldest first on purpose: a queue that shows the newest request at
    the top quietly buries the one that has been waiting longest, which
    is the opposite of what a person triaging needs.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                g.id::text,
                c.id::text,
                c.reference,
                g.reason_codes,
                g.question,
                g.created_at,
                r.display_name,
                g.draft_rendered_at IS NOT NULL AS has_draft,
                v.decision_state
            FROM app.knowledge_gaps g
            JOIN app.cases c ON c.id = g.case_id
            JOIN app.proposal_revisions v ON v.id = g.revision_id
            LEFT JOIN app.reviewers r ON r.id = g.assigned_reviewer_id
            WHERE g.gap_state = 'OPEN'
            ORDER BY g.created_at
            """
        )
        return cur.fetchall()


def requests_for_case(conn, case_id):
    """Every request raised on one case, including settled ones.

    Settled requests are included because the history is the point: a
    reviewer looking at a case should see what was asked before, not
    only what is outstanding.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                g.id::text,
                g.reason_codes,
                g.question,
                g.gap_state,
                g.created_at,
                g.resolved_at,
                r.display_name,
                g.draft_body
            FROM app.knowledge_gaps g
            LEFT JOIN app.reviewers r ON r.id = g.assigned_reviewer_id
            WHERE g.case_id = %s
            ORDER BY g.created_at DESC
            """,
            (case_id,),
        )
        return cur.fetchall()


def request_counts(conn):
    """Open requests, for the navigation. Zero is a real answer."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM app.knowledge_gaps "
            "WHERE gap_state = 'OPEN'"
        )
        return cur.fetchone()[0]
