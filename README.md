# Cross-Border Compliance Agent

An agent for professional-services firms advising people who live in one
country and still have tax and compliance obligations in another. It reads
an emailed enquiry, works in the background, and surfaces only when a
human judgment is genuinely required.

Built for the AWS Agents for Humans Hackathon on the Strands Agents SDK.

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

`docs/BUILD_STATE.md` is the only status board. Read it before trusting
any other document here, including this one.

In short: the deterministic backend, ingestion and reviewer view are built
and verified from a clean checkout. The Bedrock adapter is written and its
four-tool registration is asserted at SDK level, but no model has been
invoked yet, so every agent run recorded so far is stamped
`DETERMINISTIC_STUB`.

## Setup

Requires Docker and Python 3.12.

    python -m venv .venv
    .venv/Scripts/python.exe -m pip install -r requirements-lock.txt
    cp .env.example .env        # then fill in both database passwords

    docker compose up -d
    python -m app.db.bootstrap  # schema, runtime role, grants
    python -m app.db.migrate apply

## Verifying

Prove the database builds from this repository with no hand-applied state,
on a disposable container that is created and destroyed by the run:

    python scripts/verify_clean_slate.py

Run every check against your own instance:

    python tests/run_all.py

Individual suites:

    python tests/db_integrity_smoke.py phase1        # DB01-DB09
    python tests/authority_boundary_smoke.py phase1  # AUTH01-AUTH21
    python tests/agent_slice_smoke.py phase1         # AGENT01-AGENT22
    python tests/ingestion_smoke.py phase1           # INGEST01-INGEST13
    python tests/reviewer_view_smoke.py phase1       # VIEW01-VIEW10

## Running it

    python scripts/register_mailbox.py you@example.com   # operator, admin
    python -m app.integrations.gmail_ingest --dry-run    # fetch, no write
    python -m app.integrations.gmail_ingest              # ingest a batch
    python -m app.reviewer.server                        # inspection view
    python scripts/bedrock_probe.py                      # AWS readiness
    python scripts/run_case.py <case_id>                 # real agent run

## Layout

    app/config.py        the only place environment settings are read
    app/db/              bootstrap, checksummed migrations, test guard
    app/domain/          context assembly, retrieval, decision rules
    app/agent/           the four tools, model adapters, the runner
    app/ingestion/       provider-agnostic ingestion and correlation
    app/reviewer/        read-only inspection view
    app/integrations/    Gmail transport and the ingestion adapter
    db/migrations/       ordered, checksummed schema and seed data
    docs/evidence/       recorded verification runs
    tests/               the suites named above
    scripts/             clean-slate proof, probes, operator tools

## Licence

MIT. See `LICENSE`.
