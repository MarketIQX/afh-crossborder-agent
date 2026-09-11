"""Register a reviewer and grant them cases. Administrator action.

Neither the runtime role nor the reviewer role can do this. Deciding who
may authorise client correspondence, and on which matters, is not a
decision the software makes about itself.

Usage:

    python scripts/register_reviewer.py "Priya Menon" priya@firm.example
    python scripts/register_reviewer.py "Priya Menon" priya@firm.example \\
        --qualification "Chartered Accountant, ICAI" --may-verify
    python scripts/register_reviewer.py --grant-all priya@firm.example
"""

import sys
import uuid
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402


def _reviewer_id(cur, email):
    cur.execute(
        "SELECT id::text FROM app.reviewers WHERE email = %s", (email,)
    )
    row = cur.fetchone()

    return row[0] if row else None


def register(name, email, qualification=None, may_verify=False):
    email = email.strip().lower()

    with psycopg.connect(**config.database_settings().admin_kwargs()) as conn:
        with conn.cursor() as cur:
            existing = _reviewer_id(cur, email)

            if existing:
                print(f"REVIEWER EXISTS: {email} {existing}")
                return existing

            reviewer_id = str(uuid.uuid4())

            cur.execute(
                "INSERT INTO app.reviewers (id, email, display_name, "
                "professional_qualification, may_verify_knowledge) "
                "VALUES (%s, %s, %s, %s, %s)",
                (reviewer_id, email, name, qualification, may_verify),
            )
        conn.commit()

    print(f"REVIEWER REGISTERED: {name} <{email}> {reviewer_id}")

    if may_verify:
        print(f"  may verify knowledge, as {qualification}")

    return reviewer_id


def grant_all(email):
    """Grant every open case. Convenience for a single-reviewer firm."""
    email = email.strip().lower()

    with psycopg.connect(**config.database_settings().admin_kwargs()) as conn:
        with conn.cursor() as cur:
            reviewer_id = _reviewer_id(cur, email)

            if reviewer_id is None:
                raise SystemExit(f"no reviewer with email {email}")

            cur.execute(
                """
                INSERT INTO app.reviewer_case_grants (reviewer_id, case_id)
                SELECT %s, c.id FROM app.cases c
                WHERE c.lifecycle_status = 'OPEN'
                ON CONFLICT (reviewer_id, case_id) DO NOTHING
                """,
                (reviewer_id,),
            )
            granted = cur.rowcount
        conn.commit()

    print(f"GRANTED: {granted} case(s) to {email}")


if __name__ == "__main__":
    argv = sys.argv[1:]

    if "--grant-all" in argv:
        index = argv.index("--grant-all")
        grant_all(argv[index + 1])
        sys.exit(0)

    if len(argv) < 2:
        raise SystemExit(
            'Usage: register_reviewer.py "Name" email [--qualification X] '
            "[--may-verify]"
        )

    qualification = (
        argv[argv.index("--qualification") + 1]
        if "--qualification" in argv
        else None
    )

    register(argv[0], argv[1], qualification, "--may-verify" in argv)
