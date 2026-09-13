"""Two questions about every claim, kept apart.

`SYSTEM_VERIFIED` was one axis doing the work of two questions that have
different answers:

    WHERE DID THIS CLAIM COME FROM?
        the system computed it, the model said it, a human said it,
        a tool returned it, a source was cited -- or nothing exists.

    HOW MUCH DOES THE SYSTEM TRUST IT?
        confirmed, merely proposed, professionally verified knowledge,
        an unverified source, conflicted, unknown -- or not applicable.

A system can perfectly verify that a tool returned a particular string.
That does not make the string true. Collapsing both questions into one
label made "the system verified this" readable as "this is
professionally correct", which it was never entitled to mean.

Neither axis is inferred from a field name. `origin` on a case fact and
`verification_status` on a knowledge unit are read from the columns
that record them; nothing here guesses from what a value is called.
"""

# --- where a claim came from -------------------------------------

PROVENANCE_SYSTEM = "SYSTEM_DERIVED"
PROVENANCE_MODEL = "MODEL_STATED"
PROVENANCE_HUMAN = "HUMAN_STATED"
PROVENANCE_TOOL = "TOOL_OBSERVED"
PROVENANCE_SOURCE = "SOURCE_CITED"
PROVENANCE_ABSENT = "UNAVAILABLE"

PROVENANCE_CLASSES = (
    PROVENANCE_SYSTEM,
    PROVENANCE_MODEL,
    PROVENANCE_HUMAN,
    PROVENANCE_TOOL,
    PROVENANCE_SOURCE,
    PROVENANCE_ABSENT,
)

# --- how much the system trusts it ---------------------------------

AUTHORITY_CONFIRMED_FACT = "CONFIRMED_FACT"
AUTHORITY_PROPOSED_FACT = "PROPOSED_FACT"
AUTHORITY_REJECTED_FACT = "REJECTED_FACT"
AUTHORITY_VERIFIED_KNOWLEDGE = "VERIFIED_KNOWLEDGE"
AUTHORITY_UNVERIFIED_SOURCE = "UNVERIFIED_SOURCE"
AUTHORITY_CONFLICTED = "CONFLICTED"
AUTHORITY_UNKNOWN = "UNKNOWN"
AUTHORITY_NOT_APPLICABLE = "NOT_APPLICABLE"
AUTHORITY_SYSTEM_FAILURE = "SYSTEM_FAILURE"

AUTHORITY_CLASSES = (
    AUTHORITY_CONFIRMED_FACT,
    AUTHORITY_PROPOSED_FACT,
    AUTHORITY_REJECTED_FACT,
    AUTHORITY_VERIFIED_KNOWLEDGE,
    AUTHORITY_UNVERIFIED_SOURCE,
    AUTHORITY_CONFLICTED,
    AUTHORITY_UNKNOWN,
    AUTHORITY_NOT_APPLICABLE,
    AUTHORITY_SYSTEM_FAILURE,
)

# `app.case_facts.origin`, per `case_facts_origin_check`. CLIENT_MESSAGE
# is the client's own words, read by a tool without the model
# interpreting them; AGENT_PROPOSED is the model's own claim about what
# it read, which is a different act even when it reads the same text.
_FACT_ORIGIN_PROVENANCE = {
    "CLIENT_MESSAGE": PROVENANCE_TOOL,
    "AGENT_PROPOSED": PROVENANCE_MODEL,
    "REVIEWER": PROVENANCE_HUMAN,
}

# `app.case_facts.status`, per `case_facts_status_check`.
_FACT_STATUS_AUTHORITY = {
    "PROPOSED": AUTHORITY_PROPOSED_FACT,
    "CONFIRMED": AUTHORITY_CONFIRMED_FACT,
    "REJECTED": AUTHORITY_REJECTED_FACT,
}

# `app.knowledge_units.verification_status`, per the verification
# ladder in docs/ARCHITECTURE.md. Only the top rung may support an
# answer; the other three are all, honestly, an unverified source.
_KNOWLEDGE_VERIFIED_STATES = frozenset({"PROFESSIONALLY_VERIFIED"})


def fact_provenance(origin):
    """Where a case fact's value came from, from its recorded origin."""
    return _FACT_ORIGIN_PROVENANCE.get(origin, PROVENANCE_ABSENT)


def fact_authority(status):
    """How much the system trusts a case fact, from its recorded status."""
    return _FACT_STATUS_AUTHORITY.get(status, AUTHORITY_UNKNOWN)


def fact_claim(origin, status):
    """Both axes for one case fact. Independent by construction:

    a fact extracted successfully (provenance resolves) is not thereby
    confirmed (authority does not move with it), and a fact a human
    typed carries HUMAN_STATED provenance whether or not the predicate
    itself is later superseded.
    """
    return {
        "provenance": fact_provenance(origin),
        "authority": fact_authority(status),
    }


def knowledge_authority(verification_status, conflicted=False):
    """How much the system trusts a cited knowledge unit.

    `conflicted` takes precedence: a unit currently known to conflict
    with another cannot be reported as settled, whatever rung it has
    climbed to.
    """
    if conflicted:
        return AUTHORITY_CONFLICTED

    if verification_status in _KNOWLEDGE_VERIFIED_STATES:
        return AUTHORITY_VERIFIED_KNOWLEDGE

    return AUTHORITY_UNVERIFIED_SOURCE


def knowledge_claim(verification_status, conflicted=False):
    """Both axes for one cited unit. Provenance is always the citation
    act itself; authority is what the corpus says about the unit.
    """
    return {
        "provenance": PROVENANCE_SOURCE,
        "authority": knowledge_authority(verification_status, conflicted),
    }
