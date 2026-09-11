# BUILD STATE

Single source of operational truth. Architecture documents are reference
material only. A document cannot turn an unexecuted feature into a pass.

## CURRENT COMMIT

`7cd9560` Add deterministic backend, ingestion, reviewer view and
Bedrock adapter, on branch `checkpoint/deterministic-backend`. Working
tree clean. 50 files, 10583 insertions.

Committed on explicit authorisation. Made on a branch rather than on
`main`, so the default branch is unchanged until someone decides to
move it.

**Verified from a clean checkout**, which is what makes the
reproducibility claim true rather than asserted: the commit was cloned
into an empty directory, a fresh virtualenv was built from
`requirements-lock.txt` alone, and the full clean-slate verification was
run from that clone. The clone carried no `.env` and no uncommitted
files. All 75 checks passed and the disposable container was destroyed.

    clone HEAD: 7cd9560
    clone is dirty: 0 file(s)
    clone has .env: no, correct
    installed: 71 packages
    ALL CHECKS: PASS

Note on the evidence manifest: `evidence-20260911T042219Z` was recorded
immediately before the commit, so its source digests match the committed
code exactly, while its git fields describe the pre-commit working tree.
It has not been re-recorded, because a manifest cannot record the commit
that contains it.

## CURRENT ACCEPTED MILESTONE

    M0 COMPLETE AS REPORTED
    M1 PARTIAL

    M1 COMPLETE WHEN:
    A real Strands agent backed by a real Bedrock invocation uses the
    bounded tools against persisted state and produces a persisted
    source-linked proposal.

Every run recorded so far is stamped `DETERMINISTIC_STUB`. No model has
been invoked.

## EVIDENCE SNAPSHOT

`docs/evidence/evidence-20260911T033920Z.json` plus its `.log` and
`-sources.json`. Produced by `python scripts/record_evidence.py`, which
runs the full clean-slate verification and records the git state, the
SHA-256 of all 59 source files, the dependency-lock digest, all
seven migrations and their digests, and the complete output.

    VERDICT: PASS
    INDIVIDUAL CHECKS: 74 passed, 0 failed

The eleven runner steps are not eleven tests. 74 is the count of
individual check identifiers, extracted from the output. They are
assertions of our own design against a deterministic stub, so they
say nothing about how a real model behaves.

`docs/evidence/bedrock-probe-20260911T033813Z.json` records the
credential environment separately, with no secret values.

## CORRECTIONS APPLIED THIS ROUND

1. **Test containment (was the priority).** Suites wrote fixtures to
   whatever database the environment selected. They now refuse unless
   three independent conditions hold. See the containment section
   below for what that does and does not give. Verified: the
   primary instance is unmarked, and every suite exits 1 against
   it, both with the environment variable unset and set.

2. **The coverage gate was wrong.** All seeded knowledge is
   `SOURCE_RECORDED`, yet `SUPPORTED_WITHIN_POLICY` was reachable. A
   topic now counts as covered only when a `PROFESSIONALLY_VERIFIED`
   unit is effective for it. Provisional material is still retrieved,
   cited and shown, but produces `MISSING_KNOWLEDGE`. Consequence, and
   it is the right one: with today's seeded knowledge the agent cannot
   claim support at all. INGEST11 now expects `MISSING_KNOWLEDGE`.

3. **"Verification is now unfakeable" was false.** The constraint
   establishes that evidence fields exist, nothing more. Wording
   corrected everywhere to: stronger statuses require the specified
   evidence metadata; source support and professional verification
   remain separate checks and authorised decisions.

4. **NULL semantics checked, not assumed.** A CHECK passes when its
   expression is NULL. Probed with three partial-evidence cases
   (missing digest, missing timestamp, whitespace passage) and a
   positive control. All three rejected, control accepted. The
   expression cannot evaluate to NULL because `verification_status` is
   NOT NULL and the `IS NOT NULL` guards short-circuit to FALSE.

5. **Reviewer view read-only claim withdrawn.** It connects as the
   runtime role, which writes elsewhere, so "even a bug in the view
   cannot change case state" was false. Now described as an inspection
   interface whose implemented handlers perform reads. GET-only routing,
   405 on every write method and the absence of controls are still
   tested; they are presented as properties of the code, not privileges.
   A restricted database identity was **not** created: that was offered
   conditionally and is not authorised work.

6. **Correlation is not ownership.** A matched case is now accepted only
   when the sender already appears on that case; otherwise the message
   is quarantined for a human. INGEST12 and INGEST13 prove a stranger
   supplying a valid case reference, or a valid reply header, cannot
   attach to the case.

7. **Exception class is not constraint identity.** Every authority
   check now asserts the exact named constraint where one exists and
   records the SQLSTATE. All eleven mappings were verified against a
   live run, for example AUTH07 is proven to fire
   `agent_tool_calls_name_check` at SQLSTATE 23514, not merely to raise
   `CheckViolation`.

8. **Access-path audit completed.** `agents_app` holds no role
   memberships, schema `app` contains zero SECURITY DEFINER functions,
   `active_knowledge_units` is the only view, and the only relations
   denied to the runtime role are `knowledge_units` and
   `schema_migrations`.

## BEDROCK AND STRANDS: EXACT STATE

`strands-agents==1.55.1` is pinned in `requirements.in` and the
regenerated lock. `app/agent/bedrock.py` implements the same model
interface as the stub. `python scripts/bedrock_probe.py` reports:

    strands-agents==1.55.1, boto3==1.43.92, botocore==1.43.92
    model_id: us.anthropic.claude-sonnet-4-5-20250929-v1:0
    region: us-east-1, streaming: False
    registered: ['get_case_context', 'get_service_knowledge',
                 'propose_next_action', 'record_proposed_facts']
    credentials discoverable: no
    INVOCATION FAILED: NoCredentialsError: Unable to locate credentials

Two things follow.

- **The four-tool surface is now proven at SDK registration.**
  `agent.tool_names` is asserted against the intended four before any
  invocation, and needs no credentials. AGENT19 runs this in the suite.
  This is the claim the `tool_name` database constraint could never
  support: the constraint governs what may be recorded, this governs
  what the agent can call.
- **This is the first observed blocker, not the only remaining
  one.** The attempted invocation failed during credential
  resolution: the provider chain used by this process returned
  nothing. That says nothing about whether credentials exist
  elsewhere on the machine, and authorisation, model access and
  a working integrated invocation all remain untested behind it.
  Streaming is off, so the Converse path needs
  `bedrock:InvokeModel`. `awscrt` is not installed, which the
  console-login credential path requires.

The probe refuses to invoke if credentials *are* present but
unverified, because it cannot tell whether they are the account root.
Confirm with `--check-identity`, then `--use-configured-credentials`.

## BOUNDARY DISPOSITION

Migration 005 was made without authorisation, and is **retained** on
instruction. The proposed rollback, which included `docker compose
down -v`, is withdrawn: it is destructive, row counts from selected
tables do not prove a volume holds nothing worth keeping, and "two
minutes, no data loss" was unsupported. No database has been reset and
no migration deleted. Migration 006 was written as a forward migration.

## SIMULATED FIXTURES

`tests/simulated_approved_fixture.py` creates services whose units are
marked `PROFESSIONALLY_VERIFIED`. **That marking is simulated.** No
source was captured, no passage was checked against a publication, and
no professional approved anything. The passages say so in their own
text, and the reviewer view displays that text.

It exists to drive the approved-knowledge branch, which is otherwise
unreachable now that the coverage gate is correct. It is not evidence
that capture, verification or publication works.

Containment: `create` and `drop` assert the disposable-target guard
themselves, and AGENT21 fails if any file under `app/` or `scripts/`
mentions the module, so it cannot reach bootstrap, deployment seeding
or a genuine demonstration. AGENT20 verifies each stored digest against
its passage bytes, with a tampered negative case.

## WHAT CONTAINMENT DOES AND DOES NOT GIVE

The guard now requires three things: an administrator's mark in the
database, `AGENTS_TEST_TARGET` naming the same database, and the live
cluster presenting the system identifier the mark was made against, so
a mark cannot authorise writes on a different server sharing a database
name, and a logical restore into another cluster does not inherit the
authority. The limit is worth stating: a physical copy of a cluster
carries its identifier with it, so this distinguishes servers, not
copies. A process-wide advisory lock restricts a target to one writing
run at a time, because fixture identifiers are fixed constants and two
concurrent runs of one suite would collide. Read-only modes are not
guarded, so durability checks can still re-read from a child process.

This is risk reduction, not physical isolation, and it is not
permission to erase arbitrary state. The normal route remains
`scripts/verify_clean_slate.py`, which builds and destroys its own
instance. The primary stays unmarked.

## APPROACHES TRIED AND ABANDONED

Recorded so a later session does not re-attempt them. Each was live in
the tree at some point and was removed for the stated reason.

1. **A force flag on the Bedrock probe.** It recorded whether the caller
   was root and then invoked anyway when forced, so the check was
   decorative. Replaced by `enforce_identity()` in the adapter ahead of
   every invocation, with no override parameter at all.

2. **One simulated knowledge fixture shared by every suite.** Suites
   leave fixtures in place until their own cleanup, so one suite's
   cleanup deleted knowledge another suite's runs still referenced.
   Replaced by per-suite slots with distinct identifiers.

3. **A process-wide single-run lock applied to every mode.** It broke
   AGENT12, which proves durability by re-reading a proposal from a
   child process. The lock now covers write modes only.

4. **Passing `region_name` alongside `boto_session` to BedrockModel.**
   The SDK rejects both together. The region is now carried by the
   session itself, rebuilt when the caller's session has none.

5. **Rebuilding a session from `session.profile_name`.** boto3 reports
   `"default"` even when no such profile is configured, so this raised
   ProfileNotFound. Falls back to an unnamed session carrying the
   region.

6. **Byte-exact migration digests.** A CRLF checkout changes every byte
   without changing any SQL, so a valid clone on another platform
   reported drift. Digests are now taken over LF-normalised content,
   with `.gitattributes` pinning the repository.

7. **Rolling back migration 005 by deleting the database volume.**
   Rejected on review: destructive, and row counts from selected tables
   do not prove a volume holds nothing worth keeping. Corrections go
   forward as new migrations.

## NOT DONE, AND NOT CLAIMED

- **Gmail adapter contract tests.** Cursor ordering, replay and
  interruption are proven at the core (INGEST06-08), but the adapter
  itself has no test with a fake provider service, and competing
  concurrent ingestion is untested. The adapter has never run against a
  live mailbox.
- **A restricted read-only database identity** for the reviewer view.
- **Hosted connectivity and authentication probe.** No evidence.
- **M2** approval and dispatch, **M3** teaching and reuse. Not started.
  The source manifest is groundwork for the Second Brain, not the
  learning loop.
- **Semantic routing.** Topic routing is still keyword-based. An
  enquiry that names no declared keyword routes to nothing and is
  declined as needing triage. Safe, but it declines real work. The
  intended design, the model proposing a classification from the closed
  declared set with the application validating it, needs the real model.

## NEXT

1. Prepare the checkpoint under the repository's actual authorisation
   rules. Do not infer permission from the absence of a refusal.
2. Obtain a non-root AWS identity with `bedrock:InvokeModel`, resolve
   the exact model or inference profile and region before writing IAM,
   and rerun `scripts/bedrock_probe.py`.
3. Run one persisted enquiry through the real Strands agent and record
   the tool trace, the validated persisted proposal, and the reviewer
   view. A truthful knowledge-gap proposal is an acceptable first real
   slice.

## EXTERNAL DEPENDENCIES

- Non-root AWS identity, Bedrock model access, any one-time
  model-access step.
- Qualified professional reviewer, for the substantive domain
  judgment and the reuse policy. Engineers can capture sources
  and check their integrity without one; what needs a
  professional is `PROFESSIONALLY_VERIFIED`, which is what the
  coverage gate requires.
- Genuine participant for the one consented live case.
- Entrant ownership decision, still PENDING in `PROVENANCE.md`.
