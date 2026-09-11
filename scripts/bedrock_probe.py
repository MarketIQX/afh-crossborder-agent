"""Record exactly where the Bedrock path stands, in this environment.

"AWS access" is not one fact, and a failure at one stage says nothing
about the stages behind it. This separates them:

1. The interpreter and environment actually running the application.
2. The AWS profile and region, and which credential provider resolved,
   or the exact resolution error.
3. The identity returned through the SAME session that would invoke
   Bedrock. An identity confirmed in another shell or profile does not
   count.
4. The tool surface the Strands agent registers. Registration only. No
   invocation, so this needs no credentials.
5. An access check that registers NO tools. A tool failure can
   therefore never be misreported as an AWS problem, and model access
   can never be hidden behind one.

There is no force flag. The identity gate lives in the adapter and
refuses missing credentials, failed STS, root, a wrong account and a
wrong principal, with no way to override any of them. This probe
cannot invoke anything the application itself would refuse to invoke.

This probe does NOT run a case. Proving access is not proving the
workflow: use scripts/run_case.py for a real persisted run.

No secret values are printed or recorded. Profile names, regions,
provider class names and ARNs are configuration, not secrets, and are
what make a failure diagnosable.

Usage:

    python scripts/bedrock_probe.py
    python scripts/bedrock_probe.py --record
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.agent import bedrock  # noqa: E402

EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"

AWS_ENV_KEYS = (
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "BEDROCK_MODEL_ID",
)


def environment():
    import importlib.metadata as metadata

    packages = {}

    for package in ("strands-agents", "boto3", "botocore", "awscrt"):
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None

    return {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "packages": packages,
        "aws_env_vars_set": {
            key: os.environ.get(key) for key in AWS_ENV_KEYS
        },
    }


def expectations():
    """What the identity gate will require. Never secret."""
    return {
        bedrock.EXPECTED_ACCOUNT_KEY: config.get(
            bedrock.EXPECTED_ACCOUNT_KEY
        ),
        bedrock.EXPECTED_PRINCIPAL_KEY: config.get(
            bedrock.EXPECTED_PRINCIPAL_KEY
        ),
    }


def credentials(session):
    """Which provider resolved, or why none did. No secret values."""
    try:
        creds = session.get_credentials()
    except Exception as exc:  # noqa: BLE001
        return {
            "resolved": False,
            "provider": None,
            "error": f"{exc.__class__.__name__}: {bedrock.scrub(exc)}",
        }

    if creds is None:
        return {
            "resolved": False,
            "provider": None,
            "error": "the credential provider chain returned nothing",
        }

    return {
        "resolved": True,
        "provider": getattr(creds, "method", "unknown"),
        "error": None,
    }


def identity(session):
    """STS through the same session Bedrock would use."""
    try:
        resolved = bedrock.resolve_identity(session)
    except bedrock.IdentityRefused as exc:
        return {"arn": None, "account": None, "error": str(exc)}

    return {
        "arn": resolved["arn"],
        "account": resolved["account"],
        "provider": resolved["provider"],
        "is_root": resolved["arn"].endswith(":root"),
        "error": None,
    }


def main(argv):
    import boto3

    session = boto3.Session()
    model = bedrock.BedrockStrandsModel(boto_session=session)

    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment(),
        "configuration": {
            **model.describe(),
            "session_region": session.region_name,
            "session_profile": session.profile_name,
        },
        "identity_gate_expectations": expectations(),
        "credentials": credentials(session),
        "identity": identity(session),
    }

    try:
        agent = model.build_agent(None)
        record["tool_surface"] = {
            "registered": sorted(agent.tool_names),
            "matches_intended": True,
            "note": "registration only; nothing was invoked",
        }
    except Exception as exc:  # noqa: BLE001
        record["tool_surface"] = {
            "registered": None,
            "matches_intended": False,
            "error": f"{exc.__class__.__name__}: {bedrock.scrub(exc)}",
        }

    try:
        outcome = bedrock.check_access(session)
        record["access_check"] = {
            "succeeded": True,
            "tools_registered": 0,
            "reply": outcome["reply"],
            "identity": outcome["identity"]["arn"],
        }
    except bedrock.IdentityRefused as exc:
        record["access_check"] = {
            "succeeded": False,
            "stage": "identity gate",
            "error": str(exc),
            "note": "no Bedrock call was made",
        }
    except bedrock.BedrockInvocationFailed as exc:
        record["access_check"] = {
            "succeeded": False,
            "stage": "model invocation",
            "error": str(exc),
        }

    print(json.dumps(record, indent=2, default=str))

    if "--record" in argv:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = EVIDENCE_DIR / f"bedrock-probe-{stamp}.json"
        path.write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )
        print(f"\nRECORDED: {path.relative_to(REPO_ROOT)}")

    return 0 if record["access_check"]["succeeded"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
