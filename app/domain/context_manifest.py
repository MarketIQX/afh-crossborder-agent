"""What the run was actually given, written down before it reasons.

A decision receipt could say what was decided but not reproduce the
ground it stood on. `app.case_facts` records when a fact row was
created and not when it became CONFIRMED, so reading a case's confirmed
facts tells you the state now. Yesterday's decision then appears to
have rested on today's facts, and a confirmation made this morning
silently improves the apparent basis of last week's answer.

The only honest fix is to capture the state at the time. This
serialises the context object the runner assembled — the same object
handed to the model, not a re-query of the same tables — and digests
it. Nothing amends it afterwards: the runtime holds INSERT and SELECT
on the table and no UPDATE or DELETE, so immutability is the absence of
a privilege rather than a promise in a comment.

Two things it deliberately does not do.

It does not capture the enquiry body or anything else about the client
beyond the facts the reasoning turned on. A manifest is a record of
material state, not an archive of correspondence, and copying private
content into a second place widens exposure for no audit gain.

It does not exist for runs that predate it, and nothing infers one.
Those runs report HISTORICAL_CONTEXT_INCOMPLETE. A reconstructed
manifest would be indistinguishable from a captured one while being a
guess, which is worse than an admitted gap.
"""

import hashlib
import json

from app.domain import provenance

MANIFEST_VERSION = "run-context-manifest-v3"


def _fact_entries(context):
    """Facts as the run saw them, split by the status they then had.

    Each entry carries both provenance axes -- where the value came
    from, and how much the system trusted it at the time -- so a
    manifest read later cannot be misread as "the system verified this
    is true" when what was verified is narrower: that a tool returned
    it, or that a professional typed it.
    """
    confirmed = []
    unconfirmed = []

    for fact in context.facts:
        entry = {
            "predicate": fact.predicate,
            "value": fact.value_text,
            "origin": fact.origin,
            "provenance": provenance.fact_provenance(fact.origin),
            "authority": provenance.fact_authority(fact.status),
        }

        if fact.status == "CONFIRMED":
            confirmed.append(entry)
        elif fact.status == "PROPOSED":
            unconfirmed.append(entry)

    confirmed.sort(key=lambda e: (e["predicate"], e["value"] or ""))
    unconfirmed.sort(key=lambda e: (e["predicate"], e["value"] or ""))

    return confirmed, unconfirmed


def _deterministic_knowledge_snapshot(units):
    """What deterministic preflight retrieved before model execution.

    `unit.citation()` is already the safe reference: id, key, topic,
    locator, verification status, effective dates -- never the
    statement text. Provenance and authority are attached the same way
    a case fact's are, so provenance and authority remain explicit. This
    is not evidence returned by Nicole's later knowledge tool.
    """
    if not units:
        return []

    observed = [
        {
            **unit.citation(),
            **provenance.knowledge_claim(unit.verification_status),
        }
        for unit in units
    ]

    observed.sort(key=lambda entry: entry["unit_id"])

    return observed


def describe(context, agent_profile_id=None, initiated_by=None, units=()):
    """The manifest for one assembled context, as a dict.

    Pure: takes the context the runner built and returns what should be
    recorded about it. Keeping it separate from the write means the
    digest can be computed and compared without touching a database.

    `units` is whatever deterministic preflight retrieved before
    reasoning began -- optional, because callers that have not yet
    passed it (or a manifest built before this field existed) still
    produce a valid manifest, just one with nothing to say about
    its preflight knowledge snapshot.
    """
    confirmed, unconfirmed = _fact_entries(context)

    body = {
        "manifest_version": MANIFEST_VERSION,
        "case_id": context.case_id,
        "service_id": context.service_id,
        "agent_profile_id": agent_profile_id,
        "initiated_by_reviewer_id": initiated_by,
        "material_date": context.material_date.isoformat(),
        "knowledge_release_id": context.knowledge_release_id,
        "confirmed_facts": confirmed,
        "unconfirmed_assertions": unconfirmed,
        "missing_material_predicates": sorted(
            context.missing_material_predicates
        ),
        "deterministic_knowledge_snapshot": _deterministic_knowledge_snapshot(units),
        # What the deterministic gates were given. Applicability is
        # decided by the application and not by the model, so its inputs
        # belong in the record of the decision rather than in a prompt.
        "applicability_inputs": {
            "material_date": context.material_date.isoformat(),
            "detected_topics": sorted(context.detected_topics),
            "in_scope_topics": sorted(context.in_scope_topics),
            "confirmed_predicates": sorted(context.confirmed_predicates),
        },
        "context_builder_version": context.context_builder_version,
        "truncations": sorted(str(t) for t in context.truncations),
    }

    body["context_digest"] = digest(body)

    return body


def canonical(body):
    """The bytes the digest covers. Excludes the digest itself."""
    return json.dumps(
        {k: v for k, v in body.items() if k != "context_digest"},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def digest(body):
    return hashlib.sha256(canonical(body)).hexdigest()


def record(cur, run_id, body, policy_envelope=None):
    """Write the manifest for a run. Once; there is no second chance.

    The table keys on `run_id`, so a duplicate raises rather than
    quietly producing two accounts of what one run saw.
    """
    cur.execute(
        """
        INSERT INTO app.run_context_manifests (
            run_id, manifest_version, case_id, service_id,
            agent_profile_id, initiated_by_reviewer_id,
            material_date, knowledge_release_id,
            confirmed_facts, unconfirmed_assertions,
            missing_material_predicates, deterministic_knowledge_snapshot,
            applicability_inputs,
            policy_envelope, context_builder_version, truncations,
            context_digest
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            %s,
            %s, %s, %s,
            %s
        )
        """,
        (
            run_id,
            body["manifest_version"],
            body["case_id"],
            body["service_id"],
            body["agent_profile_id"],
            body["initiated_by_reviewer_id"],
            body["material_date"],
            body["knowledge_release_id"],
            json.dumps(body["confirmed_facts"]),
            json.dumps(body["unconfirmed_assertions"]),
            json.dumps(body["missing_material_predicates"]),
            json.dumps(body.get("deterministic_knowledge_snapshot", [])),
            json.dumps(body["applicability_inputs"]),
            json.dumps(policy_envelope or {}),
            body["context_builder_version"],
            json.dumps(body["truncations"]),
            body["context_digest"],
        ),
    )

    return body["context_digest"]


def for_run(cur, run_id):
    """The manifest a run was bound to, or None if it predates this."""
    cur.execute(
        """
        SELECT manifest_version, case_id::text, service_id::text,
               agent_profile_id::text, initiated_by_reviewer_id::text,
               material_date, knowledge_release_id::text,
               confirmed_facts, unconfirmed_assertions,
               missing_material_predicates, deterministic_knowledge_snapshot,
               applicability_inputs,
               policy_envelope, context_builder_version, truncations,
               context_digest, captured_at
        FROM app.run_context_manifests
        WHERE run_id = %s
        """,
        (run_id,),
    )
    row = cur.fetchone()

    if row is None:
        return None

    return {
        "manifest_version": row[0],
        "case_id": row[1],
        "service_id": row[2],
        "agent_profile_id": row[3],
        "initiated_by_reviewer_id": row[4],
        "material_date": row[5].isoformat() if row[5] else None,
        "knowledge_release_id": row[6],
        "confirmed_facts": list(row[7] or []),
        "unconfirmed_assertions": list(row[8] or []),
        "missing_material_predicates": list(row[9] or []),
        "deterministic_knowledge_snapshot": list(row[10] or []),
        "applicability_inputs": dict(row[11] or {}),
        "policy_envelope": dict(row[12] or {}),
        "context_builder_version": row[13],
        "truncations": list(row[14] or []),
        "context_digest": row[15],
        "captured_at": row[16].isoformat() if row[16] else None,
    }
