"""Bicep topology and security contract tests for Azure provisioning."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AZURE = ROOT / "infra/azure"
MODULES = AZURE / "modules"


def _text(name: str) -> str:
    return (MODULES / name).read_text(encoding="utf-8")


def _compile(path: Path) -> dict:
    result = subprocess.run(
        [
            "az",
            "bicep",
            "build",
            "--file",
            str(path),
            "--stdout",
            "--only-show-errors",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_main_and_all_modules_compile_and_lint_offline():
    files = [AZURE / "main.bicep", *sorted(MODULES.glob("*.bicep"))]
    for path in files:
        _compile(path)
        lint = subprocess.run(
            [
                "az",
                "bicep",
                "lint",
                "--file",
                str(path),
                "--only-show-errors",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert lint.returncode == 0, f"{path}: {lint.stderr}"


def test_exact_eight_container_partition_ttl_and_index_contract():
    config = json.loads((AZURE / "config.example.json").read_text())
    actual = {
        item["name"]: (item["partitionKey"], item["ttl"], item["indexing"])
        for item in config["cosmos"]["containers"]
    }
    assert actual == {
        "symbols": ("/symbol", None, "symbols"),
        "telemetry": ("/metric_type", 2592000, "default"),
        "settings": ("/id", None, "default"),
        "dgi_screener": ("/symbol", None, "default"),
        "calendar": ("/symbol", None, "default"),
        "agent_traces": ("/symbol", 7776000, "default"),
        "portfolio": ("/account_id", None, "default"),
        "import_sessions": ("/session_id", -1, "default"),
    }

    cosmos = _text("cosmos.bicep")
    for path in (
        "/symbol/?",
        "/doc_type/?",
        "/timestamp/?",
        "/watchlist/covered_call/?",
        "/watchlist/cash_secured_put/?",
        "/agent_type/?",
        "/activity/?",
    ):
        assert cosmos.count(f"path: '{path}'") == 1
    for path in ("/reason/*", "/raw_response/*", "/analysis_context/*", "/*"):
        assert f"path: '{path}'" in cosmos
    assert "capabilities:" in cosmos and "EnableServerless" in cosmos
    assert "defaultConsistencyLevel: 'Session'" in cosmos
    assert "container.ttl == null" in cosmos
    assert "? {}" in cosmos
    assert "defaultTtl: container.ttl" in cosmos


def test_foundry_children_are_serialized_to_avoid_parent_conflicts():
    foundry = _text("foundry.bicep")
    assert "miniDeployment" in foundry
    assert "lunaDeployment" in foundry
    assert "solDeployment" in foundry
    assert "dependsOn: [\n    project\n  ]" in foundry
    assert "dependsOn: [\n    miniDeployment\n  ]" in foundry
    assert "dependsOn: [\n    lunaDeployment\n  ]" in foundry
    assert "for deployment in deployments" not in foundry


def test_key_consumers_wait_for_resource_producers():
    stack = _text("stack.bicep")
    assert """module environment 'container-apps-environment.bicep' = {""" in stack
    assert "dependsOn: [\n    observability\n  ]" in stack
    assert """module apps 'apps.bicep' = {""" in stack
    assert "dependsOn: [\n    environment\n    cosmos\n    foundry\n  ]" in stack
    assert """module backup 'backup.bicep' = {""" in stack
    assert (
        "dependsOn: [\n    environment\n    cosmos\n    backupIdentity\n  ]"
        in stack
    )


def test_api_is_internal_frontend_requires_entra_and_real_internal_fqdn():
    apps = _text("apps.bicep")
    auth = _text("frontend-auth.bicep")
    assert "external: false" in apps
    assert "external: frontendExternal" in apps
    assert "value: 'https://${api.properties.configuration.ingress.fqdn}'" in apps
    assert "identity: {\n    type: 'None'\n  }" in apps
    assert apps.count("type: 'None'") == 2
    assert "registries:" not in apps
    assert "platform: {\n      enabled: true" in auth
    assert "requireAuthentication: true" in auth
    assert "unauthenticatedClientAction: unauthenticatedClientAction" in auth
    assert "clientSecretSettingName: 'entra-client-secret'" in auth


def test_cosmos_and_foundry_keys_are_secure_and_only_consumed_by_secret_ref():
    stack = _text("stack.bicep")
    apps = _text("apps.bicep")
    backup = _text("backup.bicep")
    main = (AZURE / "main.bicep").read_text(encoding="utf-8")

    assert "listKeys(" in stack
    assert "@secure()\nparam cosmosKey string" in apps
    assert "@secure()\nparam foundryKey string" in apps
    assert "@secure()\nparam cosmosKey string" in backup
    assert (
        "{\n              name: 'COSMOSDB_KEY'\n"
        "              secretRef: 'cosmosdb-key'"
    ) in apps
    assert "{ name: 'COSMOSDB_KEY', secretRef: 'cosmosdb-key' }" in backup
    assert (
        "{\n              name: 'AZURE_OPENAI_API_KEY'\n"
        "              secretRef: 'foundry-api-key'"
    ) in apps
    assert "output cosmosKey" not in stack + main
    assert "output foundryKey" not in stack + main
    assert "primaryMasterKey" not in main
    assert "key1" not in main


def test_public_ghcr_topology_has_no_acr_pull_credentials_or_pull_identity():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [AZURE / "main.bicep", *MODULES.glob("*.bicep")]
    )
    forbidden = (
        "Microsoft.ContainerRegistry",
        "AcrPull",
        "registryPassword",
        "registryUsername",
        "registry-server",
        "registries:",
    )
    assert all(token not in combined for token in forbidden)
    assert combined.count("Microsoft.ManagedIdentity/userAssignedIdentities@") == 1


def test_backup_uami_has_exact_two_container_scoped_roles_and_no_extra_runtime_roles():
    identity = _text("identities-rbac.bicep")
    backup = _text("backup.bicep")
    combined = identity + "\n" + backup
    assert combined.count("Microsoft.Authorization/roleDefinitions@") == 1
    assert combined.count("Microsoft.Authorization/roleAssignments@") == 2
    assert backup.count("scope: backupContainer") == 2
    assert "ba92f5b4-2d11-453d-a403-e96b0029c9fe" in backup
    assert (
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write"
        in identity
    )
    assert "assignableScopes: [\n      resourceGroup().id" in identity
    assert "type: 'UserAssigned'" in backup
    assert "AZURE_CLIENT_ID" in backup
    assert "Owner" not in combined
    assert "AcrPull" not in combined


def test_static_topology_has_expected_resources_diagnostics_locks_and_safe_outputs():
    main = _compile(AZURE / "main.bicep")
    locks = _compile(MODULES / "locks.bicep")
    serialized = json.dumps({"main": main, "locks": locks}, sort_keys=True)
    for resource_type in (
        "Microsoft.OperationalInsights/workspaces",
        "Microsoft.App/managedEnvironments",
        "Microsoft.DocumentDB/databaseAccounts",
        "Microsoft.CognitiveServices/accounts",
        "Microsoft.ManagedIdentity/userAssignedIdentities",
        "Microsoft.App/containerApps",
        "Microsoft.Storage/storageAccounts",
        "Microsoft.App/jobs",
        "Microsoft.Insights/diagnosticSettings",
        "Microsoft.Authorization/locks",
    ):
        assert resource_type in serialized
    assert '"mode": "Incremental"' in serialized
    outputs = main["outputs"]
    assert set(outputs) == {
        "resourceGroupId",
        "apiFqdn",
        "frontendFqdn",
        "foundryEndpoint",
        "backupIdentityClientId",
        "routing",
    }
    assert all(
        token not in json.dumps(outputs).lower()
        for token in ("secret", "password", "token", "apikey", "connectionstring")
    )


def test_diagnostics_use_supported_container_apps_categories():
    diagnostics = _text("diagnostics.bicep")

    def resource_block(name: str) -> str:
        start = diagnostics.index(f"resource {name} ")
        end = diagnostics.find("\nresource ", start + 1)
        return diagnostics[start:] if end == -1 else diagnostics[start:end]

    assert "Microsoft.DocumentDB/databaseAccounts@" in diagnostics
    assert "Microsoft.CognitiveServices/accounts@" in diagnostics
    assert "Microsoft.Storage/storageAccounts/blobServices@" in diagnostics
    assert "Microsoft.App/containerApps@" in diagnostics
    assert "Microsoft.App/jobs@" in diagnostics
    assert diagnostics.count("Microsoft.Insights/diagnosticSettings@") >= 6

    cosmos = resource_block("cosmosDiagnostics")
    assert "category: 'DataPlaneRequests'" in cosmos
    assert "category: 'QueryRuntimeStatistics'" in cosmos
    assert "category: 'ControlPlaneRequests'" in cosmos
    assert "category: 'Requests'" in cosmos

    environment = resource_block("environmentDiagnostics")
    assert "category: 'ContainerAppConsoleLogs'" in environment
    assert "category: 'ContainerAppSystemLogs'" in environment
    assert "category: 'ContainerAppHTTPLogs'" in environment
    assert "category: 'AllMetrics'" in environment

    api = resource_block("apiDiagnostics")
    frontend = resource_block("frontendDiagnostics")
    backup_job = resource_block("backupJobDiagnostics")
    assert "category: 'AllMetrics'" in api
    assert "category: 'AllMetrics'" in frontend
    assert "category: 'Basic'" in backup_job
    assert "logs:" not in api
    assert "logs:" not in frontend
    assert "logs:" not in backup_job

    assert diagnostics.count("category: 'AllMetrics'") == 4
    assert "categoryGroup: 'allLogs'" not in diagnostics
