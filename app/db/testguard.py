"""Refuse to let tests write to anything but a marked disposable target.

Three independent conditions are required, and none alone is enough:

1. A durable mark inside the database, written by an administrator who
   deliberately declared that database disposable.
2. An explicit statement of intent from the caller's environment,
   naming the database it believes it is about to write to.
3. The live cluster presenting the same system identifier the mark was
   written against, so a mark cannot authorise writes on a different
   server that happens to use the same database name, and a logical
   restore into another cluster does not inherit the authority.
   Note the limit: a physical copy of a cluster carries its
   identifier with it, so this distinguishes servers, not copies.

Database names are deliberately not used as a safety signal. A name
containing "test" proves nothing about what a database holds.

A process-wide advisory lock additionally restricts a target to one
test run at a time. Suite-specific fixture slots keep suites from
colliding with each other; they do nothing about two runs of the same
suite, which would share every fixture identifier.

What this is not: physical isolation. It reduces the chance of writing
to the wrong place. It is not permission to erase arbitrary state, and
the normal route for running the suites remains the clean-slate
provisioner, which builds and destroys its own instance.

This guard covers fixture writes and cleanup. It does not cover
`bootstrap` or `migrate`, which are the legitimate way to set up a real
instance and must keep working against one.
"""

import os

import psycopg

from app import config

ENV_TARGET = "AGENTS_TEST_TARGET"

PURPOSE = "DISPOSABLE_TEST_TARGET"

MARKER_TABLE = "app.disposable_test_target"

# Arbitrary constant, shared by every suite so they serialise per target.
SINGLE_RUN_LOCK_KEY = 8264117

_run_lock_connection = None


class UnsafeTestTarget(SystemExit):
    """The target is not a database tests are allowed to write to."""


def _refuse(reason, remedy):
    raise UnsafeTestTarget(
        "REFUSING TO WRITE TEST FIXTURES.\n"
        f"  Reason: {reason}\n"
        f"  Remedy: {remedy}"
    )


def server_identity(cur):
    """The cluster's system identifier, or None if unreadable."""
    try:
        cur.execute("SELECT system_identifier::text FROM pg_control_system()")
        return cur.fetchone()[0]
    except psycopg.Error:
        return None


def read_marker(cur):
    """Return (database, marker or None). Never raises on absence."""
    cur.execute("SELECT current_database()")
    current = cur.fetchone()[0]

    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (MARKER_TABLE,))

    if not cur.fetchone()[0]:
        return current, None

    cur.execute(
        f"SELECT dbname, purpose, token, marked_at, marked_by, "
        f"system_identifier FROM {MARKER_TABLE} LIMIT 1"
    )
    row = cur.fetchone()

    if row is None:
        return current, None

    return current, {
        "dbname": row[0],
        "purpose": row[1],
        "token": row[2],
        "marked_at": row[3],
        "marked_by": row[4],
        "system_identifier": row[5],
    }


def assert_disposable_cursor(cur):
    """Raise unless this database is a marked, intended test target.

    Takes a cursor so it can run inside a transaction that is about to
    write, which is what lets fixture helpers enforce it themselves
    rather than trusting their caller to have checked.
    """
    intended = (os.environ.get(ENV_TARGET) or "").strip()
    current, marker = read_marker(cur)

    if not intended:
        _refuse(
            f"{ENV_TARGET} is not set, so the caller has not stated "
            f"which database it intends to write to.",
            f"Set {ENV_TARGET}={current} only if that database is "
            f"disposable, or run scripts/verify_clean_slate.py which "
            f"provisions a throwaway instance.",
        )

    if marker is None:
        _refuse(
            f"database {current!r} carries no disposable-target mark.",
            "An administrator must run "
            "scripts/mark_disposable.py <dbname> against a database "
            "whose contents may be created and deleted freely.",
        )

    if marker["purpose"] != PURPOSE:
        _refuse(
            f"the mark in {current!r} says {marker['purpose']!r}.",
            "Only a DISPOSABLE_TEST_TARGET mark permits fixture writes.",
        )

    if marker["dbname"] != current:
        _refuse(
            f"the mark names {marker['dbname']!r} but this connection is "
            f"to {current!r}.",
            "Re-mark the database deliberately if it really is "
            "disposable.",
        )

    if intended != current:
        _refuse(
            f"{ENV_TARGET} names {intended!r} but this connection is to "
            f"{current!r}.",
            "Point the connection settings and the intent at the same "
            "database.",
        )

    live = server_identity(cur)

    if marker["system_identifier"] is None:
        _refuse(
            f"the mark in {current!r} predates server-identity "
            f"recording, so it cannot show which server it was made "
            f"against.",
            "Re-run scripts/mark_disposable.py to replace it.",
        )

    if live is None:
        _refuse(
            "this connection cannot read the cluster system identifier, "
            "so the server the mark was made against cannot be "
            "confirmed.",
            "Run the guard on a connection permitted to read "
            "pg_control_system().",
        )

    if live != marker["system_identifier"]:
        _refuse(
            f"the mark was made against cluster "
            f"{marker['system_identifier']}, but this connection reaches "
            f"cluster {live}. Same database name, different server.",
            "Mark the server you actually intend to write to.",
        )

    return marker["token"]


def assert_disposable(conn):
    """Connection-level wrapper around `assert_disposable_cursor`."""
    with conn.cursor() as cur:
        return assert_disposable_cursor(cur)


def acquire_single_run_lock():
    """Restrict this target to one test run at a time.

    Fixture identifiers are fixed constants, so two concurrent runs of
    the same suite would seed, assert against and delete each other's
    rows. The lock is held on a dedicated connection for the life of
    the process and released when it exits.
    """
    global _run_lock_connection

    if _run_lock_connection is not None:
        return

    conn = psycopg.connect(
        **config.database_settings().admin_kwargs(), autocommit=True
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(%s)", (SINGLE_RUN_LOCK_KEY,)
        )
        acquired = cur.fetchone()[0]

    if not acquired:
        conn.close()
        _refuse(
            "another test run already holds this target.",
            "Wait for it to finish, or provision a separate disposable "
            "target with scripts/verify_clean_slate.py.",
        )

    _run_lock_connection = conn


def describe(conn):
    """Target description for evidence records. No credentials."""
    with conn.cursor() as cur:
        current, marker = read_marker(cur)

    return {
        "database": current,
        "marked_disposable": marker is not None,
        "token": marker["token"] if marker else None,
        "marked_at": str(marker["marked_at"]) if marker else None,
        "system_identifier": (
            marker["system_identifier"] if marker else None
        ),
    }
