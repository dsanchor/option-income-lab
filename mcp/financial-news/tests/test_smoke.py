import http.client
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error


SPEC = importlib.util.spec_from_file_location(
    "smoke", Path(__file__).resolve().parents[1] / "smoke.py"
)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def response(payload, headers=None):
    result = io.BytesIO(json.dumps(payload).encode())
    result.headers = headers or {}
    return result


class SmokeTests(unittest.TestCase):
    def test_waits_through_container_startup_resets_and_disconnects(self):
        requests = [
            ConnectionResetError("container not listening yet"),
            http.client.RemoteDisconnected("container starting"),
            response({"status": "healthy"}),
            response({"result": {"capabilities": {"tools": {}}}}, {"Mcp-Session-Id": "session"}),
            response({"result": {"tools": [{"name": "get_financial_news"}]}}),
            urllib.error.HTTPError("http://local/mcp", 400, "Session required", {}, None),
        ]
        with patch.object(smoke.urllib.request, "urlopen", side_effect=requests) as request, \
                patch.object(smoke.time, "sleep") as sleep, patch("sys.stdout", new_callable=io.StringIO):
            smoke.smoke("http://local")
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(request.call_args_list[4].args[0].headers["Mcp-session-id"], "session")

    def test_startup_wait_is_bounded(self):
        with patch.object(smoke.urllib.request, "urlopen", side_effect=ConnectionResetError) as request, \
                patch.object(smoke.time, "sleep"):
            with self.assertRaises(ConnectionResetError):
                smoke.smoke("http://local")
            self.assertEqual(request.call_count, 30)


if __name__ == "__main__":
    unittest.main()
