"""Offline contract tests for the self-contained Azure provisioner config."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from src.ai_functions import AI_FUNCTIONS

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "infra/azure/config.example.json"
SCHEMA_PATH = ROOT / "infra/azure/schema/provision-config.schema.json"
VALIDATOR_PATH = ROOT / "infra/azure/validate_config.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "azure_validate_config", VALIDATOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator()


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_example_passes_draft_2020_schema_and_runtime_validator():
    config = _config()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    ).validate(config)
    VALIDATOR.validate(config)


@pytest.mark.parametrize(
    ("path", "key", "value"),
    [
        ((), "unexpected", True),
        (("names",), "unexpected", "value"),
        (("models", "routing"), "unexpected", "value"),
        (("backup",), "unexpected", "value"),
        (("frontendAuth",), "unexpected", "value"),
    ],
)
def test_runtime_validator_rejects_unknown_keys_at_every_closed_level(
    path, key, value
):
    config = _config()
    target = config
    for part in path:
        target = target[part]
    target[key] = value
    with pytest.raises(VALIDATOR.ConfigError, match="unknown"):
        VALIDATOR.validate(config, check_backend=False)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("backup", "schedule"), "0 0 * * *"),
        (("backup", "localTime"), "29:99"),
        (("backup", "softDeleteDays"), 0),
        (("apps", "api", "ingress", "external"), True),
        (("frontendAuth", "enabled"), False),
        (
            ("frontendAuth", "unauthenticatedClientAction"),
            "AllowAnonymous",
        ),
    ],
)
def test_runtime_validator_enforces_schema_security_invariants(path, value):
    config = _config()
    target = config
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(VALIDATOR.ConfigError):
        VALIDATOR.validate(config, check_backend=False)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("telegramBotTokenEnvironmentVariable", "123456:secret-value"),
        ("telegramChatIdEnvironmentVariable", "123456789"),
    ],
)
def test_runtime_validator_rejects_optional_secret_values(key, value):
    config = _config()
    config["optionalSecrets"][key] = value

    with pytest.raises(VALIDATOR.ConfigError, match="environment variable"):
        VALIDATOR.validate(config, check_backend=False)


@pytest.mark.parametrize(
    "secret_property",
    ["secret", "password", "token", "apiKey", "connectionString"],
)
def test_secret_bearing_properties_are_rejected_recursively(secret_property):
    config = _config()
    config["tags"][secret_property] = "SENTINEL-DO-NOT-STORE"
    with pytest.raises(VALIDATOR.ConfigError, match="secret-bearing"):
        VALIDATOR.validate(config, check_backend=False)


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/dsanchor/api:latest",
        "ghcr.io/dsanchor/api:main",
        "ghcr.io/dsanchor/api:v1.2.3",
        "myregistry.azurecr.io/api:sha-abcdef0",
        "ghcr.io/dsanchor/api:sha-abcdef",
        "ghcr.io/dsanchor/api@sha256:abc",
    ],
)
def test_mutable_or_non_ghcr_images_are_rejected(image):
    config = _config()
    config["images"]["api"] = image
    with pytest.raises(VALIDATOR.ConfigError, match="public GHCR immutable"):
        VALIDATOR.validate(config, check_backend=False)


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/dsanchor/api:sha-abcdef0",
        "ghcr.io/dsanchor/api:sha-" + "a" * 40,
        "ghcr.io/dsanchor/api@sha256:" + "b" * 64,
    ],
)
def test_sha_tag_and_digest_ghcr_images_are_accepted(image):
    config = _config()
    config["images"]["api"] = image
    VALIDATOR.validate(config, check_backend=False)


def test_model_set_and_all_runtime_functions_resolve_to_declared_deployments():
    config = _config()
    deployments = {
        item["deploymentName"]: (item["model"], item["version"], item["sku"])
        for item in config["models"]["deployments"]
    }
    assert deployments == VALIDATOR.EXPECTED_MODELS

    routing = config["models"]["routing"]
    assert set(routing["roles"]) == set(AI_FUNCTIONS)
    assert len(deployments) == 3 < len(AI_FUNCTIONS)
    assert all(
        routing["roles"].get(function_id, routing["default"]) in deployments
        for function_id in AI_FUNCTIONS
    )
    assert {
        role: routing["roles"][role]
        for role in ("monitor_assessment", "monitor_roll", "analysis")
    } == {
        "monitor_assessment": "gpt-5.6-luna",
        "monitor_roll": "gpt-5.6-luna",
        "analysis": "gpt-5.6-luna",
    }


def test_backend_versioned_routing_matches_infrastructure_routing():
    config = _config()
    backend = yaml.safe_load((ROOT / "backend/config.yaml").read_text())
    assert backend["ai"]["model_deployment"] == "${MODEL_DEPLOYMENT}"
    for function_id in AI_FUNCTIONS:
        assert backend["ai"]["models"].get(
            function_id, config["models"]["routing"]["default"]
        ) == config["models"]["routing"]["roles"][function_id]


def test_duplicate_or_undeclared_model_routing_fails_closed():
    duplicate = _config()
    duplicate["models"]["deployments"][1] = copy.deepcopy(
        duplicate["models"]["deployments"][0]
    )
    with pytest.raises(VALIDATOR.ConfigError, match="duplicate deploymentName"):
        VALIDATOR.validate(duplicate, check_backend=False)

    undeclared = _config()
    undeclared["models"]["routing"]["roles"]["analysis"] = "undeclared"
    with pytest.raises(VALIDATOR.ConfigError, match="undeclared"):
        VALIDATOR.validate(undeclared, check_backend=False)
