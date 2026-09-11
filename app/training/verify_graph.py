"""Two challengers, run in parallel, adjudicated by code.

A single verifier asked "do these passages support this claim" answers
one question with one framing, and a model asked one question tends to
find one answer. Challenged four ways earlier it agreed once, which was
reassuring and is not a guarantee: it was still one opinion.

So the check is now a Strands graph with two nodes that have no edge
between them, which means the SDK runs them concurrently. They are asked
different questions on purpose:

    faithfulness  does the passage actually state this?
    overreach     does the claim go further than the passage does?

Those fail differently. A claim can be word-for-word present and still
overreach by dropping a condition, and a claim can be a fair summary
while stating a number the passage never gives. One question catches one
of those.

Adjudication is deterministic and conservative, and it is code rather
than a third model. A model asked to settle a disagreement between two
models will usually find a way to agree with something. The rule here is
that either challenger can veto, and only unanimous agreement produces
SUPPORTED. That is the direction a professional wants the error to run:
the cost of an unnecessary warning is a few seconds of reading, and the
cost of a false pass is guidance a client relies on.

`GraphResult` also carries accumulated usage across nodes, which is why
the graph earns its place beyond the parallelism: the cost of verifying
one candidate becomes a number we record rather than an estimate.
"""

from strands.multiagent import GraphBuilder

SUPPORTED = "SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
UNCLEAR = "UNCLEAR"

FAITHFULNESS = "faithfulness"
OVERREACH = "overreach"

FAITHFULNESS_PROMPT = """\
You are checking whether a passage states a claim.

You will be given a claim and the exact passages cited for it. You have
nothing else. If the passages do not state the claim, the answer is NO
even when you believe the claim is true: the question is whether this
citation carries this claim, not whether the claim is correct.

Answer with exactly one word on the first line, YES or NO, then one
sentence saying why. If you are unsure, answer NO. An unnecessary
warning costs a reader seconds. A false pass becomes guidance a client
relies on.
"""

OVERREACH_PROMPT = """\
You are checking one thing: does the claim cover anyone the passage does
not?

That is the whole question. Not whether the claim is correct, not
whether it is complete, not whether it is well written. Only whether it
reaches past its source.

Ask it as a test. Think of a person the claim applies to. Is there such
a person the passage would not have covered? If yes, answer NO. If every
person the claim covers is someone the passage covers, answer YES.

Omissions cut both ways and the direction is what matters.

An omission that makes the rule apply to FEWER people is incomplete, and
incomplete is fine. A passage giving a 182-day test or a 60-day test,
summarised as only the 182-day test, covers fewer people. YES.

An omission that makes the rule apply to MORE people is overreach. It
does not matter what was dropped:

  a condition joined by AND, so the remaining condition alone qualifies
  a subject or qualifier, so a rule about residents becomes a rule
    about everyone
  an except-where clause, so people the exception removes are put back

All of those are NO.

Answer with exactly one word on the first line, YES or NO, and no
reasoning before it. Then one sentence naming the person the claim
covers and the passage does not. If you cannot name such a person, the
answer is YES. Do not reconsider mid-answer: decide, then write.
"""


def build_prompt(statement, topic, passages):
    body = "\n\n".join(
        f"[passage {p['ordinal']}]\n{p['passage']}" for p in passages
    )

    return (
        f"Claim:\n{statement}\n\n"
        f"Topic: {topic}\n\n"
        f"Passages cited for it:\n\n{body}\n"
    )


# Two nodes need two executions. Four leaves room for a retry and
# still refuses to run away. A challenger silent for ninety seconds has
# failed, whatever it is doing.
MAX_NODE_EXECUTIONS = 4
NODE_TIMEOUT_SECONDS = 90
GRAPH_TIMEOUT_SECONDS = 180


def build_graph(agent_factory):
    """Two challengers, no edge between them, so Strands runs them at once."""
    builder = GraphBuilder()

    # Explicit bounds. This graph is acyclic and cannot loop today; the
    # limits exist so it still cannot the day an edge is added.
    builder.set_max_node_executions(MAX_NODE_EXECUTIONS)
    builder.set_node_timeout(NODE_TIMEOUT_SECONDS)
    builder.set_execution_timeout(GRAPH_TIMEOUT_SECONDS)

    builder.add_node(agent_factory(FAITHFULNESS_PROMPT), FAITHFULNESS)
    builder.add_node(agent_factory(OVERREACH_PROMPT), OVERREACH)

    # Both are entry points. There is deliberately no edge: neither
    # challenger should see the other's answer, because a second opinion
    # that has read the first is not a second opinion.
    builder.set_entry_point(FAITHFULNESS)
    builder.set_entry_point(OVERREACH)

    return builder.build()


# A checker that answered NO and then wrote "Wait - reconsidering: YES"
# produced a false negative on a correct claim when only the first word
# was read. A model arguing with itself has not answered.
_RECONSIDERS = ("RECONSIDER", "ON REFLECTION", "ACTUALLY, ", "WAIT")


def _first_word_is_yes(text):
    """Read the verdict, and refuse to read one that contradicts itself.

    Conservative on ambiguity, because an unnecessary warning costs a
    reader seconds and a false pass becomes guidance a client relies on.
    But self-contradiction is reported as unclear rather than resolved
    in either direction: picking a side for a model that could not is
    inventing an answer.
    """
    body = str(text or "").strip()
    upper = body.upper()
    verdict = None

    for line in body.splitlines():
        stripped = line.strip().strip("*# ").upper()

        if not stripped:
            continue

        if stripped.startswith("YES"):
            verdict = True
        elif stripped.startswith("NO"):
            verdict = False

        # A first non-empty line answering neither is not an answer.
        break

    if verdict is None:
        return None

    if any(marker in upper for marker in _RECONSIDERS):
        return None

    return verdict


def _node_text(result, node_id):
    node = (result.results or {}).get(node_id)

    if node is None:
        return ""

    return str(getattr(node, "result", node))


def adjudicate(faithful, within_scope):
    """Either challenger may veto. Only unanimity passes.

    Written as a plain function so the rule can be read, tested and
    argued with, without a model in the loop.
    """
    if faithful is False or within_scope is False:
        return NOT_SUPPORTED

    if faithful is True and within_scope is True:
        return SUPPORTED

    return UNCLEAR


def verify(agent_factory, statement, topic, passages):
    """Run both challengers and return (verdict, reason, usage)."""
    if not passages:
        return NOT_SUPPORTED, "the cited passages could not be found", {}

    graph = build_graph(agent_factory)
    result = graph(build_prompt(statement, topic, passages))

    faith_text = _node_text(result, FAITHFULNESS)
    reach_text = _node_text(result, OVERREACH)

    faithful = _first_word_is_yes(faith_text)
    within_scope = _first_word_is_yes(reach_text)

    verdict = adjudicate(faithful, within_scope)

    parts = []

    if faithful is not True:
        parts.append(f"states it: {_one_sentence(faith_text)}")

    if within_scope is not True:
        parts.append(f"stays within it: {_one_sentence(reach_text)}")

    if not parts:
        parts.append(
            "both checks agree the passage states this and the claim "
            "does not reach beyond it"
        )

    usage = {
        "nodes": len(result.results or {}),
        "completed": result.completed_nodes,
        "failed": result.failed_nodes,
        "execution_ms": result.execution_time,
        "input_tokens": getattr(result.accumulated_usage, "inputTokens", None),
        "output_tokens": getattr(
            result.accumulated_usage, "outputTokens", None
        ),
    }

    return verdict, " | ".join(parts)[:880], usage


def _one_sentence(text):
    lines = [
        line.strip()
        for line in str(text or "").strip().splitlines()
        if line.strip()
    ]

    if len(lines) > 1:
        return lines[1][:220]

    return (lines[0][:220] if lines else "no answer")
