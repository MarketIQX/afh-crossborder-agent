"""Register a personal agent for a reviewer.

An agent profile is administered, not self-registered. The runtime role
holds SELECT on `app.agent_profiles` and not INSERT, so this runs as
the schema owner: an agent that can create its own identity can name
itself into an authority nobody granted it.

Usage:

    python scripts/register_agent_profile.py --owner aks@example.com
    python scripts/register_agent_profile.py --owner <reviewer-id> \\
        --name Maya

The name defaults to AGENT_DISPLAY_NAME, so a deployment that has
already chosen what to call its agent does not have to repeat it here.
Nothing behaves differently because of the name.

Prints what it did, or what already existed. Registering twice is not
an error: the second run reports the profile it found.
"""

import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import psycopg  # noqa: E402

from app import config  # noqa: E402


def flag(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def resolve_owner(cur, wanted):
    """The reviewer this agent will act for, by id or email."""
    cur.execute(
        """
        SELECT id::text, display_name, is_active
        FROM app.reviewers
        WHERE id::text = %s OR email = lower(%s)
        """,
        (wanted, wanted),
    )

    return cur.fetchone()


def main(argv):
    owner = flag(argv, "--owner")

    if not owner:
        raise SystemExit(
            "Usage: register_agent_profile.py --owner <id|email> "
            "[--name <display name>]"
        )

    display_name = flag(
        argv, "--name", config.get("AGENT_DISPLAY_NAME", "Nicole")
    )

    with psycopg.connect(
        **config.database_settings().admin_kwargs(), autocommit=True
    ) as conn:
        with conn.cursor() as cur:
            found = resolve_owner(cur, owner)

            if found is None:
                raise SystemExit(
                    f"No reviewer matches {owner!r}. Register the person "
                    f"first with scripts/register_reviewer.py."
                )

            owner_id, owner_name, is_active = found

            if not is_active:
                raise SystemExit(
                    f"{owner_name} is not an active reviewer. An agent "
                    f"acts on its owner's authority, so an inactive "
                    f"owner would mean an agent that can do nothing."
                )

            cur.execute(
                """
                SELECT id::text, display_name
                FROM app.agent_profiles
                WHERE owner_reviewer_id = %s AND is_active
                ORDER BY created_at, id
                """,
                (owner_id,),
            )
            existing = cur.fetchall()

            for profile_id, name in existing:
                if name == display_name:
                    print(
                        f"ALREADY REGISTERED: {name} for {owner_name}\n"
                        f"  agent_profile_id: {profile_id}"
                    )
                    return 0

            profile_id = str(uuid.uuid4())

            cur.execute(
                """
                INSERT INTO app.agent_profiles (
                    id, owner_reviewer_id, display_name
                ) VALUES (%s, %s, %s)
                """,
                (profile_id, owner_id, display_name),
            )

    print(
        f"REGISTERED: {display_name}, the personal agent of {owner_name}\n"
        f"  agent_profile_id: {profile_id}\n"
        f"  owner:            {owner_id}\n"
        f"\n"
        f"Authority is the owner's, never the agent's. Runs started by "
        f"the autonomy loop will act as this agent when "
        f"CONSOLE_ACTING_REVIEWER names its owner."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
