"""Whose agent acted, and where its authority comes from.

Four identities get confused with each other, and keeping them apart is
the whole job of this module:

    HUMAN PRINCIPAL   the Partner. Authority lives here.
    AGENT PROFILE     Nicole. Acts for a principal. Holds none of its own.
    RUNTIME           Strands. Replaceable.
    MODEL             whatever provider is configured. Replaceable.

"Nicole" is not a model and not a process. She is a row owned by a
person, and the model behind her can be swapped without her identity
changing — which is the point of separating them. A receipt that says
only `model = gpt-oss-120b` cannot answer whose decision it was.

Authority derives one way. `authority_of` resolves a profile to its
owner and every permission question is then asked about the owner, so
there is no path by which an agent holds more than the human behind it.
Renaming a profile changes presentation and nothing else: the display
name is never consulted when deciding anything.

This module cannot create a profile. The runtime is granted SELECT and
not INSERT, so an agent cannot register an identity for itself — an
agent that can name itself can name itself into an authority nobody
gave it.
"""

MANIFEST_ABSENT = "HISTORICAL_CONTEXT_INCOMPLETE"

PERSONAL_WORK_AGENT = "PERSONAL_WORK_AGENT"


def profile(cur, agent_profile_id):
    """One agent profile, or None. Inactive profiles are not returned."""
    if not agent_profile_id:
        return None

    cur.execute(
        """
        SELECT p.id::text,
               p.display_name,
               p.agent_role,
               p.owner_reviewer_id::text,
               r.display_name,
               r.is_active
        FROM app.agent_profiles p
        JOIN app.reviewers r ON r.id = p.owner_reviewer_id
        WHERE p.id = %s AND p.is_active
        """,
        (agent_profile_id,),
    )
    row = cur.fetchone()

    if row is None:
        return None

    return {
        "agent_profile_id": row[0],
        "display_name": row[1],
        "agent_role": row[2],
        "owner_reviewer_id": row[3],
        "owner_display_name": row[4],
        "owner_is_active": row[5],
    }


def profile_for_owner(cur, reviewer_id):
    """The one active agent belonging to this person, or None.

    Refuses rather than guesses when the answer is not unique. A
    person may come to own more than one active profile, and choosing
    between them by creation order would be a deterministic answer
    with no authorisation behind it -- nobody decided the older one
    speaks for its owner. An explicit AGENT_ACTING_PROFILE_ID is how
    the ambiguity is meant to be resolved; see acting_agent.py.
    """
    if not reviewer_id:
        return None

    cur.execute(
        """
        SELECT id::text
        FROM app.agent_profiles
        WHERE owner_reviewer_id = %s AND is_active
        ORDER BY created_at, id
        LIMIT 2
        """,
        (reviewer_id,),
    )
    rows = cur.fetchall()

    if len(rows) != 1:
        return None

    return profile(cur, rows[0][0])


def authority_of(cur, agent_profile_id):
    """The human whose authority this agent acts under, or None.

    Every permission question about an agent is answered by asking it
    about this person instead. The agent contributes nothing of its own,
    which is why there is no path from naming an agent to widening what
    it may do.
    """
    found = profile(cur, agent_profile_id)

    if found is None or not found["owner_is_active"]:
        return None

    return found["owner_reviewer_id"]


def may_agent_access_case(cur, agent_profile_id, case_id):
    """Whether this agent may act on this case.

    Delegates, rather than repeating the rule. The agent's reach is its
    owner's reach: no grant of its own, no exception for being an agent.
    """
    from app.domain import access

    principal = authority_of(cur, agent_profile_id)

    if principal is None:
        return False

    return access.may_access_case(cur, principal, case_id)
