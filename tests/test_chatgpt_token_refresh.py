"""刷新 Token 的策略编排测试。

盯的是一个线上反复出现的现象:``/api/auth/session`` 返回 200 且带着
``accessToken``,但那个 AT 就是会话 cookie 里原有的旧值 —— 库里等于没刷新,
调用方却收到了"成功"。这种假成功必须被识别出来,并降级去走账号密码
(带 TOTP 2FA)的协议登录。
"""

import contextlib
import unittest
from unittest import mock

from platforms.chatgpt.token_refresh import (
    STRATEGY_LOGIN,
    STRATEGY_SESSION,
    TokenRefreshManager,
    TokenRefreshResult,
)

_ME_PATH = "/backend-api/me"
_SESSION_PATH = "/api/auth/session"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _FakeCookies:
    def __init__(self):
        self.store = {}

    def set(self, name, value, **kwargs):
        self.store[name] = value

    def get(self, name, default=""):
        return self.store.get(name, default)


class _FakeSession:
    """只实现刷 Token 会碰到的两种请求:取会话、验 Token。"""

    def __init__(self, *, session_response=None, me_response=None, me_error=None):
        self.cookies = _FakeCookies()
        self._session_response = session_response
        self._me_response = me_response
        self._me_error = me_error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if _SESSION_PATH in url:
            return self._session_response
        if self._me_error is not None:
            raise self._me_error
        return self._me_response

    def me_calls(self):
        return [url for url in self.calls if _ME_PATH in url]


class _AuthResult:
    def __init__(self):
        self.email = ""
        self.password = ""
        self.access_token = ""
        self.refresh_token = ""
        self.session_token = ""
        self.id_token = ""
        self.cookie_header = ""
        self.totp_secret = ""


class _Account:
    def __init__(self, **overrides):
        self.email = "demo@example.com"
        self.password = "pw"
        self.access_token = "at-old"
        self.refresh_token = ""
        self.session_token = ""
        self.id_token = ""
        self.client_id = ""
        self.totp_secret = ""
        for key, value in overrides.items():
            setattr(self, key, value)


def _manager(fake_session, **overrides):
    logs = []
    manager = TokenRefreshManager(
        proxy_url=None,
        extra_config={},
        log_fn=logs.append,
        **overrides,
    )
    manager._create_session = lambda: fake_session
    manager.logs = logs
    return manager


class SessionTokenVerificationTests(unittest.TestCase):
    def test_access_token_identical_to_previous_is_not_a_success(self):
        """会话端点把旧 AT 原样回放 —— 这是最常见的假成功,不能算刷新成功。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-old"})
        )
        manager = _manager(fake)
        login_result = TokenRefreshResult(
            success=False, error_message="协议登录跑完但没拿到 access_token"
        )

        with mock.patch.object(manager, "refresh_by_login", return_value=login_result) as login:
            result = manager.refresh_account(_Account(session_token="st-1"))

        self.assertFalse(result.success)
        attempt = next(item for item in result.attempts if item.strategy == STRATEGY_SESSION)
        self.assertFalse(attempt.ok)
        self.assertIn("与刷新前相同", attempt.message)
        # 旧值一比对就能定性,不必再打 /me
        self.assertEqual(fake.me_calls(), [])
        # 而且要真的继续降级,不能停在"假成功"上
        login.assert_called_once()

    def test_identical_access_token_falls_back_to_protocol_login(self):
        """假成功之后必须继续降级,走账号密码 + TOTP 那条路拿新 AT。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-old"})
        )
        manager = _manager(fake)
        login_result = TokenRefreshResult(
            success=True, access_token="at-from-login", strategy=STRATEGY_LOGIN
        )

        with mock.patch.object(manager, "refresh_by_login", return_value=login_result) as login:
            result = manager.refresh_account(
                _Account(session_token="st-1", totp_secret="TOTPSECRET")
            )

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_LOGIN)
        self.assertEqual(result.access_token, "at-from-login")
        login.assert_called_once()
        self.assertEqual(login.call_args.args[1], "pw")
        # 2FA 号全靠这个密钥自动算码,丢了下游只能干等
        self.assertEqual(login.call_args.kwargs.get("totp_secret"), "TOTPSECRET")

    def test_rotated_but_rejected_access_token_falls_back(self):
        """换出来的是新 AT,但上游已经把它作废了 —— 同样不算成功。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-rotated"}),
            me_response=_FakeResponse(401, {"error": {"code": "token_invalidated"}}),
        )
        manager = _manager(fake)
        login_result = TokenRefreshResult(
            success=True, access_token="at-from-login", strategy=STRATEGY_LOGIN
        )

        with mock.patch.object(manager, "refresh_by_login", return_value=login_result) as login:
            result = manager.refresh_account(_Account(session_token="st-1"))

        self.assertEqual(result.strategy, STRATEGY_LOGIN)
        login.assert_called_once()
        self.assertEqual(len(fake.me_calls()), 1)
        attempt = next(item for item in result.attempts if item.strategy == STRATEGY_SESSION)
        self.assertIn("校验未通过", attempt.message)

    def test_rotated_and_valid_access_token_wins_without_login(self):
        """真换出了可用的新 AT 就到此为止,不该再打一遍登录链。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-new"}),
            me_response=_FakeResponse(200, {"id": "user-1"}),
        )
        manager = _manager(fake)

        with mock.patch.object(manager, "refresh_by_login") as login:
            result = manager.refresh_account(_Account(session_token="st-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_SESSION)
        self.assertEqual(result.access_token, "at-new")
        login.assert_not_called()

    def test_probe_failure_does_not_condemn_the_new_token(self):
        """验证请求自己没跑通(网络抖动)不能当凭证失效 —— 那比误判成功更坑。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-new"}),
            me_error=RuntimeError("连接被重置"),
        )
        manager = _manager(fake)

        with mock.patch.object(manager, "refresh_by_login") as login:
            result = manager.refresh_account(_Account(session_token="st-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_SESSION)
        login.assert_not_called()

    def test_stale_access_token_is_not_carried_into_a_failed_result(self):
        """session 失败时手里那个废 AT 不能进最终结果,否则会把库里的旧 AT 覆盖掉。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-old"})
        )
        manager = _manager(fake)
        login_result = TokenRefreshResult(
            success=False, error_message="协议登录跑完但没拿到 access_token"
        )

        with mock.patch.object(manager, "refresh_by_login", return_value=login_result):
            result = manager.refresh_account(_Account(session_token="st-1"))

        self.assertFalse(result.success)
        self.assertEqual(result.access_token, "")

    def test_session_is_skipped_without_stored_session_token(self):
        fake = _FakeSession()
        manager = _manager(fake)
        login_result = TokenRefreshResult(
            success=True, access_token="at-from-login", strategy=STRATEGY_LOGIN
        )

        with mock.patch.object(manager, "refresh_by_login", return_value=login_result):
            result = manager.refresh_account(_Account(access_token="", session_token=""))

        self.assertEqual(result.strategy, STRATEGY_LOGIN)
        self.assertEqual(fake.calls, [])


class TokenProbeTests(unittest.TestCase):
    def test_valid_token_is_accepted(self):
        manager = _manager(_FakeSession(me_response=_FakeResponse(200, {"id": "u"})))
        self.assertEqual(manager._probe_access_token("at"), ("valid", ""))
        self.assertEqual(manager.validate_token("at"), (True, None))

    def test_rejected_token_is_invalid(self):
        manager = _manager(
            _FakeSession(me_response=_FakeResponse(401, {"error": {"code": "token_invalidated"}}))
        )
        verdict, detail = manager._probe_access_token("at")
        self.assertEqual(verdict, "invalid")
        self.assertIn("401", detail)
        self.assertFalse(manager.validate_token("at")[0])

    def test_probe_transport_failure_is_unknown(self):
        manager = _manager(_FakeSession(me_error=RuntimeError("超时")))
        self.assertEqual(manager._probe_access_token("at")[0], "unknown")
        # unknown 按有效处理:只否定上游明确拒绝的 AT
        self.assertTrue(manager.validate_token("at")[0])

    def test_missing_token_is_invalid_without_any_request(self):
        fake = _FakeSession()
        manager = _manager(fake)
        self.assertEqual(manager._probe_access_token("")[0], "invalid")
        self.assertEqual(fake.calls, [])


class LazyMailProviderTests(unittest.TestCase):
    def test_resolver_is_not_touched_until_login_is_reached(self):
        calls = []

        def resolver():
            calls.append(1)
            return None, "读不到收件箱"

        manager = _manager(_FakeSession(), mail_provider_resolver=resolver)
        self.assertEqual(calls, [])
        manager._resolve_mail_provider()
        manager._resolve_mail_provider()
        # 只解析一次:失败了也不在后续调用里反复重试
        self.assertEqual(len(calls), 1)
        self.assertEqual(manager.mail_provider, None)

    def test_login_fallback_uses_the_resolved_provider(self):
        provider = object()
        calls = []

        def resolver():
            calls.append(1)
            return provider, ""

        manager = _manager(_FakeSession(), mail_provider_resolver=resolver)
        manager._build_sms_callback = lambda: None
        seen = {}

        class _FakeFlow:
            def __init__(self, config, **kwargs):
                self.result = _AuthResult()

            def run_protocol_login(self, mail_provider, email, password=""):
                seen["provider"] = mail_provider
                self.result.access_token = "at-from-login"
                self.result.refresh_token = "rt-from-login"

        with mock.patch("platforms.chatgpt.protocol.AuthFlow", _FakeFlow), mock.patch(
            "platforms.chatgpt.protocol_log_relay.mirror_protocol_logs",
            contextlib.nullcontext,
        ):
            result = manager.refresh_by_login("demo@example.com", "pw", totp_secret="S")

        self.assertTrue(result.success)
        self.assertEqual(result.refresh_token, "rt-from-login")
        self.assertIs(seen["provider"], provider)
        self.assertEqual(len(calls), 1)


class RefreshAccountDataWiringTests(unittest.TestCase):
    """库侧胶水必须把惰性工厂交出去,否则降级到协议登录时没有收件通道。"""

    def _run(self, **kwargs):
        captured = {}

        class _StubManager:
            def __init__(self, **manager_kwargs):
                captured.update(manager_kwargs)

            def refresh_account(self, account):
                return TokenRefreshResult(success=False, error_message="stub")

        from services.chatgpt_token_refresh import refresh_account_data

        with mock.patch(
            "services.chatgpt_token_refresh.TokenRefreshManager", _StubManager
        ):
            refresh_account_data(
                email="demo@example.com",
                config={},
                **kwargs,
            )
        return captured

    def test_lazy_resolver_is_injected_even_with_session_token(self):
        captured = self._run(
            password="pw",
            extra={"session_token": "st-1", "access_token": "at-old"},
        )
        self.assertIsNotNone(captured.get("mail_provider_resolver"))

    def test_resolver_is_skipped_when_login_fallback_is_disabled(self):
        captured = self._run(
            password="pw",
            extra={"session_token": "st-1"},
            allow_login=False,
        )
        self.assertIsNone(captured.get("mail_provider_resolver"))

    def test_resolver_is_skipped_without_password(self):
        captured = self._run(password="", extra={"session_token": "st-1"})
        self.assertIsNone(captured.get("mail_provider_resolver"))


if __name__ == "__main__":
    unittest.main()
