# Nicole Architecture

**Models infer. Deterministic systems govern. Humans authorise.**

This document describes the architecture we can defend for the AWS Agents for Humans submission. It separates current implementation evidence from roadmap intent.

- **Live diagram:** [`architecture/nicole-live-architecture.svg`](architecture/nicole-live-architecture.svg)
- **2031 target:** [`architecture/nicole-2031-vision.svg`](architecture/nicole-2031-vision.svg)

The live diagram is the submission claim. The 2031 diagram is the product thesis.

## System thesis

Professional work is not made safe by asking a model to be careful. Nicole therefore separates:

1. what the model may see,
2. what the model may infer,
3. what the system permits it to claim,
4. what only a professional may authorise.

The model is allowed to be probabilistic. Authority is not.

## Current request path

```text
NRI enquiry
  -> persistence / conservative correlation
  -> deterministic service routing or human triage
  -> bounded case context
  -> Amazon Bedrock AgentCore Runtime
  -> Nicole / Strands agent
  -> Groq-hosted model inference
  -> four bounded tools
  -> deterministic evidence / applicability / authority gates
  -> private PostgreSQL durable state
  -> Partner Dashboard / professional review
```
## Layer 1 - deterministic before inference

Before a model is allowed to reason, the application establishes identity, case scope, service scope, material context and the knowledge surface.

Key properties:

- an untriaged case cannot be reasoned over;
- ambiguous routing waits for a person rather than guessing;
- case/service identity is server-bound rather than model-selected;
- context assembly refuses rather than silently dropping authority-critical state;
- retrieval reads the governed active knowledge surface rather than the base knowledge table;
- the runtime does not gain generic access to arbitrary cases or arbitrary services through tool arguments.

## Layer 2 - probabilistic reasoning

Nicole is a Strands agent. The current live AgentCore deployment uses the Groq provider path with `openai/gpt-oss-120b`; the repository also retains the original AWS Bedrock provider adapter.

The agent can call only:

```text
get_case_context()
get_service_knowledge(query, service_id)
record_proposed_facts(facts, evidence_refs)
propose_next_action(action)
```

Hooks and steering provide bounded containment and corrective guidance around tool use and client-facing output. They are defence in depth, not the final authority boundary.

## Layer 3 - deterministic after inference

The model may propose; the application decides whether the proposal is admissible.

The deterministic layer checks:

- service and case scope;
- required facts and unknowns;
- three-valued applicability (`TRUE`, `FALSE`, `UNKNOWN`);
- verification status of knowledge;
- declared source conflicts;
- citation requirements;
- append-only proposal/revision invariants;
- content guards for client-facing drafts;
- exact-content digest binding before execution.

A model proposing a state outside the evidence-permitted set is refused and the refusal is recorded.
## Layer 4 - professional authority

Professional authority is separate from runtime capability.

The runtime cannot:

- confirm its own proposed facts;
- write an approval for its own draft;
- publish or activate verified knowledge;
- grant itself a broader service scope;
- rewrite append-only proposal history.

The professional can review, reject, edit, approve and publish knowledge through separately governed paths. Approval and execution remain different powers.

## Governed learning loop

Nicole does not autonomously decide what becomes institutional knowledge.

```text
source document
  -> extracted candidate
  -> source/evidence verification
  -> professional review
  -> ACCEPT or REJECT
  -> accepted unit becomes professionally verified
  -> active release
  -> future fresh agent session may retrieve it
```

Supported training inputs today are `.pdf`, `.docx`, `.txt` and `.md`. A scanned-image PDF with no readable text is refused and requires an OCR layer first. OCR is not presented as a current capability.

Only `PROFESSIONALLY_VERIFIED` knowledge is admissible as support for a client-facing answer.

## Observable decision evidence

Nicole does not record private chain-of-thought as audit evidence. The durable basis of a decision is observable:

```text
initial bounded context
+ tool calls and results
+ established / proposed / unknown facts
+ cited governed knowledge
+ deterministic gate outcomes
+ model/runtime/provider identity
+ proposal outcome
```

`app/domain/receipt.py` projects these authoritative rows into a decision receipt. The same case surface also exposes the tool trajectory. A future correction-receipt contract is documented but not claimed as implemented.
## Live AWS foundation

Current verified deployment properties:

| Component | Current state |
|---|---|
| Agent runtime | Amazon Bedrock AgentCore Runtime `NicoleProfessionalAgent`, status `READY` |
| Region | `us-east-1` |
| Runtime language | Python 3.12 |
| Network mode | VPC |
| Runtime metadata | MMDSv2 required |
| Subnets | two private runtime subnets across separate availability zones |
| Runtime ENIs | observed in both configured private subnets |
| Database | Amazon RDS PostgreSQL 16.14 |
| DB exposure | non-public |
| DB storage | encrypted |
| DB ingress | PostgreSQL 5432 from the runtime security group |
| Outbound model access | NAT-backed runtime egress |
| Model credential | AgentCore Identity API-key credential provider `NicoleGroq` |
| DB credential | AWS Secrets Manager, hydrated at runtime |
| Runtime IAM | dedicated AgentCore execution role; no AdministratorAccess or RDS-admin authority |
| Deployment artifact | immutable S3 object with exact VersionId and recorded SHA-256 |

The exact deployment candidate was built for ARM64, scanned, imported under an ARM64/Python 3.12 environment, uploaded, downloaded by exact VersionId and byte-verified before Runtime creation.

The runtime environment carries non-secret configuration only. The Groq key and PostgreSQL password are not embedded as plain environment values in the deployment configuration.

## Live runtime evidence versus inference

The following are execution evidence, not diagram inference:

- code executing inside the actual AgentCore microVM resolved the application database secret;
- it authenticated to private RDS as the application runtime role;
- the same database role was refused when attempting privileged schema-administration access;
- a live invocation with a nonexistent case returned `RUN_REFUSED` instead of inventing case context.

The following are **not** yet proven by a successful live case:

- a complete AgentCore Identity -> Groq call inside a valid case trajectory;
- a full live Strands tool trajectory over a legitimate persisted NRI case;
- live proposal/receipt persistence from that successful case;
- repeated pass^k or adversarial reliability on the live deployment.
## Current live blocker

The production RDS has the deployed service definitions but no operational bootstrap rows yet:

```text
mailboxes  = 0
reviewers  = 0
cases      = 0
agent_runs = 0
```

That is why a successful unseen live NRI case has not yet been completed. The runtime identity is intentionally not allowed to create administrative bootstrap state. Granting it that authority simply to manufacture a green demo would invalidate the separation-of-powers claim.

The correct remaining path is operator bootstrap through the intended administrative boundary, followed by a synthetic enquiry through normal persistence/triage and then AgentCore invocation.

## Known hardening items

These are not hidden behind the word "production-ready":

- runtime security-group egress is broader than the desired final least-privilege posture;
- the Partner Dashboard has server-bound acting identity but no production sign-in/authentication;
- `/knowledge` is a placeholder route rather than the full knowledge-management explorer;
- scanned-document OCR is not implemented;
- full native OpenTelemetry/AgentCore observability is not integrated into the application path;
- native repeated agent-evaluation infrastructure and pass^k live acceptance remain future gates;
- successful unseen live-case execution remains open for the bootstrap reason above.

## Verification gates

The repository currently defines 345 checks across eighteen suites. `scripts/verify_clean_slate.py` creates a throwaway PostgreSQL target from repository files, applies migrations, runs the complete suite and destroys the target.

Latest submission pass after the Nicole Partner Dashboard changes:

```text
ALL CHECKS: PASS
CLEAN SLATE: PASS
```

A stale developer database may fail migration verification because historical migration drift is intentionally refused. That is not papered over by changing migration hashes or silently adopting state.
## 2031 direction

The target architecture extends the same invariant rather than replacing it: richer capability may increase, but authority remains explicit and external to model preference.

Roadmap areas include multi-tenant firm isolation, richer professional domains, multimodal evidence/OCR, object storage for evidence, semantic retrieval where it improves measured outcomes, asynchronous workflows, richer observability, native evaluation pipelines, additional integrations and specialized agents only where cross-domain coordination is justified by eval evidence.

Multi-agent architecture is therefore not a maturity checkbox. The current single professional agent with specialized bounded tools is simpler, easier to evaluate and easier to govern. Additional agents belong only where measured task decomposition requires them.

## What we defend

We defend the architecture as a governed professional-agent system in which:

- Strands provides the model-driven agent loop;
- Amazon Bedrock AgentCore provides the deployed runtime and workload-identity foundation;
- Groq currently provides live inference for the AgentCore deployment through the provider abstraction;
- AWS Secrets Manager and IAM keep secrets/privileges outside source code;
- private RDS holds durable application, evidence and audit state;
- deterministic database and application controls bound what model output can become;
- professional acceptance is required before candidate learning becomes reusable verified knowledge;
- the current deterministic regression surface passes from a clean repository-built database;
- live invalid input fails closed and live runtime-to-private-RDS access has been exercised.

We do not defend roadmap boxes as implemented, `READY` as equivalent to end-to-end success, deterministic checks as stochastic reliability, or the current local Partner Dashboard as an authenticated public production application.

That distinction is intentional. A professional system should be easier to trust because its limits are visible, not because its architecture diagram is optimistic.
