"""Why Anika decided what she decided, assembled from the record.

A professional should not need six joins to answer that question, and
the answer should not be a model's account of its own thinking. So this
builds one artefact from the rows that are already authoritative:
the run, its tool calls, the proposal revision, the facts, the release
it relied on, and what became of it.

It is a projection, not a second source of truth. Nothing here is
stored; every field is read from the tables that own it, so a receipt
cannot drift from the thing it describes and cannot be edited into
saying something the database does not. The digest is over the
projection, so the same underlying rows always produce the same
receipt.

What this deliberately does not contain
---------------------------------------

There is no field for the model's private deliberation, and no attempt
to persist one. Free-form internal reasoning is not audit evidence: it
is unverifiable, it cannot be compared between runs, and treating it as
the record of why something happened would make the account worse
rather than better. What is recorded instead is the observable basis --
which facts were established, which were only asserted, what remained
unknown, which units were cited, which tools ran and what they
returned, which release and prompt were in force, and what the run
finally did.

What this cannot reconstruct
----------------------------

Fact *status* has no history. `app.case_facts` records when a row was
created but not when it became CONFIRMED, so the confirmed facts here
are the case's facts as they stand now, not provably as they stood when
the run read them. The facts the run itself proposed are exact, because
those rows carry its `run_id`. The receipt says which is which rather
than quietly presenting current state as historical state, and
`reconstruction_limits` names the gap. Closing it properly needs a
status-transition record, which does not exist and is not invented here.
"""

import hashlib
import json

from app.domain import actionability

RECEIPT_VERSION = "agent-decision-receipt-v0"

# Field names that would amount to storing hidden deliberation. Checked
# rather than merely avoided, so a later addition has to argue with a
# failing test instead of slipping in.
FORBIDDEN_FIELDS = frozenset(
    {
        "chain_of_thought",
        "deliberation",
        "hidden_reasoning",
        "inner_monologue",
        "internal_reasoning",
        "raw_reasoning",
        "reasoning_trace",
        "scratchpad",
        "thoughts",
    }
)

RECONSTRUCTION_LIMITS = (
    "Confirmed facts are the case's current facts. app.case_facts has "
    "no status-transition history, so this receipt cannot prove which "
    "of them were already confirmed when the run read the case. Facts "
    "proposed by this run are exact, being the rows that carry its "
    "run_id.",
)


class ReceiptUnavailable(Exception):
    """There is no such revision, so there is nothing to account for."""


def _when(value):
    """Timestamps as text, so a receipt is JSON and its digest stable."""
    return value.isoformat() if value is not None else None


def _core(cur, revision_id):
    cur.execute(
        """
        SELECT
            r.id::text,
            r.proposal_id::text,
            r.revision,
            r.decision_state,
            r.summary,
            r.payload,
            r.cited_unit_ids,
            r.missing_predicates,
            r.requires_professional_verification,
            r.knowledge_release_id::text,
            r.created_at,
            p.case_id::text,
            c.reference,
            a.id::text,
            a.operation_id,
            a.runner,
            a.model_id,
            a.model_config,
            a.prompt_version,
            a.prompt_digest,
            a.tool_schema_version,
            a.context_builder_version,
            a.knowledge_release_id::text,
            a.result_state,
            a.failure_reason,
            a.started_at,
            a.ended_at,
            a.latency_ms,
            a.input_tokens,
            a.output_tokens,
            a.tool_call_count
        FROM app.proposal_revisions r
        JOIN app.action_proposals p ON p.id = r.proposal_id
        JOIN app.cases c ON c.id = p.case_id
        JOIN app.agent_runs a ON a.id = r.run_id
        WHERE r.id = %s
        """,
        (revision_id,),
    )

    return cur.fetchone()


def _tool_trajectory(cur, run_id):
    """What actually ran, in order, and whether each call came back."""
    cur.execute(
        """
        SELECT sequence, tool_name, result_summary, error, duration_ms
        FROM app.agent_tool_calls
        WHERE run_id = %s
        ORDER BY sequence
        """,
        (run_id,),
    )

    trajectory = []

    for sequence, name, result_summary, error, duration_ms in cur.fetchall():
        trajectory.append(
            {
                "sequence": sequence,
                "tool": name,
                "outcome": "ERROR" if error else "OK",
                "error": error or None,
                "duration_ms": duration_ms,
                # A reference to what came back, not the payload. The
                # row itself is the evidence; this says what shape it
                # had so the receipt cannot imply more than it read.
                "result_keys": sorted(result_summary)
                if isinstance(result_summary, dict)
                else None,
            }
        )

    return trajectory


def _facts(cur, case_id, run_id):
    """Established, asserted, and which of those this run wrote."""
    cur.execute(
        """
        SELECT predicate, value_text, status, origin, evidence_refs,
               run_id::text, created_at
        FROM app.case_facts
        WHERE case_id = %s
        ORDER BY predicate, created_at
        """,
        (case_id,),
    )

    confirmed = []
    proposed_by_run = []
    asserted_elsewhere = []

    for (
        predicate,
        value_text,
        status,
        origin,
        evidence_refs,
        fact_run,
        created_at,
    ) in cur.fetchall():
        entry = {
            "predicate": predicate,
            "value": value_text,
            "origin": origin,
            "evidence_refs": list(evidence_refs or []),
            "recorded_at": _when(created_at),
        }

        if status == "CONFIRMED":
            confirmed.append(entry)
        elif status == "PROPOSED" and fact_run == run_id:
            proposed_by_run.append(entry)
        elif status == "PROPOSED":
            asserted_elsewhere.append(entry)

    return confirmed, proposed_by_run, asserted_elsewhere


def build(cur, revision_id):
    """One decision, accounted for. Raises if the revision is unknown."""
    row = _core(cur, revision_id)

    if row is None:
        raise ReceiptUnavailable(
            f"revision {revision_id} does not exist, so there is no "
            f"decision to account for"
        )

    (
        revision_uuid,
        proposal_id,
        revision_number,
        decision_state,
        summary,
        payload,
        cited_unit_ids,
        missing_predicates,
        requires_verification,
        revision_release,
        revision_created,
        case_id,
        case_reference,
        run_id,
        operation_id,
        runner,
        model_id,
        model_config,
        prompt_version,
        prompt_digest,
        tool_schema_version,
        context_builder_version,
        run_release,
        result_state,
        failure_reason,
        started_at,
        ended_at,
        latency_ms,
        input_tokens,
        output_tokens,
        tool_call_count,
    ) = row

    confirmed, proposed_by_run, asserted_elsewhere = _facts(
        cur, case_id, run_id
    )

    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "case": {"case_id": case_id, "reference": case_reference},
        "run": {
            "run_id": run_id,
            "operation_id": operation_id,
            "result_state": result_state,
            "failure_reason": failure_reason,
            "started_at": _when(started_at),
            "ended_at": _when(ended_at),
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_call_count": tool_call_count,
        },
        "system": {
            "agent_runtime": runner,
            "model_id": model_id,
            "model_config": model_config or {},
            "prompt_version": prompt_version,
            "prompt_digest": prompt_digest,
            "tool_schema_version": tool_schema_version,
            "context_builder_version": context_builder_version,
        },
        "knowledge": {
            # Both, because a difference between them is itself
            # information: one is the release the run opened with, the
            # other the release the proposal says it relied on.
            "release_in_force": run_release,
            "release_relied_on": revision_release,
            "cited_unit_ids": list(cited_unit_ids or []),
        },
        "facts": {
            "established": confirmed,
            "proposed_by_this_run": proposed_by_run,
            "asserted_elsewhere": asserted_elsewhere,
            "unknown": list(missing_predicates or []),
        },
        "tools": _tool_trajectory(cur, run_id),
        "decision": {
            "proposal_id": proposal_id,
            "revision_id": revision_uuid,
            "revision": revision_number,
            "decision_state": decision_state,
            "summary": summary,
            "payload": payload or {},
            "requires_professional_verification": requires_verification,
            "decided_at": _when(revision_created),
        },
        # Whether this may be presented as work to do. One rule, the
        # same one the queue and the case page use.
        "actionable": actionability.run_is_actionable(result_state),
        "reconstruction_limits": list(RECONSTRUCTION_LIMITS),
    }

    receipt["receipt_digest"] = digest(receipt)

    return receipt


def canonical(receipt):
    """The bytes the digest is taken over. Excludes the digest itself."""
    body = {
        key: value
        for key, value in receipt.items()
        if key != "receipt_digest"
    }

    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def digest(receipt):
    return hashlib.sha256(canonical(receipt)).hexdigest()


def field_names(receipt, prefix=""):
    """Every key in the receipt, nested ones included."""
    names = set()

    for key, value in receipt.items():
        names.add(key)

        if isinstance(value, dict):
            names |= field_names(value, prefix=f"{prefix}{key}.")
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    names |= field_names(entry, prefix=f"{prefix}{key}.")

    return names
