import copy
import importlib.util
import itertools
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deploy", ROOT / "deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)

IMAGE = "ghcr.io/example/financial-news-mcp@sha256:" + "a" * 64
ENVIRONMENT = {
    "id": "/subscriptions/sub/resourceGroups/existing-rg/providers/Microsoft.App/managedEnvironments/existing-env",
    "location": "swedencentral",
}


class FakeArm:
    subscription = "sub"

    def __init__(self, existing=None, states=None):
        self.existing = existing
        self.states = iter(states) if states else itertools.repeat({"healthState": "Healthy", "active": True})
        self.calls = []
        self.body = None

    def request(self, method, resource, body=None, missing_ok=False):
        self.calls.append((method, resource, copy.deepcopy(body)))
        if "/managedEnvironments/" in resource:
            return ENVIRONMENT
        if method == "PUT":
            self.body = copy.deepcopy(body)
            return {}
        if "/revisions/" in resource:
            state = next(self.states)
            return {"properties": state} if state is not None else None
        if self.body is None:
            return self.existing
        app = copy.deepcopy(self.body)
        app["properties"].update({
            "provisioningState": "Succeeded",
            "latestReadyRevisionName": "ca-financial-news--r123-1",
        })
        app["properties"]["configuration"]["ingress"]["fqdn"] = "app.internal.example"
        return app


def run(arm, **overrides):
    arguments = dict(
        arm=arm, group="existing-rg", environment_name="existing-env",
        app_name="ca-financial-news", image=IMAGE, key="dummy-not-a-real-key",
        revision="r123-1", sleep=lambda _: None, attempts=3,
    )
    arguments.update(overrides)
    return deploy.deploy(**arguments)


class DeploymentTests(unittest.TestCase):
    def test_create_and_update_converge_without_changing_other_resources(self):
        first = FakeArm()
        endpoint, revision = run(first)
        self.assertEqual(endpoint, "https://app.internal.example/mcp")
        self.assertEqual(revision, "ca-financial-news--r123-1")
        existing = first.body
        second = FakeArm(existing=existing)
        run(second)
        self.assertEqual(first.body, second.body)
        self.assertEqual(
            [resource for method, resource, _ in second.calls if method == "PUT"],
            ["/subscriptions/sub/resourceGroups/existing-rg/providers/Microsoft.App/containerApps/ca-financial-news"],
        )
        properties = second.body["properties"]
        self.assertEqual(properties["managedEnvironmentId"], ENVIRONMENT["id"])
        config = properties["configuration"]
        self.assertIs(config["ingress"]["external"], False)
        self.assertEqual(config["activeRevisionsMode"], "Single")
        self.assertNotIn("registries", config)
        self.assertEqual(config["secrets"], [{"name": "finnhub-api-key", "value": "dummy-not-a-real-key"}])
        container = properties["template"]["containers"][0]
        self.assertEqual(container["image"], IMAGE)
        self.assertEqual(container["env"][0], {"name": "FINNHUB_API_KEY", "secretRef": "finnhub-api-key"})
        self.assertNotIn("dummy-not-a-real-key", str(container))
        self.assertEqual({probe["type"] for probe in container["probes"]}, {"Liveness", "Readiness"})
        self.assertEqual(properties["template"]["scale"], {"minReplicas": 1, "maxReplicas": 1})

    def test_secret_rotation_updates_secret_and_forces_new_revision_for_same_image(self):
        arm = FakeArm()
        run(arm)
        existing = arm.body
        updated = FakeArm(existing=existing)
        # A previous healthy revision must not satisfy the new deployment.
        with self.assertRaisesRegex(RuntimeError, "within 10 minutes"):
            run(updated, key="rotated-dummy-key", revision="r124-1")
        self.assertEqual(updated.body["properties"]["template"]["revisionSuffix"], "r124-1")
        self.assertEqual(updated.body["properties"]["configuration"]["secrets"][0]["value"], "rotated-dummy-key")
        self.assertEqual(updated.body["properties"]["template"]["containers"][0]["image"], IMAGE)

    def test_waits_for_active_healthy_target_revision(self):
        arm = FakeArm(states=[None, {"healthState": "Healthy", "active": False},
                              {"healthState": "Healthy", "active": True}])
        run(arm)
        revisions = [resource for method, resource, _ in arm.calls if "/revisions/" in resource]
        self.assertEqual(len(revisions), 3)
        self.assertTrue(all(resource.endswith("/ca-financial-news--r123-1") for resource in revisions))

    def test_unhealthy_revision_fails(self):
        with self.assertRaisesRegex(RuntimeError, "health checks"):
            run(FakeArm(states=[{"healthState": "Unhealthy", "active": True}]))

    def test_refuses_unowned_app_or_environment_migration(self):
        cases = [
            {"tags": {}, "properties": {"managedEnvironmentId": ENVIRONMENT["id"]}},
            {"tags": {"managed-by": deploy.OWNER}, "properties": {"managedEnvironmentId": "/another/environment"}},
        ]
        for existing in cases:
            with self.subTest(existing=existing):
                arm = FakeArm(existing=existing)
                with self.assertRaises(ValueError):
                    run(arm)
                self.assertFalse(any(method == "PUT" for method, _, _ in arm.calls))

    def test_empty_secret_and_mutable_image_fail_before_any_arm_request(self):
        for overrides in ({"key": " \n"}, {"image": "ghcr.io/example/app:latest"}):
            with self.subTest(overrides=overrides):
                arm = FakeArm()
                with self.assertRaises(ValueError):
                    run(arm, **overrides)
                self.assertEqual(arm.calls, [])

    def test_key_removed_before_azure_cli_and_not_in_summary(self):
        variables = {
            "FINNHUB_API_KEY": "private-dummy-key",
            "IMAGE": IMAGE,
            "REVISION_SUFFIX": "r123-1",
        }

        def create_arm():
            self.assertNotIn("FINNHUB_API_KEY", os.environ)
            return FakeArm()

        with patch.dict(os.environ, variables, clear=True), \
                patch.object(deploy, "Arm", side_effect=create_arm), \
                patch.object(deploy, "deploy", return_value=("https://internal/mcp", "revision")) as apply, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            deploy.main()
            self.assertEqual(apply.call_args.args[5], "private-dummy-key")
            self.assertNotIn("private-dummy-key", output.getvalue())
            self.assertIn("https://internal/mcp", output.getvalue())


if __name__ == "__main__":
    unittest.main()
