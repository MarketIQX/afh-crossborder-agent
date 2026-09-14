"""RSEC01-RSEC06. AgentCore secrets never ride in runtime env config."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import runtime_secrets  # noqa: E402

PASSES = []
FAILURES = []


def check(name, condition, detail=""):
    (PASSES if condition else FAILURES).append(
        name if condition else f"{name} FAIL: {detail}"
    )


class StubConfig:
    def __init__(self, values):
        self.values = values

    def get(self, name, default=""):
        return self.values.get(name, default)


def swap_config(values):
    original = runtime_secrets.config
    runtime_secrets.config = StubConfig(values)
    return original


def restore_config(original):
    runtime_secrets.config = original


def rsec01_direct_password_wins():
    original = swap_config({"POSTGRES_APP_PASSWORD": "local-only"})
    try:
        source = runtime_secrets.hydrate_postgres_app_password()
    finally:
        restore_config(original)

    check(
        "RSEC01 DIRECT PASSWORD WINS",
        source == "environment",
        f"source={source!r}",
    )


def rsec02_missing_sources_refuse():
    original = swap_config({})
    try:
        try:
            runtime_secrets.hydrate_postgres_app_password()
        except runtime_secrets.RuntimeSecretUnavailable as exc:
            message = str(exc)
        else:
            message = ""
    finally:
        restore_config(original)

    check(
        "RSEC02 MISSING SOURCES REFUSE",
        "POSTGRES_APP_SECRET_ARN" in message,
        f"message={message!r}",
    )


def rsec03_secret_manager_hydrates_only_app_password():
    calls = {}

    class Client:
        def get_secret_value(self, **kwargs):
            calls.update(kwargs)
            return {"SecretString": "runtime-db-secret"}

    class FakeBoto3:
        @staticmethod
        def client(name, region_name=None):
            calls["service"] = name
            calls["region"] = region_name
            return Client()

    original_module = sys.modules.get("boto3")
    original_config = swap_config(
        {
            "POSTGRES_APP_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret:x",
            "AWS_REGION": "us-east-1",
        }
    )
    saved = os.environ.pop("POSTGRES_APP_PASSWORD", None)
    sys.modules["boto3"] = FakeBoto3

    try:
        source = runtime_secrets.hydrate_postgres_app_password()
        hydrated = os.environ.get("POSTGRES_APP_PASSWORD")
    finally:
        restore_config(original_config)
        if original_module is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = original_module
        if saved is None:
            os.environ.pop("POSTGRES_APP_PASSWORD", None)
        else:
            os.environ["POSTGRES_APP_PASSWORD"] = saved

    check(
        "RSEC03 SECRETS MANAGER HYDRATES APP PASSWORD",
        source == "secretsmanager"
        and hydrated == "runtime-db-secret"
        and calls.get("service") == "secretsmanager"
        and calls.get("SecretId", "").endswith(":secret:x"),
        f"source={source!r} calls={calls!r}",
    )


def rsec04_secret_errors_do_not_leak_values():
    class Client:
        def get_secret_value(self, **kwargs):
            raise RuntimeError("do-not-leak-this-value")

    class FakeBoto3:
        @staticmethod
        def client(name, region_name=None):
            return Client()

    original_module = sys.modules.get("boto3")
    original_config = swap_config(
        {"POSTGRES_APP_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret:x"}
    )
    sys.modules["boto3"] = FakeBoto3

    try:
        try:
            runtime_secrets.hydrate_postgres_app_password()
        except runtime_secrets.RuntimeSecretUnavailable as exc:
            message = str(exc)
        else:
            message = ""
    finally:
        restore_config(original_config)
        if original_module is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = original_module

    check(
        "RSEC04 SECRET ERRORS DO NOT LEAK VALUES",
        "RuntimeError" in message and "do-not-leak-this-value" not in message,
        f"message={message!r}",
    )



def rsec05_json_secret_hydrates_password_field():
    class Client:
        def get_secret_value(self, **kwargs):
            return {"SecretString": '{"username":"agents_app","password":"json-db-secret"}'}

    class FakeBoto3:
        @staticmethod
        def client(name, region_name=None):
            return Client()

    original_module = sys.modules.get("boto3")
    original_config = swap_config(
        {"POSTGRES_APP_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret:x"}
    )
    saved = os.environ.pop("POSTGRES_APP_PASSWORD", None)
    sys.modules["boto3"] = FakeBoto3
    try:
        source = runtime_secrets.hydrate_postgres_app_password()
        hydrated = os.environ.get("POSTGRES_APP_PASSWORD")
    finally:
        restore_config(original_config)
        if original_module is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = original_module
        if saved is None:
            os.environ.pop("POSTGRES_APP_PASSWORD", None)
        else:
            os.environ["POSTGRES_APP_PASSWORD"] = saved

    check(
        "RSEC05 JSON SECRET HYDRATES PASSWORD FIELD",
        source == "secretsmanager" and hydrated == "json-db-secret",
        f"source={source!r} hydrated_match={hydrated == 'json-db-secret'}",
    )


def rsec06_bad_json_shape_refuses_without_leak():
    secret_value = '{"username":"agents_app","token":"do-not-leak-json-token"}'

    class Client:
        def get_secret_value(self, **kwargs):
            return {"SecretString": secret_value}

    class FakeBoto3:
        @staticmethod
        def client(name, region_name=None):
            return Client()

    original_module = sys.modules.get("boto3")
    original_config = swap_config(
        {"POSTGRES_APP_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:1:secret:x"}
    )
    sys.modules["boto3"] = FakeBoto3
    try:
        try:
            runtime_secrets.hydrate_postgres_app_password()
        except runtime_secrets.RuntimeSecretUnavailable as exc:
            message = str(exc)
        else:
            message = ""
    finally:
        restore_config(original_config)
        if original_module is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = original_module

    check(
        "RSEC06 BAD JSON SHAPE REFUSES WITHOUT LEAK",
        "password field" in message and "do-not-leak-json-token" not in message,
        f"message={message!r}",
    )

def main():
    for run in (
        rsec01_direct_password_wins,
        rsec02_missing_sources_refuse,
        rsec03_secret_manager_hydrates_only_app_password,
        rsec04_secret_errors_do_not_leak_values,
        rsec05_json_secret_hydrates_password_field,
        rsec06_bad_json_shape_refuses_without_leak,
    ):
        run()

    for name in PASSES:
        print(f"PASS  {name}")
    for line in FAILURES:
        print(f"FAIL  {line}")
    print(f"\nRUNTIME SECRETS: {len(PASSES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
