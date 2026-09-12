"""Record which class of person a signed rule is about.

    python scripts/tag_applicability.py
    python scripts/tag_applicability.py --unit <id> --residency NON_RESIDENT
    python scripts/tag_applicability.py --unit <id> --citizenship INDIAN
    python scripts/tag_applicability.py --unit <id> --residency ""

This is a professional judgement, not a configuration value, which is
why it is a deliberate act by a named reviewer rather than something
inferred from the text of the rule. A model could guess which class of
person a statement is about; a guess is exactly what must not decide
whose tax position a rule governs.

Run with no arguments to read the corpus and decide. Nothing is written
until a unit is named.

Reviewer connection throughout. Migration 022 grants UPDATE on exactly
two columns, so this cannot alter what a rule says, the evidence under
it, or whose name is on it. Migration 019's CHECK constraints refuse an
invalid class in the database, not merely here.

Clearing a dimension (passing an empty string) means "this rule does not
narrow itself on that dimension", which is the default. It is NOT the
same as unknown: an unrestricted rule applies to everyone, while a
restricted rule whose fact is unestablished becomes a question to the
client.
"""

import argparse
import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.training import store  # noqa: E402

SETTINGS = config.database_settings()


def reviewer_conn():
    return psycopg.connect(**SETTINGS.reviewer_kwargs())


def listing(conn):
    """Everything signed, with its current restriction and its words."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id::text, u.topic, u.applies_to_residency,
                   u.applies_to_citizenship, u.statement, s.service_key
            FROM app.knowledge_units u
            JOIN app.knowledge_releases r ON r.id = u.release_id
            JOIN app.services s ON s.id = r.service_id
            WHERE u.verification_status = 'PROFESSIONALLY_VERIFIED'
            ORDER BY s.service_key, u.topic, u.unit_key
            """
        )
        rows = cur.fetchall()

    if not rows:
        print("No professionally verified units. Nothing to tag.")
        return

    allowed = store.applicability_values()

    print(f"{len(rows)} signed unit(s). Accepted classes:")

    for name, values in allowed.items():
        print(f"  {name:<12} {', '.join(values)}")

    print("\n  (blank means the rule does not narrow itself there, so it "
          "applies to everyone)\n")

    for unit_id, topic, res, cit, statement, service in rows:
        print(f"{unit_id}")
        print(f"  service    {service}")
        print(f"  topic      {topic}")
        print(f"  residency  {res or '-'}")
        print(f"  citizenship{'':<1}{cit or '-'}")
        print(f"  says       {statement[:150]}"
              f"{'...' if len(statement) > 150 else ''}")
        print()

    print("To record a judgement:")
    print("  python scripts/tag_applicability.py --unit <id> "
          "--residency NON_RESIDENT")


def main():
    parser = argparse.ArgumentParser(
        description="Record which class of person a signed rule is about."
    )
    parser.add_argument("--unit", help="knowledge unit id")
    parser.add_argument(
        "--residency",
        help="RESIDENT, NON_RESIDENT, RNOR, or \"\" to clear",
    )
    parser.add_argument(
        "--citizenship",
        help="INDIAN, PIO, FOREIGN, or \"\" to clear",
    )
    args = parser.parse_args()

    with reviewer_conn() as conn:
        if not args.unit:
            listing(conn)
            return 0

        if args.residency is None and args.citizenship is None:
            print(
                "Name at least one dimension. Nothing was changed.",
                file=sys.stderr,
            )
            return 2

        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic, applies_to_residency, "
                "applies_to_citizenship FROM app.knowledge_units "
                "WHERE id = %s",
                (args.unit,),
            )
            before = cur.fetchone()

        if before is None:
            print(f"No knowledge unit {args.unit}.", file=sys.stderr)
            return 1

        print(f"before  topic={before[0]} residency={before[1] or '-'} "
              f"citizenship={before[2] or '-'}")

        try:
            after = store.set_applicability(
                conn,
                args.unit,
                residency=args.residency,
                citizenship=args.citizenship,
            )
        except store.TrainingRefused as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1

        conn.commit()

        print(f"after   topic={after['topic']} "
              f"residency={after['applies_to_residency'] or '-'} "
              f"citizenship={after['applies_to_citizenship'] or '-'}")
        print("\nRecorded. A case whose confirmed facts contradict this "
              "class will now have the rule excluded, with the ground "
              "written into the decision record.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
