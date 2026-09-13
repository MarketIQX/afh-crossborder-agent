"""Which agent a background run acts as.

The console has a human at the keyboard and binds its identity at
startup. A run started by the autonomy loop or by a script has nobody
at the keyboard, and the honest question is not "who clicked" but "on
whose standing authority is this firm running an agent".

That is configuration: the same controlled Partner the console is bound
to, and the agent profile that Partner owns. Resolving it here means
one answer rather than each entry point inventing its own, and it means
a run can say whose agent acted even when no person triggered it.

Returns None rather than guessing. A firm with no configured acting
reviewer, or a Partner with no agent profile, produces runs that record
no agent — which is accurate. Inventing an attribution so the field
looks populated would put a name on work nobody authorised.
"""

from app import config
from app.domain import agent_identity

ACTING_REVIEWER_SETTING = "CONSOLE_ACTING_REVIEWER"

# Resolves ambiguity when one person owns more than one active profile.
# Optional: most deployments have exactly one, and profile_for_owner
# resolves that case without it.
ACTING_AGENT_SETTING = "AGENT_ACTING_PROFILE_ID"


def _configured_reviewer(cur):
    """The Partner this deployment runs as, resolved to an id.

    Accepts an id or an email, the same way the console does, and
    resolves it against the active reviewers so a stale setting cannot
    attribute work to somebody who has left.
    """
    wanted = (config.get(ACTING_REVIEWER_SETTING, "") or "").strip()

    if not wanted:
        return None

    cur.execute(
        """
        SELECT id::text
        FROM app.reviewers
        WHERE is_active AND (id::text = %s OR email = lower(%s))
        LIMIT 1
        """,
        (wanted, wanted),
    )
    row = cur.fetchone()

    return row[0] if row else None


def profile_id(cur):
    """The agent profile a background run should act as, or None.

    An explicit AGENT_ACTING_PROFILE_ID is validated, not trusted: it
    must name an active profile owned by the configured principal, or
    this refuses exactly as an unset value would. Without one,
    ownership must be unambiguous -- profile_for_owner refuses if the
    principal owns more than one active profile.
    """
    reviewer_id = _configured_reviewer(cur)

    if reviewer_id is None:
        return None

    explicit = (config.get(ACTING_AGENT_SETTING, "") or "").strip()

    if explicit:
        found = agent_identity.profile(cur, explicit)

        if found is None or found["owner_reviewer_id"] != reviewer_id:
            return None

        return explicit

    found = agent_identity.profile_for_owner(cur, reviewer_id)

    return found["agent_profile_id"] if found else None
