"""Strands agent backed by Bedrock, behind the same model interface.

This registers exactly four tools with the SDK and verifies the
registration before invoking anything. That check is what the earlier
`tool_name` database constraint could not provide: the constraint only
governs what may be recorded, while this governs what the agent can
call.

Streaming is disabled so the Converse path is used non-streaming, which
needs `bedrock:InvokeModel` and not
`bedrock:InvokeModelWithResponseStream`. That keeps the permission
surface as small as the work requires.

Errors are surfaced with their exact class and message, scrubbed of
anything resembling a credential, because a precise AWS error is the
evidence needed to fix access. A vague "AWS access" report is not.
"""

import re

from strands import Agent, tool
from strands.models import BedrockModel

from app import config
from app.agent import tools as tools_module
from app.agent.model import PROMPT_VERSION, SYSTEM_PROMPT, prompt_digest

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_REGION = "us-east-1"

DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.2

USER_PROMPT = (
    "A new enquiry is waiting on the case bound to this run. Work "
    "through it now. Start by calling get_case_context(). Finish by "
    "calling propose_next_action() exactly once."
)

_SECRET_PATTERNS = (
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(aws_secret_access_key\s*=\s*)\S+"),
    re.compile(r"(?i)(session[_-]?token\s*[=:]\s*)\S+"),
)


class BedrockInvocationFailed(Exception):
    """The model could not be invoked. Carries the exact reason."""


def scrub(text):
    """Remove anything credential-shaped from an error before logging."""
    cleaned = str(text)

    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)

    return cleaned


EXPECTED_ACCOUNT_KEY = "AFH_AWS_ACCOUNT_ID"
EXPECTED_PRINCIPAL_KEY = "AFH_AWS_EXPECTED_PRINCIPAL"


class IdentityRefused(Exception):
    """This AWS identity may not be used to invoke the model."""


def resolve_identity(session):
    """Who this session actually is. Never returns secret values."""
    try:
        credentials = session.get_credentials()
    except Exception as exc:  # noqa: BLE001
        raise IdentityRefused(
            f"credential resolution failed: {exc.__class__.__name__}: "
            f"{scrub(exc)}"
        ) from exc

    if credentials is None:
        raise IdentityRefused(
            "the credential provider chain returned nothing, so there is "
            "no identity to check"
        )

    try:
        caller = session.client("sts").get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        raise IdentityRefused(
            f"STS did not confirm an identity: {exc.__class__.__name__}: "
            f"{scrub(exc)}"
        ) from exc

    return {
        "arn": caller.get("Arn", ""),
        "account": caller.get("Account", ""),
        "provider": getattr(credentials, "method", "unknown"),
    }


def enforce_identity(session):
    """Refuse unless this is exactly the principal we intend to use.

    There is deliberately no override. A caller who could switch this
    off could invoke the model as the account root, which is the thing
    it exists to prevent.
    """
    expected_account = (config.get(EXPECTED_ACCOUNT_KEY) or "").strip()
    expected_principal = (config.get(EXPECTED_PRINCIPAL_KEY) or "").strip()

    if not expected_account or not expected_principal:
        raise IdentityRefused(
            f"{EXPECTED_ACCOUNT_KEY} and {EXPECTED_PRINCIPAL_KEY} must "
            f"both be set. Without them there is nothing to check the "
            f"caller against, and an unchecked identity must never reach "
            f"the model."
        )

    identity = resolve_identity(session)
    arn = identity["arn"]

    if arn.endswith(":root"):
        raise IdentityRefused(
            "this session is the account root. Root must never invoke "
            "the model. Use the dedicated deployment principal."
        )

    if identity["account"] != expected_account:
        raise IdentityRefused(
            f"account {identity['account']} is not the expected "
            f"{expected_account}"
        )

    if arn != expected_principal:
        raise IdentityRefused(
            f"principal {arn} is not the expected {expected_principal}"
        )

    return identity


def with_region(session, region):
    """Return a session that definitely carries a region.

    `boto3.Session` has no setter for this, so when the caller's session
    has no region a matching one is rebuilt from the same profile.
    """
    if session is None or session.region_name:
        return session

    import boto3
    from botocore.exceptions import ProfileNotFound

    try:
        return boto3.Session(
            profile_name=session.profile_name, region_name=region
        )
    except ProfileNotFound:
        # `profile_name` reports "default" even when no such profile is
        # configured, so the name is not evidence that one exists.
        return boto3.Session(region_name=region)


def check_access(session, model_id=None, region=None):
    """Prove model access with no tools registered.

    Separated from the real run on purpose. If the access check used
    the real tool surface, a deliberate tool failure would look like an
    AWS problem, and a successful model call could be reported as a
    failure.
    """
    identity = enforce_identity(session)

    model = BedrockModel(
        model_id=model_id or config.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
        boto_session=with_region(
            session, region or config.get("AWS_REGION", DEFAULT_REGION)
        ),
        max_tokens=64,
        streaming=False,
    )

    agent = Agent(
        model=model,
        system_prompt="Answer with the single word: ready.",
    )

    if agent.tool_names:
        raise BedrockInvocationFailed(
            f"the access check registered tools {agent.tool_names}; it "
            f"must register none"
        )

    try:
        result = agent("Answer with the single word: ready.")
    except Exception as exc:  # noqa: BLE001
        raise BedrockInvocationFailed(
            f"{exc.__class__.__name__}: {scrub(exc)}"
        ) from exc

    return {"identity": identity, "reply": str(result)[:200]}


def build_tool_functions(bound):
    """Wrap the bounded tool object as SDK tools.

    The wrappers add nothing. Scope, validation, persistence and the
    tool trace all remain in `AgentTools`, so the Bedrock path and the
    deterministic path go through identical logic.
    """

    @tool(name="get_case_context")
    def get_case_context() -> dict:
        """Return the enquiry, the facts on file, the facts this service
        materially requires, and the topics the enquiry was routed to.
        Takes no arguments: the case is bound by the server."""
        return bound.get_case_context()

    @tool(name="get_service_knowledge")
    def get_service_knowledge(query: str, service_id: str = None) -> dict:
        """Retrieve approved, effective professional knowledge for the
        bound service. Returns units with their source locator and
        verification status, the topics with no professionally verified
        guidance, and any declared conflicts between sources."""
        return bound.get_service_knowledge(query, service_id)

    @tool(name="record_proposed_facts")
    def record_proposed_facts(
        facts: list, evidence_refs: list = None
    ) -> dict:
        """Record facts the enquiry itself states. Each fact is an
        object with a 'predicate' and a 'value'. Everything recorded is
        marked PROPOSED and means nothing until a human confirms it."""
        return bound.record_proposed_facts(facts, evidence_refs)

    @tool(name="propose_next_action")
    def propose_next_action(action: dict) -> dict:
        """Persist exactly one proposal for a human reviewer. The action
        needs a 'decision_state' and a 'summary', and may carry
        'requested_information' and 'payload'. The server refuses any
        decision state the evidence does not permit."""
        return bound.propose_next_action(action)

    return [
        get_case_context,
        get_service_knowledge,
        record_proposed_facts,
        propose_next_action,
    ]


class BedrockStrandsModel:
    """Real model runs. Stamped BEDROCK_STRANDS in the run record."""

    runner = "BEDROCK_STRANDS"
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        model_id=None,
        region=None,
        temperature=DEFAULT_TEMPERATURE,
        max_tokens=DEFAULT_MAX_TOKENS,
        boto_session=None,
    ):
        self.model_id = model_id or config.get(
            "BEDROCK_MODEL_ID", DEFAULT_MODEL_ID
        )
        self.region = region or config.get("AWS_REGION", DEFAULT_REGION)
        self.temperature = temperature
        self.max_tokens = max_tokens

        # When supplied, this is the session whose identity was checked,
        # so the check and the invocation cannot diverge.
        self.boto_session = boto_session

    def prompt_digest(self):
        return prompt_digest()

    def describe(self):
        """Configuration, for the run record and for evidence."""
        return {
            "runner": self.runner,
            "model_id": self.model_id,
            "region": self.region,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "streaming": False,
            "prompt_version": self.prompt_version,
            "prompt_digest": self.prompt_digest(),
        }

    def build_agent(self, bound):
        """Construct the agent and prove its tool surface."""
        model_kwargs = {
            "model_id": self.model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "streaming": False,
        }

        # The SDK rejects a region and a session together, so the region
        # is carried by the session when there is one. A session with no
        # region would otherwise fail later and far less clearly.
        session = self.session_with_region()

        if session is not None:
            model_kwargs["boto_session"] = session
        else:
            model_kwargs["region_name"] = self.region

        model = BedrockModel(**model_kwargs)

        agent = Agent(
            model=model,
            tools=build_tool_functions(bound),
            system_prompt=SYSTEM_PROMPT,
        )

        registered = tuple(sorted(agent.tool_names))
        expected = tuple(sorted(tools_module.TOOL_NAMES))

        if registered != expected:
            raise BedrockInvocationFailed(
                f"tool surface mismatch: the agent registered "
                f"{registered}, expected exactly {expected}. Refusing to "
                f"invoke a model with a tool surface we did not intend."
            )

        return agent

    def session(self):
        """The session this adapter will invoke through."""
        if self.boto_session is not None:
            return self.boto_session

        import boto3

        return boto3.Session()

    def session_with_region(self):
        """The same session, guaranteed to carry a region."""
        return with_region(self.boto_session, self.region)

    def run(self, bound):
        # Ahead of everything. No credentials, no STS, root, wrong
        # account or wrong principal all stop here, before any Bedrock
        # call is made.
        enforce_identity(self.session())

        agent = self.build_agent(bound)

        try:
            result = agent(USER_PROMPT)
        except Exception as exc:  # noqa: BLE001
            raise BedrockInvocationFailed(
                f"{exc.__class__.__name__}: {scrub(exc)}"
            ) from exc

        return self._usage(result)

    @staticmethod
    def _usage(result):
        """Token usage if the SDK reported any. Absence is not an error."""
        usage = {}

        metrics = getattr(result, "metrics", None)
        accumulated = getattr(metrics, "accumulated_usage", None)

        if isinstance(accumulated, dict):
            usage["input_tokens"] = accumulated.get("inputTokens")
            usage["output_tokens"] = accumulated.get("outputTokens")

        usage.setdefault("input_tokens", None)
        usage.setdefault("output_tokens", None)

        return usage
