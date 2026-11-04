import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "check_public_image", Path(__file__).resolve().parents[1] / "check_public_image.py"
)
public_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(public_image)


class PublicPullTests(unittest.TestCase):
    def test_anonymous_token_and_exact_manifest_are_required(self):
        manifest = b'{"schemaVersion":2}'
        digest = hashlib.sha256(manifest).hexdigest()
        token = io.BytesIO(json.dumps({"token": "anonymous-pull-token"}).encode())
        with patch.object(public_image.urllib.request, "urlopen", side_effect=[
            token, io.BytesIO(manifest),
        ]) as request:
            public_image.check(f"ghcr.io/example/image@sha256:{digest}")
        token_url = request.call_args_list[0].args[0]
        self.assertIn("scope=repository%3Aexample%2Fimage%3Apull", token_url)
        manifest_request = request.call_args_list[1].args[0]
        self.assertEqual(manifest_request.headers["Authorization"], "Bearer anonymous-pull-token")
        self.assertTrue(manifest_request.full_url.endswith(f"/manifests/sha256:{digest}"))

    def test_manifest_digest_mismatch_and_mutable_tags_are_rejected(self):
        with patch.object(public_image.urllib.request, "urlopen", side_effect=[
            io.BytesIO(b'{"token":"anonymous"}'), io.BytesIO(b"wrong manifest"),
        ]):
            with self.assertRaisesRegex(ValueError, "does not match"):
                public_image.check("ghcr.io/example/image@sha256:" + "a" * 64)
        with patch.object(public_image.urllib.request, "urlopen") as request:
            with self.assertRaisesRegex(ValueError, "immutable"):
                public_image.check("ghcr.io/example/image:latest")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
