"""Regression checks for RDS-safe runtime-role bootstrap convergence."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import bootstrap  # noqa: E402


class FakeCursor:
    def __init__(self, role_states):
        self.role_states = role_states
        self.executed = []
        self.role_lookups = []
        self._row = None

    def execute(self, query, params=None):
        text = str(query)
        self.executed.append((text, params))

        if "pg_roles" in text:
            role = params[0]
            self.role_lookups.append(role)
            self._row = self.role_states.get(role)
        elif "pg_namespace" in text:
            self._row = (1,)
        elif "has_schema_privilege" in text:
            self._row = (True, False)
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def statements(self, prefix):
        return [text for text, _ in self.executed if prefix in text]


class Settings:
    app_user = "agents_app"
    reviewer_user = "agents_reviewer"


SAFE = (True, False, False)


def assert_fails_closed(state, protected_word):
    cur = FakeCursor({"agents_app": state})

    try:
        bootstrap._ensure_role(cur, "agents_app", "password")
    except SystemExit as exc:
        if protected_word not in str(exc):
            raise RuntimeError(f"wrong failure: {exc}")
    else:
        raise RuntimeError("unsafe existing role was accepted")

    if cur.statements("ALTER ROLE"):
        raise RuntimeError("protected state was altered instead of rejected")


def test_existing_safe_role():
    cur = FakeCursor({"agents_app": SAFE})
    result = bootstrap._ensure_role(cur, "agents_app", "password")
    alter = cur.statements("ALTER ROLE")

    if result != "CONVERGED" or len(alter) != 1:
        raise RuntimeError("safe existing role did not converge")

    for forbidden in ("SUPERUSER", "NOSUPERUSER", "REPLICATION", "NOREPLICATION"):
        if forbidden in alter[0]:
            raise RuntimeError(
                f"existing-role ALTER contains protected attribute {forbidden}"
            )

    for required in (
        "LOGIN",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "PASSWORD",
    ):
        if required not in alter[0]:
            raise RuntimeError(f"existing-role ALTER omitted {required}")

    print("BOOTROLE01 SAFE EXISTING ROLE CONVERGES: PASS")


def test_unsafe_protected_roles_fail_closed():
    assert_fails_closed((True, True, False), "SUPERUSER")
    print("BOOTROLE02 SUPERUSER EXISTING ROLE FAILS CLOSED: PASS")

    assert_fails_closed((True, False, True), "REPLICATION")
    print("BOOTROLE03 REPLICATION EXISTING ROLE FAILS CLOSED: PASS")


def test_create_path_remains_least_privilege():
    cur = FakeCursor({})
    result = bootstrap._ensure_role(cur, "agents_app", "password")
    create = cur.statements("CREATE ROLE")

    if result != "CREATED" or len(create) != 1:
        raise RuntimeError("new role was not created")

    for required in (
        "LOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOREPLICATION",
    ):
        if required not in create[0]:
            raise RuntimeError(f"CREATE ROLE omitted {required}")

    print("BOOTROLE04 CREATE PATH LEAST PRIVILEGE: PASS")


def test_both_roles_are_verified():
    cur = FakeCursor({"agents_app": SAFE, "agents_reviewer": SAFE})
    bootstrap._verify(cur, Settings())

    if cur.role_lookups != ["agents_app", "agents_reviewer"]:
        raise RuntimeError(f"both roles not verified: {cur.role_lookups!r}")

    print("BOOTROLE05 BOTH RUNTIME ROLES VERIFIED: PASS")

    cur = FakeCursor({"agents_app": SAFE, "agents_reviewer": (True, False, True)})
    try:
        bootstrap._verify(cur, Settings())
    except SystemExit as exc:
        if "agents_reviewer has REPLICATION" not in str(exc):
            raise RuntimeError(f"reviewer protected-state failure was wrong: {exc}")
    else:
        raise RuntimeError("reviewer replication state was not rejected")

    print("BOOTROLE06 REVIEWER PROTECTED STATE VERIFIED: PASS")


def main():
    test_existing_safe_role()
    test_unsafe_protected_roles_fail_closed()
    test_create_path_remains_least_privilege()
    test_both_roles_are_verified()
    print("BOOTSTRAP ROLE CONVERGENCE: PASS")


if __name__ == "__main__":
    main()
