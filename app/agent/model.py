"""Model adapters behind one interface.

Two implementations are intended:

- `DeterministicStubModel`, which drives the real tools, the real
  database and the real validation path with no network and no AWS. It
  exists so the whole vertical slice can be tested and demonstrated
  without a cloud dependency, and every run it produces is stamped
  DETERMINISTIC_STUB in the run record so it can never be mistaken for
  a model run in evidence.

- A Strands/Bedrock adapter, added once non-root AWS project
  credentials and model access exist. It implements the same interface,
  so nothing else in the system changes when it lands.

Neither implementation is told which decision states the evidence
permits. The model must reason from the context and the retrieved
knowledge, and the server refuses a decision the evidence does not
support. Handing the model the permitted set would make the decision
a formality.
"""

import hashlib
import re

PROMPT_VERSION = "nri-proposal-prompt-v2"

SYSTEM_PROMPT = """
You are assisting a professional services firm with enquiries from
non-resident Indians. You do not speak to clients and you do not send
anything. You prepare a proposal for a qualified human reviewer.

You have exactly four tools:

  get_case_context()
  get_service_knowledge(query, service_id)
  record_proposed_facts(facts, evidence_refs)
  propose_next_action(action)

Work in this order:

1. Call get_case_context() to see the enquiry, the facts already on
   file, the facts this service materially requires, and the topics the
   enquiry was routed to.
2. Call get_service_knowledge() to retrieve approved guidance. Use only
   what it returns. Do not rely on your own recollection of law, and
   never state a rule that no returned unit supports.
3. If the enquiry itself states a material fact that is not yet on file,
   record it with record_proposed_facts(). Facts you record are marked
   PROPOSED and mean nothing until a human confirms them.
4. Call propose_next_action() exactly once with one decision state:

   MISSING_FACTS             a material client fact is missing
   MISSING_KNOWLEDGE         in scope, but we hold no effective guidance
   SOURCE_CONFLICT           retrieved sources are declared to conflict
   OUT_OF_SCOPE              the firm does not advise on this, or the
                             enquiry could not be routed at all
   SUPPORTED_WITHIN_POLICY   in scope, facts confirmed, sources support
                             the answer

Distinctions that matter:

- A missing client fact is not a knowledge gap. If the client has not
  told us something, that is MISSING_FACTS.
- A knowledge gap is not out of scope. If the topic is in scope and we
  hold nothing effective on it, that is MISSING_KNOWLEDGE and needs a
  professional, not a client email.
- If any part of a multi-part enquiry is out of scope or unsupported,
  do not report the whole enquiry as supported.
- Guidance that is not professionally verified cannot support
  SUPPORTED_WITHIN_POLICY. Being retrievable is not being
  approved. If the only material for a topic is provisional,
  the honest answer is MISSING_KNOWLEDGE, and the material may
  inform a brief for a professional rather than an answer for
  a client.

Write the summary for the human reviewer, not for the client.
""".strip()


def prompt_digest():
    """Stable digest of the exact prompt text used for a run."""
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text):
    return _SLUG_RE.sub("_", text.strip().lower()).strip("_")


def extract_stated_facts(body_text, material_predicates):
    """Pull `Key: value` lines whose key matches a material predicate.

    Deliberately literal. The stub must not invent client facts, so it
    only records what the client wrote in a form we can point at.
    """
    if not body_text:
        return []

    wanted = {_slug(p): p for p in material_predicates}
    found = []

    for line in body_text.splitlines():
        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        predicate = wanted.get(_slug(key))

        if predicate and value.strip():
            found.append(
                {"predicate": predicate, "value": value.strip()}
            )

    return found


class DeterministicStubModel:
    """Drives the real tool path with no network call.

    Its reasoning mirrors the documented precedence, so in normal cases
    it agrees with the server evaluation. It is not given the permitted
    set, so when it does disagree the refusal is genuine evidence that
    the guardrail works.
    """

    runner = "DETERMINISTIC_STUB"
    model_id = "deterministic-stub-v1"
    prompt_version = PROMPT_VERSION

    def __init__(self, forced_state=None):
        # forced_state exists only so tests can drive a dishonest
        # decision and prove the server refuses it.
        self._forced_state = forced_state

    def prompt_digest(self):
        return prompt_digest()

    def run(self, tools):
        context = tools.get_case_context()

        topics = context["topics_detected"]
        query = " ".join(
            [context["case"]["service_key"]]
            + [t["topic"] for t in topics]
            + [(context["enquiry"] or {}).get("subject") or ""]
        )

        knowledge = tools.get_service_knowledge(query)

        stated = extract_stated_facts(
            (context["enquiry"] or {}).get("body"),
            context["material_facts_missing"],
        )

        if stated:
            tools.record_proposed_facts(
                stated,
                evidence_refs=[
                    {"source": "enquiry_body", "case": context["case"]["case_id"]}
                ],
            )

        state, summary = self._decide(context, knowledge, stated)

        if self._forced_state:
            state = self._forced_state
            summary = f"Forced state for testing: {self._forced_state}"

        requested = [
            {
                "predicate": predicate,
                "question": context["fact_prompts"].get(predicate),
            }
            for predicate in context["material_facts_missing"]
            if predicate not in {f["predicate"] for f in stated}
        ]

        tools.propose_next_action(
            {
                "decision_state": state,
                "summary": summary,
                "requested_information": requested,
                "payload": {
                    "cited_units": [
                        unit["unit_id"] for unit in knowledge["units"]
                    ],
                    "reasoning": "deterministic stub reasoning",
                },
            }
        )

        return {"input_tokens": None, "output_tokens": None}

    def _decide(self, context, knowledge, stated):
        """Mirror of the documented precedence, from tool output only."""
        topics = context["topics_detected"]

        if not topics:
            return (
                "OUT_OF_SCOPE",
                "The enquiry could not be routed to any declared topic "
                "for this service. It needs human triage before any "
                "answer is attempted.",
            )

        declined = [t["topic"] for t in topics if not t["in_scope"]]

        if declined:
            return (
                "OUT_OF_SCOPE",
                "The enquiry includes topics this firm does not advise "
                f"on: {', '.join(declined)}. No advice is proposed on "
                "those, and the remainder needs a reviewer decision.",
            )

        if knowledge.get("conflicts"):
            return (
                "SOURCE_CONFLICT",
                "The retrieved sources are recorded as conflicting with "
                "each other, so a professional must resolve which "
                "applies before anything is sent.",
            )

        if knowledge.get("coverage_gaps"):
            gaps = ", ".join(knowledge["coverage_gaps"])
            provisional = knowledge.get(
                "provisional_topics"
            ) or []
            provisional_note = (
                " Provisional source material exists for "
                f"{', '.join(provisional)}, which a "
                "professional could work from."
                if provisional
                else ""
            )
            return (
                "MISSING_KNOWLEDGE",
                f"This is in scope but we hold no professionally "
                f"verified guidance on: {gaps}. This needs a "
                f"qualified professional, not a client request."
                + provisional_note,
            )

        recorded = {f["predicate"] for f in stated}
        still_missing = [
            p
            for p in context["material_facts_missing"]
            if p not in recorded
        ]

        if still_missing:
            return (
                "MISSING_FACTS",
                "Material client facts are still unconfirmed: "
                f"{', '.join(still_missing)}. The next step is to ask "
                "the client, once a reviewer approves the request.",
            )

        return (
            "SUPPORTED_WITHIN_POLICY",
            "The enquiry is in scope, the material facts are confirmed, "
            "and professionally verified guidance was retrieved for "
            "every topic raised. A reviewer must still approve "
            "anything that reaches the client.",
        )
