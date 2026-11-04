"""Execute API deployment shell wiring with a fake Azure CLI, never Azure."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/docker-publish.yml"


@pytest.mark.parametrize("state", ["internal", "missing", "external", "other_environment"])
def test_api_deploy_sets_optional_news_url_only_for_same_internal_environment(state):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(s for s in workflow["jobs"]["deploy"]["steps"] if s.get("id") == "deploy-api")
    script = step["run"].replace("${{ steps.tag.outputs.tag }}", "sha-test")
    prelude = """
    az() {
      if [ "$2" = "update" ]; then
        printf 'UPDATE %s\\n' "$*" >&2
        echo api--test
      elif [ "$4" = "$AZURE_FINANCIAL_NEWS_APP" ]; then
        if [ "$NEWS_STATE" = "missing" ]; then return 3; fi
        echo "$NEWS_APP_JSON"
      else
        echo /environments/api
      fi
    }
    """
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", prelude + script],
        env={
            **os.environ,
            "AZURE_API_APP": "custom-api",
            "AZURE_FINANCIAL_NEWS_APP": "custom-news",
            "AZURE_RESOURCE_GROUP": "custom-rg",
            "GITHUB_OUTPUT": "/dev/stdout",
            "NEWS_STATE": state,
            "NEWS_APP_JSON": json.dumps({
                "fqdn": "custom-news.internal.custom-env.azurecontainerapps.io",
                "external": state == "external",
                "environment": "/environments/other" if state == "other_environment" else "/environments/api",
            }),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    expected = (
        "https://custom-news.internal.custom-env.azurecontainerapps.io/mcp"
        if state == "internal" else ""
    )
    assert f"--set-env-vars FINANCIAL_NEWS_MCP_URL={expected} --query" in result.stderr
    assert "revision=api--test" in result.stdout
    assert ("::warning::" in result.stdout) == (state != "internal")
