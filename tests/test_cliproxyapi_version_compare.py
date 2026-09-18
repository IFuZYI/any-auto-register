import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from services.cliproxyapi_sync import (
    _decode_jwt_exp,
    _parse_time_value,
    build_version_compare,
    sync_chatgpt_cliproxyapi_status,
)


def _jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.signature"


class ParseTimeValueTests(unittest.TestCase):
    def test_parses_offset_string(self):
        parsed = _parse_time_value("2026-03-31T12:00:00+08:00")
        self.assertEqual(parsed, datetime(2026, 3, 31, 4, 0, tzinfo=timezone.utc))

    def test_parses_utc_z_string(self):
        parsed = _parse_time_value("2026-03-31T04:00:00Z")
        self.assertEqual(parsed, datetime(2026, 3, 31, 4, 0, tzinfo=timezone.utc))

    def test_parses_naive_string_as_utc_plus_8(self):
        parsed = _parse_time_value("2026-03-31T12:00:00")
        self.assertEqual(parsed.utcoffset(), timedelta(hours=8))

    def test_parses_epoch_seconds_and_milliseconds(self):
        seconds = 1774000000
        self.assertEqual(_parse_time_value(seconds), _parse_time_value(seconds * 1000))
        self.assertEqual(_parse_time_value(seconds).timestamp(), float(seconds))

    def test_returns_none_for_garbage(self):
        for value in ("", None, True, "not-a-time", {}, []):
            self.assertIsNone(_parse_time_value(value), msg=repr(value))


class DecodeJwtExpTests(unittest.TestCase):
    def test_decodes_exp(self):
        self.assertEqual(_decode_jwt_exp(_jwt(1774000000)), 1774000000)

    def test_returns_zero_for_opaque_token(self):
        for value in ("", None, "session-token", "a.b"):
            self.assertEqual(_decode_jwt_exp(value), 0)


class BuildVersionCompareTests(unittest.TestCase):
    def _tokens(self, local_exp: int, remote_exp: int) -> tuple[dict, dict]:
        return {"access_token": _jwt(local_exp)}, {"present": True, "access_token": _jwt(remote_exp)}

    def test_missing_remote(self):
        local, _ = self._tokens(1774000000, 0)
        result = build_version_compare(local, {"present": False})
        self.assertEqual(result["direction"], "missing_remote")
        self.assertFalse(result["remote_has_credentials"])

    def test_missing_local_when_no_access_token(self):
        _, remote = self._tokens(0, 1774000000)
        result = build_version_compare({"access_token": ""}, remote)
        self.assertEqual(result["direction"], "missing_local")

    def test_local_newer(self):
        local, remote = self._tokens(1774000000, 1773000000)
        result = build_version_compare(local, remote)
        self.assertEqual(result["direction"], "local_newer")
        self.assertIn("本地", result["detail"])

    def test_remote_newer(self):
        local, remote = self._tokens(1773000000, 1774000000)
        result = build_version_compare(local, remote)
        self.assertEqual(result["direction"], "remote_newer")
        self.assertIn("远端", result["detail"])

    def test_in_sync_within_tolerance(self):
        local, remote = self._tokens(1774000000, 1774000000 - 30)
        result = build_version_compare(local, remote)
        self.assertEqual(result["direction"], "in_sync")

    def test_falls_back_to_last_refresh_when_exp_unparsable(self):
        result = build_version_compare(
            {"access_token": "opaque", "last_refresh": "2026-03-31T12:00:00+08:00"},
            {
                "present": True,
                "access_token": "opaque",
                "expired": "",
                "last_refresh": "2026-03-30T12:00:00+08:00",
            },
        )
        self.assertEqual(result["direction"], "local_newer")

    def test_unknown_when_nothing_comparable(self):
        result = build_version_compare(
            {"access_token": "opaque"},
            {"present": True, "access_token": "opaque"},
        )
        self.assertEqual(result["direction"], "unknown")
        self.assertTrue(result["remote_has_credentials"])

    def test_remote_expired_field_is_used(self):
        # 远端只有 expired 串、没有 access_token 时也能比
        result = build_version_compare(
            {"access_token": _jwt(1774000000)},
            {"present": True, "expired": "2026-03-01T00:00:00+08:00"},
        )
        self.assertEqual(result["direction"], "local_newer")
        self.assertFalse(result["remote_has_credentials"])


class RemoteSyncVersionFieldsTests(unittest.TestCase):
    def _account(self, access_token: str):
        class _A:
            pass

        account = _A()
        account.email = "demo@example.com"
        account.user_id = "acct-123"
        account.token = access_token
        account.extra = {"access_token": access_token}
        return account

    def test_not_found_reports_missing_remote(self):
        account = self._account(_jwt(1774000000))
        with mock.patch("services.cliproxyapi_sync.list_auth_files", return_value=[]):
            result = sync_chatgpt_cliproxyapi_status(account, api_url="http://cpa.local", api_key="k")
        self.assertEqual(result["remote_state"], "not_found")
        self.assertEqual(result["version_direction"], "missing_remote")
        self.assertFalse(result["remote_has_credentials"])

    def test_matched_auth_file_carries_version_fields(self):
        local_exp = 1774000000
        remote_exp = 1773000000
        account = self._account(_jwt(local_exp))
        auth_files = [
            {
                "name": "demo@example.com.json",
                "provider": "codex",
                "email": "demo@example.com",
                "auth_index": "auth-001",
                "status": "active",
                "status_message": "",
                "unavailable": False,
                "access_token": _jwt(remote_exp),
                "expired": "2026-03-06T00:00:00+08:00",
                "last_refresh": "2026-03-01T00:00:00+08:00",
            }
        ]
        with mock.patch("services.cliproxyapi_sync.list_auth_files", return_value=auth_files):
            with mock.patch(
                "services.cliproxyapi_sync._probe_remote_auth",
                return_value={
                    "last_probe_at": "2026-03-31T00:00:00Z",
                    "last_probe_status_code": 200,
                    "last_probe_error_code": "",
                    "last_probe_message": "ok",
                    "remote_state": "usable",
                },
            ):
                result = sync_chatgpt_cliproxyapi_status(account, api_url="http://cpa.local", api_key="k")

        self.assertTrue(result["uploaded"])
        self.assertEqual(result["version_direction"], "local_newer")
        self.assertTrue(result["remote_has_credentials"])
        self.assertTrue(result["local_at_expires_at"])
        self.assertTrue(result["remote_at_expires_at"])
        self.assertNotEqual(result["local_at_expires_at"], result["remote_at_expires_at"])


if __name__ == "__main__":
    unittest.main()
