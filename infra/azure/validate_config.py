#!/usr/bin/env python3
"""Strict, offline validation for the Azure provisioner configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
AI_FUNCTIONS = {
    "monitor_assessment", "monitor_roll", "analysis", "buy_tracker",
    "supervisor", "alpha", "summary", "report", "technical_analysis",
    "plan_monitor", "chat", "symbol_chat", "activity_chat", "dps_insights",
    "api_chat",
}
EXPECTED_CONTAINERS = {
    "symbols": ("/symbol", None, "symbols"),
    "telemetry": ("/metric_type", 2592000, "default"),
    "settings": ("/id", None, "default"),
    "dgi_screener": ("/symbol", None, "default"),
    "calendar": ("/symbol", None, "default"),
    "agent_traces": ("/symbol", 7776000, "default"),
    "portfolio": ("/account_id", None, "default"),
    "import_sessions": ("/session_id", -1, "default"),
}
EXPECTED_MODELS = {
    "gpt-5.4-mini": ("gpt-5.4-mini", "2026-03-17", "GlobalStandard"),
    "gpt-5.6-luna": ("gpt-5.6-luna", "2026-07-09", "GlobalStandard"),
    "gpt-5.6-sol": ("gpt-5.6-sol", "2026-07-09", "GlobalStandard"),
}
EXPECTED_PROVIDERS = {
    "Microsoft.App", "Microsoft.DocumentDB", "Microsoft.CognitiveServices",
    "Microsoft.Storage", "Microsoft.ManagedIdentity",
    "Microsoft.OperationalInsights", "Microsoft.Insights",
    "Microsoft.Authorization",
}
TOP_KEYS = {
    "$schema", "schemaVersion", "subscriptionId", "tenantId", "location",
    "resourceGroupName", "suffix", "names", "tags", "images", "models",
    "cosmos", "apps", "backup", "frontendAuth", "optionalSecrets",
    "diagnostics", "locks", "providers",
}
SECRET_KEY = re.compile(
    r"(secret|password|token|api.?key|connection.?string)", re.IGNORECASE
)
IMAGE_RE = re.compile(
    r"^ghcr\.io/[a-z0-9._-]+/[a-z0-9._/-]+"
    r"(?::sha-[0-9a-fA-F]{7,40}|@sha256:[0-9a-fA-F]{64})$"
)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ConfigError(ValueError):
    pass


def fail(message: str) -> None:
    raise ConfigError(message)


def exact_keys(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{path} must be an object")
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing:
        fail(f"{path} is missing: {', '.join(sorted(missing))}")
    if extra:
        fail(f"{path} has unknown keys: {', '.join(sorted(extra))}")
    return value


def reject_secret_properties(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if (
                SECRET_KEY.search(key)
                and key != "optionalSecrets"
                and not key.endswith("EnvironmentVariable")
            ):
                fail(f"{child_path}: secret-bearing properties are forbidden")
            reject_secret_properties(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_properties(child, f"{path}[{index}]")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read JSON config: {exc}")
    if not isinstance(value, dict):
        fail("configuration root must be an object")
    return value


def validate_backend_routing(config: dict[str, Any]) -> None:
    backend = yaml.safe_load((ROOT / "backend/config.yaml").read_text(encoding="utf-8"))
    ai = backend.get("ai") or {}
    actual_default = ai.get("model_deployment")
    if actual_default != "${MODEL_DEPLOYMENT}":
        fail("backend/config.yaml ai.model_deployment must remain ${MODEL_DEPLOYMENT}")
    actual_roles = ai.get("models") or {}
    desired = config["models"]["routing"]
    for role in AI_FUNCTIONS:
        expected = desired["roles"][role]
        actual = actual_roles.get(role, desired["default"])
        if actual != expected:
            fail(
                f"models.routing.roles.{role}={expected!r} diverges from "
                f"backend/config.yaml resolved value {actual!r}"
            )


def validate(config: dict[str, Any], check_backend: bool = True) -> None:
    allowed_top = TOP_KEYS
    missing = allowed_top - {"$schema"} - config.keys()
    extra = config.keys() - allowed_top
    if missing:
        fail(f"configuration is missing: {', '.join(sorted(missing))}")
    if extra:
        fail(f"configuration has unknown keys: {', '.join(sorted(extra))}")
    reject_secret_properties(config)
    if config["schemaVersion"] != 1:
        fail("schemaVersion must be 1")
    for key in ("subscriptionId", "tenantId"):
        if not isinstance(config[key], str) or not UUID_RE.fullmatch(config[key]):
            fail(f"{key} must be a UUID")
    if not re.fullmatch(r"[a-z0-9]+", config["location"]):
        fail("location must use the Azure lowercase canonical form")
    if not re.fullmatch(r"[a-z0-9]{4,12}", config["suffix"]):
        fail("suffix must be 4-12 lowercase letters or digits")

    names = exact_keys(config["names"], {
        "logAnalytics", "containerAppsEnvironment", "apiApp", "frontendApp", "mcpApp",
        "cosmosAccount", "foundryAccount", "foundryProject", "backupStorage",
        "backupContainer", "backupIdentity", "backupJob",
    }, "names")
    if not re.fullmatch(r"[a-z0-9]{3,24}", names["backupStorage"]):
        fail("names.backupStorage must be a valid globally scoped Storage name")
    if (
        config["suffix"] not in names["backupStorage"]
        or config["suffix"] not in names["foundryAccount"]
    ):
        fail("suffix must be embedded in globally scoped Foundry and Storage names")

    exact_keys(config["images"], {"api", "frontend"}, "images")
    for key, image in config["images"].items():
        if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
            fail(f"images.{key} must be a public GHCR immutable sha tag or digest")

    models = exact_keys(config["models"], {"deployments", "routing"}, "models")
    if not isinstance(models["deployments"], list) or len(models["deployments"]) != 3:
        fail("models.deployments must contain exactly three entries")
    deployments: dict[str, dict[str, Any]] = {}
    for index, deployment in enumerate(models["deployments"]):
        exact_keys(
            deployment,
            {"deploymentName", "model", "version", "sku", "capacity"},
            f"models.deployments[{index}]",
        )
        name = deployment["deploymentName"]
        if name in deployments:
            fail(f"duplicate deploymentName: {name}")
        deployments[name] = deployment
        if not isinstance(deployment["capacity"], int) or deployment["capacity"] < 1:
            fail(f"models.deployments[{index}].capacity must be a positive integer")
    if set(deployments) != set(EXPECTED_MODELS):
        fail("models.deployments must be exactly mini, luna, and sol")
    for name, expected in EXPECTED_MODELS.items():
        item = deployments[name]
        actual = (item["model"], item["version"], item["sku"])
        if actual != expected:
            fail(f"{name} must use model/version/SKU {expected}")

    routing = exact_keys(models["routing"], {"default", "roles"}, "models.routing")
    roles = exact_keys(routing["roles"], AI_FUNCTIONS, "models.routing.roles")
    declared = set(deployments)
    if routing["default"] not in declared:
        fail("models.routing.default references an undeclared deployment")
    invalid = {role: model for role, model in roles.items() if model not in declared}
    if invalid:
        fail(f"routing references undeclared deployments: {invalid}")
    for role in ("monitor_assessment", "monitor_roll", "analysis"):
        if roles[role] != "gpt-5.6-luna":
            fail(f"{role} must route to gpt-5.6-luna")

    cosmos = exact_keys(config["cosmos"], {"databaseName", "containers"}, "cosmos")
    if cosmos["databaseName"] != "stock-options-manager":
        fail("cosmos.databaseName must be stock-options-manager")
    if not isinstance(cosmos["containers"], list) or len(cosmos["containers"]) != 8:
        fail("cosmos.containers must contain exactly eight entries")
    actual_containers: dict[str, tuple[Any, Any, Any]] = {}
    for index, container in enumerate(cosmos["containers"]):
        exact_keys(
            container,
            {"name", "partitionKey", "ttl", "indexing"},
            f"cosmos.containers[{index}]",
        )
        if container["name"] in actual_containers:
            fail(f"duplicate Cosmos container: {container['name']}")
        actual_containers[container["name"]] = (
            container["partitionKey"], container["ttl"], container["indexing"]
        )
    if actual_containers != EXPECTED_CONTAINERS:
        fail(
            "Cosmos containers, partition keys, TTLs, or indexing profiles "
            "differ from the contract"
        )

    apps = exact_keys(config["apps"], {"api", "frontend", "mcp"}, "apps")
    api = exact_keys(
        apps["api"],
        {"ingress", "minReplicas", "maxReplicas", "cpu", "memory", "healthPath"},
        "apps.api",
    )
    ingress = exact_keys(api["ingress"], {"external", "targetPort"}, "apps.api.ingress")
    if ingress != {"external": False, "targetPort": 8000}:
        fail("API ingress must be internal on port 8000")
    if api["minReplicas"] != 1 or api["maxReplicas"] != 1:
        fail("API scheduler owner must run at exactly one replica")
    if api["healthPath"] != "/healthz":
        fail("API healthPath must be /healthz")
    frontend = exact_keys(
        apps["frontend"],
        {"targetPort", "minReplicas", "maxReplicas", "cpu", "memory"},
        "apps.frontend",
    )
    if (
        frontend["targetPort"] != 3000
        or frontend["minReplicas"] < 1
        or frontend["maxReplicas"] < frontend["minReplicas"]
    ):
        fail("frontend port/replica limits are invalid")
    mcp = exact_keys(
        apps["mcp"],
        {"ingress", "minReplicas", "maxReplicas", "cpu", "memory"},
        "apps.mcp",
    )
    mcp_ingress = exact_keys(mcp["ingress"], {"external", "targetPort"}, "apps.mcp.ingress")
    if mcp_ingress != {"external": False, "targetPort": 8001}:
        fail("MCP ingress must be internal on port 8001")
    if mcp["minReplicas"] < 1 or mcp["maxReplicas"] < mcp["minReplicas"]:
        fail("MCP port/replica limits are invalid")

    backup = exact_keys(config["backup"], {
        "schedule", "timezone", "localTime", "dailyRetentionDays",
        "monthlyAnchorRetentionDays", "runRetentionDays", "softDeleteDays",
    }, "backup")
    if backup["schedule"] != "15 23 * * *":
        fail("backup.schedule must be 15 23 * * *")
    if not isinstance(backup["timezone"], str) or not backup["timezone"].strip():
        fail("backup.timezone must be a non-empty IANA timezone")
    if not isinstance(backup["localTime"], str) or not re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", backup["localTime"]
    ):
        fail("backup.localTime must be a valid 24-hour HH:MM value")
    for key in ("dailyRetentionDays", "runRetentionDays"):
        if (
            not isinstance(backup[key], int)
            or isinstance(backup[key], bool)
            or backup[key] < 1
        ):
            fail(f"backup.{key} must be a positive integer")
    if (
        not isinstance(backup["monthlyAnchorRetentionDays"], int)
        or isinstance(backup["monthlyAnchorRetentionDays"], bool)
        or backup["monthlyAnchorRetentionDays"] < 365
    ):
        fail("backup.monthlyAnchorRetentionDays must be at least 365")
    if (
        not isinstance(backup["softDeleteDays"], int)
        or isinstance(backup["softDeleteDays"], bool)
        or not 1 <= backup["softDeleteDays"] <= 365
    ):
        fail("backup.softDeleteDays must be between 1 and 365")

    auth = exact_keys(
        config["frontendAuth"],
        {
            "enabled",
            "unauthenticatedClientAction",
            "clientIdEnvironmentVariable",
            "clientSecretEnvironmentVariable",
        },
        "frontendAuth",
    )
    if (
        auth["enabled"] is not True
        or auth["unauthenticatedClientAction"] != "RedirectToLoginPage"
    ):
        fail("frontend Microsoft Entra authentication is mandatory and fail-closed")
    for key in ("clientIdEnvironmentVariable", "clientSecretEnvironmentVariable"):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", auth[key]):
            fail(f"frontendAuth.{key} must name an environment variable")

    optional_secrets = exact_keys(
        config["optionalSecrets"],
        {
            "telegramBotTokenEnvironmentVariable",
            "telegramChatIdEnvironmentVariable",
        },
        "optionalSecrets",
    )
    for key, value in optional_secrets.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", value):
            fail(f"optionalSecrets.{key} must name an environment variable")
    exact_keys(config["diagnostics"], {"enabled"}, "diagnostics")
    exact_keys(config["locks"], {"enabled"}, "locks")
    if (
        config["diagnostics"]["enabled"] is not True
        or config["locks"]["enabled"] is not True
    ):
        fail("diagnostics and delete locks are mandatory")
    if set(config["providers"]) != EXPECTED_PROVIDERS:
        fail("providers must exactly match the accepted provider set")
    if check_backend:
        validate_backend_routing(config)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--canonical", action="store_true")
    parser.add_argument("--no-backend-check", action="store_true")
    args = parser.parse_args()
    try:
        config = load_json(args.config)
        validate(config, check_backend=not args.no_backend_check)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.canonical:
        print(json.dumps(config, sort_keys=True, separators=(",", ":")))
    else:
        print("Configuration valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
