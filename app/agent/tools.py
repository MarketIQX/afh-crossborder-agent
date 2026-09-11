"""The four bounded tools the model is allowed to call.

Design rules that hold regardless of what the model asks for:

- No tool takes a case id. The case is bound server side when the run
  starts, so there is no argument the model could set to reach another
  client's case.
- `get_service_knowledge` does accept a service id, because the agreed
  tool contract includes one, but it is checked against the bound scope
  and a mismatch is refused and recorded rather than quietly ignored.
  A silent ignore would hide an attempt; a recorded refusal is evidence.
- Facts the agent records are always PROPOSED and always attributed to
  the agent. The database does not grant it the status column, so this
  is not merely a convention in this file.
- There is no tool for sending anything, approving anything, publishing
  knowledge, changing scope, running shell commands or making HTTP
  requests. The absence is the point.

Every call, including a refused one, is appended to the tool trace.
"""

import json
import threading
import uuid
from dataclasses import dataclass

from app.domain import decision, knowledge
from app.domain.context import tokenize

TOOL_SCHEMA_VERSION = "tool-schema-v1"

TOOL_NAMES = (
    "get_case_context",
    "get_service_knowledge",
    "record_proposed_facts",
    "propose_next_action",
)

MAX_PROPOSED_FACTS = 20


class ToolRefused(Exception):
    """The tool call was rejected. The reason is recorded in the trace."""


@dataclass(frozen=True)
class Binding:
    """Scope established by the server before the model is invoked."""

    case_id: str
    service_id: str
    run_id: str


@dataclass
class ProposalRecord:
    proposal_id: str
    revision: int
    decision_state: str


class ToolTrace:
    """Append-only record of what the model actually did."""

    def __init__(self, conn, run_id):
        self._conn = conn
        self._run_id = run_id
        self._sequence = 0
        self._lock = threading.Lock()
        self.calls = []

    def record(self, tool_name, arguments, result_summary=None, error=None):
        # Strands runs tools concurrently. Take the number once, under a
        # lock, and use that value everywhere: re-reading the counter
        # lets another thread's increment be observed instead.
        with self._lock:
            self._sequence += 1
            sequence = self._sequence

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.agent_tool_calls (
                    id, run_id, sequence, tool_name, arguments,
                    result_summary, error
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    self._run_id,
                    sequence,
                    tool_name,
                    json.dumps(arguments, default=str),
                    (
                        json.dumps(result_summary, default=str)
                        if result_summary is not None
                        else None
                    ),
                    error,
                ),
            )

        with self._lock:
            self.calls.append(
                {
                    "sequence": sequence,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "error": error,
                }
            )

    @property
    def count(self):
        with self._lock:
            return self._sequence

    def ordered_calls(self):
        """The trace in execution order, whatever order it completed in."""
        with self._lock:
            return sorted(self.calls, key=lambda call: call["sequence"])


class AgentTools:
    """The tool surface handed to the model for exactly one run."""

    schema_version = TOOL_SCHEMA_VERSION

    def __init__(self, conn, binding, context, evaluation, trace):
        self._conn = conn
        self._binding = binding
        self._context = context
        self._evaluation = evaluation
        self._trace = trace
        self.proposal = None

    # -- tool 1 ---------------------------------------------------------

    def get_case_context(self):
        """Return the bound case. Takes no arguments by design."""
        payload = self._context.to_payload()

        self._trace.record(
            "get_case_context",
            {},
            {
                "case_id": self._context.case_id,
                "facts": len(self._context.facts),
                "missing_material_facts": list(
                    self._context.missing_material_predicates
                ),
                "topics": list(self._context.detected_topics),
            },
        )

        return payload

    # -- tool 2 ---------------------------------------------------------

    def get_service_knowledge(self, query, service_id=None):
        """Return approved, effective knowledge for the bound service."""
        arguments = {"query": query, "service_id": service_id}

        if service_id is not None and service_id != self._binding.service_id:
            message = (
                "service_id does not match the scope bound to this run. "
                "Refusing to retrieve knowledge for another service."
            )
            self._trace.record(
                "get_service_knowledge", arguments, error=message
            )
            raise ToolRefused(message)

        if not query or not str(query).strip():
            message = "query must be a non-empty string"
            self._trace.record(
                "get_service_knowledge", arguments, error=message
            )
            raise ToolRefused(message)

        query_tokens = tokenize(str(query))

        topics = tuple(
            match.topic
            for match in self._context.topic_matches
            if match.is_in_scope
        )

        release_id = self._context.knowledge_release_id

        if release_id is None:
            self._trace.record(
                "get_service_knowledge",
                arguments,
                {"units": 0, "reason": "no active knowledge release"},
            )
            return {
                "knowledge_release_id": None,
                "units": [],
                "coverage_gaps": list(topics),
            }

        with self._conn.cursor() as cur:
            units = knowledge.retrieve(
                cur, release_id, topics, self._context.material_date
            )
            conflicts = knowledge.declared_conflicts(
                cur, [unit.unit_id for unit in units]
            )

        approved_units = knowledge.approved(units)
        gaps = knowledge.coverage_gaps(approved_units, topics)
        provisional = tuple(
            sorted(
                {
                    unit.topic
                    for unit in units
                    if not unit.professionally_verified
                    and unit.topic in gaps
                }
            )
        )

        result = {
            "knowledge_release_id": release_id,
            "retrieval_version": knowledge.RETRIEVAL_VERSION,
            "query_tokens": sorted(query_tokens),
            "units": [
                {
                    **unit.citation(),
                    "statement": unit.statement,
                }
                for unit in units
            ],
            "approved_unit_ids": [
                unit.unit_id for unit in approved_units
            ],
            "coverage_gaps": list(gaps),
            "provisional_topics": list(provisional),
            "conflicts": list(conflicts),
        }

        self._trace.record(
            "get_service_knowledge",
            arguments,
            {
                "units": len(units),
                "unit_ids": [unit.unit_id for unit in units],
                "coverage_gaps": result["coverage_gaps"],
            },
        )

        return result

    # -- tool 3 ---------------------------------------------------------

    def record_proposed_facts(self, facts, evidence_refs=None):
        """Record facts the agent believes hold, always as PROPOSED."""
        arguments = {"facts": facts, "evidence_refs": evidence_refs}

        if not isinstance(facts, (list, tuple)) or not facts:
            message = "facts must be a non-empty list"
            self._trace.record("record_proposed_facts", arguments, error=message)
            raise ToolRefused(message)

        if len(facts) > MAX_PROPOSED_FACTS:
            message = (
                f"at most {MAX_PROPOSED_FACTS} facts may be proposed in "
                f"one call, got {len(facts)}"
            )
            self._trace.record("record_proposed_facts", arguments, error=message)
            raise ToolRefused(message)

        refs = json.dumps(list(evidence_refs or []), default=str)
        written = []

        for item in facts:
            predicate = str(item.get("predicate", "")).strip()

            if not predicate:
                message = "every fact needs a non-empty predicate"
                self._trace.record(
                    "record_proposed_facts", arguments, error=message
                )
                raise ToolRefused(message)

            value = item.get("value")
            fact_id = str(uuid.uuid4())

            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.case_facts (
                        id, case_id, predicate, value_text, origin,
                        evidence_refs, run_id
                    ) VALUES (%s, %s, %s, %s, 'AGENT_PROPOSED', %s, %s)
                    """,
                    (
                        fact_id,
                        self._binding.case_id,
                        predicate,
                        None if value is None else str(value),
                        refs,
                        self._binding.run_id,
                    ),
                )

            written.append({"fact_id": fact_id, "predicate": predicate})

        self._trace.record(
            "record_proposed_facts",
            arguments,
            {"written": written, "status": "PROPOSED"},
        )

        return {"recorded": written, "status": "PROPOSED"}

    # -- tool 4 ---------------------------------------------------------

    def propose_next_action(self, action):
        """Persist an immutable proposal revision, if the evidence allows it.

        The model chooses the decision state. The server checks that the
        evidence permits it and refuses otherwise, so a confident answer
        cannot be produced over missing facts, absent sources or a
        declared conflict between sources.
        """
        arguments = {"action": action}

        if not isinstance(action, dict):
            message = "action must be an object"
            self._trace.record("propose_next_action", arguments, error=message)
            raise ToolRefused(message)

        state = str(action.get("decision_state", "")).strip()
        summary = str(action.get("summary", "")).strip()

        if not summary:
            message = "action.summary must be a non-empty string"
            self._trace.record("propose_next_action", arguments, error=message)
            raise ToolRefused(message)

        try:
            decision.validate(self._evaluation, state)
        except decision.DecisionRefused as exc:
            self._trace.record(
                "propose_next_action", arguments, error=str(exc)
            )
            raise ToolRefused(str(exc)) from exc

        cited = self._evaluation.citations_for(state)
        missing = self._evaluation.missing_predicates

        payload = {
            "model_payload": action.get("payload", {}),
            "requested_information": action.get("requested_information", []),
            "rationale": list(self._evaluation.rationale),
            "coverage_gaps": list(self._evaluation.coverage_gaps),
            "provisional_topics": list(
                self._evaluation.provisional_topics
            ),
            "out_of_scope_topics": list(self._evaluation.out_of_scope_topics),
            "conflicts": list(self._evaluation.conflicts),
            "rules_version": self._evaluation.rules_version,
            "permitted_states": sorted(self._evaluation.permitted_states),
        }

        record = self._persist(state, summary, cited, missing, payload)
        self.proposal = record

        self._trace.record(
            "propose_next_action",
            arguments,
            {
                "proposal_id": record.proposal_id,
                "revision": record.revision,
                "decision_state": record.decision_state,
                "cited_unit_ids": list(cited),
            },
        )

        return {
            "proposal_id": record.proposal_id,
            "revision": record.revision,
            "decision_state": record.decision_state,
        }

    # -- persistence ----------------------------------------------------

    def _persist(self, state, summary, cited, missing, payload):
        with self._conn.transaction():
            return self._persist_atomic(
                state, summary, cited, missing, payload
            )

    def _persist_atomic(self, state, summary, cited, missing, payload):
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, current_revision FROM app.action_proposals "
                "WHERE case_id = %s ORDER BY created_at ASC LIMIT 1",
                (self._binding.case_id,),
            )
            row = cur.fetchone()

            if row is None:
                proposal_id = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO app.action_proposals (id, case_id) "
                    "VALUES (%s, %s)",
                    (proposal_id, self._binding.case_id),
                )
                current = 0
            else:
                proposal_id, current = row

            revision = current + 1

            cur.execute(
                """
                INSERT INTO app.proposal_revisions (
                    id, proposal_id, revision, run_id, decision_state,
                    summary, payload, cited_unit_ids, missing_predicates,
                    knowledge_release_id,
                    requires_professional_verification
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    str(uuid.uuid4()),
                    proposal_id,
                    revision,
                    self._binding.run_id,
                    state,
                    summary,
                    json.dumps(payload, default=str),
                    json.dumps(list(cited)),
                    json.dumps(list(missing)),
                    self._evaluation.knowledge_release_id,
                    self._evaluation.requires_professional_verification,
                ),
            )

            cur.execute(
                "UPDATE app.action_proposals "
                "SET current_revision = %s, updated_at = now() "
                "WHERE id = %s",
                (revision, proposal_id),
            )

        return ProposalRecord(
            proposal_id=proposal_id,
            revision=revision,
            decision_state=state,
        )
