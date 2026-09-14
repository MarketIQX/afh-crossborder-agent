# Nicole - AWS Agents for Humans submission text

## One-line description

**Nicole is a governed learning professional agent for NRI and cross-border tax work: she does the repetitive evidence gathering, reasoning and drafting, while deterministic controls and the professional retain authority over what may be claimed, learned and acted on.**

## The problem

Cross-border professional work repeats constantly but is not safely reducible to FAQ automation. A Chartered Accountant serving NRIs repeatedly reconstructs the same analysis from emails, client facts, effective rules and institutional knowledge: tax residency, return filing, remittances, account status, FEMA and related compliance questions.

The expensive part is not typing an answer. It is determining which facts matter, which rule applies to this person and date, whether the underlying source is trustworthy, and where professional judgment is still required.

Ordinary AI creates a different risk: a fluent answer can sound authoritative even when the rule is inapplicable, the evidence is incomplete or the source has never been professionally accepted.

Nicole was built around one question:

> **Can an AI agent do the repetitive work of a professional firm without quietly acquiring the authority of the professional?**
## Who it is for

Nicole is designed for Chartered Accountants, tax professionals and small professional-services firms serving NRIs and other cross-border clients. These firms need leverage without handing professional authority to a model.

## How Nicole works

A client enquiry is persisted and conservatively correlated to a case. A deterministic router proposes service scope; ambiguity waits for a person. Context is assembled server-side. Nicole then runs as a Strands agent on Amazon Bedrock AgentCore Runtime and uses four bounded tools to read case context, retrieve governed service knowledge, record proposed facts and propose one next action.

The model reasons probabilistically, but a deterministic layer independently checks scope, required facts, source verification, three-valued applicability, conflicts and citation requirements. A proposal outside the evidence-permitted set is refused.

The professional sees the case in the Nicole Partner Dashboard: the client's own words, Nicole's proposed outcome, the draft where applicable, tool trajectory and decision evidence. The runtime cannot approve its own work or promote its own learning into verified knowledge.

**Models infer. Deterministic systems govern. Humans authorise.**

## Governed learning

Nicole can evolve, but she cannot declare her own output to be institutional truth.

Professionals can upload supported documents (`.pdf`, `.docx`, `.txt`, `.md`). Nicole extracts candidate knowledge and verifies the candidate against captured source evidence. The professional accepts or rejects the candidate. Only accepted material can become professionally verified knowledge and be reused by a fresh agent session.

Rejected candidates stay rejected. Scanned PDFs with no readable text are refused until an OCR layer exists; OCR is roadmap work, not a current claim.
## Technical implementation

Nicole uses Strands as a real agent loop rather than as a wrapper around one fixed model call:

- four bounded tools with model-driven selection;
- lifecycle hooks for scope and budget controls;
- steering/interventions for unsafe or malformed client-facing output;
- skills loaded on demand rather than one permanently inflated prompt;
- durable run, tool-call and context records;
- deterministic validation before a model proposal can become actionable.

The live AWS foundation is deployed, not hypothetical:

- Amazon Bedrock AgentCore Runtime `NicoleProfessionalAgent` is `READY` in `us-east-1`;
- Python 3.12, VPC mode and MMDSv2 are active;
- the runtime is attached to two private subnets;
- Amazon RDS PostgreSQL 16.14 is non-public and encrypted;
- RDS accepts PostgreSQL traffic from the runtime security group;
- AgentCore Identity provider `NicoleGroq` supplies the model credential;
- AWS Secrets Manager hydrates the application database password at runtime;
- the runtime uses a dedicated IAM execution role without AdministratorAccess or RDS-admin authority;
- the deployed artifact is a versioned S3 object whose exact VersionId and SHA-256 were round-trip verified.

The current AgentCore deployment uses Groq-hosted `openai/gpt-oss-120b` through a provider abstraction. The repository also retains an AWS Bedrock adapter, so model provider and professional authority are separate concerns.
## What is proven

The repository currently passes **345 checks across eighteen suites** from a clean disposable PostgreSQL build. The clean-slate verifier creates the database from repository files, applies migrations, runs the complete regression surface and destroys the target.

Important live evidence is separate from those deterministic tests:

- runtime command execution inside the actual AgentCore microVM resolved the application database secret and authenticated to private RDS;
- the same runtime database identity was denied access to privileged schema-administration state;
- a live AgentCore invocation against a nonexistent case returned `RUN_REFUSED` rather than inventing context;
- the Nicole Partner Dashboard renders Dashboard, Requests, Learning, Train Nicole and Knowledge surfaces;
- governed training acceptance/rejection and fresh-session reuse are exercised in the test system.

The project deliberately separates source-code proof, artifact proof, AWS runtime proof and agent-behavior proof. `READY` is not presented as an end-to-end case result, and 345 deterministic checks are not presented as stochastic reliability.

## Creativity and originality

The core design idea is **probabilistic reasoning with deterministic authority**.

The interesting part is not adding another retrieval layer. It is making authority explicit:

- proposed facts cannot silently become confirmed facts;
- retrieved material cannot silently become admissible evidence;
- model-selected wording cannot silently become approved client communication;
- candidate learning cannot silently become firm knowledge;
- a professional decision leaves durable evidence about the case, tools, sources and state that produced it.

This turns "human in the loop" from a button at the end into a separation-of-powers architecture.
## Potential impact

For the professional, Nicole reduces repeated reading, retrieval and drafting while keeping consequential judgment visible and attributable. For the firm, accepted learning becomes reusable institutional knowledge instead of remaining trapped in inboxes or individual memory. For the client, the system is designed to prefer a bounded clarification or escalation over a confident unsupported answer.

The long-term product direction is a multi-tenant professional-intelligence platform for firms handling tax, FEMA, global mobility and adjacent judgment-heavy workflows. That roadmap is shown separately from the live submission architecture so future ambition is not confused with current implementation evidence.

## Current limitations - stated explicitly

A successful unseen NRI case has **not yet completed end-to-end on the live AgentCore deployment**. The live RDS currently contains the deployed service definitions but zero operational mailbox, reviewer, case and agent-run rows. The runtime correctly lacks authority to bootstrap those administrative rows itself.

We therefore do not claim that `READY` means the entire case workflow is live. The remaining valid live sequence is operator bootstrap through the intended administrative boundary, a synthetic enquiry through normal persistence/triage, AgentCore invocation, model/tool execution, proposal persistence and independent receipt verification.

Other known limits:

- the Partner Dashboard has server-bound acting identity but no production authentication;
- `/knowledge` exists but is still a placeholder rather than the finished knowledge explorer;
- scanned-document OCR is not implemented;
- runtime security-group egress is broader than the desired production least-privilege posture;
- full native OpenTelemetry/AgentCore observability and repeated pass^k agent acceptance are not yet integrated;
- the 2031 architecture is a target architecture, not an implementation claim.

We would rather show those limits than erase the boundary that makes the product trustworthy.
## Architecture diagrams

- **Current implementation:** [`docs/architecture/nicole-live-architecture.png`](architecture/nicole-live-architecture.svg)
- **2031 target architecture:** [`docs/architecture/nicole-2031-vision.svg`](architecture/nicole-2031-vision.svg)

The first is the hackathon evidence claim. The second is the long-term product thesis.

## Demo narrative

The five-minute demo should show one coherent story:

```text
problem and audience
-> Nicole Partner Dashboard
-> one NRI case and the bounded agent/tool path
-> professional review and authority boundary
-> governed learning: accept one candidate, reject another
-> reuse of accepted knowledge
-> live AWS/AgentCore architecture evidence
-> 2031 target architecture
```

Close with:

> **Reasoning can be probabilistic. Authority cannot.**

## Repository and licence

Public repository: `https://github.com/MarketIQX/afh-crossborder-agent`

Licence: MIT. Project provenance and the fresh-code boundary are documented in `PROVENANCE.md`.
