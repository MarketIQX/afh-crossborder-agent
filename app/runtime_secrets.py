"""Runtime-only secret hydration for AgentCore.

No secret value belongs in AgentCore environment variables or in the
repository. Local development may still provide POSTGRES_APP_PASSWORD
directly; the deployed runtime resolves it from Secrets Manager using
its execution role.
"""

import json
import os

from app import config


class RuntimeSecretUnavailable(RuntimeError):
    """A secret required by the runtime could not be resolved."""


def hydrate_postgres_app_password():
    """Ensure the least-privileged database password is in-process.

    Returns only the source label, never the secret. The admin and
    reviewer passwords are deliberately not supported by this runtime.
    """
    if (config.get("POSTGRES_APP_PASSWORD", "") or "").strip():
        return "environment"

    secret_arn = (config.get("POSTGRES_APP_SECRET_ARN", "") or "").strip()
    if not secret_arn:
        raise RuntimeSecretUnavailable(
            "POSTGRES_APP_PASSWORD is absent and POSTGRES_APP_SECRET_ARN "
            "is not configured"
        )

    try:
        import boto3

        client = boto3.client(
            "secretsmanager",
            region_name=config.get("AWS_REGION", "us-east-1"),
        )
        response = client.get_secret_value(SecretId=secret_arn)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeSecretUnavailable(
            "Secrets Manager could not provide POSTGRES_APP_PASSWORD: "
            f"{exc.__class__.__name__}"
        ) from exc

    value = response.get("SecretString", "")
    if not isinstance(value, str) or not value:
        raise RuntimeSecretUnavailable(
            "POSTGRES_APP_SECRET_ARN returned no SecretString"
        )

    password = value
    if value.lstrip().startswith("{"):
        try:
            document = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeSecretUnavailable(
                "POSTGRES_APP_SECRET_ARN returned malformed JSON"
            ) from exc

        password = document.get("password", "") if isinstance(document, dict) else ""
        if not isinstance(password, str) or not password:
            raise RuntimeSecretUnavailable(
                "POSTGRES_APP_SECRET_ARN JSON has no non-empty password field"
            )

    os.environ["POSTGRES_APP_PASSWORD"] = password
    return "secretsmanager"
