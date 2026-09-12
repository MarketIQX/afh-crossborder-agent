# Anika — submission text

Draft of the Devpost text description. Kept in the repository so it can be
reviewed against the code rather than written from memory.

---

## Anika

**An agent that does a professional firm's repetitive work, and cannot
quietly get it wrong.**

Anika reads a cross-border tax firm's email, does the analysis
unattended, and reaches a person only when the decision is genuinely
theirs. What makes it a professional agent is not that it answers — it is
that a wrong answer is structurally refused before a human ever sees it,
and an honest "I cannot tell you yet, and here is what I need" is a
first-class outcome rather than a failure.

### The problem, and who has it

Someone who moves abroad keeps a permanent, low-grade compliance burden
in the country they left: residency tests to satisfy, returns to file,
remittance rules to observe. The questions repeat endlessly across
clients; the answers are judgment-heavy and turn on facts only the client
holds.

The firms that handle this are small — a few chartered accountants and
their staff — and they spend their days re-deriving the same analysis
from scratch, per client, from statute they already know. It is
repetitive without being mechanical, which is exactly why it has resisted
automation: the cost of a confident wrong answer is professional
liability, so nobody sane hands it to a system that cannot be held to a
standard.

That is the gap Anika is built for. Not "answer tax questions" — any
model does that badly. **Make a model's output safe enough for a
professional to put their name on.**

### The failure that shaped the architecture

Early on, a test drove the decision layer directly, with no model
involved at all. It supplied a rule about **resident** individuals:
professionally verified, on topic, effective on the date in question. The
client was a **non-resident**. Every unrelated gate was deliberately
satisfied.

The system authorised a supported answer, cited that rule as its basis,
recorded that no professional review was needed, and gave its reason:

> *"in scope, material facts confirmed, effective guidance retrieved, no
> declared conflict"*

Every clause of that sentence was true. The conclusion was wrong.

It was not a missing check. The layer read five fields about the case and
**not one of them described the client**, so there was no field in which
"does this rule even apply to this person?" could be expressed. A
reviewer auditing the rules would have found nothing missing, because the
vocabulary had no word for what was wrong.

That is the failure mode Anika is built against: not a model that lies,
but a governing system whose language is too small to state the
constraint.

### How it works

Four layers, and only one of them infers.

1. **Deterministic — what the model may know.** An identity gate that
   refuses to invoke under the account root. A keyword-dominance router
   with no model in it, which refuses a genuine straddle and sends it to
   a human. Bounded context assembly that refuses to run rather than drop
   scope, dates or missing facts to fit a budget. Retrieval scoped by a
   database view, not a filter.
2. **Probabilistic — the only inferring layer.** Claude Sonnet 4.5 on
   Amazon Bedrock, through Strands Agents, with four tools and skills
   loaded on demand. It proposes **one** decision state and writes the
   client copy. Both are proposals.
3. **Deterministic — what the model may claim.** Three-valued
   applicability, evaluated *before* provenance. A verification bar.
   A state validator that raises when the model proposes something the
   evidence does not permit. Copy guards that refuse machine words and
   statutory citations in a client letter. A content digest rechecked at
   dispatch.
4. **Human — the only layer that can say yes.** Approve, reject with a
   reason, or edit and send with the edit attributed to the reviewer.
   Publishing knowledge is a human act: the agent cannot promote its own
   findings to citable.

**Models infer. Deterministic systems govern. Humans authorise.**

Retrieval sits in the middle on purpose. It is how the agent *finds* the
right guidance, never the authority on whether that guidance may be
*used*. Making retrieval the authority is the standard mistake:
everything retrieved becomes usable.

### Three-valued applicability

The fix to that original failure is the piece we are most confident
about, because it is provably load-bearing.

| Verdict | Meaning | Outcome |
|---|---|---|
| `TRUE` | the rule's conditions match what is established | admissible |
| `FALSE` | they contradict it | excluded, and recorded |
| `UNKNOWN` | the rule turns on something nobody has established | **becomes a question** |

A boolean has to fold `UNKNOWN` into one of the others, and both choices
are wrong. As `TRUE`, a rule that may not apply becomes the basis of an
answer. As `FALSE`, the evidence disappears silently — and if residency
is unestablished, dropping every resident-rule conceals the fact that
residency is *precisely* what needs establishing, then reports a
knowledge gap where none exists.

The third value routes to "missing facts" and names the predicate, so the
system asks. The answer becomes *"I cannot tell you whether this applies
until we know X"* — true, and useful.

Applicability reads only **confirmed** facts. A fact the model proposed
about the client can never decide which rules govern that client.

### Built on Strands Agents

- **Agent loop and tools** — four `@tool` functions over a bounded,
  server-assembled context.
- **Hooks** — `BeforeToolCallEvent` cancels any call reaching outside the
  case's bound service, and caps each tool at seven calls per run.
- **Interventions** — `InterventionHandler` guards that refuse
  client-facing copy carrying machine words or statutory citations,
  proven against the actual defective letter that prompted them.
- **Skills** — markdown procedures loaded on demand via `AgentSkills`
  rather than held in every prompt.
- **Multi-agent** — a `GraphBuilder` graph runs two adversarial verifiers
  in parallel over extracted knowledge; either can veto, and only
  unanimity passes.
- **Evals** — a golden suite scored against the verification graph,
  persisted per run with the prompt digest and git commit.

### What is actually proven

**180 checks across twelve suites, passing from nothing.** A throwaway
PostgreSQL container is built from the repository alone, every check runs
against it, and the container is destroyed.

The checks that matter most run with **no model and no database**,
because a safe result produced by a well-behaved model is not evidence of
a safe architecture — a safe result produced while the model is assumed
to be wrong is.

And those checks were themselves tested. With the applicability gate
removed at runtime, five of them fail, including every one that exists
because of the original defect. A check that would pass without the fix
defends nothing.

Separation of powers is enforced by database privilege rather than
application code, because a code check holds only for the paths someone
remembered to route through it. Read from the live catalogue: the agent
runtime holds **no privilege of any kind** on the knowledge table, cannot
write an approval, and cannot alter a fact it proposed. A reviewer cannot
author the agent's proposal. **No `DELETE` grant exists anywhere in the
schema** for either role.

### What it does not do yet

Stated because leaving it out would overclaim.

- Applicability now **excludes on real rows** — a verified rule about
  residents is refused for a confirmed non-resident, through real context
  assembly and retrieval, with the ground recorded. But no unit in the live
  corpus carries a restriction yet, so on today's data every unit still
  applies to everyone. What is missing is reviewer tagging at sign-off, not
  the mechanism.
- **A reviewer can confirm a fact, through the database only.** That grant
  was absent until now, which made a supported answer unreachable on any
  input. The console route to do it from the interface does not exist yet.
- **The applicability field means less than tax applicability.** A `TRUE`
  verdict currently makes a rule a citable basis, which is sufficiency
  semantics. A professional review refused to tag the deemed-residency rule
  because Indian citizenship is necessary but not sufficient under the
  Income-tax Act 2025 — the rule also turns on an income threshold and on
  non-liability to tax elsewhere. That rule is deliberately untagged rather
  than half-encoded. Exclusion and asking are sound; only admission
  overclaims.
- **Rules are dated by publication, not by statutory force, and the
  material date is the enquiry date rather than the tax year.** The temporal
  mechanism exists and is wired to the wrong dates.
- Two applicability dimensions, exact-match vocabulary. Treaty country,
  income type and entity type have no expression.
- One corridor in scope. Anything outside it is refused honestly and
  drafted to a human, because an agent that guesses outside its
  competence is worse than no agent.

### What is next

Applicability at sign-off, so a reviewer sets who a rule is for while
they are already reading it. Fact confirmation in the console, which
turns the gate from proven to useful. Propositions rather than units as
the atom of knowledge, so a citation points at one rule instead of three.

---

**Repository:** https://github.com/MarketIQX/afh-crossborder-agent ·
MIT · architecture in
[docs/ARCHITECTURE.md](https://github.com/MarketIQX/afh-crossborder-agent/blob/main/docs/ARCHITECTURE.md)
