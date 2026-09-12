# Cross-Border Compliance Agent

An agent for professional-services firms advising people who live in one
country and still have tax and compliance obligations in another. It reads
an emailed enquiry, does the work unattended, and surfaces only when a
human judgment is genuinely required.

Built for the AWS Agents for Humans Hackathon on the Strands Agents SDK.

**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) is the shortest route to what
matters here:** the four layers, the algebra that decides what may honestly be
claimed, the privilege boundaries read from the live catalogue, and an explicit
list of what is proven versus what is merely built. It also names the failure
the architecture was reshaped around.

## The problem

Migration creates a permanent, low-grade compliance burden. Someone who
moves abroad still has residency tests to satisfy, returns to file, and
remittance rules to observe in the country they left. The questions are
repetitive, the answers are judgment-heavy, and the firms that handle them
spend their days re-deriving the same analysis from scratch for each
client.

This is exactly the shape of work the hackathon describes: routine,
repetitive, and quietly expensive in professional time.

## Scope: architecture is jurisdiction-agnostic, this build is India-first

Services, topics and knowledge are **data, not code**. A corridor is a row
in `app.services` with its own topic catalogue, its own required facts and
its own versioned knowledge release. Adding a second corridor is a data
operation.

This build seeds and demonstrates one corridor, the India corridor:
residency under the Income-tax Act, return filing, and FEMA account and
remittance questions for non-resident Indians. That is the corridor the
demonstration covers, and no claim is made about any other until one is
seeded and shown working.

## How it works

An enquiry arrives by email and is persisted and correlated to a case. A
human triages the case into a service. Only then can the agent read it,
and it has exactly four tools:

    get_case_context()
    get_service_knowledge(query, service_id)
    record_proposed_facts(facts, evidence_refs)
    propose_next_action(action)

There is no send tool, no approval tool, no knowledge-publishing tool, no
shell and no generic HTTP. The absence is the point. The agent reads the
enquiry, retrieves approved and versioned knowledge, decides what is
missing, and proposes exactly one next action for a reviewer.

## Why the proposal can be trusted

The limits are enforced by database privileges and constraints, not by the
prompt. They hold even if the model is manipulated or the application code
is wrong.

- The case and the service are bound server side before the model runs, so
  no tool argument can reach another client's case. A mismatched
  `service_id` is refused and the attempt is recorded.
- Facts the agent records are always PROPOSED. The runtime role is not
  granted the `status` column, so it cannot confirm its own findings, and
  cannot attribute a fact to a reviewer.
- The runtime role holds no privilege on the knowledge table. It reads a
  view restricted to ACTIVE releases, so unapproved drafts are unreachable
  rather than merely unrequested.
- A proposal claiming support must cite sources and name the release it
  relied on, or the insert is rejected.
- Proposal revisions are append-only, so a later approval can be bound to
  exact content.
- The model chooses the decision state; the server refuses any state the
  evidence does not permit, and records the refusal.

## Honesty about knowledge

Knowledge carries its verification state explicitly:

    UNVERIFIED              nothing recorded
    SOURCE_RECORDED         a locator exists, content not captured
    SOURCE_VERIFIED         passage captured and digested
    PROFESSIONALLY_VERIFIED a qualified professional signed it off

Claiming either stronger state requires a captured passage, a digest and a
timestamp, enforced by a constraint. Everything seeded here is
`SOURCE_RECORDED`, and a topic counts as covered only when a
professionally verified unit is effective for it. The consequence is
deliberate: with the current corpus the agent **cannot** claim support, and
correctly reports a knowledge gap instead.

## Ingestion is conservative

Duplicate provider messages are absorbed. Mail from the agent's own
mailbox is never ingested. When correlation could point at more than one
case, or when the sender has no standing on the matched case, the message
is quarantined for a human rather than guessed into a case. The provider
cursor advances only after messages are durable, so a crash replays rather
than losing mail.

## Current state

`docs/BUILD_STATE.md` is the status board and carries more detail than
this file.

The deterministic backend, ingestion, the reviewer console, and approval
and dispatch are built and verified from a clean checkout. The agent runs
on real Amazon Bedrock through the Strands Agents SDK: recorded runs in
`docs/evidence/real-run-*.json` are stamped `BEDROCK_STRANDS` with model
`us.anthropic.claude-sonnet-4-5-20250929-v1:0`. Those runs executed in an
AWS-provided workshop account, which proves the code path rather than any
particular account.

One result from those runs is worth reporting because no stub could have
produced it. On two separate cases the model called
`get_service_knowledge` naming the service by its key rather than its
UUID, and the server refused it as out of scope. That was our defect,
not the model testing a boundary: the key it used belonged to the very
service bound to the run. Both the tool and the lifecycle hook now
accept the bound service under either name and still refuse a genuinely
different one. The episode is recorded here because the refusals were
previously described in this file as the model attempting to reach
another service's knowledge, and that description was wrong.

## What is not built

Stated plainly, because a reader should not have to infer it.

- **Nothing runs on a schedule.** A case is put through the agent by
  `scripts/run_case.py <case_id>`. There is no poller, scheduler or
  daemon, so "works in the background" describes the design and not yet
  the deployment.
- **No message has been sent to a real recipient through the workflow.**
  Gmail send is proven separately in `app/integrations/`, with a recorded
  round trip, but the dispatcher in the reviewer console still uses the
  simulated provider.
- **No authentication.** The reviewer is selected from a list, not
  verified. Role privileges and per-case grants are real and enforced in
  the database; the identity in front of them is not. The console says so
  on every page.
- **No teaching loop yet.** A professional cannot yet answer a knowledge
  gap and have that answer become reusable verified knowledge.
- **One corridor.** India only, as described above.

## Setup

Requires Docker and Python 3.12. Commands below use the virtual
environment's interpreter explicitly, because the dependencies are not
installed system-wide and bare `python` will fail.

    python -m venv .venv
    .venv/Scripts/python.exe -m pip install -r requirements-lock.txt

    cp .env.example .env

`.env.example` is generated from the declared contract in
`app/config.py`, so it is always complete. Fill in the three database
passwords, which are the only values needed to build and verify the
database. The AWS and Gmail sections are required only for the parts that
use them, and each fails fast naming the missing key rather than guessing.

    docker compose up -d
    .venv/Scripts/python.exe -m app.db.bootstrap    # schema, roles, grants
    .venv/Scripts/python.exe -m app.db.migrate apply

## Verifying

141 checks across nine suites. Every one names what it proves, and the
suites that write to the database refuse to run against a target that has
not been marked disposable.

Prove the database builds from this repository with no hand-applied
state, on a container created and destroyed by the run:

    .venv/Scripts/python.exe scripts/verify_clean_slate.py

Run everything against your own instance:

    .venv/Scripts/python.exe tests/run_all.py

Individual suites:

    tests/env_contract_smoke.py                  ENV01-ENV09
    tests/applicability_smoke.py                 APPLY01-APPLY14
    tests/db_integrity_smoke.py phase1           DB01-DB10
    tests/authority_boundary_smoke.py phase1     AUTH01-AUTH23
    tests/agent_slice_smoke.py phase1            AGENT01-AGENT29
    tests/ingestion_smoke.py phase1              INGEST01-INGEST14
    tests/reviewer_console_smoke.py phase1       VIEW01-VIEW12
    tests/autonomy_smoke.py phase1               AUTO01-AUTO10
    tests/approval_dispatch_smoke.py phase1      APPROVE01-APPROVE20

The claims in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) are checked here too:
`ENV09` counts the check ids in every suite and fails if that document or this
one states a total the suites do not define.

`env_contract_smoke.py` and `applicability_smoke.py` need no database and
no credentials.

The first checks this repository's own claims: that `.env.example` matches the declared
contract, that every setting the code reads is declared, that no real
account id or usable secret appears in the example, and that the file
names and check ranges printed above are true. Those statements were all
false at one point, and nothing caught it, so now something does.

The second runs the deterministic decision layer with no model and no
database, and asks whether a rule that is true, verified, on topic and in
date can support an answer to a client it is not about. It could: the
layer authorised a supported answer for a non-resident resting on a
verified rule about residents, and gave as its reason "in scope, material
facts confirmed, effective guidance retrieved, no declared conflict". Every
clause true, conclusion wrong. Applicability is now three-valued, and the
third value is the point: an unestablished condition becomes a question
to the client rather than a rule quietly dropped from the evidence.

## Running it

    .venv/Scripts/python.exe scripts/register_mailbox.py you@example.com
    .venv/Scripts/python.exe -m app.integrations.gmail_ingest --dry-run
    .venv/Scripts/python.exe -m app.integrations.gmail_ingest
    .venv/Scripts/python.exe scripts/register_reviewer.py "Name" a@b.example
    .venv/Scripts/python.exe scripts/bedrock_probe.py
    .venv/Scripts/python.exe scripts/run_case.py <case_id>
    .venv/Scripts/python.exe -m app.reviewer.server

The last command serves the reviewer console on
`http://127.0.0.1:8080/`. It is a long-running process: leave it open and
stop it with Ctrl+C.

## Layout

    app/config.py        the declared settings contract, read nowhere else
    app/db/              bootstrap, checksummed migrations, test guard
    app/domain/          context, retrieval, decision rules, drafting,
                         approval and content digests
    app/agent/           the four tools, model adapters, the runner
    app/ingestion/       provider-agnostic ingestion and correlation
    app/dispatch/        send providers and the controlled dispatcher
    app/reviewer/        the decision console
    app/integrations/    Gmail transport and the ingestion adapter
    db/migrations/       ordered, checksummed schema and seed data
    docs/evidence/       recorded verification and real agent runs
    tests/               the suites named above
    scripts/             clean-slate proof, probes, operator tools

## Licence

MIT. See `LICENSE`.
