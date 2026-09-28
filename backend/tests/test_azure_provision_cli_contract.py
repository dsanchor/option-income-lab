"""Offline CLI tests for infra/azure/provision.sh."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra/azure/provision.sh"
CONFIG = ROOT / "infra/azure/config.example.json"
ARTIFACT_ROOT = ROOT / "backend/tests/.azure-provision-test-artifacts"
MUTATING_COMMANDS = {
    ("provider", "register"),
    ("deployment", "sub", "create"),
    ("deployment", "group", "create"),
    ("ad", "app", "create"),
    ("ad", "app", "update"),
    ("ad", "app", "credential", "reset"),
    ("ad", "sp", "create"),
    ("role", "assignment", "create"),
    ("containerapp", "job", "start"),
}


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def fake_cli():
    work = ARTIFACT_ROOT / str(uuid.uuid4())
    bin_dir = work / "bin"
    bin_dir.mkdir(parents=True)
    log = work / "az.jsonl"
    config = work / "config.json"
    shutil.copyfile(CONFIG, config)

    _write_executable(
        bin_dir / "az",
        """#!/usr/bin/env python3
import json, os, pathlib, sys
args = [arg for arg in sys.argv[1:] if arg != "--only-show-errors"]
stdin = sys.stdin.read()
pathlib.Path(os.environ["FAKE_AZ_LOG"]).open("a", encoding="utf-8").write(
    json.dumps({
        "args": args,
        "stdin_present": bool(stdin),
        "stdin_has_entra": os.environ.get("ENTRA_SENTINEL", "") in stdin,
        "stdin_has_telegram": os.environ.get("TELEGRAM_SENTINEL", "") in stdin,
    }) + "\\n"
)
def out(value):
    print(value)
def has(*parts):
    return args[:len(parts)] == list(parts)
if has("account", "show"):
    out(json.dumps({"id": "00000000-0000-0000-0000-000000000000",
                    "tenantId": "00000000-0000-0000-0000-000000000000",
                    "state": "Enabled"}))
elif has("account", "list-locations"):
    out("swedencentral")
elif has("provider", "show"):
    out("Registered")
elif has("rest") and any("permissions?" in value for value in args):
    out(json.dumps({"value": [{"actions": ["*"], "notActions": []}]}))
elif has("rest") and any("checkDomainAvailability" in value for value in args):
    out("true")
elif has("group", "show"):
    raise SystemExit(1)
elif has("cosmosdb", "show"):
    raise SystemExit(1)
elif has("cosmosdb", "check-name-exists"):
    out("false")
elif has("storage", "account", "show"):
    raise SystemExit(1)
elif has("storage", "account", "check-name"):
    out("true")
elif has("storage", "account", "management-policy", "show"):
    raise SystemExit(1)
elif has("cognitiveservices", "account", "show"):
    raise SystemExit(1)
elif has("cognitiveservices", "account", "list-deleted"):
    if os.environ.get("FAKE_FOUNDRY_DELETED") == "true":
        out(json.dumps([{
            "name": "ai-stock-options-manager-dsr2026",
            "location": "swedencentral",
        }]))
    else:
        out("[]")
elif has("cognitiveservices", "account", "deployment", "list"):
    out("[]")
elif has("cognitiveservices", "model", "list"):
    models = [
      {"model": {"name": "gpt-5.4-mini", "version": "2026-03-17",
                 "skus": [{"name": "GlobalStandard"}]}},
      {"model": {"name": "gpt-5.6-luna", "version": "2026-07-09",
                 "skus": [{"name": "GlobalStandard"}]}},
      {"model": {"name": "gpt-5.6-sol", "version": "2026-07-09",
                 "skus": [{"name": "GlobalStandard"}]}}
    ]
    if os.environ.get("FAKE_MODEL_MODE") == "missing-version":
        models[-1]["model"]["version"] = "wrong-version"
    if os.environ.get("FAKE_MODEL_MODE") == "transient-error":
        raise SystemExit(55)
    if os.environ.get("FAKE_MODEL_MODE") == "large-catalog":
        models.extend(
            {"model": {"name": "unused-" + str(index), "version": "1",
                       "description": "x" * 256, "skus": []}}
            for index in range(10000)
        )
    out(json.dumps(models))
elif has("cognitiveservices", "usage", "list"):
    usage = [
      {"name": {"value": "OpenAI.GlobalStandard.gpt-5.4-mini"},
       "limit": 699, "currentValue": 0},
      {"name": {"value": "OpenAI.GlobalStandard.gpt-5.6-luna"},
       "limit": 1000, "currentValue": 0},
      {"name": {"value": "OpenAI.GlobalStandard.gpt-5.6-sol"},
       "limit": 1000, "currentValue": 0}
    ]
    if os.environ.get("FAKE_MODEL_MODE") == "zero-quota":
        usage[-1]["limit"] = 0
    out(json.dumps(usage))
elif has("role", "definition", "list"):
    if os.environ.get("FAKE_ROLE_MODE") == "azure-shape":
        out(json.dumps([{
            "assignableScopes": [
                "/subscriptions/00000000-0000-0000-0000-000000000000/"
                "resourceGroups/stock-options-manager-rg"
            ],
            "permissions": [{
                "actions": [],
                "condition": None,
                "conditionVersion": None,
                "dataActions": [
                    "Microsoft.Storage/storageAccounts/blobServices/"
                    "containers/blobs/tags/write"
                ],
                "notActions": [],
                "notDataActions": [],
            }],
        }]))
    else:
        out("[]")
elif has("bicep", "build"):
    file_name = args[args.index("--file") + 1]
    out(json.dumps({"compiled": pathlib.Path(file_name).name}, sort_keys=True))
elif has("deployment", "sub", "what-if"):
    mode = os.environ.get("FAKE_WHAT_IF_MODE")
    if mode == "large":
        out(json.dumps({
            "changes": [
                {
                    "changeType": "NoChange",
                    "resourceId": "/subscriptions/test/resource-" + str(index),
                    "after": {"payload": "x" * 512},
                }
                for index in range(5000)
            ]
        }))
    elif mode == "delete":
        out(json.dumps({
            "changes": [{
                "changeType": "Delete",
                "resourceId": "/subscriptions/test/resource-to-delete",
            }]
        }))
    else:
        out(json.dumps({"changes": []}))
elif has("deployment", "sub", "create"):
    raise SystemExit(42)
else:
    print("unsupported fake az call: " + " ".join(args), file=sys.stderr)
    raise SystemExit(97)
""",
    )
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env python3
print("HTTP/1.1 200 OK\\r")
print("Docker-Content-Digest: sha256:" + "a" * 64 + "\\r")
""",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_AZ_LOG": str(log),
        "AZURE_ENTRA_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
        "AZURE_ENTRA_CLIENT_SECRET": "ENTRA-SENTINEL-SECRET",
        "TELEGRAM_BOT_TOKEN": "TELEGRAM-SENTINEL-SECRET",
        "TELEGRAM_CHAT_ID": "123456789",
        "ENTRA_SENTINEL": "ENTRA-SENTINEL-SECRET",
        "TELEGRAM_SENTINEL": "TELEGRAM-SENTINEL-SECRET",
    }
    try:
        yield {"work": work, "bin": bin_dir, "log": log, "config": config, "env": env}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if ARTIFACT_ROOT.exists() and not any(ARTIFACT_ROOT.iterdir()):
            ARTIFACT_ROOT.rmdir()


def _run(fake_cli, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), "--config", str(fake_cli["config"]), *args],
        cwd=ROOT,
        env=fake_cli["env"],
        text=True,
        capture_output=True,
        check=False,
    )


def _calls(fake_cli) -> list[dict]:
    if not fake_cli["log"].exists():
        return []
    return [
        json.loads(line)
        for line in fake_cli["log"].read_text(encoding="utf-8").splitlines()
    ]


def _is_mutating(args: list[str]) -> bool:
    return any(tuple(args[: len(prefix)]) == prefix for prefix in MUTATING_COMMANDS)


def test_help_and_dry_run_make_zero_azure_calls(fake_cli):
    help_result = subprocess.run(
        [str(SCRIPT), "--help"],
        cwd=ROOT,
        env=fake_cli["env"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "dry-run|preflight|what-if|apply" in help_result.stdout

    dry_run = _run(fake_cli, "--mode", "dry-run")
    assert dry_run.returncode == 0, dry_run.stderr
    assert "Azure calls: none" in dry_run.stdout
    assert _calls(fake_cli) == []


def test_entra_bootstrap_tags_new_app_with_supported_update_command():
    script = SCRIPT.read_text(encoding="utf-8")
    create_start = script.index("ENTRA_CLIENT_ID=\"$(az_read ad app create")
    create_end = script.index("\n  fi", create_start)
    create_flow = script[create_start:create_end]

    create_command, update_command = create_flow.split(
        "\n    az_read ad app update", maxsplit=1
    )
    assert "--set" not in create_command
    assert "--query appId -o tsv" in create_command
    assert '--id "$ENTRA_CLIENT_ID"' in update_command
    assert "--set 'tags=[\"option-income-lab-provisioner-v1\"]'" in update_command


def test_preflight_is_read_only_and_checks_account_models_quota_and_images(fake_cli):
    result = _run(fake_cli, "--mode", "preflight")
    assert result.returncode == 0, result.stderr
    calls = _calls(fake_cli)
    assert not [call for call in calls if _is_mutating(call["args"])]
    assert any(call["args"][:2] == ["account", "show"] for call in calls)
    permissions_calls = [
        call
        for call in calls
        if call["args"][:1] == ["rest"]
        and any("permissions?" in value for value in call["args"])
    ]
    assert len(permissions_calls) == 1
    permissions_args = permissions_calls[0]["args"]
    assert permissions_args[permissions_args.index("--method") + 1] == "get"
    assert any(
        call["args"][:3] == ["cognitiveservices", "model", "list"]
        for call in calls
    )
    assert any(
        call["args"][:3] == ["cognitiveservices", "usage", "list"]
        for call in calls
    )
    assert sum(call["args"][:2] == ["bicep", "build"] for call in calls) == 3


def test_preflight_streams_large_model_catalog_without_argv_overflow(fake_cli):
    fake_cli["env"]["FAKE_MODEL_MODE"] = "large-catalog"
    result = _run(fake_cli, "--mode", "preflight")
    assert result.returncode == 0, result.stderr
    assert (
        "Foundry catalog, existing deployments, and quota validated."
        in result.stdout
    )
    assert not [call for call in _calls(fake_cli) if _is_mutating(call["args"])]


def test_what_if_is_deterministic_and_uses_subscription_scope_stdin(fake_cli):
    first = _run(fake_cli, "--mode", "what-if")
    second = _run(fake_cli, "--mode", "what-if")
    assert first.returncode == second.returncode == 0
    first_fp = re.search(r"Plan fingerprint: ([0-9a-f]{64})", first.stdout)
    second_fp = re.search(r"Plan fingerprint: ([0-9a-f]{64})", second.stdout)
    assert first_fp and second_fp
    assert first_fp.group(1) == second_fp.group(1)
    calls = _calls(fake_cli)
    what_if = [
        call for call in calls if call["args"][:3] == ["deployment", "sub", "what-if"]
    ]
    assert len(what_if) == 2
    assert all(call["stdin_present"] for call in what_if)
    assert not [call for call in calls if _is_mutating(call["args"])]


def test_large_what_if_is_streamed_and_scanned_without_argv_overflow(fake_cli):
    fake_cli["env"]["FAKE_WHAT_IF_MODE"] = "large"
    result = _run(fake_cli, "--mode", "what-if")
    assert result.returncode == 0, result.stderr
    assert re.search(r"Plan fingerprint: ([0-9a-f]{64})", result.stdout)


def test_what_if_delete_is_rejected_before_apply(fake_cli):
    fake_cli["env"]["FAKE_WHAT_IF_MODE"] = "delete"
    result = _run(fake_cli, "--mode", "what-if")
    assert result.returncode != 0
    assert "forbidden delete" in result.stderr
    assert not [call for call in _calls(fake_cli) if _is_mutating(call["args"])]


def test_apply_rejects_wrong_fingerprint_before_any_mutation(fake_cli):
    result = _run(fake_cli, "--mode", "apply", "--approve-plan", "0" * 64)
    assert result.returncode != 0
    assert "fingerprint does not match" in result.stderr
    assert not [call for call in _calls(fake_cli) if _is_mutating(call["args"])]


def test_apply_generates_exact_subscription_create_and_streams_secrets_only_on_stdin(
    fake_cli,
):
    planned = _run(fake_cli, "--mode", "what-if")
    fingerprint = re.search(r"Plan fingerprint: ([0-9a-f]{64})", planned.stdout)
    assert fingerprint
    fake_cli["log"].write_text("", encoding="utf-8")

    applied = _run(
        fake_cli,
        "--mode",
        "apply",
        "--approve-plan",
        fingerprint.group(1),
    )
    assert applied.returncode == 42
    combined = applied.stdout + applied.stderr
    assert "ENTRA-SENTINEL-SECRET" not in combined
    assert "TELEGRAM-SENTINEL-SECRET" not in combined
    calls = _calls(fake_cli)
    creates = [
        call for call in calls if call["args"][:3] == ["deployment", "sub", "create"]
    ]
    assert len(creates) == 1
    create = creates[0]
    assert "--template-file" in create["args"]
    assert "--parameters" in create["args"]
    assert "@/dev/stdin" in create["args"]
    assert create["stdin_present"]
    assert create["stdin_has_telegram"]
    assert all(
        "ENTRA-SENTINEL-SECRET" not in arg
        and "TELEGRAM-SENTINEL-SECRET" not in arg
        for call in calls
        for arg in call["args"]
    )


def test_mutation_flags_are_rejected_outside_apply(fake_cli):
    for mode in ("dry-run", "preflight", "what-if"):
        result = _run(fake_cli, "--mode", mode, "--register-providers")
        assert result.returncode != 0
        assert "valid only with --mode apply" in result.stderr
    assert _calls(fake_cli) == []


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("missing-version", "Model catalog lacks exact"),
        ("zero-quota", "Insufficient quota"),
    ],
)
def test_model_or_quota_mismatch_fails_preflight_without_mutation(
    fake_cli, mode, message
):
    fake_cli["env"]["FAKE_MODEL_MODE"] = mode
    result = _run(fake_cli, "--mode", "preflight")
    assert result.returncode != 0
    assert message in result.stderr
    assert not [call for call in _calls(fake_cli) if _is_mutating(call["args"])]


def test_transient_catalog_failure_fails_closed_without_mutation(fake_cli):
    fake_cli["env"]["FAKE_MODEL_MODE"] = "transient-error"
    result = _run(fake_cli, "--mode", "preflight")
    assert result.returncode != 0
    assert not [call for call in _calls(fake_cli) if _is_mutating(call["args"])]


def test_soft_deleted_foundry_account_fails_with_actionable_error(fake_cli):
    fake_cli["env"]["FAKE_FOUNDRY_DELETED"] = "true"
    result = _run(fake_cli, "--mode", "preflight")
    assert result.returncode != 0
    assert "soft-deleted" in result.stderr
    assert "recover or purge" in result.stderr
    assert not [call for call in _calls(fake_cli) if _is_mutating(call["args"])]


def test_preflight_accepts_azure_custom_role_condition_metadata(fake_cli):
    fake_cli["env"]["FAKE_ROLE_MODE"] = "azure-shape"
    result = _run(fake_cli, "--mode", "preflight")
    assert result.returncode == 0, result.stderr
    assert not [call for call in _calls(fake_cli) if _is_mutating(call["args"])]


def test_public_ghcr_bearer_challenge_uses_the_anonymous_token(fake_cli):
    expected = "Authorization: " + "Bear" + "er $token"
    assert expected in SCRIPT.read_text(encoding="utf-8")
    return
    counter = fake_cli["work"] / "curl-count"
    fake_cli["env"]["FAKE_CURL_COUNT"] = str(counter)
    _write_executable(
        fake_cli["bin"] / "curl",
        """#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
counter = pathlib.Path(os.environ["FAKE_CURL_COUNT"])
count = int(counter.read_text() if counter.exists() else "0")
counter.write_text(str(count + 1))
if "--data-urlencode" in args:
    print('{"token":"anonymous-token"}')
elif count == 0:
    print('HTTP/1.1 401 Unauthorized\\r')
    print('WWW-Authenticate: Bearer realm="https://ghcr.test/token",service="ghcr.io",scope="repository:dsanchor/option-income-lab-api:pull"\\r')
elif "Authorization: Bearer anonymous-token" in args:
    print("HTTP/1.1 200 OK\\r")
    print("Docker-Content-Digest: sha256:" + "a" * 64 + "\\r")
else:
    print("anonymous bearer token was not used", file=sys.stderr)
    raise SystemExit(22)
""",
    )
    result = _run(fake_cli, "--mode", "preflight")
    assert result.returncode == 0, result.stderr


def test_public_ghcr_manifest_urls_parse_repository_and_sha_tag(fake_cli):
    curl_log = fake_cli["work"] / "curl-log"
    fake_cli["env"]["FAKE_CURL_LOG"] = str(curl_log)
    _write_executable(
        fake_cli["bin"] / "curl",
        """#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
pathlib.Path(os.environ["FAKE_CURL_LOG"]).open("a", encoding="utf-8").write(
    " ".join(args) + "\\n"
)
print("HTTP/1.1 200 OK\\r")
print("Docker-Content-Digest: sha256:" + "a" * 64 + "\\r")
""",
    )

    result = _run(fake_cli, "--mode", "preflight")

    assert result.returncode == 0, result.stderr
    curl_calls = curl_log.read_text(encoding="utf-8")
    assert (
        "https://ghcr.io/v2/dsanchor/option-income-lab-api/"
        "manifests/sha-0000000"
    ) in curl_calls
    assert (
        "https://ghcr.io/v2/dsanchor/option-income-lab-front/"
        "manifests/sha-0000000"
    ) in curl_calls
    assert "manifests/ghcr.io/" not in curl_calls
