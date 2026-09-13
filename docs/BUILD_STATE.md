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
`get_service_knowledge` naming its own bound service by key rather than
UUID. The server wrongly refused both as out of scope, recorded them, and
the
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

## 2026-09-11 EVENING: THE SUPERVISOR

The brief asks for an agent that "runs autonomously in the background and
only surfaces when there's a real decision to make." Until now the
surfacing half was built and enforced, and the running half was a person
typing `scripts/run_case.py <case_id>`. This closes that.

    fetch mail  ->  triage  ->  reason  ->  draft  ->  [STOP]

`app/autonomy/loop.py` drives those stages on a timer and stops where a
human judgment is genuinely required. It never approves and never
sends, and `AUTO07` asserts that structurally: the module may not
reference approval, the dispatcher, or any send provider. Autonomy here
means nobody has to start the work, not that nobody has to authorise the
outcome.

### Triage stopped being a manual break

`app/autonomy/router.py` decides which service a case belongs to by
matching seeded keywords against the client's own words. There is no
language model in it, and `AUTO08` asserts that too.

The reason is not simplicity. The model must never choose its own service
scope, because that is exactly the boundary it has twice been recorded
attempting to cross. Handing it triage would hand it the thing it tried
to reach. A deterministic router is not the model, so letting it route
preserves the property that matters.

A privilege note worth recording, because it corrects a belief: the
runtime role already held `UPDATE` on `cases.service_id`. Triage was
"operator only" because no agent tool reached it, not because a grant
prevented it. That is still true and is now stated rather than assumed.

### Dominance, not uniqueness

The first router refused any case where two services matched. Run
read-only against the two real persisted enquiries before being trusted,
it refused both. Priya's enquiry says "residency" and "resident" and asks
about filing a return, and is unmistakable to any reader, but she also
mentions a "rented flat", which tripped a capital-gains keyword. One
incidental noun vetoed the whole decision.

The rule is now dominance: the leading service must carry at least two
distinct matches and at least twice the nearest rival's. Both real cases
now route. A genuine straddle, someone asking about residency and FEMA
remittance together, still fails that test and still reaches a person.

The seeded keyword data was deliberately left alone. "flat" is a weak
keyword, but weak keywords are a permanent condition of matching human
prose, and an algorithm that only works on a hand-tuned vocabulary is not
worth defending.

### A scope can no longer be acquired anonymously

Migration `009_autonomous_triage.sql` adds `triage_method` and
`triaged_at` to cases, with a constraint that a service scope requires
both. `app.triage_attempts` records every routing decision including the
refusals, so a reviewer reading the queue can see what the machine found
ambiguous rather than guessing why a case is still waiting.

That constraint immediately broke three test fixtures and one production
path, all of which had been assigning a service scope anonymously with
nothing objecting. `assign_service` and the fixtures now record `HUMAN`.
Breaking them was the point.

## M9: a rule that is true can still not be yours

This one was found by a test designed to fail, and it did.

The probe drove `decision.evaluate` directly, with no model anywhere in
it. A rule about RESIDENT individuals, professionally verified, on
topic, effective on the material date. A client established as a
NON-RESIDENT. Every gate unrelated to applicability deliberately
satisfied: routable, in scope, active release, no declared conflict, no
missing facts.

The layer permitted `SUPPORTED_WITHIN_POLICY`. It offered that rule as
the basis of the answer. It recorded
`requires_professional_verification = false`. Its rationale was "in
scope, material facts confirmed, effective guidance retrieved, no
declared conflict". Every clause of that sentence was true and the
conclusion was wrong.

The reason matters more than the result. This was not a check somebody
forgot to write. `evaluate` read five fields off the context, and not
one of them was an attribute of the client, so there was no field in
which applicability could be expressed at all. A reviewer auditing the
rules would have found nothing missing, because the vocabulary had no
word for the thing that was wrong.

### Three values, and the third is the point

    TRUE     the rule's conditions match what is established
    FALSE    they contradict it
    UNKNOWN  the rule turns on something nobody has established

A boolean has to fold UNKNOWN into one of the others and both choices
are wrong. TRUE admits a rule that may not apply. FALSE removes
evidence silently and hides the reason: if residency is unestablished,
dropping every resident-rule conceals the fact that residency is
exactly what needs establishing, and reports a corpus gap where there
is none. So UNKNOWN becomes a question to the client, and the state is
`MISSING_FACTS` rather than `MISSING_KNOWLEDGE`, because what is
missing is a fact.

Applicability runs before verification and coverage, not after. A rule
can be true, signed, on topic and in date and still be about somebody
else; admitting it as evidence and then checking its provenance gets
the order backwards.

`applicability.assess` is called by both the knowledge tool and the
decision layer. That is deliberate and it is a repeat fix: these two
once disagreed about what had been retrieved, and the validator saw
less than the model did, which is the wrong direction for a guard to be
wrong in. The tool now hands over four disjoint buckets -- usable,
unverified, unknown-applicability, not-applicable -- because a model
reading the same statement twice under two different instructions will
follow whichever it read last.

### Two things the build got wrong on the way

Migration 019 first registered `residency_status` and
`citizenship_status` as material facts, which demands both on every case
before anything can be answered. That is wrong twice. It makes the
three-valued logic decorative, because `MISSING_FACTS` would fire
whether or not any retrieved rule turned on residency. And it asks a
client filing a straightforward return for their residential status
before answering a question that does not depend on it. "Always ask" is
not caution, it is a refusal to reason about what the question needs.
Migration 020 makes the demand conditional: only a verified rule whose
applicability actually turns on the fact raises it.

The second was a loop. A reviewer confirms `residency_status` as "Non
Resident Indian"; the vocabulary has no exact entry for that wording, so
the attribute is unset, so a resident-rule is UNKNOWN, so the predicate
becomes a missing fact, so the next draft asks the client a question
they have already answered -- and the reviewer watches the system ignore
them with no explanation. Unestablished and unreadable are different
failures. The first is a question for the client. The second is ours, so
it escalates to a professional and drafts nothing. Substring matching
would have hidden it and been worse: "non-resident" contains
"resident", and a system that guessed would answer confidently from the
wrong half of the law.

### What the checks are worth

`APPLY01-APPLY14` run the deterministic layer with no model and no
database. That is the point of them. A safe result produced by Claude is
not evidence of a safe architecture; a safe result produced while Claude
is assumed to be wrong is.

They were then checked for being decoration. With the gate removed at
runtime -- `assess` replaced by the behaviour the code had before
applicability existed -- five of them fail, including every check that
exists because of the original defect. A check that passes with the fix
and would have passed without it defends nothing.

That left one hole worth naming, because it is the same mistake this
build has made before in other costumes: every one of those fourteen
checks constructs its units in Python. A wrong row index in either
retrieval query would leave every unit reading NULL, every unit
unrestricted, every unit applicable to everyone -- and all fourteen
would still pass while the gate stood wide open. `AGENT28` reads a
stored restriction back through both retrieval paths, which have
different select lists and different row offsets, and then requires it
to exclude for a non-resident and not exclude for a resident.
Exclusion that happens either way proves nothing.

Three attempts at that fixture were refused by the database before one
was accepted: a professional claim must name the reviewer who signed it,
and any verification claim needs the captured passage and digest that
would let a reviewer check it. The refusals were right and the test was
wrong each time. The probe now asserts no evidence it does not have.

### What this does not yet do

Nothing in the live corpus carries a restriction. Twelve units, five
professionally verified, and `applies_to_residency` and
`applies_to_citizenship` are NULL on every one of them, which means
every unit is unrestricted, which means applicable to everyone. The gate
is enforced, proven and currently inert on real data.

That is honest rather than reassuring. The mechanism is the hard part
and it is done; what remains is a control in the reviewer console so
that setting applicability is part of signing a unit off, rather than a
column somebody has to remember.

### State

144 checks across nine suites, passing from a clean slate.
`APPLY01-APPLY14` and `AGENT28` are new.

Sections above this one are a log and describe the state at the time
they were written. This block is the only part that claims to be
current.

Still not built, and next:

- **Applicability at sign-off.** The reviewer console has no control for
  it, so the gate cannot yet change a real answer.
- **Granularity.** All five signed units bundle several propositions
  each. The unit is the wrong atom; the proposition is. This limits how
  precisely anything can be cited, restricted or superseded.
- **Submission artifacts.** The demo video, the Devpost description and
  the builder.aws.com post. Each is mandatory or scored and none exists.

Deliberately not being built: embeddings, GraphRAG, AgentCore,
per-department agents, authentication. Each was considered and each
would cost more than it returns before the deadline.


## P2.2A + P2.2A.1: actionability, identity, receipts

This block supersedes every earlier one, including the paragraph above
that claims to be current. Everything above is a log of what was true
when it was written.

**248 checks across sixteen suites, passing from a clean slate.**
`ACCESS01-08`, `RUNSAFE01-15`, `IDENT01-05`, `SEND01-03`,
`RECEIPT01-15` and `VIEW13` are new. No migration was required for any
of it: every table and column these use already existed, and both
database roles already held the privileges.

### What was wrong, and is now closed

**A failed run's proposal could be presented as work to do.** Three
separate queries selected "the current proposal for a case" and
disagreed. The first fix of this defect changed the two that nothing
renders and missed `inbox.queue`, which is the one `do_GET` builds the
inbox from, so the suite went green while the console was still wrong.
On live data the case whose most recent run had FAILED was being shown
in lane `NEEDS_PROFESSIONAL` — "Anika has no professionally verified
guidance for this, so it was escalated to you" — and counted as work
needing a professional. A crash was being reported as a professional
escalation, and it was also masking a letter that had already been
drafted from an earlier successful run.

The rule is now `result_state = 'SUCCEEDED'`, defined once in
`app/domain/actionability.py` and carried by all three selections plus
`drafting.compose`, which refuses at the point of the act because
hiding a proposal from a view does not stop its revision id being a
valid argument. `RUNSAFE11` fails if any query drifts from the
constant. `RUNSAFE12` splices the old predicate back in and requires it
to re-admit the in-flight and refused cases.

`<> 'FAILED'` was wrong for a reason worth recording: `agent_runs` has
four states, the runner commits the proposal through a tool call while
the run still reads `RUNNING`, and sets the terminal state afterwards
on an autocommit connection. So a committed revision under a `RUNNING`
run is an ordinary observable state, and a run that dies between those
two statements leaves one permanently actionable.

**Browser-supplied reviewer ids carried authority.** In three places,
not one: case access, the reviewer recorded as professionally verifying
a knowledge unit, and the reviewer recorded as approving a client
letter. The acting identity is now resolved once when the process
starts and request input is not consulted; the hidden fields and link
parameters that carried it are removed rather than left looking
load-bearing.

**A decision could not be explained without SQL.** `app/domain/receipt.py`
projects one artefact from the rows that already own the truth. Nothing
is stored, so it cannot drift from what it describes. It replaced an
ad-hoc block on the case page that had been assembling its own subset
of the same facts, which is the same arrangement that let three queries
disagree.

### Limits that must be stated, not implied

**The console identity is a controlled, server-bound demo identity. It
is not authentication.** The acting reviewer comes from
`CONSOLE_ACTING_REVIEWER` or `--acting-reviewer`, is resolved at
startup, and no query string or form field can change it. Nothing
verifies that the person at the keyboard is the reviewer the process is
bound to. Case grants are only a boundary if the identity being
checked is not the caller's to choose; that is what changed. Real
authentication is not built and must not be claimed.

**The inbox listing is not scoped to service or membership.**
`inbox.queue` returns every matter, with sender address, subject and
the agent's summary, to whoever opens the console. Case *content* reads
and every case mutation are authorised per case; the listing is not.
This was left open deliberately. Filtering it by `reviewer_case_grants`
would hide ungranted cases from everyone — work silently disappearing
is worse for a firm than over-disclosure — and it would make case
grants do routing, which they are not. It needs a person-to-service
membership concept, which does not exist yet.

**Historical fact state is not reconstructable.** `app.case_facts`
records when a fact row was created but not when it became `CONFIRMED`.
So the confirmed facts in a decision receipt are the case's facts as
they stand now, not provably the ones the run read. Facts the run
proposed are exact, because those rows carry its `run_id`. The receipt
carries a `reconstruction_limits` field saying this, and `RECEIPT13`
fails if it is removed. Closing it properly needs a status-transition
record or a context manifest bound to each run. Neither exists, and
historical state has not been reconstructed by inference.

**The five `PROFESSIONALLY_VERIFIED` units carry a provenance caveat.**
They were signed before the identity fix, through the path where the
verifying reviewer came from browser input. The signature trigger means
the recorded reviewer matches the database session that wrote it, so
the rows are internally consistent and attributable to that session —
but the reviewer id itself was selectable by whoever was at the
browser. The units are not deleted or rewritten: the caveat is recorded
here instead, because editing the evidence to make the story cleaner is
the failure this project exists to avoid. Units signed after this
change do not carry the caveat.

### Not done, and not claimed

- No authentication.
- No tenant isolation. There is one firm in this schema.
- `AGENT_BLOCKED`, the lane for a run that could not finish, is proven
  by fixture and has not appeared in live data.
- The correction receipt is a written contract in
  `docs/ARCHITECTURE.md`, marked PLANNED. Nothing implements it.
- Service membership, assignment, work stages and the dashboard are
  untouched.


## P2.2A.2 final QC: five hardening contracts closed

Supersedes the block above. **320 checks across eighteen suites,
passing from a clean slate.** No migration beyond 028, already
committed at the prior checkpoint. Two real defects were found and
closed by writing the test before the fix, exactly as the method
requires; three mutation tests confirm each guard actually depends on
the code it claims to.

### What was found

**A run could get stuck RUNNING forever if its context manifest could
not be written.** The run row and its manifest are two separate
commits on autocommit; the first durable, the second attempted after.
Forcing the second to fail reproduced exactly the failure this project
closed once already, in a different place: `('...', 'RUNNING', None,
False)`. `runner.py` now finalises the run as `FAILED` with a named
reason before re-raising, and never invokes the model when the context
it would reason over cannot be recorded. `CTXFAIL01-06`.

**Confirming a fact a run had proposed moved that run's decision
digest weeks later.** `written_by_this_run` filtered on the fact's
current status, so a professional's confirmation retroactively changed
what the run appeared to have asserted. Reproduced (`682b40c2ea3b`
became `202f0d4d8bae`) and closed by dropping the status filter. Fixing
the test that caught it required correcting an assumption: confirmation
is not an `UPDATE` — no role holds that grant on `case_facts`, ever
(migration 021) — it is always a second, separate row. `DRSTABLE01-06`.

### What was added

**Provenance and authority, as two axes instead of one.**
`app/domain/provenance.py`. `SYSTEM_VERIFIED` was answering "where did
this come from" and "how much is it trusted" with the same label. A
cited knowledge unit's authority now reads through
`app.active_knowledge_units` — the runtime role has held no privilege
on the base table since migration 005 — so a unit belonging to a
superseded release is reported the same as a citation to nothing,
honestly, rather than assumed still verified. `PROV01-04`.

**Tool calls carry what they already knew about trust.**
`get_service_knowledge` already separates verified-and-usable material
from material it consulted but would not vouch for; the receipt was
discarding that and reporting bare `OUTCOME: OK`. Now surfaced as
`trust_signals`, read generically from whatever a call's own
`result_summary` already recorded — nothing computed fresh, nothing
duplicated beyond counts and ids the summary had already trimmed
itself to. A failed call carries no trust signal: it found nothing, and
must not read as having found nothing trustworthy specifically.
`TOOLTRUST01-03`.

**Identity moved out of the digest.** The receipt covered every field
but its own digest, and two of those fields were display names.
Renaming an agent rewrote the digest of a decision made months earlier
— reproduced (`8f0306cb92d5` became `888aed7ec010` from a rename
alone) and closed by moving names to a `display` section the digest
excludes; the identity section now carries ids only. `HID01-04`.

**One business correlation id, designated rather than invented.**
`case_id`. Every artifact in a journey — enquiry, runs, manifests,
proposals, receipts — already resolves to it. A composite foreign key
(`run_context_manifests_case_matches_run`) makes a manifest naming a
different case than its own run impossible to write, not merely
unobserved. `CORR01-03`.

**Effective authority is an intersection, stated and tested as one.**
`AUTHZ01` proves the reach half (an agent cannot access a case its
principal cannot); `AUTHZ02` proves the second term bites
independently — a principal's own grant to approve a letter does not
reach through the agent into an `INSERT` on `app.approvals`, which the
runtime role does not hold regardless of who is behind it.

**The golden dataset contract, with no professional content in it.**
`app/evals/enquiry_contract.py`. Four banks; the held-out and
adversarial ones refuse to load into anything marked `for_runtime`.
`agent_visible()` is unconditional and additive — it keeps only
`subject`, `body`, `material_date`, so a new grader-only field added to
the contract later is excluded by default rather than by remembering
to add it. `GOLD01-09`. No case files exist yet, and none is invented.

### The caveat, again, more plainly

The five `PROFESSIONALLY_VERIFIED` units signed before the identity fix
must not be presented in any judge-facing demonstration as fresh proof
of the corrected verification path. They are internally consistent —
attributable to the session that wrote them — but the reviewer id
itself was browser-selectable when they were signed. A unit re-signed
after this fix, through the now server-bound console, is the honest
demonstration. These five are not that, and are not re-reviewed here.

### Not done, and not claimed

- No production authentication, no per-agent IAM, no cryptographic
  non-repudiation. The runtime cannot rewrite its own manifest — that
  is measured per role, not asserted, and the schema owner can alter
  anything.
- The correction receipt remains a written contract only.
- Service membership, assignment, work stages, the dashboard: untouched.
- The live demo database is still at migration 027. Applying 028,
  registering an agent profile, and running one live smoke test are
  operator actions, not taken here without being asked.


## P2.2A.2: two blockers closed, gate accepted

Supersedes the block above. **337 checks across eighteen suites,
passing from a clean slate.** Migration 028 extended (still
uncommitted) with `deterministic_knowledge_snapshot` on
`run_context_manifests`, and `agent_profiles`/`agent_runs` unchanged
from the prior block.

The previous report called P2.2A.2 "final acceptance closed" while
its own text said the console still fell back to the first active
reviewer when nothing was configured. That was a real contradiction,
not a rhetorical one, and it is why this block exists separately
rather than folded into the last.

### Both blockers, reproduced before being fixed

**An unconfigured console had authority.** `_actor()` refused an
invalid or inactive binding correctly — both already read `403` — and
then fell back to `reviewers[0][0]` for the one case that matters
most: nothing configured at all (`200`). Closed: empty now means no
authority, the same answer as a wrong binding. The same question,
asked of agent attribution: two active profiles owned by one person
were resolved by creation order, a deterministic answer nobody
authorised. `profile_for_owner` now refuses ambiguity;
`AGENT_ACTING_PROFILE_ID` resolves it when set, validated against the
configured principal rather than trusted. `IDENT06-09`, `AGID06-09`.

**A cited unit's authority could be rewritten by superseding its
release.** Reproduced outside any suite before writing a line of fix:
a `PROFESSIONALLY_VERIFIED` citation read back `VERIFIED_KNOWLEDGE`
before superseding its release and `UNVERIFIED_SOURCE` after, digest
moved. `_cited_units` read current corpus state at receipt-build time
for a citation that happened whenever the run actually ran — the same
shape of defect the context manifest already closed for case facts,
recurring in the other kind of evidence a decision rests on. Closed by
snapshotting `deterministic_knowledge_snapshot` in the manifest, from the
retrieval `_gather_knowledge` already performs before the model
reasons — nothing new computed, only kept rather than discarded.
Re-running the same reproduction after the fix: authority unchanged,
digest unchanged, and current retrieval independently confirmed to
still exclude the superseded unit. `KGHIST01-05`, `TOOLOBS01-04`.

Both mutation-tested: reverting either fix, the suite that closed it
fails for the reason it should.

### Correction-receipt prerequisite, recorded and not solved here

Confirmation is append-only — a new, separate row, never an edit of
the run's own proposed fact (migration 021). That is what makes a
decision's digest stable against later confirmation. It also means no
row currently says which earlier proposed value a confirmation
resolves; today that link could only be inferred from case, predicate,
value and time, which is ambiguous the moment two proposals for the
same predicate exist.

**This must be closed before a Correction Receipt can safely reference
a parent decision digest**, so that a future `ΔX` between decision
states is computed from an explicit link rather than a guess. Not
implemented in this slice. Recorded here as the prerequisite it is,
not deferred silently.

### Not done, and not claimed

- No production authentication, no per-agent IAM, no cryptographic
  non-repudiation.
- The correction receipt remains a written contract; its prerequisite
  above is newly identified, not newly closed.
- Service membership, assignment, work stages, the dashboard:
  untouched.
- The live demo database is still at migration 027. Applying 028,
  configuring the acting principal and agent, registering Nicole, and
  running one live smoke test are operator actions, not taken here
  without being asked.
