"""Steering: a second opinion that can send Anika back before she acts.

A hook can only allow or cancel. Steering can do the more useful thing:
hand the work back with a reason and let the agent try again. Strands
calls the result an intervention, and the action we mostly want is
`Guide`.

Two handlers live here, and both exist because of a specific defect that
reached a real person.

`ClientCopyGuard` reads what Anika proposes to say to a client before it
becomes a draft. The letter that went out asked Priya to supply
"days present in india preceding four years" and "india sourced income
present" -- raw database column names, in correspondence, to a client.
Nothing in the system objected, because nothing was looking at the words.
This looks at the words. It refuses column names, statutory citations
and the language of the machine, and it says why, so the next attempt is
better rather than merely different.

Citations deserve their own note. The internal record must cite: a
proposal claiming support without a source is rejected by a database
constraint, and that stays. But a client does not want to read
"per section 6(1)(a) of the Income-tax Act". A professional writes the
answer and keeps the authority in the file. So citations are mandatory
underneath and forbidden on the surface, and this is the thing that
enforces the second half.

`EvidenceFirstGuard` enforces an order of operations. Support may not be
proposed by a model that never looked anything up. The database will
reject an uncited claim of support anyway; being told beforehand is
cheaper than being rejected afterwards, and produces a better second
attempt.
"""

import re

from strands.interventions import Guide, InterventionHandler, Proceed

# Field names from the schema. If any of these reach a client it means
# generated text was passed through untranslated.
MACHINE_WORDS = (
    "assessment_year",
    "country_of_residence",
    "days_present_in_india",
    "india_sourced_income",
    "predicate",
    "service_id",
    "case_id",
    "knowledge_unit",
    "verification_status",
    "source_recorded",
    "professionally_verified",
    "missing_facts",
    "missing_knowledge",
    "source_conflict",
    "out_of_scope",
    "supported_within_policy",
    "system_failure",
)

# The same names with underscores rendered as spaces, which is how the
# defect actually surfaced: "days present in india preceding four years".
SPACED_MACHINE_WORDS = tuple(
    word.replace("_", " ") for word in MACHINE_WORDS if "_" in word
)

CITATION_PATTERNS = (
    re.compile(r"\bsection\s+\d+", re.I),
    re.compile(r"\bsec\.?\s*\d+", re.I),
    re.compile(r"\bs\.\s?\d+\(", re.I),
    re.compile(r"\bincome[- ]tax act\b", re.I),
    re.compile(r"\bcircular\s+no", re.I),
    re.compile(r"\bnotification\s+no", re.I),
    re.compile(r"\bfema\s+\d", re.I),
    re.compile(r"\brule\s+\d+", re.I),
)

SUPPORT_STATE = "SUPPORTED_WITHIN_POLICY"

RETRIEVAL_TOOL = "get_service_knowledge"
PROPOSE_TOOL = "propose_next_action"


def _tool_name(event):
    return (getattr(event, "tool_use", None) or {}).get("name") or ""


def _tool_input(event):
    return (getattr(event, "tool_use", None) or {}).get("input") or {}


def client_facing_text(payload):
    """Every string in a proposal that a client could end up reading."""
    parts = []

    for key in ("client_message", "requested_information", "summary"):
        value = payload.get(key)

        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values())

    return "\n".join(parts)


def inspect_copy(text):
    """Return the problems in client-facing text. Empty means it is fine.

    Deliberately a plain function with no model in it, so it can be
    tested offline and reasoned about.
    """
    problems = []
    lowered = text.lower()

    found = sorted({word for word in MACHINE_WORDS if word in lowered})

    if found:
        problems.append(
            f"these are database field names, not words for a client: "
            f"{', '.join(found)}"
        )

    spaced = sorted({word for word in SPACED_MACHINE_WORDS if word in lowered})

    if spaced:
        problems.append(
            f"these read as field names with the underscores removed: "
            f"{', '.join(spaced)}"
        )

    citations = sorted(
        {
            match.group(0)
            for pattern in CITATION_PATTERNS
            for match in pattern.finditer(text)
        }
    )

    if citations:
        problems.append(
            f"remove the statutory references {', '.join(citations)}. The "
            f"citation belongs in the internal record, which is required "
            f"and unaffected. A client reads the answer, not the authority."
        )

    return problems


class ClientCopyGuard(InterventionHandler):
    """Send client-facing wording back when it reads like a database."""

    name = "client_copy_guard"

    def __init__(self, trace=None):
        self.trace = trace
        self.guided = []

    def before_tool_call(self, event, **kwargs):
        if _tool_name(event) != PROPOSE_TOOL:
            return Proceed()

        payload = _tool_input(event)
        text = client_facing_text(payload)

        if not text.strip():
            return Proceed()

        problems = inspect_copy(text)

        if not problems:
            return Proceed()

        self.guided.append(problems)

        if self.trace is not None:
            self.trace.record(
                tool_name=PROPOSE_TOOL,
                arguments=payload,
                error=f"steering guided client copy: {'; '.join(problems)}",
            )

        return Guide(
            feedback=(
                "This wording is going to a client who is not a tax "
                "professional and has never seen this system. Rewrite it "
                "in plain professional English, then propose again. "
                + " ".join(problems)
            ),
            reason="client-facing copy failed review",
        )


class EvidenceFirstGuard(InterventionHandler):
    """Support may not be claimed by a model that never looked anything up."""

    name = "evidence_first_guard"

    def __init__(self, trace=None):
        self.trace = trace
        self.retrieved = False
        self.guided = []

    def before_tool_call(self, event, **kwargs):
        name = _tool_name(event)

        if name == RETRIEVAL_TOOL:
            self.retrieved = True

            return Proceed()

        if name != PROPOSE_TOOL:
            return Proceed()

        payload = _tool_input(event)
        state = str(payload.get("decision_state") or "").upper()

        if state != SUPPORT_STATE or self.retrieved:
            return Proceed()

        self.guided.append(state)

        if self.trace is not None:
            self.trace.record(
                tool_name=PROPOSE_TOOL,
                arguments=payload,
                error="steering guided: support proposed before retrieval",
            )

        return Guide(
            feedback=(
                f"You proposed {SUPPORT_STATE} without calling "
                f"{RETRIEVAL_TOOL} even once. A claim that the firm's "
                f"guidance covers this must rest on that guidance. "
                f"Retrieve first, then propose."
            ),
            reason="support proposed with no retrieval in this run",
        )


def build_interventions(trace=None):
    """The steering every run is constructed with."""
    return [ClientCopyGuard(trace=trace), EvidenceFirstGuard(trace=trace)]
