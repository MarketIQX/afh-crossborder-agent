"""Deterministic migration runner with a checksum ledger.

Before this existed, nothing recorded which migrations had been applied,
in what order, or whether a migration file was edited after it had
already run. The database state was an oral tradition.

Guarantees provided here:

- Each migration file is applied at most once, in filename order.
- The SHA-256 of every applied file is stored. If a file changes after
  it was applied, every later run fails loudly instead of silently
  diverging.
- The migration and its ledger row commit in the same transaction, so
  a crash cannot leave the ledger disagreeing with the schema.

Modes:

  status   report each migration file and the ledger, change nothing
  verify   exit non-zero if anything is pending or has drifted
  apply    apply pending migrations
  adopt    record pending files as applied WITHOUT executing them

`adopt` exists only because migration 001 was applied to this machine
before the ledger existed. Adoption is an operator assertion, not proof.
The proof that the repository can build the database is a clean-slate
run of `apply` against an empty instance.
"""

import hashlib
import re
import sys

import psycopg

from app import config

MIGRATIONS_DIR = config.REPO_ROOT / "db" / "migrations"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS app.schema_migrations (
    filename text PRIMARY KEY,
    sha256 text NOT NULL,
    applied_mode text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    applied_by text NOT NULL DEFAULT current_user,

    CONSTRAINT schema_migrations_mode_check
        CHECK (applied_mode IN ('EXECUTED', 'ADOPTED'))
)
"""

_BEGIN_RE = re.compile(r"\A\s*BEGIN\s*;", re.IGNORECASE)
_COMMIT_RE = re.compile(r"COMMIT\s*;\s*\Z", re.IGNORECASE)


def _digest(path):
    """SHA-256 over the file, normalised to LF line endings.

    Detects edited SQL. Deliberately does not detect the line-ending
    convention of the machine that checked the file out, because that
    would make every cross-platform clone look like tampering.
    """
    raw = path.read_bytes()
    normalised = raw.replace(b"\r\n", b"\n")

    return hashlib.sha256(normalised).hexdigest()


def _discover():
    if not MIGRATIONS_DIR.is_dir():
        raise SystemExit(f"No migrations directory at {MIGRATIONS_DIR}")

    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)


def _strip_outer_transaction(text):
    """Remove a file's own outer BEGIN/COMMIT so the runner owns the txn.

    The runner needs the DDL and the ledger row in one transaction. A
    COMMIT inside the file would end that transaction early and leave
    the ledger able to disagree with the schema. The checksum is taken
    over the original file bytes, so the file remains the artifact of
    record regardless of this transformation.
    """
    has_begin = bool(_BEGIN_RE.search(text))
    has_commit = bool(_COMMIT_RE.search(text))

    if has_begin != has_commit:
        raise SystemExit(
            "Migration has an unbalanced outer transaction. "
            "Use either both BEGIN and COMMIT, or neither."
        )

    if not has_begin:
        return text

    body = _BEGIN_RE.sub("", text, count=1)
    return _COMMIT_RE.sub("", body, count=1)


def _reject_unsupported(path, text):
    if "CONCURRENTLY" in text.upper():
        raise SystemExit(
            f"{path.name} uses CONCURRENTLY, which cannot run inside a "
            f"transaction. This runner does not support it."
        )


def _ensure_ledger(cur):
    cur.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname = 'app'",
    )

    if cur.fetchone() is None:
        raise SystemExit(
            "Schema 'app' does not exist. Run "
            "`python -m app.db.bootstrap` first."
        )

    cur.execute(LEDGER_DDL)


def _read_ledger(cur):
    cur.execute(
        "SELECT filename, sha256, applied_mode "
        "FROM app.schema_migrations"
    )
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def _classify(files, ledger):
    """Return (report, pending, drifted) for the current file set."""
    report = []
    pending = []
    drifted = []

    for path in files:
        digest = _digest(path)
        recorded = ledger.get(path.name)

        if recorded is None:
            state = "PENDING"
            pending.append(path)
        elif recorded[0] != digest:
            state = "DRIFTED"
            drifted.append(path)
        else:
            state = f"APPLIED ({recorded[1]})"

        report.append((path.name, state, digest[:12]))

    for filename in sorted(ledger):
        if not any(path.name == filename for path in files):
            report.append((filename, "MISSING FROM REPOSITORY", "-"))
            drifted.append(None)

    return report, pending, drifted


def _print_report(report):
    for filename, state, digest in report:
        print(f"MIGRATION {filename}: {state} sha256={digest}")


def _record(cur, path, digest, mode):
    cur.execute(
        "INSERT INTO app.schema_migrations "
        "(filename, sha256, applied_mode) VALUES (%s, %s, %s)",
        (path.name, digest, mode),
    )


def _rehash(conn, files, ledger):
    """Re-record digests for applied migrations under the current method.

    Only rows whose file is still present are touched, and every change
    is printed with both values so the operator can see what was
    re-recorded rather than trusting that nothing moved.
    """
    changed = []

    for path in files:
        recorded = ledger.get(path.name)

        if recorded is None:
            continue

        current = _digest(path)

        if recorded[0] != current:
            changed.append((path.name, recorded[0], current))

    if not changed:
        print("MIGRATE REHASH: every recorded digest already matches")
        return 0

    with conn.cursor() as cur:
        for filename, old, new in changed:
            cur.execute(
                "UPDATE app.schema_migrations SET sha256 = %s "
                "WHERE filename = %s",
                (new, filename),
            )
            print(
                f"MIGRATE REHASHED: {filename} {old[:12]} -> {new[:12]}"
            )

    conn.commit()

    print(f"MIGRATE REHASH: {len(changed)} digest(s) re-recorded")
    return 0


def run(mode):
    settings = config.database_settings()
    files = _discover()

    print(f"MIGRATE TARGET: {settings.target()}")
    print(f"MIGRATE MODE: {mode}")

    with psycopg.connect(**settings.admin_kwargs()) as conn:
        with conn.cursor() as cur:
            _ensure_ledger(cur)
            ledger = _read_ledger(cur)
        conn.commit()

        report, pending, drifted = _classify(files, ledger)
        _print_report(report)

        if drifted and mode != "rehash":
            raise SystemExit(
                "MIGRATE: FAIL. A migration changed after it was applied, "
                "or an applied migration is gone from the repository. "
                "Resolve by hand; the runner will not guess."
            )

        if mode == "status":
            print(f"MIGRATE STATUS: {len(pending)} pending")
            return 0

        if mode == "rehash":
            return _rehash(conn, files, ledger)

        if mode == "verify":
            if pending:
                raise SystemExit(
                    f"MIGRATE VERIFY: FAIL. {len(pending)} migration(s) "
                    f"not applied."
                )
            print("MIGRATE VERIFY: PASS")
            return 0

        if not pending:
            print("MIGRATE: nothing to do")
            return 0

        for path in pending:
            text = path.read_text(encoding="utf-8")
            _reject_unsupported(path, text)
            digest = _digest(path)

            with conn.cursor() as cur:
                if mode == "apply":
                    cur.execute(_strip_outer_transaction(text))
                    _record(cur, path, digest, "EXECUTED")
                    conn.commit()
                    print(f"MIGRATE APPLIED: {path.name}")
                else:
                    _record(cur, path, digest, "ADOPTED")
                    conn.commit()
                    print(f"MIGRATE ADOPTED WITHOUT EXECUTION: {path.name}")

    print("MIGRATE: PASS")
    return 0


def main(argv):
    modes = ("status", "verify", "apply", "adopt", "rehash")

    if len(argv) != 2 or argv[1] not in modes:
        raise SystemExit(f"Usage: python -m app.db.migrate {'|'.join(modes)}")

    if argv[1] == "adopt":
        print(
            "WARNING: adopt records migrations as applied without running "
            "them. This is an operator assertion, not verified proof."
        )

    if argv[1] == "rehash":
        print(
            "WARNING: rehash re-records digests for migrations already "
            "applied. Run it only when the digest METHOD changed, never "
            "to silence a real edit. You are asserting the SQL itself is "
            "unchanged."
        )

    try:
        return run(argv[1])
    except psycopg.Error as exc:
        raise SystemExit(f"MIGRATE FAILED: {exc.__class__.__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
