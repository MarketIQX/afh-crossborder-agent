# BUILD STATE

Single source of operational truth. Architecture documents are reference
material only. A document cannot turn an unexecuted feature into a pass.

## CURRENT COMMIT

`4644768` Require standing for drafting and for revocation, on `main`,
pushed to the public repository at github.com/MarketIQX/afh-crossborder-agent.

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

    M0 COMPLETE
    M1 COMPLETE
    M2 PARTIAL

M1 closed on 2026-09-11. A real Strands agent on real Bedrock
read a persisted case through the four bounded tools and produced a
persisted source-linked proposal.

    runner          BEDROCK_STRANDS
    model_id        us.anthropic.claude-sonnet-4-5-20250929-v1:0
    region          us-east-1
    result_state    SUCCEEDED
    decision_state  MISSING_FACTS
    run_id          4c82b6fb-1a9b-4a59-ac45-a1a2de7171db
    tool calls      7, of which 2 were refused

**Caveat that travels with this result.** The run executed in the
workshop account `591893241838`, which expires. It proves the code
path, not the submission account. Moving to the project account is
three values in `.env`.

## EVIDENCE SNAPSHOT

`docs/evidence/evidence-20260911T080430Z.json` plus its `.log` and
`-sources.json`. Produced by `python scripts/record_evidence.py`, which
runs the full clean-slate verification and records the git state, the
SHA-256 of all 59 source files, the dependency-lock digest, all
seven migrations and their digests, and the complete output.

    VERDICT: PASS
    INDIVIDUAL CHECKS: 95 passed, 0 failed

The runner steps are not tests. 95 is the count of
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

## WHAT THE FIRST REAL MODEL RUN REVEALED

Two things no stub could have produced.

**The model reached outside its scope, and the boundary held.** At
sequence 2 and 3 of both recorded runs, Claude Sonnet 4.5 called
`get_service_knowledge` with a `service_id` of its own choosing. The
server refused both, recorded the attempts with their reason, and the
model then called the tool correctly and finished the work. Every scope
test before this used a deliberately hostile stub written to attempt the
violation. This was a frontier model reasoning normally about a real
case, and it is reproducible: it happened on two different cases.

**The trace had a concurrency bug the database was masking.** The first
run's evidence file recorded sequences 1,3,3,5,5,6,7 while the database
held 1..7. Strands executes tools concurrently, and `ToolTrace.record`
incremented a shared counter then read it again, so two threads could
observe the same later value. The database was correct because its
unique constraint would have rejected a collision; the in-memory list,
which is what the evidence file is built from, was not. The artefact we
would have shown someone was wrong while the authoritative store was
fine, which is the more dangerous way round. Fixed by taking the
sequence once under a lock, and confirmed on a second live run where the
evidence file and the database agree exactly.

## M2: APPROVAL AND DISPATCH

Built and verified, not yet exercised by a human through an interface.

Approving and sending are separate database powers:

    runtime   approve=false  dispatch=true   draft=true
    reviewer  approve=true   dispatch=false  draft=false

Neither identity alone can put a message in front of a client. An
approval records the digest of the content the reviewer was shown, and
dispatch recomputes it and refuses on any difference, so an approval
cannot be carried onto different text. Dispatch commits the attempt
before calling the provider, so a crash mid-send leaves evidence.

`SEND_UNKNOWN` is a first-class outcome. A timeout after the request
left gives no way to know whether the message went, so it blocks a
retry, while a definite failure permits exactly one.

APPROVE01-APPROVE20 cover the refusal matrix, and every refusal check
also asserts the provider was never called, because a refusal that still
sent something is not a refusal.

Two authorisation gaps were found in self-review after the first M2
commit and closed in `4644768`: a draft could name any recipient with
nothing tying the address to the case, and revocation took no reviewer
so any reviewer-role connection could withdraw any approval.

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
- **M2 dispatch has never sent a real message.** The approval and dispatch
  core is built and tested against a simulated provider, but the Gmail
  provider has never run, and no reviewer interface exists, so no human
  has actually approved anything through a screen.
- **Authorisation without authentication.** Reviewer identity is a
  parameter the caller supplies. Grants and role separation are real and
  tested; there is no login verifying that the person clicking approve
  is that reviewer.
- **M3** teaching and reuse. Not started. The source manifest is
  groundwork for the Second Brain, not the learning loop.
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

## 2026-09-11 EVENING: THE REPOSITORY'S OWN CLAIMS

Counts elsewhere in this file are historical and correct for the commit
they describe. Current state is below.

**106 checks across seven suites.** ENV01-ENV08 are new.

    tests/env_contract_smoke.py          ENV01-ENV08     no database
    tests/db_integrity_smoke.py          DB01-DB10
    tests/authority_boundary_smoke.py    AUTH01-AUTH21
    tests/agent_slice_smoke.py           AGENT01-AGENT22
    tests/ingestion_smoke.py             INGEST01-INGEST13
    tests/reviewer_console_smoke.py      VIEW01-VIEW12
    tests/approval_dispatch_smoke.py     APPROVE01-APPROVE20

### What was wrong, and why nothing caught it

Four documents drifted from the code independently, each maintained by
hand. `.env.example` documented three Gmail keys the live `.env` no
longer had, and omitted `POSTGRES_REVIEWER_PASSWORD` and all four AWS
keys, two of which the identity gate requires. Since the README instructs
a reader to copy `.env.example`, anyone following our own setup
instructions reached a system that could not call Bedrock at all. The
README also named a deleted test file, claimed check ranges that had
moved, omitted the APPROVE suite entirely, used a bare `python` that
fails outside the virtualenv, and stated that no model had ever been
invoked, hours after a real Bedrock run was recorded.

None of it was catchable by a test, so none of it was caught.

### The fix is structural, not a correction

`app/config.py` now declares the contract: every setting the software
reads, its group, whether it is required, whether it is secret, and a
placeholder. `.env.example` is generated from that declaration by
`scripts/generate_env_example.py` and is never edited by hand.

`tests/env_contract_smoke.py` turns each document's claims into
assertions: the example matches the declaration, every setting any call
site reads is declared or explicitly exempt, no real account id or usable
secret appears in the public example, the test files the README names
exist, the ranges it prints are true, and the README does not deny a real
model run while evidence of one sits in `docs/evidence/`.

That suite found two settings this work had itself missed,
`GMAIL_TEST_RECIPIENT` and `GMAIL_SEND_SMOKE_APPROVED`.

Five settings are deliberately outside the contract:
`GMAIL_EXTERNAL_SEND_APPROVED`, `GMAIL_EXTERNAL_TEST_RECIPIENT`,
`GMAIL_ROUNDTRIP_STATE_FILE`, `GMAIL_SEND_SMOKE_APPROVED` and
`GMAIL_TEST_RECIPIENT`. Each gates a real outbound send, and each is read
straight from the process environment so that authorising real mail is a
deliberate act in a shell and can never be inherited from a copied file.

### Gmail: proven, now reachable, still not wired

OAuth was completed on 2026-09-10 with a recorded external round trip
(`~/.marketiqx-hackathon/state/gmail_roundtrip.json`). The token carries
`gmail.readonly` and `gmail.send`, belongs to the agent mailbox, and its
refresh token still works. The three pointers are now in `.env`, so
`config.require("GMAIL_TOKEN_FILE")` resolves where it previously exited.

Two things remain, and neither is a credential problem.

1. `app/reviewer/server.py` still constructs `SimulatedProvider()`.
   Nothing builds a Gmail service for the dispatcher, so no message sent
   through the workflow has ever left the building.
2. `gmail_ingest._credentials()` refuses any credential where
   `creds.valid` is false, with "Reauthorize deliberately rather than
   silently." Access tokens expire hourly, so this fires routinely even
   though the refresh token is healthy. Refreshing an access token is not
   re-consent; no user interaction is involved. As written, this gate
   made unattended ingestion impossible. **Changed**, after establishing
   that the two things the gate conflated are not the same act.
   Refreshing an access token is a machine-to-machine exchange involving
   no person and no consent screen. Obtaining consent is a browser flow a
   human completes. Only the second has to be deliberate.

   Ingestion now refreshes an expired access token and continues. A
   refused refresh, which is what withdrawn consent or an expired refresh
   token actually looks like, still stops the run and names the command
   to reauthorize by hand. The module cannot start a consent flow, and
   `INGEST14` asserts that structurally by reading the source, so it
   holds with no credentials and no network. The token file is still
   written only by the deliberate auth flow.

   Verified end to end afterwards: a dry-run ingest refreshed the stored
   credential, passed the mailbox identity gate, and fetched 25 messages
   from the real agent mailbox without writing anything.

### Unattended operation is designed but not deployed

There is no scheduler, poller or daemon anywhere in the tree. A case
reaches the agent only through `scripts/run_case.py <case_id>`. The
hackathon brief asks for an agent that "runs autonomously in the
background and only surfaces when there's a real decision to make." The
surfacing half is built and enforced; the running half is manual. The
README now says so plainly under "What is not built" rather than
implying otherwise in its opening paragraph, which it previously did.

### Correction to an earlier entry

"Entrant ownership decision, still PENDING in `PROVENANCE.md`" under
EXTERNAL DEPENDENCIES is stale. It was resolved in `f0a2d11`.
