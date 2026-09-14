"""Cluster level database bootstrap.

Migration `001_mail_ingestion.sql` creates tables in schema `app` and
grants column privileges to role `agents_app`. Neither the schema nor
the role was ever created by anything in this repository, so a clean
clone could not build the database. This module closes that gap.

It is deliberately separate from the migration ledger:

- Roles and passwords are cluster state driven by the environment, not
  immutable versioned DDL, so they must converge on every run rather
  than be applied exactly once.
- Migrations are checksummed and applied exactly once, in order.

Running this twice is safe. Running it against an already correct
instance changes nothing and still verifies the outcome.
"""

import sys

import psycopg
from psycopg import sql

from app import config

SCHEMA = "app"


def _role_exists(cur, role):
    cur.execute(
        "SELECT rolcanlogin, rolsuper, rolreplication "
        "FROM pg_roles WHERE rolname = %s",
        (role,),
    )
    return cur.fetchone()


def _ensure_role(cur, role, password):
    """Create the runtime role, or converge its password and attributes.

    The password is passed through `sql.Literal` so it is quoted by the
    driver. The composed statement is never printed.
    """
    existing = _role_exists(cur, role)

    if existing is None:
        cur.execute(
            sql.SQL(
                "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD {}"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        return "CREATED"

    _can_login, is_super, can_replicate = existing

    # RDS master users have CREATEROLE but are not PostgreSQL SUPERUSERs.
    # They therefore cannot alter these protected attributes, even to set
    # their safe values. Treat an unsafe existing role as a configuration
    # failure rather than trying to repair it with unavailable authority.
    if is_super:
        raise SystemExit(
            f"BOOTSTRAP ROLE {role}: existing role is SUPERUSER"
        )

    if can_replicate:
        raise SystemExit(
            f"BOOTSTRAP ROLE {role}: existing role has REPLICATION"
        )

    cur.execute(
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN NOCREATEDB "
            "NOCREATEROLE NOINHERIT PASSWORD {}"
        ).format(sql.Identifier(role), sql.Literal(password))
    )
    return "CONVERGED"


def _ensure_schema(cur, owner):
    cur.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname = %s",
        (SCHEMA,),
    )
    present = cur.fetchone() is not None

    if not present:
        cur.execute(
            sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                sql.Identifier(SCHEMA), sql.Identifier(owner)
            )
        )

    return "PRESENT" if present else "CREATED"


def _ensure_privileges(cur, settings):
    """Grant exactly what the runtime role needs and revoke the rest.

    The runtime role may connect and resolve names inside schema `app`.
    It may not create objects anywhere, and it gets no table privileges
    here. Table and column privileges are granted only by migrations,
    so the least-privilege surface stays reviewable in version control.
    """
    app_role = sql.Identifier(settings.app_user)
    reviewer_role = sql.Identifier(settings.reviewer_user)
    schema = sql.Identifier(SCHEMA)
    database = sql.Identifier(settings.dbname)

    cur.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            database, app_role
        )
    )

    cur.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database)
    )

    cur.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, app_role)
    )

    cur.execute(
        sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
            schema, app_role
        )
    )

    cur.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            database, reviewer_role
        )
    )

    cur.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            schema, reviewer_role
        )
    )

    cur.execute(
        sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
            schema, reviewer_role
        )
    )

    cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def _verify(cur, settings):
    """Assert the intended end state. Raises on any deviation."""
    failures = []

    for role in (settings.app_user, settings.reviewer_user):
        role_row = _role_exists(cur, role)

        if role_row is None:
            failures.append(f"role {role} missing")
            continue

        can_login, is_super, can_replicate = role_row

        if not can_login:
            failures.append(f"role {role} cannot log in")

        if is_super:
            failures.append(f"role {role} is SUPERUSER")

        if can_replicate:
            failures.append(f"role {role} has REPLICATION")

    cur.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname = %s",
        (SCHEMA,),
    )

    if cur.fetchone() is None:
        failures.append(f"schema {SCHEMA} missing")

    for role in (settings.app_user, settings.reviewer_user):
        cur.execute(
            "SELECT has_schema_privilege(%s, %s, 'USAGE'), "
            "has_schema_privilege(%s, %s, 'CREATE')",
            (role, SCHEMA, role, SCHEMA),
        )
        has_usage, has_create = cur.fetchone()

        if not has_usage:
            failures.append(f"{role} lacks USAGE on {SCHEMA}")

        if has_create:
            failures.append(f"{role} holds CREATE on {SCHEMA}")

    if failures:
        raise SystemExit("BOOTSTRAP VERIFY: FAIL\n  " + "\n  ".join(failures))


def run():
    settings = config.database_settings()
    app_password = config.require("POSTGRES_APP_PASSWORD")
    reviewer_password = config.require("POSTGRES_REVIEWER_PASSWORD")

    print(f"BOOTSTRAP TARGET: {settings.target()}")

    with psycopg.connect(**settings.admin_kwargs()) as conn:
        with conn.cursor() as cur:
            role_state = _ensure_role(cur, settings.app_user, app_password)
            print(f"BOOTSTRAP ROLE {settings.app_user}: {role_state}")

            reviewer_state = _ensure_role(
                cur, settings.reviewer_user, reviewer_password
            )
            print(
                f"BOOTSTRAP ROLE {settings.reviewer_user}: "
                f"{reviewer_state}"
            )

            schema_state = _ensure_schema(cur, settings.admin_user)
            print(f"BOOTSTRAP SCHEMA {SCHEMA}: {schema_state}")

            _ensure_privileges(cur, settings)
            print("BOOTSTRAP PRIVILEGES: APPLIED")

            _verify(cur, settings)

        conn.commit()

    print("BOOTSTRAP VERIFY: PASS")


if __name__ == "__main__":
    try:
        run()
    except psycopg.Error as exc:
        raise SystemExit(f"BOOTSTRAP FAILED: {exc.__class__.__name__}: {exc}")

    sys.exit(0)
