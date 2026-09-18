import base64
import json
import unittest
from unittest import mock

from services.cpa_account_sync import (
    build_cpa_sync_report,
    sync_chatgpt_account_with_cpa,
    sync_chatgpt_accounts_with_cpa,
)


def _jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.signature"


LOCAL_EXP = 1774000000
REMOTE_EXP = 1773000000


class DummyAccount:
    def __init__(self, *, email="demo@example.com", access_token=None, extra=None, account_id=1):
        self.id = account_id
        self.email = email
        self.user_id = "acct-123"
        self.token = access_token if access_token is not None else _jwt(LOCAL_EXP)
        self.extra = dict(extra or {})
        self.extra.setdefault("access_token", self.token)
        self.updated_at = None

    def get_extra(self):
        return self.extra

    def set_extra(self, value):
        self.extra = value


def _remote_file(email="demo@example.com", access_token=None, status="active"):
    return {
        "name": f"{email}.json",
        "provider": "codex",
        "email": email,
        "auth_index": "auth-001",
        "status": status,
        "status_message": "",
        "unavailable": False,
        "access_token": access_token if access_token is not None else _jwt(REMOTE_EXP),
        "refresh_token": "remote-rt",
        "id_token": "remote-id",
        "last_refresh": "2026-03-01T00:00:00+08:00",
    }


def _probe_ok():
    return {
        "auth": {"state": "access_token_valid", "http_status": 200, "error_code": "", "message": "ok"},
        "subscription": {"plan": "free"},
        "codex": {"state": "usable"},
    }


class SyncReportTests(unittest.TestCase):
    def test_report_counts_directions(self):
        accounts = [
            DummyAccount(email="newer@example.com", access_token=_jwt(LOCAL_EXP), account_id=1),
            DummyAccount(email="older@example.com", access_token=_jwt(REMOTE_EXP - 500000), account_id=2),
            DummyAccount(email="missing@example.com", access_token=_jwt(LOCAL_EXP), account_id=3),
        ]
        files = [
            _remote_file("newer@example.com", _jwt(REMOTE_EXP)),
            _remote_file("older@example.com", _jwt(REMOTE_EXP)),
        ]
        with mock.patch("services.cpa_account_sync.list_auth_files", return_value=files) as list_mock:
            report = build_cpa_sync_report(accounts, api_url="http://cpa.local", api_key="k")

        self.assertEqual(list_mock.call_count, 1)
        self.assertTrue(report["reachable"])
        self.assertEqual(report["counts"]["local_newer"], 1)
        self.assertEqual(report["counts"]["remote_newer"], 1)
        self.assertEqual(report["counts"]["missing_remote"], 1)
        self.assertEqual(len(report["items"]), 3)

    def test_report_marks_unreachable(self):
        with mock.patch(
            "services.cpa_account_sync.list_auth_files",
            side_effect=RuntimeError("CLIProxyAPI 无法连接"),
        ):
            report = build_cpa_sync_report([DummyAccount()], api_url="http://cpa.local", api_key="k")

        self.assertFalse(report["reachable"])
        self.assertEqual(report["items"][0]["direction"], "unreachable")


class SingleAccountSyncTests(unittest.TestCase):
    def _run(self, account, files, mode="auto", probe=None, remote_state=None):
        sync_state = {"remote_state": remote_state or "usable", "uploaded": True}
        with mock.patch("services.cpa_account_sync.list_auth_files", return_value=files):
            with mock.patch(
                "services.cpa_account_sync._push",
                side_effect=lambda *a, **kw: {"ok": True, "skipped": False, "message": "已推送"},
            ) as push_mock:
                with mock.patch("services.cpa_account_sync._resync", return_value=sync_state):
                    result = sync_chatgpt_account_with_cpa(
                        account,
                        mode=mode,
                        api_url="http://cpa.local",
                        api_key="k",
                        commit=False,
                    )
        return result, push_mock

    def test_missing_remote_pushes(self):
        account = DummyAccount()
        result, push_mock = self._run(account, [])
        self.assertEqual(result["direction"], "missing_remote")
        self.assertEqual(result["action"], "push")
        self.assertTrue(result["ok"])
        self.assertEqual(push_mock.call_count, 1)

    def test_local_newer_pushes(self):
        account = DummyAccount(access_token=_jwt(LOCAL_EXP))
        result, push_mock = self._run(account, [_remote_file()])
        self.assertEqual(result["direction"], "local_newer")
        self.assertEqual(result["action"], "push")
        self.assertEqual(push_mock.call_count, 1)

    def test_remote_newer_pulls_and_updates_credentials(self):
        account = DummyAccount(access_token=_jwt(REMOTE_EXP - 500000), account_id=1)
        account.extra["sync_statuses"] = {"cliproxyapi": {"remote_state": "usable", "uploaded": True}}
        remote_token = _jwt(REMOTE_EXP)
        files = [_remote_file(access_token=remote_token)]
        with mock.patch("services.cpa_account_sync.list_auth_files", return_value=files):
            with mock.patch("services.cpa_account_sync._resync", return_value={"uploaded": True, "remote_state": "usable"}):
                result = sync_chatgpt_account_with_cpa(account, mode="auto", api_url="http://cpa.local", api_key="k", commit=False)

        self.assertEqual(result["direction"], "remote_newer")
        self.assertEqual(result["action"], "pull")
        self.assertTrue(result["ok"])
        self.assertEqual(account.token, remote_token)
        self.assertEqual(account.extra["access_token"], remote_token)
        self.assertEqual(account.extra["refresh_token"], "remote-rt")
        self.assertEqual(account.extra["id_token"], "remote-id")
        self.assertEqual(account.extra["sync_statuses"]["cpa_sync"]["last_action"], "pull")

    def test_in_sync_skips(self):
        account = DummyAccount(access_token=_jwt(LOCAL_EXP))
        result, push_mock = self._run(account, [_remote_file(access_token=_jwt(LOCAL_EXP))])
        self.assertEqual(result["direction"], "in_sync")
        self.assertEqual(result["action"], "skip")
        self.assertTrue(result["ok"])
        self.assertEqual(push_mock.call_count, 0)

    def test_push_only_skips_remote_newer(self):
        account = DummyAccount(access_token=_jwt(REMOTE_EXP - 500000))
        result, push_mock = self._run(account, [_remote_file()], mode="push_only")
        self.assertEqual(result["direction"], "remote_newer")
        self.assertEqual(result["action"], "skip")
        self.assertEqual(push_mock.call_count, 0)

    def test_pull_only_skips_local_newer(self):
        account = DummyAccount(access_token=_jwt(LOCAL_EXP))
        result, _ = self._run(account, [_remote_file()], mode="pull_only")
        self.assertEqual(result["action"], "skip")
        self.assertIn("pull_only", result["message"])

    def test_pull_unavailable_without_remote_credentials(self):
        account = DummyAccount(access_token=_jwt(REMOTE_EXP - 500000))
        files = [{"name": "demo@example.com.json", "provider": "codex", "email": "demo@example.com",
                  "auth_index": "auth-001", "status": "active", "expired": "2026-03-06T00:00:00+08:00"}]
        with mock.patch("services.cpa_account_sync.list_auth_files", return_value=files):
            result = sync_chatgpt_account_with_cpa(account, mode="auto", api_url="http://cpa.local", api_key="k", commit=False)

        self.assertEqual(result["direction"], "remote_newer")
        self.assertFalse(result["ok"])
        self.assertIn("未返回 access_token", result["message"])
        self.assertEqual(account.extra["access_token"], _jwt(REMOTE_EXP - 500000))

    def test_pull_blocked_when_remote_invalid(self):
        account = DummyAccount(access_token=_jwt(REMOTE_EXP - 500000))
        account.extra["sync_statuses"] = {"cliproxyapi": {"remote_state": "access_token_invalidated", "uploaded": True}}
        with mock.patch("services.cpa_account_sync.list_auth_files", return_value=[_remote_file()]):
            result = sync_chatgpt_account_with_cpa(account, mode="auto", api_url="http://cpa.local", api_key="k", commit=False)

        self.assertFalse(result["ok"])
        self.assertIn("拒绝", result["message"])

    def test_push_skips_when_local_probe_not_uploadable(self):
        account = DummyAccount()
        with mock.patch("services.cpa_account_sync.list_auth_files", return_value=[]):
            with mock.patch(
                "platforms.chatgpt.status_probe.probe_local_chatgpt_status",
                return_value={"auth": {"state": "access_token_invalidated", "message": "invalidated"}},
            ):
                with mock.patch("services.chatgpt_sync.upload_account_model_to_cpa") as upload_mock:
                    result = sync_chatgpt_account_with_cpa(
                        account, mode="auto", api_url="http://cpa.local", api_key="k", commit=False
                    )

        self.assertEqual(result["action"], "skip")
        self.assertFalse(result["ok"])
        self.assertIn("不可上传", result["message"])
        upload_mock.assert_not_called()

    def test_unreachable_reports_failure(self):
        account = DummyAccount()
        with mock.patch(
            "services.cpa_account_sync.list_auth_files",
            side_effect=RuntimeError("CLIProxyAPI 无法连接"),
        ):
            result = sync_chatgpt_account_with_cpa(account, mode="auto", api_url="http://cpa.local", api_key="k", commit=False)

        self.assertEqual(result["action"], "fail")
        self.assertEqual(result["direction"], "unreachable")


class BatchSyncTests(unittest.TestCase):
    def test_batch_fetches_auth_files_once_and_summarizes(self):
        accounts = [
            DummyAccount(email="push@example.com", access_token=_jwt(LOCAL_EXP), account_id=1),
            DummyAccount(email="pull@example.com", access_token=_jwt(REMOTE_EXP - 500000), account_id=2),
            DummyAccount(email="same@example.com", access_token=_jwt(LOCAL_EXP), account_id=3),
        ]
        accounts[1].extra["sync_statuses"] = {"cliproxyapi": {"remote_state": "usable"}}
        files = [
            _remote_file("push@example.com", _jwt(REMOTE_EXP)),
            _remote_file("pull@example.com", _jwt(REMOTE_EXP)),
            _remote_file("same@example.com", _jwt(LOCAL_EXP)),
        ]
        with mock.patch("services.cpa_account_sync.list_auth_files", return_value=files) as list_mock:
            with mock.patch(
                "services.cpa_account_sync._push",
                return_value={"ok": True, "skipped": False, "message": "已推送"},
            ):
                with mock.patch(
                    "platforms.chatgpt.status_probe.probe_local_chatgpt_status",
                    return_value=_probe_ok(),
                ):
                    with mock.patch("services.cpa_account_sync.time.sleep") as sleep_mock:
                        summary = sync_chatgpt_accounts_with_cpa(
                            accounts, mode="auto", api_url="http://cpa.local", api_key="k", commit=False
                        )

        self.assertEqual(list_mock.call_count, 1)
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["pushed"], 1)
        self.assertEqual(summary["pulled"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["failed"], 0)

    def test_batch_unreachable_marks_all_failed(self):
        accounts = [DummyAccount(account_id=1), DummyAccount(account_id=2)]
        with mock.patch(
            "services.cpa_account_sync.list_auth_files",
            side_effect=RuntimeError("CLIProxyAPI 无法连接"),
        ):
            summary = sync_chatgpt_accounts_with_cpa(accounts, mode="auto", api_url="http://cpa.local", api_key="k", commit=False)

        self.assertTrue(summary["unreachable"])
        self.assertEqual(summary["failed"], 2)
        self.assertTrue(all(item["action"] == "fail" for item in summary["items"]))


if __name__ == "__main__":
    unittest.main()
