"""Deterministic control injected into the agent's own lifecycle.

Until now every limit on Nicole lived in two places: the database, which
holds whatever happens, and the inside of each tool, which refuses bad
arguments after the call has already been made. Both are real. Neither is
visible where the agent actually runs.

Strands provides hook points around the model call and around every tool
call, and deterministic code placed there decides whether the agent may
continue at all. That is a better place for two of our rules, because a
refusal that happens before the call is cheaper, clearer in the trace,
and cannot depend on a tool implementation being correct.

Two hooks live here.

`ServiceScopeHook` cancels any retrieval aimed at a service other than
the one bound to this case, under either of that service's names. The
bound service's own key is in scope: the model reaches for a readable
name before it reaches for a UUID, and doing so is not a violation.
Earlier runs recorded refusals that were exactly that, a readable name
compared against a UUID, and they were wrongly described as the model
trying to reach another service. What this stops is a genuinely
different service.

`ToolBudgetHook` caps how many times any one tool may be called in a
single run. An agent that retries a failing tool indefinitely is not
thinking, it is looping, and every loop costs a real model call.

Neither hook is the only thing standing between the agent and the
mistake. The tool still validates, and the database still refuses. These
are the outermost of three, and the cheapest.
"""

from strands.hooks import (
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

# How many times one tool may be called within a single run before the
# hook stops it. Seven is comfortably above the six calls a well-behaved
# run has needed, and far below a loop.
DEFAULT_TOOL_BUDGET = 7

SCOPE_REFUSAL = (
    "refused: this case is bound to service {bound}, and no tool may "
    "read another service's knowledge. Call get_service_knowledge "
    "without a service_id, or with {bound}."
)

BUDGET_REFUSAL = (
    "refused: {tool} has already been called {count} times in this run, "
    "which is the limit. Work with what you have, or report what is "
    "missing."
)


def _tool_name(event):
    """The tool being called, however the SDK spells it."""
    use = getattr(event, "tool_use", None) or {}

    return use.get("name") or ""


def _tool_input(event):
    use = getattr(event, "tool_use", None) or {}

    return use.get("input") or {}


class ServiceScopeHook(HookProvider):
    """Cancel any tool call that reaches outside the bound service.

    The case and its service are fixed server side before the agent
    runs. The model may not widen that, and this is where the attempt
    stops.
    """

    def __init__(self, bound_service_id, trace=None, bound_service_key=""):
        self.bound_service_id = str(bound_service_id)
        self.bound_service_key = str(bound_service_key or "")
        self.trace = trace
        self.refusals = []

    def names_bound_service(self, candidate):
        """The bound service under either of its names is in scope."""
        wanted = str(candidate or "").strip().lower()

        if not wanted:
            return True

        return wanted in {
            self.bound_service_id.strip().lower(),
            self.bound_service_key.strip().lower(),
        } - {""}

    def register_hooks(self, registry: HookRegistry, **kwargs):
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool_call)

    def on_before_tool_call(self, event: BeforeToolCallEvent):
        requested = str(_tool_input(event).get("service_id") or "").strip()

        if self.names_bound_service(requested):
            return

        reason = SCOPE_REFUSAL.format(bound=self.bound_service_id)
        self.refusals.append((_tool_name(event), requested))

        if self.trace is not None:
            self.trace.record(
                tool_name=_tool_name(event),
                arguments=_tool_input(event),
                error=f"hook refused out-of-scope service_id {requested}",
            )

        # A string cancels the call and hands this text back to the
        # model, so it learns why rather than simply failing.
        event.cancel_tool = reason


class ToolBudgetHook(HookProvider):
    """Stop a single tool being called more times than a run can justify.

    The workshop calls this rate limiting. The point is not cost alone:
    an agent that calls the same tool ten times is not going to succeed
    on the eleventh, and it should be made to say what it is missing.
    """

    def __init__(self, budget=DEFAULT_TOOL_BUDGET, trace=None):
        self.budget = int(budget)
        self.trace = trace
        self.counts = {}
        self.blocked = []

    def register_hooks(self, registry: HookRegistry, **kwargs):
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool_call)

    def on_before_tool_call(self, event: BeforeToolCallEvent):
        name = _tool_name(event)

        if not name:
            return

        self.counts[name] = self.counts.get(name, 0) + 1
        count = self.counts[name]

        if count <= self.budget:
            return

        reason = BUDGET_REFUSAL.format(tool=name, count=self.budget)
        self.blocked.append((name, count))

        if self.trace is not None:
            self.trace.record(
                tool_name=name,
                arguments=_tool_input(event),
                error=f"hook refused: budget of {self.budget} exhausted",
            )

        event.cancel_tool = reason


def build_hooks(
    bound_service_id,
    trace=None,
    budget=DEFAULT_TOOL_BUDGET,
    bound_service_key="",
):
    """The hooks every run is constructed with."""
    return [
        ServiceScopeHook(
            bound_service_id, trace=trace, bound_service_key=bound_service_key
        ),
        ToolBudgetHook(budget=budget, trace=trace),
    ]
