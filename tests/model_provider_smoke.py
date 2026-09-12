"""MODEL01-MODEL12. Changing who answers must not change what is asked.

The competition requires Strands Agents as its foundation and describes
Amazon Bedrock and AgentCore as encouraged rather than required. Bedrock
access here expired mid-build, so the provider became configuration. That
refactor touched the one line in the system that decides which model
answers, which makes it exactly the kind of change that can be wrong
without anything failing.

Two risks are specific to it.

The first is silent drift on the path that already worked. The Bedrock
arguments were assembled at three separate call sites and are now
assembled once. If that single assembly differs from the historical
calls in any way -- a temperature where there was none, a region
alongside a session, a streaming default -- the agent would still run
and would run differently. So the historical argument lists are written
out here as literals and compared exactly. They are the specification;
the adapter has to match them, not the other way round.

The second is substitution. A provider that cannot be built must refuse.
If a missing key quietly fell back to another provider, every run record
and eval score afterwards would name a model that did not answer it, and
the evidence would be false while looking complete. That is the same rule
`app/dispatch/providers.py` applies to sending mail, for the same reason.

No model, no database, no network. Nothing here is constructed against a
provider; the arguments are compared as data, and the refusals are
observed as exceptions.

Usage:

    python tests/model_provider_smoke.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.agent import model_provider  # noqa: E402

FAILURES = []
PASSES = []

MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "us-east-1"
SESSION = object()


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name} FAIL: {detail}")


class StubConfig:
    """Configuration as data, so ambient `.env` cannot decide a result.

    Without this, a key added to `.env` later would silently turn a
    refusal check into a construction attempt, and the suite would stop
    testing what it claims to test.
    """

    def __init__(self, values):
        self.values = values

    def get(self, name, default=""):
        return self.values.get(name, default)


def with_config(values):
    """Swap the adapter's configuration for the duration of one check."""
    original = model_provider.config
    model_provider.config = StubConfig(values)
    return original


def restore(original):
    model_provider.config = original


# ---------------------------------------------------------------------
# The historical argument lists, copied from the three call sites as
# they stood before the adapter existed. These are the specification.
# ---------------------------------------------------------------------

HISTORICAL = (
    (
        "the access check",
        # BedrockModel(model_id=..., boto_session=..., max_tokens=64,
        #              streaming=False)
        {
            "model_id": MODEL_ID,
            "boto_session": SESSION,
            "max_tokens": 64,
            "streaming": False,
        },
        {
            "model_id": MODEL_ID,
            "max_tokens": 64,
            "boto_session": SESSION,
            "temperature": None,
            "region": REGION,
        },
    ),
    (
        "the agent run, with a session",
        {
            "model_id": MODEL_ID,
            "temperature": 0.2,
            "max_tokens": 1024,
            "streaming": False,
            "boto_session": SESSION,
        },
        {
            "model_id": MODEL_ID,
            "max_tokens": 1024,
            "temperature": 0.2,
            "boto_session": SESSION,
            "region": REGION,
        },
    ),
    (
        "the agent run, with no session",
        {
            "model_id": MODEL_ID,
            "temperature": 0.2,
            "max_tokens": 1024,
            "streaming": False,
            "region_name": REGION,
        },
        {
            "model_id": MODEL_ID,
            "max_tokens": 1024,
            "temperature": 0.2,
            "boto_session": None,
            "region": REGION,
        },
    ),
    (
        "the training pipeline",
        {
            "model_id": MODEL_ID,
            "boto_session": SESSION,
            "max_tokens": 2048,
            "temperature": 0.0,
            "streaming": False,
        },
        {
            "model_id": MODEL_ID,
            "max_tokens": 2048,
            "temperature": 0.0,
            "boto_session": SESSION,
            "region": REGION,
        },
    ),
    (
        "the eval graph",
        {
            "model_id": MODEL_ID,
            "boto_session": SESSION,
            "max_tokens": 512,
            "temperature": 0.0,
            "streaming": False,
        },
        {
            "model_id": MODEL_ID,
            "max_tokens": 512,
            "temperature": 0.0,
            "boto_session": SESSION,
            "region": REGION,
        },
    ),
)


def model01_bedrock_is_still_the_default():
    """An unset setting must mean the provider this project always used."""
    original = with_config({})
    try:
        name = model_provider.selected()
    finally:
        restore(original)

    check(
        "MODEL01 AN UNSET PROVIDER IS BEDROCK",
        name == model_provider.BEDROCK,
        f"selected() returned {name!r} with nothing configured",
    )


def model02_historical_arguments_are_reproduced_exactly():
    """Every pre-adapter call must be reproduced argument for argument."""
    wrong = []

    for label, expected, call in HISTORICAL:
        produced = model_provider.bedrock_kwargs(
            call["model_id"],
            call["max_tokens"],
            call["temperature"],
            call["boto_session"],
            call["region"],
        )

        if produced != expected:
            missing = set(expected) - set(produced)
            extra = set(produced) - set(expected)
            changed = {
                k: (expected[k], produced[k])
                for k in set(expected) & set(produced)
                if expected[k] != produced[k]
            }
            wrong.append(
                f"{label}: missing={sorted(missing)} "
                f"extra={sorted(extra)} changed={changed}"
            )

    check(
        "MODEL02 HISTORICAL BEDROCK ARGUMENTS ARE UNCHANGED",
        not wrong,
        "; ".join(wrong),
    )


def model03_a_session_and_a_region_are_never_both_sent():
    """The SDK rejects both together, so the adapter must never pass both."""
    with_session = model_provider.bedrock_kwargs(
        MODEL_ID, 64, None, SESSION, REGION
    )
    without = model_provider.bedrock_kwargs(
        MODEL_ID, 64, None, None, REGION
    )

    check(
        "MODEL03 A SESSION AND A REGION ARE MUTUALLY EXCLUSIVE",
        "region_name" not in with_session
        and "boto_session" not in without
        and without["region_name"] == REGION,
        f"with_session={sorted(with_session)} without={sorted(without)}",
    )


def model04_no_temperature_is_not_a_zero_temperature():
    """The access check passes none, and none must not become a value."""
    kwargs = model_provider.bedrock_kwargs(MODEL_ID, 64, None, SESSION)

    check(
        "MODEL04 AN ABSENT TEMPERATURE STAYS ABSENT",
        "temperature" not in kwargs,
        f"the adapter invented temperature={kwargs.get('temperature')!r}",
    )


def model05_an_unknown_provider_is_refused_by_name():
    """A typo must stop the run, not pick something."""
    try:
        model_provider.selected("anthropci")
    except model_provider.ProviderUnavailable as exc:
        message = str(exc)
    else:
        message = ""

    check(
        "MODEL05 AN UNKNOWN PROVIDER IS REFUSED",
        "anthropci" in message and "bedrock" in message,
        f"message was {message!r}",
    )


def model06_a_missing_key_refuses_rather_than_substituting():
    """Absent credentials must not become a different provider."""
    original = with_config({})
    outcome = None
    try:
        model_provider.build(
            max_tokens=64, provider=model_provider.ANTHROPIC
        )
    except model_provider.ProviderUnavailable as exc:
        outcome = str(exc)
    except Exception as exc:  # noqa: BLE001
        outcome = f"WRONG EXCEPTION {exc.__class__.__name__}"
    finally:
        restore(original)

    check(
        "MODEL06 A MISSING KEY REFUSES AND SUBSTITUTES NOTHING",
        outcome is not None and "ANTHROPIC_API_KEY" in outcome,
        f"outcome was {outcome!r}",
    )


def model07_a_refusal_returns_no_model_at_all():
    """A refused build must not hand back something usable."""
    original = with_config({})
    returned = "nothing returned"
    try:
        returned = model_provider.build(
            max_tokens=64, provider=model_provider.ANTHROPIC
        )
    except model_provider.ProviderUnavailable:
        returned = None
    except Exception:  # noqa: BLE001
        pass
    finally:
        restore(original)

    check(
        "MODEL07 A REFUSAL YIELDS NO MODEL",
        returned is None,
        f"build returned {returned!r} instead of refusing",
    )


def model08_a_bedrock_build_without_a_model_id_refuses():
    """An unresolved model id must not become a guess."""
    original = with_config({})
    outcome = None
    try:
        model_provider.build(
            max_tokens=64, provider=model_provider.BEDROCK
        )
    except model_provider.ProviderUnavailable as exc:
        outcome = str(exc)
    except Exception as exc:  # noqa: BLE001
        outcome = f"WRONG EXCEPTION {exc.__class__.__name__}"
    finally:
        restore(original)

    check(
        "MODEL08 AN UNRESOLVED MODEL ID REFUSES",
        outcome is not None and "BEDROCK_MODEL_ID" in outcome,
        f"outcome was {outcome!r}",
    )


def model09_the_aws_gate_applies_exactly_to_aws():
    """The identity gate must not be skipped for AWS, nor forced on others."""
    original = with_config({})
    try:
        for_bedrock = model_provider.requires_aws(
            model_provider.BEDROCK
        )
        for_anthropic = model_provider.requires_aws(
            model_provider.ANTHROPIC
        )
        by_default = model_provider.requires_aws()
    finally:
        restore(original)

    check(
        "MODEL09 THE AWS IDENTITY GATE APPLIES EXACTLY TO AWS",
        for_bedrock and by_default and not for_anthropic,
        f"bedrock={for_bedrock} default={by_default} "
        f"anthropic={for_anthropic}",
    )


def model10_the_model_id_resolves_without_building_anything():
    """A record of what answered must not require constructing it."""
    original = with_config(
        {
            "BEDROCK_MODEL_ID": MODEL_ID,
            "ANTHROPIC_MODEL_ID": "configured-anthropic-id",
        }
    )
    try:
        bedrock_id = model_provider.resolved_model_id(
            model_provider.BEDROCK
        )
        anthropic_id = model_provider.resolved_model_id(
            model_provider.ANTHROPIC
        )
        explicit = model_provider.resolved_model_id(
            model_provider.BEDROCK, "named-explicitly"
        )
    finally:
        restore(original)

    check(
        "MODEL10 THE MODEL ID RESOLVES WITHOUT A CONSTRUCTION",
        bedrock_id == MODEL_ID
        and anthropic_id == "configured-anthropic-id"
        and explicit == "named-explicitly",
        f"bedrock={bedrock_id!r} anthropic={anthropic_id!r} "
        f"explicit={explicit!r}",
    )


def model11_no_call_site_constructs_a_provider_directly():
    """One assembly point, or the guarantee above is worth nothing.

    MODEL02 proves the adapter reproduces the historical arguments. That
    proves nothing about the running system if a module still builds its
    own model beside it, so the absence of every other construction is
    itself a check.
    """
    allowed = REPO_ROOT / "app/agent/model_provider.py"
    offenders = []

    for folder in ("app", "scripts"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            if path == allowed or "__pycache__" in str(path):
                continue

            text = path.read_text(encoding="utf-8", errors="replace")

            for token in ("BedrockModel(", "AnthropicModel("):
                if token in text:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}: {token}"
                    )

    check(
        "MODEL11 ONLY THE ADAPTER CONSTRUCTS A MODEL",
        not offenders,
        f"constructed elsewhere: {offenders}",
    )


def model12_the_run_record_names_the_provider():
    """A stored run must say which provider answered it."""
    from app.agent import bedrock as bedrock_module

    described = bedrock_module.BedrockStrandsModel(
        model_id=MODEL_ID, region=REGION
    ).describe()

    check(
        "MODEL12 A RUN RECORD NAMES ITS PROVIDER",
        described.get("provider") == model_provider.selected(),
        f"describe() gave provider="
        f"{described.get('provider')!r}, keys={sorted(described)}",
    )


CHECKS = (
    model01_bedrock_is_still_the_default,
    model02_historical_arguments_are_reproduced_exactly,
    model03_a_session_and_a_region_are_never_both_sent,
    model04_no_temperature_is_not_a_zero_temperature,
    model05_an_unknown_provider_is_refused_by_name,
    model06_a_missing_key_refuses_rather_than_substituting,
    model07_a_refusal_returns_no_model_at_all,
    model08_a_bedrock_build_without_a_model_id_refuses,
    model09_the_aws_gate_applies_exactly_to_aws,
    model10_the_model_id_resolves_without_building_anything,
    model11_no_call_site_constructs_a_provider_directly,
    model12_the_run_record_names_the_provider,
)


def main():
    for run in CHECKS:
        run()

    for name in PASSES:
        print(f"PASS  {name}")

    for line in FAILURES:
        print(f"FAIL  {line}")

    print(
        f"\nMODEL PROVIDER: {len(PASSES)} passed, "
        f"{len(FAILURES)} failed"
    )

    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
