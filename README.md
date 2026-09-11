# Agents for Humans - NRI Professional Agent

An agent that prepares professional-services work on enquiries from
non-resident Indians, and hands every consequential decision to a
qualified human.

Built for the AWS Agents for Humans Hackathon. No pre-existing Anika
application source code is copied into this repository. See
`PROVENANCE.md`.

## Current state

`docs/BUILD_STATE.md` is the only status board. Read it before trusting
any other document here, including this one.

In short: M0 is complete, M1 is partial. The deterministic backend,
ingestion and reviewer view are built and tested. No Strands agent and
no Bedrock model call exist yet, so every agent run recorded so far is
stamped `DETERMINISTIC_STUB`.

## The idea

An enquiry arrives by email and is persisted and correlated to a case.
A human triages the case into a service. Only then can the agent read
it, and it has exactly four tools:

    get_case_context()
    get_service_knowledge(query, service_id)
    record_proposed_facts(facts, evidence_refs)
    propose_next_action(action)

There is no send tool, no approval tool, no knowledge-publishing tool,
no shell and no generic HTTP. The agent reads the enquiry, retrieves
approved and versioned knowledge, decides what is missing, and proposes
one next action for a reviewer.

What makes the proposal trustworthy is that the limits are enforced by
the database rather than by the prompt:

- The case and the service are bound server side before the model runs,
  so no tool argument can reach another client's case. A mismatched
  `service_id` is refused and the attempt is recorded.
- Facts the agent records are always PROPOSED. The runtime role is not
  granted the `status` column, so it cannot confirm its own findings,
  and cannot attribute a fact to a reviewer.
- The runtime role holds no privilege on the knowledge table. It reads
  a view restricted to ACTIVE releases, so unapproved drafts are
  unreachable rather than merely unrequested.
- A proposal claiming support must cite sources and name the release it
  relied on, or the insert is rejected.
- Proposal revisions are append-only, so a later approval can be bound
  to exact content.
- The model picks the decision state; the server refuses any state the
  evidence does not permit and records the refusal.

Knowledge carries its verification state honestly. Everything seeded
here is `SOURCE_RECORDED`, meaning a statutory locator exists and the
source content has not been captured. Claiming `SOURCE_VERIFIED`
requires a captured passage, a digest and a timestamp, enforced by a
constraint. Nothing is `PROFESSIONALLY_VERIFIED`, so every proposal is
flagged as requiring sign-off, and the reviewer view labels a supported
decision "Covered for review, professional sign-off required".

Ingestion is conservative. Duplicate provider messages are absorbed,
mail from our own mailbox is never ingested, and when correlation could
point at more than one case the message is quarantined for a human
instead of guessed into one. The provider cursor is advanced only after
messages are durable, so a crash replays rather than loses mail.

## Setup

Requires Docker and Python 3.12.

    python -m venv .venv
    .venv/Scripts/python.exe -m pip install -r requirements-lock.txt
    cp .env.example .env        # then fill in both passwords

    docker compose up -d
    python -m app.db.bootstrap  # schema, runtime role, grants
    python -m app.db.migrate apply

## Verifying

Run every check against your instance:

    python tests/run_all.py

Prove the database builds from the repository with no hand-applied
state, on a disposable container that is destroyed afterwards:

    python scripts/verify_clean_slate.py

Individual suites:

    python tests/db_integrity_smoke.py phase1        # DB01-DB09
    python tests/authority_boundary_smoke.py phase1  # AUTH01-AUTH21
    python tests/agent_slice_smoke.py phase1         # AGENT01-AGENT18
    python tests/ingestion_smoke.py phase1           # INGEST01-INGEST11
    python tests/reviewer_view_smoke.py phase1       # VIEW01-VIEW10

## Running it

    python scripts/register_mailbox.py you@example.com   # operator, admin
    python -m app.integrations.gmail_ingest --dry-run    # fetch, no write
    python -m app.integrations.gmail_ingest              # ingest a batch
    python -m app.reviewer.server                        # read-only view

## Layout

    app/config.py        the only place environment settings are read
    app/db/              bootstrap and the checksummed migration runner
    app/domain/          context assembly, retrieval, decision rules
    app/agent/           the four tools, model adapters, the runner
    app/ingestion/       provider-agnostic ingestion and correlation
    app/integrations/    Gmail transport proofs and the Gmail adapter
    app/reviewer/        read-only reviewer view
    db/migrations/       ordered, checksummed schema and seed data
    tests/               the suites named above
    scripts/             clean-slate proof, mailbox registration
