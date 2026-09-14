"""Why Nicole decided what she decided, assembled from the record.

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
from app.domain import provenance

RECEIPT_VERSION = "agent-decision-receipt-v1"
REASONING_RECEIPT_VERSION = "agent-reasoning-receipt-v2"

# Who asserted a thing. Never inferred from a field name: the
# payload key called `rationale` is the application's own
# finding, and a receipt that guessed from the name would
# present it as the model's testimony.
ORIGIN_SYSTEM = "SYSTEM_VERIFIED"
ORIGIN_MODEL = "MODEL_STATED"
ORIGIN_HUMAN = "HUMAN_STATED"
ORIGIN_ABSENT = "UNAVAILABLE"

ORIGIN_CLASSES = (
    ORIGIN_SYSTEM,
    ORIGIN_MODEL,
    ORIGIN_HUMAN,
    ORIGIN_ABSENT,
)

# Excluded from the canonical bytes. Everything here is current
# presentation that a rename may change, and a digest that moved
# when somebody was renamed would let history be retitled.
UNDIGESTED_SECTIONS = ("receipt_digest", "display")

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


# Which half of the proposal payload came from where. `tools.py` builds
# one dict out of two sources and the names do not tell you which:
# `rationale` is the deterministic evaluation's reasoning, not the
# model's, and putting it on the model's side would present the
# application's own finding as the model's testimony.
#
# Anything the model supplied. Bounded on purpose: an open-ended dump
# would be the hidden-deliberation field this design refuses, under a
# different name. The last six are not yet produced -- the tool contract
# does not ask for them -- and are listed so that asking for them later
# is a one-line change rather than a redesign.
MODEL_STATED_FIELDS = (
    "model_payload",
    "requested_information",
    "reason_codes",
    "decision_factors",
    "uncertainties",
    "information_needed",
    "conditions_that_would_change_outcome",
    "proposed_next_action",
)

# Written by the deterministic evaluation, from `self._evaluation` in
# `tools.py`. These are findings of the application, and belong on the
# system-verified side however much `rationale` sounds like the model
# talking.
DETERMINISTIC_GATE_FIELDS = (
    "rationale",
    "coverage_gaps",
    "provisional_topics",
    "out_of_scope_topics",
    "conflicts",
    "permitted_states",
    "rules_version",
)


def _partition(payload, names):
    if not isinstance(payload, dict):
        return {}

    return {
        name: payload[name]
        for name in names
        if payload.get(name) not in (None, "", [], {})
    }


def _model_stated(payload):
    """What the model said about its own reasoning, and nothing else.

    Returned under a name that says who is asserting it. The model
    cannot certify the truth of its own explanation, so this is
    testimony, not a finding.
    """
    return {
        "asserted_by": "MODEL",
        "verified": False,
        "fields": _partition(payload, MODEL_STATED_FIELDS),
    }


def _gate_state(payload):
    """What the deterministic evaluation concluded, and permitted."""
    return {
        "asserted_by": "APPLICATION",
        "verified": True,
        "fields": _partition(payload, DETERMINISTIC_GATE_FIELDS),
    }


class ReceiptUnavailable(Exception):
    """There is no such revision, so there is nothing to account for."""


def _when(value):
    """Timestamps as text, so a receipt is JSON and its digest stable."""
    return value.isoformat() if value is not None else None


def _provider_and_runtime(runner):
    """Split the recorded runner into the provider and the runtime.

    `runner` is stored as PROVIDER_RUNTIME, which is convenient for one
    constraint and misleading in a receipt: a reader needs to know that
    swapping the provider did not change who acted. The stub has no
    provider and says so.
    """
    if not runner:
        return None, None

    if runner == "DETERMINISTIC_STUB":
        return "NONE", "DETERMINISTIC_STUB"

    provider, _, runtime = runner.partition("_")

    return provider or None, runtime or None


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
            a.agent_profile_id::text,
            a.initiated_by_reviewer_id::text,
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


# Counts a tool's own result_summary may carry that distinguish what
# was usable from what was merely seen. Read generically -- by key, not
# by tool name -- because the point is not to know which tool this is,
# but to refuse to let OUTCOME: OK stand in for "returned trusted
# material" when the summary itself says otherwise.
_TRUST_SIGNAL_KEYS = (
    "units",
    "consulted_unverified",
    "applicability_unknown",
    "not_applicable",
)


def _trust_signals(result_summary):
    """What this call's own summary says about what it could not use.

    A call can succeed and still have found nothing trustworthy: it
    consulted an unverified source, or a verified one whose
    applicability could not be resolved. Neither may support a
    conclusion, and OUTCOME: OK alone does not say so. None of this is
    computed here -- it is read from what the tool already recorded.
    """
    if not isinstance(result_summary, dict):
        return None

    present = {
        key: result_summary[key]
        for key in _TRUST_SIGNAL_KEYS
        if key in result_summary
    }

    return present or None


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
                # None on an errored call: a call that did not
                # complete reported nothing about what it found, and
                # must not be read as having found nothing trustworthy
                # -- it found nothing at all.
                "trust_signals": (
                    _trust_signals(result_summary) if not error else None
                ),
                # Safe references captured from the exact result the tool
                # returned in this run.  Never reconstructed from current
                # corpus state and never presented as model cognition.
                "tool_returned_evidence": (
                    list(result_summary.get("returned_evidence") or [])
                    if (
                        name == "get_service_knowledge"
                        and isinstance(result_summary, dict)
                        and not error
                    )
                    else []
                ),
            }
        )

    return trajectory


def _facts(cur, case_id, run_id):
    """Established now, asserted elsewhere, and what this run wrote.

    The third of these carries no status. Which rows a run wrote is
    fixed for ever; whether any of them has since been confirmed is
    current state, and mixing the two made a historical digest move
    when a professional confirmed a fact weeks later.
    """
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
    written_by_run = []
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
            **provenance.fact_claim(origin, status),
        }

        if fact_run == run_id:
            # No status. This run asserted this, and that does not
            # stop being true when somebody later confirms it.
            written_by_run.append(entry)

        if status == "CONFIRMED":
            confirmed.append(entry)
        elif status == "PROPOSED" and fact_run != run_id:
            asserted_elsewhere.append(entry)

    return confirmed, written_by_run, asserted_elsewhere


def _cited_units_live(cur, unit_ids):
    """The fallback: current status, for a run with no manifest to ask.

    Reads `app.active_knowledge_units`, the view the runtime role can
    actually see: migration 005 revoked direct SELECT on the base
    table. A unit whose release has since been superseded is therefore
    genuinely invisible here, and is reported the same as a citation to
    nothing rather than assumed still verified. Only reached when no
    bound manifest exists -- current status is not a substitute for a
    historical observation, only the best available answer without one.
    """
    if not unit_ids:
        return []

    cur.execute(
        """
        SELECT u.id::text, u.verification_status,
               EXISTS (
                   SELECT 1 FROM app.knowledge_unit_conflicts c
                   WHERE c.unit_id = u.id OR c.conflicting_unit_id = u.id
               ) AS conflicted
        FROM app.active_knowledge_units u
        WHERE u.id = ANY(%s)
        """,
        (list(unit_ids),),
    )

    found = {row[0]: row for row in cur.fetchall()}
    cited = []

    for unit_id in unit_ids:
        row = found.get(unit_id)

        if row is None:
            # Cited, but the unit no longer resolves. Reported rather
            # than skipped: a citation to nothing is itself material.
            cited.append(
                {
                    "unit_id": unit_id,
                    "verification_status": None,
                    **provenance.knowledge_claim(None),
                }
            )
            continue

        _id, verification_status, conflicted = row

        cited.append(
            {
                "unit_id": unit_id,
                "verification_status": verification_status,
                **provenance.knowledge_claim(
                    verification_status, conflicted
                ),
            }
        )

    return cited


def _cited_units(cur, unit_ids, manifest):
    """Provenance and authority for each unit this decision cited.

    Prefers the bound manifest's own `deterministic_knowledge_snapshot` -- what
    the deterministic evaluation actually retrieved, before the model
    reasoned, snapshotted at that moment. That is the fix for the
    defect this function used to have: reading current corpus state at
    receipt-build time meant superseding a cited unit's release
    silently downgraded a decision's evidence months after the fact.

    Falls back to a live lookup only for a run with no manifest, or one
    from before this field existed. Both axes stay the same either way;
    only where the authority came from changes.
    """
    if not unit_ids:
        return []

    observed = {
        entry["unit_id"]: entry
        for entry in (manifest or {}).get("deterministic_knowledge_snapshot") or ()
    }

    if not observed:
        return _cited_units_live(cur, unit_ids)

    cited = []

    for unit_id in unit_ids:
        entry = observed.get(unit_id)

        if entry is None:
            # Cited, but deterministic preflight never retrieved it --
            # a proposal citing something outside what it retrieved,
            # which should not happen and is reported rather than
            # silently backfilled from current state.
            cited.append(
                {
                    "unit_id": unit_id,
                    "verification_status": None,
                    **provenance.knowledge_claim(None),
                }
            )
            continue

        cited.append(
            {
                "unit_id": entry["unit_id"],
                "verification_status": entry["verification_status"],
                "provenance": entry["provenance"],
                "authority": entry["authority"],
            }
        )

    return cited


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
        run_agent_profile_id,
        initiated_by,
        started_at,
        ended_at,
        latency_ms,
        input_tokens,
        output_tokens,
        tool_call_count,
    ) = row

    confirmed, written_by_run, asserted_elsewhere = _facts(
        cur, case_id, run_id
    )

    from app.domain import agent_identity, context_manifest

    acting = agent_identity.profile(cur, run_agent_profile_id)
    provider_name, runtime_name = _provider_and_runtime(runner)
    trajectory = _tool_trajectory(cur, run_id)
    manifest = context_manifest.for_run(cur, run_id)

    # A manifest is the authority for what the run saw. Without one,
    # the same fields mean something weaker -- the case as it stands
    # now -- and the receipt has to say which it is giving.
    if manifest:
        facts_as_of = "RUN"
        established = manifest["confirmed_facts"]
        asserted = manifest["unconfirmed_assertions"]
        unknown = manifest["missing_material_predicates"]
        material_date = manifest["material_date"]
        limits = []
    else:
        facts_as_of = "CURRENT"
        established = confirmed
        asserted = asserted_elsewhere
        unknown = list(missing_predicates or [])
        material_date = None
        limits = list(RECONSTRUCTION_LIMITS)

    receipt = {
        # Every schema this artifact is shaped by, so a receipt
        # read years later can be interpreted against the contract it
        # was written under rather than the current one.
        "receipt_version": RECEIPT_VERSION,
        "reasoning_receipt_version": REASONING_RECEIPT_VERSION,
        "context_manifest_version": (
            manifest["manifest_version"]
            if manifest
            else agent_identity.MANIFEST_ABSENT
        ),
        # The business correlation id. Designated rather than
        # introduced: every artifact in the journey -- enquiry, runs,
        # manifests, proposals, receipts -- already resolves to the
        # case, and a second identifier meaning the same thing would
        # create two answers to one question. It is independent of any
        # tracing system's ids.
        "correlation_id": case_id,
        "case": {"case_id": case_id},
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
        # Four identities that were previously one field. The model is
        # replaceable without the agent changing; the agent acts for a
        # human and holds no authority of its own.
        "identity": {
            "human_principal_id": initiated_by,
            "agent_profile_id": run_agent_profile_id,
            "agent_role": acting["agent_role"] if acting else None,
            "provider": provider_name,
            "runtime": runtime_name,
            "model": model_id,
            # Recorded verbatim as well, because the split above is an
            # interpretation of it and the row is the evidence.
            "runner_recorded": runner,
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
            # Each with both provenance axes. A cited unit's own
            # authority -- whether it climbed to the professionally
            # verified rung -- is a property of the source, kept
            # separate from the fact that citing it was a system act.
            "cited_units": _cited_units(
                cur, cited_unit_ids or [], manifest
            ),
            # RUN: read from this run's own manifest, immune to a
            # later supersession. CURRENT: no manifest bound to this
            # run, so the corpus was asked as it stands today.
            "system_support_evidence_as_of": (
                "RUN"
                if (manifest or {}).get("deterministic_knowledge_snapshot")
                else "CURRENT"
            ),
        },
        "facts": {
            # RUN means these are the facts the run was handed, taken
            # from its bound manifest. CURRENT means no manifest exists
            # and this is the case as it stands today, which is not the
            # same claim.
            "as_of": facts_as_of,
            "established": established,
            # What this run asserted, without a status: the rows
            # are historical, their statuses are not.
            "written_by_this_run": written_by_run,
            "asserted_elsewhere": asserted,
            "unknown": unknown,
        },
        "context": {
            "manifest_version": (
                manifest["manifest_version"]
                if manifest
                else agent_identity.MANIFEST_ABSENT
            ),
            "context_digest": (
                manifest["context_digest"] if manifest else None
            ),
            "material_date": material_date,
            "applicability_inputs": (
                manifest["applicability_inputs"] if manifest else {}
            ),
            "policy_envelope": (
                manifest["policy_envelope"] if manifest else {}
            ),
            "captured_at": manifest["captured_at"] if manifest else None,
        },
        "tools": trajectory,
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
        # What this particular receipt cannot establish. Empty when a
        # manifest exists, because then it can.
        "reconstruction_limits": limits,
        # Which half of this receipt is which. Everything named here
        # was read from rows the application owns.
        "system_verified_basis": {
            "asserted_by": "APPLICATION",
            "verified": True,
            "sections": [
                "case",
                "run",
                "identity",
                "system",
                "knowledge",
                "facts",
                "context",
                "tools",
                "decision",
                "gates",
            ],
        },
        "gates": _gate_state(payload or {}),
        "model_stated_rationale": _model_stated(payload or {}),
        # The observable decision basis, in the order it was acquired,
        # with each part owning its origin:
        #
        #   S    deterministic system preflight state
        #   T    what tools returned during the run
        #   G    what the deterministic gates concluded
        #   M    what the model said about its own reasoning
        #
        # X0 alone is not "everything the agent knew": the tools told
        # it things afterwards, and a receipt that stopped at X0 would
        # describe a decision made on less than was available.
        "reasoning": {
            "system_preflight_basis": {
                "origin": ORIGIN_SYSTEM if manifest else ORIGIN_ABSENT,
                "context_digest": (
                    manifest["context_digest"] if manifest else None
                ),
                "manifest_version": (
                    manifest["manifest_version"]
                    if manifest
                    else agent_identity.MANIFEST_ABSENT
                ),
            },
            "actual_tool_trajectory": {
                "origin": ORIGIN_SYSTEM if trajectory else ORIGIN_ABSENT,
                "count": len(trajectory),
                "sequence": [call["sequence"] for call in trajectory],
                "calls": trajectory,
            },
            "system_support_evidence": {
                "origin": ORIGIN_SYSTEM,
                "unit_ids": list(cited_unit_ids or []),
                "note": "deterministic support evidence, not model citations",
            },
            "deterministic_gates": {
                "origin": ORIGIN_SYSTEM,
                "fields": sorted(_gate_state(payload or {})["fields"]),
            },
            "model_rationale": {
                # UNAVAILABLE where the model supplied nothing, which
                # is the truthful answer. Synthesising an explanation
                # after the run would be inventing testimony.
                "origin": (
                    ORIGIN_MODEL
                    if _model_stated(payload or {})["fields"]
                    else ORIGIN_ABSENT
                ),
                "fields": sorted(_model_stated(payload or {})["fields"]),
            },
            "human_corrections": {
                # The correction receipt is a written contract and is
                # not built, so there is nothing human-stated to carry
                # yet. Named here so its absence is visible rather than
                # simply missing.
                "origin": ORIGIN_ABSENT,
                "note": "correction receipts are contracted, not built",
            },
        },
        # Current presentation, outside the digest. A reader who sees a
        # name beside a digest will assume the digest covers it.
        "display": {
            "agent_profile": acting["display_name"] if acting else None,
            "human_principal": (
                acting["owner_display_name"] if acting else None
            ),
            "case_reference": case_reference,
            "note": (
                "current display metadata; not covered by "
                "receipt_digest, because a rename must not rewrite a "
                "historical decision"
            ),
        },
    }

    receipt["receipt_digest"] = digest(receipt)

    return receipt


def canonical(receipt):
    """The bytes the digest is taken over. Excludes the digest itself."""
    body = {
        key: value
        for key, value in receipt.items()
        if key not in UNDIGESTED_SECTIONS
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


# ---------------------------------------------------------------------
# Who may read which field.
# ---------------------------------------------------------------------

SAFE_DISPLAY = "SAFE_DISPLAY"
CASE_SENSITIVE = "CASE_SENSITIVE"
ENGINEERING_ONLY = "ENGINEERING_ONLY"

# Sections a viewer outside the case must not see the contents of. The
# shape stays visible -- how many facts were established, how many
# remained unknown -- because that is what demonstrates the reasoning,
# and the values are what would expose a client.
SENSITIVITY = {
    "correlation_id": SAFE_DISPLAY,
    "receipt_version": SAFE_DISPLAY,
    "reasoning_receipt_version": SAFE_DISPLAY,
    "context_manifest_version": SAFE_DISPLAY,
    "case": SAFE_DISPLAY,
    "identity": SAFE_DISPLAY,
    "run": SAFE_DISPLAY,
    "system": ENGINEERING_ONLY,
    "knowledge": SAFE_DISPLAY,
    "facts": CASE_SENSITIVE,
    "context": CASE_SENSITIVE,
    "tools": SAFE_DISPLAY,
    "decision": CASE_SENSITIVE,
    "gates": SAFE_DISPLAY,
    "model_stated_rationale": CASE_SENSITIVE,
    "system_verified_basis": SAFE_DISPLAY,
    "reasoning": SAFE_DISPLAY,
    "display": CASE_SENSITIVE,
    "actionable": SAFE_DISPLAY,
    "reconstruction_limits": SAFE_DISPLAY,
    "receipt_digest": SAFE_DISPLAY,
}

ENGINEERING_TRACE = "ENGINEERING_TRACE"
PARTNER_RECEIPT = "PARTNER_RECEIPT"
DEMO_RECEIPT = "DEMO_RECEIPT"

VIEWS = (ENGINEERING_TRACE, PARTNER_RECEIPT, DEMO_RECEIPT)

REDACTED = "[redacted]"


def _shape_of(value):
    """What remains of a sensitive value once its content is removed.

    Counts, not contents. "three facts were established and one was
    missing" is the part that demonstrates the reasoning; which facts
    they were is the part that belongs to the client.
    """
    if isinstance(value, dict):
        return {
            key: _shape_of(inner) for key, inner in value.items()
        }

    if isinstance(value, list):
        return {"count": len(value)}

    if value is None or isinstance(value, bool):
        return value

    return REDACTED


def view(receipt, audience=PARTNER_RECEIPT):
    """One receipt, rendered for one audience.

    A projection for reading. It is deliberately not re-digested: the
    digest belongs to the authoritative record, and a digest over a
    redacted view would invite comparing two things that were never
    the same document.
    """
    if audience not in VIEWS:
        raise ValueError(f"unknown audience {audience!r}")

    if audience == ENGINEERING_TRACE:
        return dict(receipt)

    rendered = {}

    for key, value in receipt.items():
        level = SENSITIVITY.get(key, CASE_SENSITIVE)

        if audience == PARTNER_RECEIPT:
            # A Partner reads their own case. Everything about it is
            # theirs; the technical detail is merely noise to them.
            if level == ENGINEERING_ONLY:
                continue

            rendered[key] = value
            continue

        # The demonstration view. Structure, not client content.
        if level == ENGINEERING_ONLY:
            continue

        rendered[key] = value if level == SAFE_DISPLAY else _shape_of(value)

    rendered["rendered_for"] = audience

    return rendered
