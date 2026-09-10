import os
import sys
import psycopg

from psycopg.errors import (
    UniqueViolation,
    InsufficientPrivilege,
    CheckViolation,
)

HOST = "127.0.0.1"
PORT = 5433
DB = "agents_for_humans"

ADMIN_USER = "agents_admin"
APP_USER = "agents_app"

MAILBOX_ID = "91000000-0000-0000-0000-000000000001"
CASE_ID = "91500000-0000-0000-0000-000000000001"

MESSAGE_ID = "92000000-0000-0000-0000-000000000001"
DUPLICATE_ROW_ID = "92000000-0000-0000-0000-000000000002"
PREMATCHED_ROW_ID = "92000000-0000-0000-0000-000000000003"

PROVIDER_MESSAGE_ID = "qc-db-provider-message-001"

DEDUP_CONSTRAINT = "inbound_messages_provider_dedup"
CORRELATION_CONSTRAINT = "inbound_messages_case_correlation_consistency"


def admin_conn():
    return psycopg.connect(
        host=HOST,
        port=PORT,
        dbname=DB,
        user=ADMIN_USER,
        password=os.environ["DB_ADMIN_PASSWORD"],
    )


def app_conn():
    return psycopg.connect(
        host=HOST,
        port=PORT,
        dbname=DB,
        user=APP_USER,
        password=os.environ["DB_APP_PASSWORD"],
    )


def seed():
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.inbound_messages WHERE mailbox_id = %s",
                (MAILBOX_ID,),
            )

            cur.execute(
                "DELETE FROM app.ingestion_cursors WHERE mailbox_id = %s",
                (MAILBOX_ID,),
            )

            cur.execute(
                "DELETE FROM app.mailboxes WHERE id = %s",
                (MAILBOX_ID,),
            )

            cur.execute(
                "DELETE FROM app.cases WHERE id = %s",
                (CASE_ID,),
            )

            cur.execute(
                """
                INSERT INTO app.mailboxes (
                    id,
                    provider,
                    address
                )
                VALUES (
                    %s,
                    'gmail',
                    'qc-fixture@example.invalid'
                )
                """,
                (MAILBOX_ID,),
            )

    print("QC FIXTURE SEEDED: PASS")


def db01_valid_insert():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.inbound_messages (
                    id,
                    mailbox_id,
                    provider_message_id,
                    provider_thread_id,
                    rfc_message_id,
                    sender_address,
                    recipient_addresses,
                    subject,
                    body_text,
                    received_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    %s,
                    %s,
                    now()
                )
                """,
                (
                    MESSAGE_ID,
                    MAILBOX_ID,
                    PROVIDER_MESSAGE_ID,
                    "qc-thread-001",
                    "<qc-message-001@example.invalid>",
                    "sender@example.invalid",
                    '["qc-fixture@example.invalid"]',
                    "QC database integrity fixture",
                    "Controlled test data only.",
                ),
            )

    print("DB01 VALID RUNTIME INSERT: PASS")


def db02_initial_state():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    correlation_status,
                    case_id,
                    correlation_method
                FROM app.inbound_messages
                WHERE id = %s
                """,
                (MESSAGE_ID,),
            )

            row = cur.fetchone()

    expected = ("UNASSIGNED", None, None)

    if row != expected:
        raise RuntimeError(
            f"DB02 FAIL: expected {expected!r}, got {row!r}"
        )

    print("DB02 INITIAL UNASSIGNED STATE: PASS")


def db03_duplicate_rejected():
    try:
        with app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.inbound_messages (
                        id,
                        mailbox_id,
                        provider_message_id,
                        sender_address,
                        recipient_addresses,
                        received_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        now()
                    )
                    """,
                    (
                        DUPLICATE_ROW_ID,
                        MAILBOX_ID,
                        PROVIDER_MESSAGE_ID,
                        "another@example.invalid",
                        '["qc-fixture@example.invalid"]',
                    ),
                )

    except UniqueViolation as exc:
        actual = exc.diag.constraint_name

        if actual != DEDUP_CONSTRAINT:
            raise RuntimeError(
                f"DB03 FAIL: wrong unique constraint: {actual!r}"
            )

        print(
            "DB03 DUPLICATE REJECTED BY "
            f"{actual}: PASS"
        )
        return

    raise RuntimeError(
        "DB03 FAIL: duplicate provider message was accepted"
    )


def db04_row_count_one():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM app.inbound_messages
                WHERE mailbox_id = %s
                  AND provider_message_id = %s
                """,
                (
                    MAILBOX_ID,
                    PROVIDER_MESSAGE_ID,
                ),
            )

            count = cur.fetchone()[0]

    if count != 1:
        raise RuntimeError(
            f"DB04 FAIL: expected exactly 1 row, found {count}"
        )

    print("DB04 DUPLICATE LEFT EXACTLY ONE ROW: PASS")


def db05_prematched_insert_rejected():
    try:
        with app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.inbound_messages (
                        id,
                        mailbox_id,
                        provider_message_id,
                        sender_address,
                        recipient_addresses,
                        received_at,
                        correlation_status
                    )
                    VALUES (
                        %s,
                        %s,
                        'qc-prematched-provider-id',
                        'sender@example.invalid',
                        %s::jsonb,
                        now(),
                        'MATCHED'
                    )
                    """,
                    (
                        PREMATCHED_ROW_ID,
                        MAILBOX_ID,
                        '["qc-fixture@example.invalid"]',
                    ),
                )

    except InsufficientPrivilege:
        print(
            "DB05 PRE-MATCHED INSERT DENIED BY RUNTIME PRIVILEGE: PASS"
        )
        return

    raise RuntimeError(
        "DB05 FAIL: runtime supplied correlation_status during INSERT"
    )


def db06_evidence_update_rejected():
    try:
        with app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.inbound_messages
                    SET subject = 'TAMPERED'
                    WHERE id = %s
                    """,
                    (MESSAGE_ID,),
                )

    except InsufficientPrivilege:
        print(
            "DB06 PROVIDER EVIDENCE UPDATE DENIED: PASS"
        )
        return

    raise RuntimeError(
        "DB06 FAIL: runtime modified protected provider evidence"
    )


def db07_invalid_match_rejected():
    try:
        with app_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.inbound_messages
                    SET
                        correlation_status = 'MATCHED',
                        correlation_method = 'THREAD_RFC'
                    WHERE id = %s
                    """,
                    (MESSAGE_ID,),
                )

    except CheckViolation as exc:
        actual = exc.diag.constraint_name

        if actual != CORRELATION_CONSTRAINT:
            raise RuntimeError(
                f"DB07 FAIL: wrong CHECK constraint: {actual!r}"
            )

        print(
            "DB07 INVALID MATCH REJECTED BY "
            f"{actual}: PASS"
        )
        return

    raise RuntimeError(
        "DB07 FAIL: MATCHED without case_id was accepted"
    )


def db08_valid_match_transition():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.cases (id)
                VALUES (%s)
                """,
                (CASE_ID,),
            )

            cur.execute(
                """
                UPDATE app.inbound_messages
                SET
                    case_id = %s,
                    correlation_status = 'MATCHED',
                    correlation_method = 'THREAD_RFC'
                WHERE id = %s
                """,
                (
                    CASE_ID,
                    MESSAGE_ID,
                ),
            )

    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    case_id::text,
                    correlation_status,
                    correlation_method
                FROM app.inbound_messages
                WHERE id = %s
                """,
                (MESSAGE_ID,),
            )

            row = cur.fetchone()

    expected = (
        CASE_ID,
        "MATCHED",
        "THREAD_RFC",
    )

    if row != expected:
        raise RuntimeError(
            f"DB08 FAIL: expected {expected!r}, got {row!r}"
        )

    print("DB08 VALID UNASSIGNED TO MATCHED TRANSITION: PASS")


def db09_evidence_preserved():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    provider_message_id,
                    sender_address,
                    subject,
                    body_text
                FROM app.inbound_messages
                WHERE id = %s
                """,
                (MESSAGE_ID,),
            )

            row = cur.fetchone()

    expected = (
        PROVIDER_MESSAGE_ID,
        "sender@example.invalid",
        "QC database integrity fixture",
        "Controlled test data only.",
    )

    if row != expected:
        raise RuntimeError(
            f"DB09 FAIL: protected evidence changed: {row!r}"
        )

    print("DB09 PROTECTED PROVIDER EVIDENCE PRESERVED: PASS")


def db10_restart_persistence():
    with app_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    provider_message_id,
                    subject,
                    case_id::text,
                    correlation_status,
                    correlation_method
                FROM app.inbound_messages
                WHERE id = %s
                """,
                (MESSAGE_ID,),
            )

            row = cur.fetchone()

    expected = (
        PROVIDER_MESSAGE_ID,
        "QC database integrity fixture",
        CASE_ID,
        "MATCHED",
        "THREAD_RFC",
    )

    if row != expected:
        raise RuntimeError(
            f"DB10 FAIL: durable state mismatch: {row!r}"
        )

    print("DB10 POSTGRES RESTART PERSISTENCE: PASS")


def cleanup():
    with admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.inbound_messages WHERE mailbox_id = %s",
                (MAILBOX_ID,),
            )

            cur.execute(
                "DELETE FROM app.ingestion_cursors WHERE mailbox_id = %s",
                (MAILBOX_ID,),
            )

            cur.execute(
                "DELETE FROM app.mailboxes WHERE id = %s",
                (MAILBOX_ID,),
            )

            cur.execute(
                "DELETE FROM app.cases WHERE id = %s",
                (CASE_ID,),
            )

    print("QC FIXTURE CLEANUP: PASS")


def phase1():
    seed()

    db01_valid_insert()
    db02_initial_state()
    db03_duplicate_rejected()
    db04_row_count_one()
    db05_prematched_insert_rejected()
    db06_evidence_update_rejected()
    db07_invalid_match_rejected()
    db08_valid_match_transition()
    db09_evidence_preserved()

    print("DATABASE BEHAVIORAL PHASE 1: PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: db_integrity_smoke.py phase1|restart|cleanup"
        )

    mode = sys.argv[1]

    if mode == "phase1":
        phase1()

    elif mode == "restart":
        db10_restart_persistence()

    elif mode == "cleanup":
        cleanup()

    else:
        raise SystemExit(
            f"Unknown mode: {mode}"
        )
