"""Deploy only the dedicated internal app using an in-memory ARM request."""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

API_VERSION = "2024-03-01"
OWNER = "option-income-lab/financial-news-mcp"


class Arm:
    def __init__(self):
        result = subprocess.run(
            ["az", "account", "get-access-token", "--resource", "https://management.azure.com/",
             "--output", "json", "--only-show-errors"],
            check=True, capture_output=True, text=True,
        )
        credentials = json.loads(result.stdout)
        self.token = credentials["accessToken"]
        self.subscription = credentials["subscription"]

    def request(self, method, resource, body=None, missing_ok=False):
        request = urllib.request.Request(
            f"https://management.azure.com{resource}?api-version={API_VERSION}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            # ARM error bodies can echo request properties; never log them.
            raise RuntimeError(f"ARM {method} failed (HTTP {error.code})") from None


def specification(environment, image, key, revision):
    return {
        "location": environment["location"],
        "tags": {"managed-by": OWNER},
        "properties": {
            "managedEnvironmentId": environment["id"],
            "configuration": {
                "activeRevisionsMode": "Single",
                "secrets": [{"name": "finnhub-api-key", "value": key}],
                "ingress": {
                    "external": False, "targetPort": 3000, "transport": "auto",
                    "allowInsecure": False,
                    "traffic": [{"latestRevision": True, "weight": 100}],
                },
            },
            "template": {
                "revisionSuffix": revision,
                "containers": [{
                    "name": "financial-news-mcp",
                    "image": image,
                    "env": [
                        {"name": "FINNHUB_API_KEY", "secretRef": "finnhub-api-key"},
                        {"name": "PORT", "value": "3000"},
                    ],
                    "resources": {"cpu": 0.25, "memory": "0.5Gi"},
                    "probes": [
                        {
                            "type": kind,
                            "httpGet": {"path": "/health", "port": 3000, "scheme": "HTTP"},
                            "initialDelaySeconds": 10, "periodSeconds": 10,
                            "timeoutSeconds": 3, "failureThreshold": 6,
                        }
                        for kind in ("Liveness", "Readiness")
                    ],
                }],
                "scale": {"minReplicas": 1, "maxReplicas": 1},
            },
        },
    }


def deploy(arm, group, environment_name, app_name, image, key, revision,
           sleep=time.sleep, attempts=60):
    if not key.strip():
        raise ValueError("FINNHUB_API_KEY must be configured in the production environment")
    if not re.fullmatch(r"ghcr.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("IMAGE must be an immutable GHCR digest")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}[a-z0-9]", app_name) or "--" in app_name:
        raise ValueError("Container App name must be 2–32 lowercase alphanumeric/hyphen characters")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}[a-z0-9]", revision) or "--" in revision:
        raise ValueError("Invalid unique revision suffix")
    for value in (group, environment_name):
        if not re.fullmatch(r"[A-Za-z0-9_.()-]+", value):
            raise ValueError("Invalid resource group or environment name")
    base = f"/subscriptions/{arm.subscription}/resourceGroups/{group}/providers/Microsoft.App"
    environment = arm.request("GET", f"{base}/managedEnvironments/{environment_name}")
    resource = f"{base}/containerApps/{app_name}"
    existing = arm.request("GET", resource, missing_ok=True)
    if existing:
        if existing.get("tags", {}).get("managed-by") != OWNER:
            raise ValueError("Refusing to overwrite an app not owned by this deployment")
        previous_environment = existing["properties"]["managedEnvironmentId"]
        if previous_environment.lower() != environment["id"].lower():
            raise ValueError("Refusing to move an app out of its existing environment")
    arm.request("PUT", resource, specification(environment, image, key, revision))
    expected_revision = f"{app_name}--{revision}"
    for _ in range(attempts):
        app = arm.request("GET", resource)
        properties = app["properties"]
        if properties.get("provisioningState") in ("Failed", "Canceled"):
            raise RuntimeError("Container App provisioning failed")
        state = arm.request("GET", f"{resource}/revisions/{expected_revision}", missing_ok=True)
        status = (state or {}).get("properties", {})
        if status.get("healthState") == "Unhealthy" or status.get("runningState") in (
            "Failed", "Degraded", "ProvisionFailed", "ActivationFailed"
        ):
            raise RuntimeError("Target revision failed its health checks")
        if (properties.get("provisioningState") == "Succeeded"
                and properties.get("latestReadyRevisionName") == expected_revision
                and status.get("healthState") == "Healthy" and status.get("active") is True):
            ingress = properties["configuration"]["ingress"]
            if ingress["external"] is not False:
                raise RuntimeError("Refusing to report a publicly exposed endpoint")
            return f"https://{ingress['fqdn']}/mcp", expected_revision
        sleep(10)
    raise RuntimeError("Target revision was not healthy and active within 10 minutes")


def main():
    # The key remains in memory: no CLI argument, parameter file, or ARM output.
    key = os.environ.pop("FINNHUB_API_KEY", "")
    if not key.strip():
        raise ValueError("FINNHUB_API_KEY must be configured in the production environment")
    endpoint, revision = deploy(
        Arm(),
        os.environ.get("AZURE_RESOURCE_GROUP", "stock-options-manager-rg"),
        os.environ.get("AZURE_CONTAINER_APPS_ENVIRONMENT", "cae-stock-options-manager"),
        os.environ.get("AZURE_FINANCIAL_NEWS_APP", "ca-stock-options-financial-news"),
        os.environ["IMAGE"], key, os.environ["REVISION_SUFFIX"],
    )
    summary = f"### Finance News MCP\n- Healthy revision: `{revision}`\n- Internal endpoint: `{endpoint}`\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        # Only deliberately sanitized messages are emitted, never request bodies.
        if isinstance(error, (ValueError, RuntimeError)):
            sys.exit(str(error))
        sys.exit("Azure deployment request failed; check connectivity and OIDC permissions.")
