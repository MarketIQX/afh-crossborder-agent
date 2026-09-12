"""Which model answers a run, decided by configuration not by import.

The competition requires Strands Agents as the foundation. Its official
rules describe Amazon Bedrock and AgentCore as encouraged, and able to
strengthen technical implementation, rather than as requirements. So the
SDK stays fixed and the provider becomes a setting.

Two properties matter here more than convenience does.

Bedrock remains the default. With `MODEL_PROVIDER` unset this module
builds the model this project has always built, from the same keyword
arguments, so nothing that runs today changes behaviour. A check proves
that equality against the historical argument list rather than asserting
it, because a silent change to how the model is constructed would be
invisible until a run produced different output.

A provider that cannot be built refuses instead of substituting. This is
the rule `app/dispatch/providers.py` already applies to sending, for the
same reason: a run recorded against one provider that silently ran on
another is worse than a run that never happened, because the evidence is
then false while looking complete.
"""

from app import config

BEDROCK = "bedrock"
ANTHROPIC = "anthropic"
GROQ = "groq"

PROVIDERS = (BEDROCK, ANTHROPIC, GROQ)

# Overridable, and unproven until a real invocation succeeds against it.
DEFAULT_ANTHROPIC_MODEL_ID = "claude-sonnet-5"
DEFAULT_GROQ_MODEL_ID = "openai/gpt-oss-120b"
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

ANTHROPIC_INSTALL_HINT = "pip install 'strands-agents[anthropic]'"
OPENAI_INSTALL_HINT = "pip install 'strands-agents[openai]'"

# Groq's free tier reads at 600 seconds by default through the OpenAI
# SDK, which is long enough that a hung request looks like a hung
# application. A model call that has not answered in a minute has
# failed as far as an enquiry is concerned, and the deterministic layer
# would rather record SYSTEM_FAILURE than wait.
GROQ_TIMEOUT_SECONDS = 60.0


class ProviderUnavailable(RuntimeError):
    """A named provider cannot be built here, and nothing is substituted."""


def selected(provider=None):
    """The provider this process will use, validated by name."""
    name = provider or config.get("MODEL_PROVIDER", BEDROCK) or BEDROCK
    name = name.strip().lower()

    if name not in PROVIDERS:
        raise ProviderUnavailable(
            f"MODEL_PROVIDER is {name!r}; it must be one of "
            f"{', '.join(PROVIDERS)}"
        )

    return name


def bedrock_kwargs(
    model_id,
    max_tokens,
    temperature=None,
    boto_session=None,
    region=None,
):
    """Exactly the arguments this project has always given to Bedrock.

    Held as data rather than inlined at each construction site so that a
    check can compare it with the historical call without reaching AWS,
    and so the three call sites cannot drift apart by hand.
    """
    kwargs = {
        "model_id": model_id,
        "max_tokens": max_tokens,
        "streaming": False,
    }

    # The access check deliberately passes no temperature, and a default
    # invented here would change what that check exercises.
    if temperature is not None:
        kwargs["temperature"] = temperature

    # The SDK rejects a region and a session together, so a session
    # carries its own region and only a sessionless call names one.
    if boto_session is not None:
        kwargs["boto_session"] = boto_session
    else:
        kwargs["region_name"] = region

    return kwargs


def _build_bedrock(model_id, max_tokens, temperature, boto_session, region):
    from strands.models import BedrockModel

    resolved = model_id or config.get("BEDROCK_MODEL_ID", "")

    if not resolved:
        # The module default lives in `app.agent.bedrock`, which imports
        # this module. Reading it here would be a second copy of the
        # same constant as well as a circular import, so the caller
        # resolves it and this refuses rather than inventing one.
        raise ProviderUnavailable(
            "no Bedrock model id was given and BEDROCK_MODEL_ID is not "
            "set"
        )

    kwargs = bedrock_kwargs(
        resolved, max_tokens, temperature, boto_session, region
    )

    descriptor = {
        "provider": BEDROCK,
        "model_id": resolved,
        "region": region,
        "streaming": False,
    }

    return BedrockModel(**kwargs), descriptor


def _build_anthropic(model_id, max_tokens, temperature):
    try:
        from strands.models.anthropic import AnthropicModel
    except ImportError as exc:
        raise ProviderUnavailable(
            f"MODEL_PROVIDER is {ANTHROPIC} but the provider is not "
            f"installed. Install it with: {ANTHROPIC_INSTALL_HINT}"
        ) from exc

    key = (config.get("ANTHROPIC_API_KEY", "") or "").strip()

    if not key:
        raise ProviderUnavailable(
            f"MODEL_PROVIDER is {ANTHROPIC} but ANTHROPIC_API_KEY is "
            f"not set. Nothing is substituted, because a run must not "
            f"be recorded against a provider that did not answer it."
        )

    resolved = model_id or config.get(
        "ANTHROPIC_MODEL_ID", DEFAULT_ANTHROPIC_MODEL_ID
    )

    kwargs = {
        "model_id": resolved,
        "max_tokens": max_tokens,
    }

    # This provider carries sampling settings in `params`, and has no
    # streaming argument at all. Reporting one would be a claim about
    # behaviour nothing here configures.
    if temperature is not None:
        kwargs["params"] = {"temperature": temperature}

    model = AnthropicModel(client_args={"api_key": key}, **kwargs)

    descriptor = {
        "provider": ANTHROPIC,
        "model_id": resolved,
        "region": None,
        "streaming": None,
    }

    return model, descriptor


def _build_groq(model_id, max_tokens, temperature):
    """Groq through Strands' own OpenAI-compatible provider.

    Groq supplies inference and nothing else. Strands keeps the agent
    loop, the tools, the hooks and the steering; the deterministic layer
    keeps policy, state, approvals and evidence. Nothing here reaches
    Groq's server-side tool runtime, and no other module imports a Groq
    client.

    Two arguments are load-bearing and neither is a default.

    `stream=False`. The adapter defaults streaming to True, and this
    project has been non-streaming since the first Bedrock call. A
    streaming request would also invalidate the structured-output path,
    which Strands runs through a non-streaming `parse()`.

    `parallel_tool_calls=False`. Groq's API defaults this to true while
    `openai/gpt-oss-120b` does not support parallel tool use, so leaving
    it out is the risk rather than the mitigation -- the omission is
    filled in by Groq's default, not by ours. Strands never sets the
    parameter itself, which was first read here as evidence that no
    conflict was possible. That inference was wrong, and it is the
    reason this line is explicit and checked.
    """
    try:
        from strands.models.openai import OpenAIModel
    except ImportError as exc:
        raise ProviderUnavailable(
            f"MODEL_PROVIDER is {GROQ} but the OpenAI-compatible "
            f"provider is not installed. Install it with: "
            f"{OPENAI_INSTALL_HINT}"
        ) from exc

    key = (config.get("GROQ_API_KEY", "") or "").strip()

    if not key:
        raise ProviderUnavailable(
            f"MODEL_PROVIDER is {GROQ} but GROQ_API_KEY is not set. "
            f"Nothing is substituted, because a run must not be "
            f"recorded against a provider that did not answer it."
        )

    resolved = model_id or config.get(
        "GROQ_MODEL_ID", DEFAULT_GROQ_MODEL_ID
    )
    base_url = config.get("GROQ_BASE_URL", DEFAULT_GROQ_BASE_URL)

    params = {
        "max_tokens": max_tokens,
        "parallel_tool_calls": False,
    }

    if temperature is not None:
        params["temperature"] = temperature

    model = OpenAIModel(
        client_args={
            "api_key": key,
            "base_url": base_url,
            "timeout": GROQ_TIMEOUT_SECONDS,
        },
        model_id=resolved,
        params=params,
        stream=False,
    )

    descriptor = {
        "provider": GROQ,
        "model_id": resolved,
        "region": None,
        "streaming": False,
        "base_url": base_url,
        "parallel_tool_calls": False,
    }

    return model, descriptor


def build(
    max_tokens,
    model_id=None,
    temperature=None,
    boto_session=None,
    region=None,
    provider=None,
):
    """Return `(model, descriptor)` for the configured provider.

    The descriptor names what was actually built, so a run record cannot
    claim a provider that did not serve it.
    """
    name = selected(provider)

    if name == BEDROCK:
        return _build_bedrock(
            model_id, max_tokens, temperature, boto_session, region
        )

    if name == GROQ:
        return _build_groq(model_id, max_tokens, temperature)

    return _build_anthropic(model_id, max_tokens, temperature)


def resolved_model_id(provider=None, model_id=None):
    """The model id the adapter would use, without building anything.

    A caller that needs to record which model answered should not have
    to construct one to find out, and must not keep its own copy of the
    defaults either.
    """
    name = selected(provider)

    if model_id:
        return model_id

    if name == BEDROCK:
        return config.get("BEDROCK_MODEL_ID", "")

    if name == GROQ:
        return config.get("GROQ_MODEL_ID", DEFAULT_GROQ_MODEL_ID)

    return config.get("ANTHROPIC_MODEL_ID", DEFAULT_ANTHROPIC_MODEL_ID)


def requires_aws(provider=None):
    """Whether the configured provider needs an AWS identity at all.

    The identity gate in `app.agent.bedrock` is unbypassable by design.
    It is also specific to AWS, so a provider that never calls AWS must
    not be made to satisfy it, and must not be allowed to skip it
    silently either. Callers ask this question explicitly.
    """
    return selected(provider) == BEDROCK
