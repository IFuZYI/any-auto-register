import unittest
from unittest import mock

from services.external_apps import list_status, management_url, probe, resolve_target
from services.external_apps import test_connection as cpa_test_connection


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class ResolveTargetTests(unittest.TestCase):
    def test_prefers_cpa_keys(self):
        with mock.patch(
            "core.config_store.config_store.get",
            side_effect=lambda key, default=None: {
                "cpa_api_url": "https://cpa.example.com",
                "cpa_api_key": "cpa-key",
                "cliproxyapi_base_url": "http://127.0.0.1:8317",
                "cliproxyapi_management_key": "old-key",
            }.get(key, default),
        ):
            url, key = resolve_target()
        self.assertEqual(url, "https://cpa.example.com")
        self.assertEqual(key, "cpa-key")

    def test_falls_back_to_legacy_keys(self):
        with mock.patch(
            "core.config_store.config_store.get",
            side_effect=lambda key, default=None: {
                "cliproxyapi_base_url": "http://127.0.0.1:8317",
                "cliproxyapi_management_key": "old-key",
            }.get(key, default),
        ):
            url, key = resolve_target()
        self.assertEqual(url, "http://127.0.0.1:8317")
        self.assertEqual(key, "old-key")

    def test_explicit_args_win(self):
        url, key = resolve_target(api_url="https://explicit.example.com/", api_key="k")
        self.assertEqual(url, "https://explicit.example.com/")
        self.assertEqual(key, "k")


class ManagementUrlTests(unittest.TestCase):
    def test_builds_management_page_url(self):
        self.assertEqual(
            management_url("https://cpa.example.com/"),
            "https://cpa.example.com/management.html",
        )

    def test_empty_when_unconfigured(self):
        with mock.patch("services.external_apps.resolve_target", return_value=("", "")):
            self.assertEqual(management_url(""), "")


class ProbeTests(unittest.TestCase):
    def test_unconfigured(self):
        with mock.patch("services.external_apps.resolve_target", return_value=("", "")):
            result = probe()
        self.assertFalse(result["reachable"])
        self.assertIn("未配置", result["message"])

    def test_connection_error(self):
        with mock.patch("services.external_apps.resolve_target", return_value=("https://cpa.example.com", "k")):
            with mock.patch("requests.get", side_effect=OSError("boom")):
                result = probe()
        self.assertFalse(result["reachable"])
        self.assertIn("无法连接", result["message"])

    def test_unauthorized_key(self):
        with mock.patch("services.external_apps.resolve_target", return_value=("https://cpa.example.com", "k")):
            with mock.patch("requests.get", return_value=_Response(401)):
                result = probe()
        self.assertTrue(result["reachable"])
        self.assertFalse(result["auth_ok"])
        self.assertIn("API Key 无效", result["message"])

    def test_ok_counts_codex_files_only(self):
        payload = {
            "files": [
                {"name": "a.json", "provider": "codex"},
                {"name": "b.json", "type": "codex"},
                {"name": "c.json", "provider": "claude"},
                "not-a-dict",
            ]
        }
        with mock.patch("services.external_apps.resolve_target", return_value=("https://cpa.example.com", "k")):
            with mock.patch("requests.get", return_value=_Response(200, payload)):
                result = probe()
        self.assertTrue(result["auth_ok"])
        self.assertEqual(result["auth_file_count"], 2)


class ListStatusTests(unittest.TestCase):
    def test_unconfigured_status(self):
        with mock.patch("services.external_apps.resolve_target", return_value=("", "")):
            items = list_status()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["name"], "cliproxyapi")
        self.assertEqual(item["kind"], "remote")
        self.assertFalse(item["configured"])
        self.assertFalse(item["running"])
        self.assertTrue(item["last_error"])

    def test_connected_status(self):
        with mock.patch("services.external_apps.resolve_target", return_value=("https://cpa.example.com", "k")):
            with mock.patch("requests.get", return_value=_Response(200, {"files": [{"provider": "codex"}]})):
                items = list_status()
        item = items[0]
        self.assertTrue(item["configured"])
        self.assertTrue(item["running"])
        self.assertEqual(item["management_url"], "https://cpa.example.com/management.html")
        self.assertEqual(item["auth_file_count"], 1)
        self.assertEqual(item["last_error"], "")

    def test_test_connection_matches_status_shape(self):
        with mock.patch("services.external_apps.resolve_target", return_value=("https://cpa.example.com", "k")):
            with mock.patch("requests.get", return_value=_Response(200, {"files": []})):
                self.assertEqual(cpa_test_connection(), list_status()[0])


if __name__ == "__main__":
    unittest.main()
