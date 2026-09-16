import unittest

from services.chatgpt_account_state import (
    RECOVERED_STATUS_REASON,
    STATUS_BEFORE_INVALID_KEY,
    apply_chatgpt_status_policy,
    classify_local_probe_health,
    classify_local_probe_state,
    classify_remote_sync_health,
    classify_remote_sync_state,
)

# 一次"这个号确实能用"的探测结论：/me 回了 200
_OK_PROBE = {
    "auth": {"state": "access_token_valid", "http_status": 200, "error_code": "", "message": ""},
    "subscription": {"plan": "plus"},
    "codex": {"state": "usable", "http_status": 200},
}

_401_PROBE = {
    "auth": {
        "state": "access_token_invalidated",
        "http_status": 401,
        "error_code": "token_invalidated",
        "message": "invalidated",
    }
}


class DummyAccount:
    def __init__(self, status="registered", extra=None):
        self.status = status
        self._extra = dict(extra or {})

    def get_extra(self):
        return dict(self._extra)

    def set_extra(self, value):
        self._extra = dict(value)


class ChatGPTAccountStateTests(unittest.TestCase):
    def test_local_401_marks_invalid(self):
        account = DummyAccount()
        reason = apply_chatgpt_status_policy(
            account,
            local_probe={
                "auth": {
                    "state": "access_token_invalidated",
                    "http_status": 401,
                    "error_code": "token_invalidated",
                    "message": "invalidated",
                }
            },
        )
        self.assertEqual(reason, "auth_401")
        self.assertEqual(account.status, "invalid")

    def test_remote_401_marks_invalid(self):
        self.assertEqual(
            classify_remote_sync_state(
                {
                    "remote_state": "access_token_invalidated",
                    "last_probe_status_code": 401,
                    "last_probe_error_code": "token_invalidated",
                    "last_probe_message": "invalidated",
                }
            ),
            "remote_401",
        )

    def test_payment_and_quota_do_not_mark_invalid(self):
        self.assertEqual(
            classify_local_probe_state(
                {
                    "auth": {"state": "access_token_valid", "http_status": 200},
                    "codex": {"state": "payment_required", "http_status": 403, "message": "payment required"},
                }
            ),
            "",
        )
        self.assertEqual(
            classify_remote_sync_state(
                {
                    "remote_state": "quota_exhausted",
                    "last_probe_status_code": 429,
                    "last_probe_error_code": "",
                    "last_probe_message": "usage limit reached",
                }
            ),
            "",
        )

    def test_deactivated_message_marks_invalid(self):
        self.assertEqual(
            classify_local_probe_state(
                {
                    "auth": {
                        "state": "banned_like",
                        "http_status": 403,
                        "error_code": "account_deactivated",
                        "message": "You do not have an account because it has been deleted or deactivated.",
                    }
                }
            ),
            "auth_deactivated",
        )


class HealthClassifierTests(unittest.TestCase):
    def test_local_health_requires_auth_200(self):
        self.assertEqual(classify_local_probe_health(_OK_PROBE), "auth_ok")
        self.assertEqual(classify_local_probe_health({"auth": {"state": "probe_failed"}}), "")
        self.assertEqual(classify_local_probe_health(None), "")
        self.assertEqual(classify_local_probe_health({}), "")

    def test_remote_health_requires_usable(self):
        self.assertEqual(classify_remote_sync_health({"remote_state": "usable"}), "remote_ok")
        self.assertEqual(classify_remote_sync_health({"remote_state": "quota_exhausted"}), "")
        self.assertEqual(classify_remote_sync_health(None), "")


class StatusRecoveryTests(unittest.TestCase):
    """曾经被判失效的号，重探正常之后标签必须跟着变回来。"""

    def test_healthy_probe_restores_previous_status(self):
        account = DummyAccount(
            status="invalid", extra={STATUS_BEFORE_INVALID_KEY: "subscribed"}
        )
        reason = apply_chatgpt_status_policy(account, local_probe=_OK_PROBE)

        self.assertEqual(reason, RECOVERED_STATUS_REASON)
        self.assertEqual(account.status, "subscribed")
        # 标记用完即删，免得下次失效时把旧值当成本次的前置状态
        self.assertNotIn(STATUS_BEFORE_INVALID_KEY, account.get_extra())

    def test_healthy_probe_defaults_to_registered(self):
        account = DummyAccount(status="invalid")
        self.assertEqual(
            apply_chatgpt_status_policy(account, local_probe=_OK_PROBE),
            RECOVERED_STATUS_REASON,
        )
        self.assertEqual(account.status, "registered")

    def test_healthy_probe_leaves_normal_status_alone(self):
        account = DummyAccount(status="registered")
        self.assertEqual(apply_chatgpt_status_policy(account, local_probe=_OK_PROBE), "")
        self.assertEqual(account.status, "registered")

    def test_inconclusive_probe_does_not_recover(self):
        """探测没跑通就不算正面证据 —— 一次抖动不能把失效洗白。"""
        for state in ("probe_failed", "missing_access_token", "unknown"):
            account = DummyAccount(status="invalid")
            probe = {"auth": {"state": state, "http_status": 0}}
            self.assertEqual(apply_chatgpt_status_policy(account, local_probe=probe), "")
            self.assertEqual(account.status, "invalid")

    def test_codex_deactivated_keeps_account_invalid(self):
        """auth 说正常、codex 说停用时按停用算，异常优先于健康。"""
        account = DummyAccount(status="invalid")
        probe = {
            "auth": {"state": "access_token_valid", "http_status": 200},
            "codex": {
                "state": "account_deactivated",
                "http_status": 403,
                "error_code": "account_deactivated",
                "message": "You do not have an account because it has been deleted or deactivated.",
            },
        }
        self.assertEqual(
            apply_chatgpt_status_policy(account, local_probe=probe), "codex_deactivated"
        )
        self.assertEqual(account.status, "invalid")

    def test_marking_invalid_remembers_previous_status(self):
        account = DummyAccount(status="subscribed")
        reason = apply_chatgpt_status_policy(account, local_probe=_401_PROBE)

        self.assertEqual(reason, "auth_401")
        self.assertEqual(account.status, "invalid")
        self.assertEqual(account.get_extra().get(STATUS_BEFORE_INVALID_KEY), "subscribed")

    def test_repeated_invalid_keeps_the_original_status(self):
        account = DummyAccount(status="trial")
        apply_chatgpt_status_policy(account, local_probe=_401_PROBE)
        apply_chatgpt_status_policy(account, local_probe=_401_PROBE)

        self.assertEqual(account.get_extra().get(STATUS_BEFORE_INVALID_KEY), "trial")

    def test_remote_sync_recovery(self):
        account = DummyAccount(status="invalid")
        reason = apply_chatgpt_status_policy(account, remote_sync={"remote_state": "usable"})

        self.assertEqual(reason, RECOVERED_STATUS_REASON)
        self.assertEqual(account.status, "registered")

    def test_remote_sync_inconclusive_does_not_recover(self):
        for state in ("unreachable", "not_found", "probe_failed", "probe_skipped"):
            account = DummyAccount(status="invalid")
            self.assertEqual(
                apply_chatgpt_status_policy(account, remote_sync={"remote_state": state}), ""
            )
            self.assertEqual(account.status, "invalid")

    def test_leftover_invalid_marker_falls_back_to_registered(self):
        """残留的旧标记不能把号恢复成 invalid（那等于没恢复）。"""
        account = DummyAccount(
            status="invalid", extra={STATUS_BEFORE_INVALID_KEY: "invalid"}
        )
        apply_chatgpt_status_policy(account, local_probe=_OK_PROBE)

        self.assertEqual(account.status, "registered")


if __name__ == "__main__":
    unittest.main()
