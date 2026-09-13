"""Executes one agent run and records everything about it.

Guarantees:

- Every attempt on an existing case leaves a run record, including
  attempts that are refused. An attempt that vanishes is worse than a
  failure, because it cannot be reviewed.
- A retrieval or context failure is recorded as SYSTEM_FAILURE by the
  server, never handed to the model to describe as a knowledge gap.
- The model is invoked with a case and a service already bound. It
  cannot choose either.
- The run is finalised with its real outcome, latency and tool count
  even when the model errors.
"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg
from psycopg.errors import UniqueViolation

from app import config
from app.agent import tools as tools_module
from app.domain import agent_identity
from app.domain import context as context_module
from app.domain import context_manifest
from app.domain import decision, knowledge


class ContextNotRecorded(Exception):
    """The run could not durably record the state it was given.

    A system failure, not a refusal. A refusal is the agent declining
    to proceed on the merits; this is the application unable to keep
    its own record, and a decision whose basis cannot be reconstructed
    afterwards should not be taken at all.
    """


class RunRefused(Exception):
    """The case may not be reasoned about at all."""


class DuplicateOperation(Exception):
    """This operation_id has already been executed."""


@dataclass
class RunResult:
    run_id: str
    case_id: str
    operation_id: str
    runner: str
    model_id: str
    result_state: str

    knowledge_release_id: str | None = None
    decision_state: str | None = None
    proposal_id: str | None = None
    revision: int | None = None
    latency_ms: int = 0
    failure_reason: str | None = None
    tool_calls: tuple = field(default_factory=tuple)
    permitted_states: tuple = field(default_factory=tuple)
    recommended_state: str | None = None

    def summary_lines(self):
        lines = [
            f"RUN_ID: {self.run_id}",
            f"CASE_ID: {self.case_id}",
            f"OPERATION_ID: {self.operation_id}",
            f"RUNNER: {self.runner}",
            f"MODEL_ID: {self.model_id}",
            f"RESULT_STATE: {self.result_state}",
            f"KNOWLEDGE_RELEASE_ID: {self.knowledge_release_id}",
            f"DECISION_STATE: {self.decision_state}",
            f"PROPOSAL_ID: {self.proposal_id}",
            f"REVISION: {self.revision}",
            f"LATENCY_MS: {self.latency_ms}",
            f"TOOL_CALLS: {len(self.tool_calls)}",
        ]

        for call in self.tool_calls:
            marker = "REFUSED" if call["error"] else "ok"
            lines.append(
                f"  {call['sequence']}. {call['tool_name']} [{marker}]"
            )

        if self.failure_reason:
            lines.append(f"FAILURE_REASON: {self.failure_reason}")

        return lines


def _connect():
    return psycopg.connect(
        **config.database_settings().app_kwargs(), autocommit=True
    )


def _case_exists(conn, case_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM app.cases WHERE id = %s", (case_id,)
        )
        return cur.fetchone() is not None


def _insert_run(
    conn,
    model,
    case_id,
    operation_id,
    release_id,
    builder,
    agent_profile_id=None,
    initiated_by=None,
):
    run_id = str(uuid.uuid4())

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.agent_runs (
                    id, case_id, operation_id, runner, model_id,
                    prompt_version, prompt_digest, tool_schema_version,
                    context_builder_version, knowledge_release_id,
                    agent_profile_id, initiated_by_reviewer_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    run_id,
                    case_id,
                    operation_id,
                    model.runner,
                    model.model_id,
                    model.prompt_version,
                    model.prompt_digest(),
                    tools_module.TOOL_SCHEMA_VERSION,
                    builder,
                    release_id,
                    agent_profile_id,
                    initiated_by,
                ),
            )
    except UniqueViolation as exc:
        raise DuplicateOperation(
            f"operation_id {operation_id} has already been executed"
        ) from exc

    return run_id


def _finalise(conn, run_id, state, latency_ms, tool_count, reason=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE app.agent_runs
            SET ended_at = %s,
                latency_ms = %s,
                tool_call_count = %s,
                result_state = %s,
                failure_reason = %s
            WHERE id = %s
            """,
            (
                datetime.now(timezone.utc),
                latency_ms,
                tool_count,
                state,
                reason,
                run_id,
            ),
        )


def _gather_knowledge(conn, ctx):
    """Retrieve what the evaluation needs, or report a system failure."""
    if ctx.knowledge_release_id is None:
        return (), (), None

    topics = ctx.in_scope_topics

    # The client's own words. Note what this does NOT establish: the
    # tool searches with the query string the model chose, not with
    # this text, so the two text arms can surface different units. The
    # intended property -- that a validator sees at least the evidence
    # the model could see -- is therefore not enforced here. See the
    # open list in docs/ARCHITECTURE.md.
    enquiry_text = ""

    if ctx.enquiry is not None:
        enquiry_text = " ".join(
            part
            for part in (ctx.enquiry.subject, ctx.enquiry.body_text)
            if part
        )

    try:
        with conn.cursor() as cur:
            units, _matched_by = knowledge.retrieve_by_query(
                cur,
                ctx.knowledge_release_id,
                topics,
                enquiry_text,
                ctx.material_date,
            )
            conflicts = knowledge.declared_conflicts(
                cur, [unit.unit_id for unit in units]
            )
    except knowledge.KnowledgeSystemFailure as exc:
        return (), (), str(exc)

    return units, conflicts, None


def execute(
    model,
    case_id,
    operation_id=None,
    token_budget=context_module.DEFAULT_TOKEN_BUDGET,
    conn=None,
    agent_profile_id=None,
):
    """Run the agent once against one case.

    `agent_profile_id` is the personal agent acting, when there is one.
    The initiating human is derived from that profile's owner rather
    than passed separately, so a caller cannot name one Partner's agent
    while attributing the run to another. Left unset -- the autonomy
    loop, the eval harness -- the run records no agent, which is
    accurate rather than convenient.
    """
    operation_id = operation_id or f"run-{uuid.uuid4()}"
    owns_connection = conn is None
    conn = conn or _connect()

    # Derived, never passed. The initiating human is whoever
    # owns the acting agent, so a caller cannot hand over one
    # Partner's agent and attribute the run to another.
    initiated_by = None

    if agent_profile_id:
        with conn.cursor() as cur:
            initiated_by = agent_identity.authority_of(
                cur, agent_profile_id
            )

    started = time.monotonic()

    try:
        try:
            ctx = context_module.assemble(conn, case_id, token_budget)
        except context_module.ContextRefused as exc:
            if not _case_exists(conn, case_id):
                raise RunRefused(str(exc)) from exc

            run_id = _insert_run(
                conn,
                model,
                case_id,
                operation_id,
                None,
                context_module.CONTEXT_BUILDER_VERSION,
                agent_profile_id,
                initiated_by,
            )
            _finalise(
                conn,
                run_id,
                "REFUSED",
                int((time.monotonic() - started) * 1000),
                0,
                str(exc),
            )

            return RunResult(
                run_id=run_id,
                case_id=case_id,
                operation_id=operation_id,
                runner=model.runner,
                model_id=model.model_id,
                result_state="REFUSED",
                failure_reason=str(exc),
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        units, conflicts, failure_reason = _gather_knowledge(conn, ctx)
        evaluation = decision.evaluate(ctx, units, conflicts, failure_reason)

        run_id = _insert_run(
            conn,
            model,
            ctx.case_id,
            operation_id,
            ctx.knowledge_release_id,
            ctx.context_builder_version,
            agent_profile_id,
            initiated_by,
        )

        # Before the model is invoked, from the context that was
        # actually assembled. Written afterwards it would describe what
        # the tables say once the run has finished, which is the
        # substitution this exists to prevent.
        #
        # And if it cannot be written, the run does not happen. The row
        # above is already committed, so failing here without
        # finalising would leave a run RUNNING for ever, with no reason
        # and nothing to distinguish it from one still in flight.
        try:
            with conn.cursor() as cur:
                context_manifest.record(
                    cur,
                    run_id,
                    context_manifest.describe(
                        ctx, agent_profile_id, initiated_by, units
                    ),
                    policy_envelope={
                        "requires_professional_approval": True,
                        "may_dispatch": False,
                        "token_budget": token_budget,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            reason = (
                f"the run could not record its context manifest, so it "
                f"did not reason: {exc.__class__.__name__}"
            )

            _finalise(
                conn,
                run_id,
                "FAILED",
                int((time.monotonic() - started) * 1000),
                0,
                reason,
            )

            raise ContextNotRecorded(reason) from exc

        binding = tools_module.Binding(
            case_id=ctx.case_id,
            service_id=ctx.service_id,
            run_id=run_id,
            service_key=getattr(ctx, "service_key", "") or "",
        )

        trace = tools_module.ToolTrace(conn, run_id)
        agent_tools = tools_module.AgentTools(
            conn, binding, ctx, evaluation, trace
        )

        result_state = "SUCCEEDED"
        reason = None

        if failure_reason:
            # The server records the failure. The model is not invited to
            # reinterpret a broken lookup as a gap in our knowledge.
            agent_tools.propose_next_action(
                {
                    "decision_state": decision.SYSTEM_FAILURE,
                    "summary": (
                        "The agent could not complete because a system "
                        "lookup failed. No conclusion about this case "
                        "should be drawn from this run."
                    ),
                    "payload": {"authored_by": "server"},
                }
            )
            result_state = "FAILED"
            reason = failure_reason
        else:
            try:
                model.run(agent_tools)

                if agent_tools.proposal is None:
                    result_state = "FAILED"
                    reason = "the model finished without proposing an action"
            except tools_module.ToolRefused as exc:
                result_state = "REFUSED"
                reason = str(exc)
            except decision.DecisionRefused as exc:
                result_state = "REFUSED"
                reason = str(exc)
            except Exception as exc:  # noqa: BLE001
                result_state = "FAILED"
                reason = f"{exc.__class__.__name__}: {exc}"

        latency_ms = int((time.monotonic() - started) * 1000)
        _finalise(conn, run_id, result_state, latency_ms, trace.count, reason)

        proposal = agent_tools.proposal

        return RunResult(
            run_id=run_id,
            case_id=ctx.case_id,
            operation_id=operation_id,
            runner=model.runner,
            model_id=model.model_id,
            result_state=result_state,
            knowledge_release_id=ctx.knowledge_release_id,
            decision_state=proposal.decision_state if proposal else None,
            proposal_id=proposal.proposal_id if proposal else None,
            revision=proposal.revision if proposal else None,
            latency_ms=latency_ms,
            failure_reason=reason,
            tool_calls=tuple(trace.ordered_calls()),
            permitted_states=tuple(sorted(evaluation.permitted_states)),
            recommended_state=evaluation.recommended_state,
        )
    finally:
        if owns_connection:
            conn.close()
