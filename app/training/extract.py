"""Propose knowledge from a document, and check the proposal.

Two model passes with different jobs, because asking one model to both
produce a claim and judge it produces a model that agrees with itself.

The extractor reads numbered passages and proposes units. It is given a
schema and returns structured output, so there is no parsing of prose
into fields and no place for a half-formed answer to survive. Critically,
it cites passages **by number**. It is never asked for a quotation and
has no way to supply one: the text is read out of `document_chunks` by
id afterwards. A fabricated citation is therefore not improbable, it is
unrepresentable.

The verifier gets one proposed unit and the real text of the passages it
cited, and answers one question: do these passages support this
statement. It never sees the rest of the document, so it cannot rescue a
claim with material the extractor did not cite. Its verdict is advisory.
It cannot accept anything; it can only warn a human, and its usefulness
is entirely in the cases where it says no.

Neither pass can publish. Everything here lands as a candidate that a
professional must read against its source before it becomes knowledge
the agent can use.
"""

import json
import uuid

from pydantic import BaseModel, Field

MAX_CHUNKS_PER_PASS = 12
MAX_UNITS_PER_PASS = 6

UNCHECKED = "UNCHECKED"
SUPPORTED = "SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
UNCLEAR = "UNCLEAR"


class ProposedUnit(BaseModel):
    """One piece of guidance a document appears to establish."""

    topic: str = Field(
        description=(
            "Exactly one of the topic keys offered. Never invent a topic."
        )
    )
    statement: str = Field(
        description=(
            "The guidance in one or two sentences, written for a "
            "professional colleague. State the rule, not the fact that a "
            "rule exists. No citations, no section numbers."
        )
    )
    supporting_passages: list[int] = Field(
        description=(
            "The numbers of the passages that establish this, from the "
            "list provided. At least one. Never a number you were not "
            "given. Never a quotation."
        )
    )
    confidence: str = Field(
        description="One of: HIGH, MEDIUM, LOW."
    )


class ExtractionResult(BaseModel):
    """Everything worth proposing from this batch of passages."""

    units: list[ProposedUnit] = Field(
        default_factory=list,
        description=(
            "Units this batch of passages genuinely establishes. An "
            "empty list is a correct and useful answer when the "
            "passages are narrative, procedural or off topic."
        ),
    )


class VerifierVerdict(BaseModel):
    """Whether the cited passages actually establish the statement."""

    verdict: str = Field(
        description=(
            "SUPPORTED if the passages plainly establish the statement. "
            "NOT_SUPPORTED if they do not, or say something different. "
            "UNCLEAR if they touch on it without settling it."
        )
    )
    reason: str = Field(
        description=(
            "One sentence a reviewer can act on. If NOT_SUPPORTED, say "
            "what the passages actually say instead."
        )
    )


EXTRACTOR_PROMPT = """\
You are reading source material a tax practice uses, so that a qualified
professional can decide what the firm is prepared to tell clients.

You are not answering anyone's question. You are proposing what this
document establishes, for a colleague to check.

Rules that matter more than coverage:

- Cite passages by their number. You will never be asked for a quotation
  and must never write one. The exact text is retrieved from the
  numbered passage after you answer.
- Never cite a passage number you were not given.
- Propose a unit only if the passages you cite establish it on their
  own. If it takes something you already know to make the claim true,
  that is your knowledge and not this document's, and it does not belong
  here.
- Returning no units is correct when the passages are narrative,
  procedural, or about something else. There is no credit for volume,
  and a wrong unit costs a professional more time than a missing one.
- Use only the topic keys you are given.
- Write the statement as guidance, not as a description of the document.
  "An individual present in India for 182 days or more in the year is
  resident" is guidance. "The document discusses residency" is not.
"""

VERIFIER_PROMPT = """\
You are checking one proposed statement against the exact passages that
were cited for it, and nothing else.

You do not have the rest of the document. That is deliberate. If the
cited passages do not establish the statement, the answer is
NOT_SUPPORTED even when you believe the statement is true, because the
question is whether this citation carries this claim.

Do not be agreeable. A verifier that approves everything is worse than
no verifier, because it converts an unchecked claim into one that looks
checked.
"""


def _numbered(chunks):
    """Passages as the extractor sees them: numbered, never named."""
    return "\n\n".join(
        f"[passage {c['ordinal']}]\n{c['passage']}" for c in chunks
    )


def build_extraction_prompt(chunks, topics, filename):
    return (
        f"Document: {filename}\n"
        f"Topic keys you may use: {', '.join(topics)}\n\n"
        f"Passages:\n\n{_numbered(chunks)}\n\n"
        f"Propose at most {MAX_UNITS_PER_PASS} units from these passages."
    )


def build_verification_prompt(unit, passages):
    body = "\n\n".join(
        f"[passage {p['ordinal']}]\n{p['passage']}" for p in passages
    )

    return (
        f"Proposed statement:\n{unit['statement']}\n\n"
        f"Topic: {unit['topic']}\n\n"
        f"The passages cited for it:\n\n{body}\n\n"
        f"Do these passages establish that statement?"
    )


def validate_citations(unit, allowed_ordinals):
    """Reject a unit that cites a passage it was not shown.

    The schema cannot express "a number from this list", so this is
    where that is enforced. A unit citing a passage outside the batch is
    discarded rather than corrected: a citation nobody offered is not a
    near miss.
    """
    cited = [int(n) for n in unit.supporting_passages]
    unknown = sorted(set(cited) - set(allowed_ordinals))

    if unknown:
        return None, f"cited passages not in this batch: {unknown}"

    if not cited:
        return None, "cited nothing"

    if unit.topic not in TOPIC_GUARD.get("allowed", []):
        return None, f"invented topic {unit.topic!r}"

    return sorted(set(cited)), ""


# Set per run so validate_citations can stay a plain function.
TOPIC_GUARD = {"allowed": []}


def batches(chunks, size=MAX_CHUNKS_PER_PASS):
    for start in range(0, len(chunks), size):
        yield chunks[start : start + size]


def extract(agent_factory, chunks, topics, filename):
    """Propose units from every passage, in batches. No database.

    `agent_factory` returns a fresh Strands agent for a given system
    prompt, so this function can be exercised with a stub offline.
    """
    TOPIC_GUARD["allowed"] = list(topics)

    proposed = []
    discarded = []

    for batch in batches(chunks):
        allowed = [c["ordinal"] for c in batch]
        agent = agent_factory(EXTRACTOR_PROMPT)

        result = agent.structured_output(
            ExtractionResult,
            build_extraction_prompt(batch, topics, filename),
        )

        for unit in result.units[:MAX_UNITS_PER_PASS]:
            cited, problem = validate_citations(unit, allowed)

            if cited is None:
                discarded.append(
                    {"statement": unit.statement[:120], "reason": problem}
                )
                continue

            proposed.append(
                {
                    "candidate_id": str(uuid.uuid4()),
                    "topic": unit.topic,
                    "statement": unit.statement.strip(),
                    "cited_ordinals": cited,
                    "confidence": (unit.confidence or "").upper(),
                }
            )

    return proposed, discarded


def verify(agent_factory, unit, chunks_by_ordinal):
    """Check one unit against only what it cited."""
    passages = [
        chunks_by_ordinal[n]
        for n in unit["cited_ordinals"]
        if n in chunks_by_ordinal
    ]

    if not passages:
        return NOT_SUPPORTED, "the cited passages could not be found"

    agent = agent_factory(VERIFIER_PROMPT)
    verdict = agent.structured_output(
        VerifierVerdict, build_verification_prompt(unit, passages)
    )

    value = (verdict.verdict or "").strip().upper()

    if value not in (SUPPORTED, NOT_SUPPORTED, UNCLEAR):
        return UNCLEAR, f"unrecognised verdict {value!r}: {verdict.reason}"

    return value, (verdict.reason or "").strip()


def summarise(proposed, discarded, verdicts):
    """What a run did, for the record and for a person reading it."""
    counted = {}

    for value in verdicts.values():
        counted[value] = counted.get(value, 0) + 1

    return json.dumps(
        {
            "proposed": len(proposed),
            "discarded": len(discarded),
            "verdicts": counted,
        },
        sort_keys=True,
    )
