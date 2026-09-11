"""Prove the repository can build its database from nothing.

The existing database on this machine was built partly by hand, so
passing tests against it proved only that this one instance works. This
script starts a disposable PostgreSQL container with no volume, builds
the database using only the repository working tree, runs every check
against it, and destroys it.

Be precise about what this establishes. It proves no hand-created
cluster state is required: bootstrap plus the migrations are sufficient
to build the database from nothing. It does NOT prove reproducibility
from a git commit, because it runs the working tree, which may contain
uncommitted changes. That claim needs a clean clone of a named commit,
built and tested the same way, and it is only true once this work is
committed.

The primary instance on port 5433 is never touched, and no persistent
volume is created, so this is safe to run at any time.

Usage:

    python scripts/verify_clean_slate.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402

CONTAINER = "agents-for-humans-clean-slate"
IMAGE = "postgres:16-alpine"
PORT = "5434"
DB_NAME = "agents_for_humans"
ADMIN_USER = "agents_admin"

READY_TIMEOUT_SECONDS = 90


def docker(args, check=True, capture=False, env=None):
    return subprocess.run(
        ["docker"] + args,
        check=check,
        capture_output=capture,
        text=True,
        env=env,
    )


def remove_container(quiet=True):
    docker(["rm", "-f", CONTAINER], check=False, capture=quiet)


def start_container(admin_password):
    """Start a throwaway instance. No volume, removed on exit."""
    env = dict(os.environ)
    env["POSTGRES_PASSWORD"] = admin_password
    env["POSTGRES_USER"] = ADMIN_USER
    env["POSTGRES_DB"] = DB_NAME

    docker(
        [
            "run",
            "--rm",
            "--detach",
            "--name",
            CONTAINER,
            "--env",
            "POSTGRES_PASSWORD",
            "--env",
            "POSTGRES_USER",
            "--env",
            "POSTGRES_DB",
            "--publish",
            f"127.0.0.1:{PORT}:5432",
            IMAGE,
        ],
        capture=True,
        env=env,
    )

    print(f"CLEAN SLATE CONTAINER: started on 127.0.0.1:{PORT}")


def wait_until_ready(admin_password):
    """Wait for a real host TCP connection, not for pg_isready.

    During initialisation the official image runs a temporary server on
    the unix socket with listen_addresses empty, so pg_isready inside
    the container reports ready while host connections are still being
    refused or dropped. Connecting the way the next step will connect is
    the only probe that cannot give a false positive.
    """
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last_error = None

    while time.monotonic() < deadline:
        try:
            with psycopg.connect(
                host="127.0.0.1",
                port=int(PORT),
                dbname=DB_NAME,
                user=ADMIN_USER,
                password=admin_password,
                connect_timeout=3,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()

            print("CLEAN SLATE CONTAINER: ready")
            return
        except psycopg.Error as exc:
            last_error = exc.__class__.__name__
            time.sleep(1)

    raise SystemExit(
        f"CLEAN SLATE: FAIL. Instance not ready within "
        f"{READY_TIMEOUT_SECONDS}s. Last error: {last_error}"
    )


def step(label, args, env):
    print(f"\n--- {label} ---")

    result = subprocess.run(
        [sys.executable] + args,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
    )

    if result.returncode != 0:
        raise SystemExit(f"CLEAN SLATE: FAIL at step: {label}")


def build_and_verify():
    """Everything here must work from the repository alone.

    No hand-applied SQL, no pre-existing role, no pre-existing
    schema. If a step here needs something that is not in the
    repository, the build is not reproducible.
    """
    env = dict(os.environ)
    env["POSTGRES_HOST"] = "127.0.0.1"
    env["POSTGRES_PORT"] = PORT
    env["POSTGRES_DB"] = DB_NAME
    env["POSTGRES_ADMIN_PASSWORD"] = config.require("POSTGRES_ADMIN_PASSWORD")
    env["POSTGRES_APP_PASSWORD"] = config.require("POSTGRES_APP_PASSWORD")
    env["POSTGRES_REVIEWER_PASSWORD"] = config.require(
        "POSTGRES_REVIEWER_PASSWORD"
    )
    env["AGENTS_TEST_TARGET"] = DB_NAME

    step("bootstrap", ["-m", "app.db.bootstrap"], env)
    step("migrate apply", ["-m", "app.db.migrate", "apply"], env)
    step("migrate verify", ["-m", "app.db.migrate", "verify"], env)
    step(
        "mark disposable",
        ["scripts/mark_disposable.py", DB_NAME, "clean-slate throwaway"],
        env,
    )
    step("full check suite", ["tests/run_all.py"], env)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    admin_password = config.require("POSTGRES_ADMIN_PASSWORD")

    print("CLEAN SLATE: building the database from repository files only")
    remove_container()

    try:
        start_container(admin_password)
        wait_until_ready(admin_password)
        build_and_verify()
    finally:
        remove_container()
        print("\nCLEAN SLATE CONTAINER: destroyed")

    print("CLEAN SLATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
