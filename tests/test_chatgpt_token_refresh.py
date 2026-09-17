"""刷新 Token 的策略编排测试。

编排现在是四条路:RT 刷新 → Session 回放 → Session 换新 → 邮箱密码协议登录。
这里盯三件事:

1. **编排层**:前一条成了就不往下走;RT / Session 都没材料时才降级到协议登录。
2. **Session 回放的验货语义**:``/api/auth/session`` 只回放会话 cookie 里嵌着的
   AT,旧值回放、被上游拒绝都不算成功 —— 这类"假成功"必须继续降级。
3. **Session 换新**:回放捞不到新 AT 时,拿同一份 session_token 恢复会话跑授权链,
   换回的是全新 AT + RT;拿回来的还是旧 AT 同样判失败。

历史上 ``/api/auth/session`` 会返回 200 且带着 ``accessToken``,但那个 AT 就是
会话 cookie 里原有的旧值 —— 库里等于没刷新,调用方却收到了"成功"。
"""

import contextlib
import unittest
from unittest import mock

from platforms.chatgpt.token_refresh import (
    STRATEGY_LOGIN,
    STRATEGY_REFRESH_TOKEN,
    STRATEGY_SESSION,
    STRATEGY_SESSION_EXCHANGE,
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
    """只实现刷 Token 会碰到的请求:RT 换令牌、取会话、验 Token。"""

    def __init__(
        self,
        *,
        token_response=None,
        token_error=None,
        session_response=None,
        me_response=None,
        me_error=None,
    ):
        self.cookies = _FakeCookies()
        self._token_response = token_response
        self._token_error = token_error
        self._session_response = session_response
        self._me_response = me_response
        self._me_error = me_error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(url)
        if self._token_error is not None:
            raise self._token_error
        return self._token_response

    def get(self, url, **kwargs):
        self.calls.append(url)
        if _SESSION_PATH in url:
            return self._session_response
        if self._me_error is not None:
            raise self._me_error
        return self._me_response

    def me_calls(self):
        return [url for url in self.calls if _ME_PATH in url]

    def session_calls(self):
        return [url for url in self.calls if _SESSION_PATH in url]


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
        self.device_id = ""


class _Account:
    def __init__(self, **overrides):
        self.email = "demo@example.com"
        self.password = "pw"
        self.access_token = "at-old"
        self.refresh_token = ""
        self.session_token = ""
        self.id_token = ""
        self.device_id = ""
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


def _login_success(access_token="at-from-login"):
    return TokenRefreshResult(
        success=True, access_token=access_token, strategy=STRATEGY_LOGIN
    )


def _login_failure(message="协议登录跑完但没拿到 access_token"):
    return TokenRefreshResult(success=False, error_message=message)


def _exchange_success(access_token="at-from-exchange", refresh_token="rt-from-exchange"):
    return TokenRefreshResult(
        success=True,
        access_token=access_token,
        refresh_token=refresh_token,
        strategy=STRATEGY_SESSION_EXCHANGE,
    )


def _exchange_failure(message="Session 换新跑完但没拿到 access_token"):
    return TokenRefreshResult(success=False, error_message=message)


class RefreshOrchestrationTests(unittest.TestCase):
    """四条路的编排:前一条成了就不往下走。"""

    def test_rt_success_wins_without_touching_anything_else(self):
        fake = _FakeSession(
            token_response=_FakeResponse(
                200,
                {
                    "access_token": "at-new",
                    "refresh_token": "rt-new",
                    "expires_in": 3600,
                },
            )
        )
        manager = _manager(fake)

        with mock.patch.object(manager, "refresh_by_session_token") as session, \
                mock.patch.object(manager, "refresh_by_session_exchange") as exchange, \
                mock.patch.object(manager, "refresh_by_login") as login:
            result = manager.refresh_account(_Account(refresh_token="rt-old"))

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_REFRESH_TOKEN)
        self.assertEqual(result.access_token, "at-new")
        self.assertEqual(result.refresh_token, "rt-new")
        session.assert_not_called()
        exchange.assert_not_called()
        login.assert_not_called()
        self.assertEqual(fake.session_calls(), [])

    def test_session_replay_success_skips_exchange_and_login(self):
        """回放直接捞出可用的新 AT —— 后面两条都不该跑。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-new"}),
            me_response=_FakeResponse(200, {"id": "user-1"}),
        )
        manager = _manager(fake)

        with mock.patch.object(manager, "refresh_by_session_exchange") as exchange, \
                mock.patch.object(manager, "refresh_by_login") as login:
            result = manager.refresh_account(_Account(session_token="st-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_SESSION)
        self.assertEqual(result.access_token, "at-new")
        exchange.assert_not_called()
        login.assert_not_called()
        self.assertEqual(len(fake.session_calls()), 1)

    def test_replayed_old_access_token_falls_back_to_session_exchange(self):
        """核心回归点:回放回来的是旧 AT(假成功)时必须继续降级,不能就此收工。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-old"})
        )
        manager = _manager(fake)

        with mock.patch.object(
            manager, "refresh_by_session_exchange", return_value=_exchange_success()
        ) as exchange, mock.patch.object(manager, "refresh_by_login") as login:
            result = manager.refresh_account(_Account(session_token="st-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_SESSION_EXCHANGE)
        self.assertEqual(result.access_token, "at-from-exchange")
        self.assertEqual(result.refresh_token, "rt-from-exchange")
        exchange.assert_called_once()
        login.assert_not_called()
        # 旧值一比对就能定性,不必再打 /me
        self.assertEqual(fake.me_calls(), [])
        session_attempt = next(
            item for item in result.attempts if item.strategy == STRATEGY_SESSION
        )
        self.assertFalse(session_attempt.ok)
        self.assertIn("与刷新前相同", session_attempt.message)

    def test_failed_rt_falls_back_to_session_then_login(self):
        """RT 死了、Session 也没材料 —— 一路降级到协议登录。"""
        fake = _FakeSession(token_response=_FakeResponse(400, text='{"error":"invalid_grant"}'))
        manager = _manager(fake)

        with mock.patch.object(
            manager, "refresh_by_login", return_value=_login_success()
        ) as login:
            result = manager.refresh_account(
                _Account(refresh_token="rt-dead", totp_secret="TOTPSECRET")
            )

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_LOGIN)
        login.assert_called_once()
        self.assertEqual(login.call_args.args[1], "pw")
        # 2FA 号全靠这个密钥自动算码,丢了下游只能干等
        self.assertEqual(login.call_args.kwargs.get("totp_secret"), "TOTPSECRET")
        rt_attempt = next(
            item for item in result.attempts if item.strategy == STRATEGY_REFRESH_TOKEN
        )
        self.assertFalse(rt_attempt.ok)
        self.assertIn("HTTP 400", rt_attempt.message)

    def test_session_exchange_failure_falls_back_to_login(self):
        """换新也失败时,最后才轮到邮箱密码协议登录。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-old"})
        )
        manager = _manager(fake)

        with mock.patch.object(
            manager, "refresh_by_session_exchange", return_value=_exchange_failure()
        ) as exchange, mock.patch.object(
            manager, "refresh_by_login", return_value=_login_success()
        ) as login:
            result = manager.refresh_account(_Account(session_token="st-1"))

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_LOGIN)
        exchange.assert_called_once()
        login.assert_called_once()

    def test_login_fallback_can_be_disabled(self):
        """关掉兜底就只剩 RT 与 Session 两条快路径,失败原因要如实带回来。"""
        fake = _FakeSession(
            token_response=_FakeResponse(500, text="boom"),
            session_response=_FakeResponse(403, text="forbidden"),
        )
        manager = _manager(fake, allow_login=False)

        with mock.patch.object(
            manager, "refresh_by_session_exchange", return_value=_exchange_failure()
        ), mock.patch.object(manager, "refresh_by_login") as login:
            result = manager.refresh_account(
                _Account(refresh_token="rt-dead", session_token="st-1")
            )

        self.assertFalse(result.success)
        login.assert_not_called()
        self.assertIn("RT 刷新", result.error_message)
        self.assertIn("Session 刷新", result.error_message)
        login_attempt = next(
            item for item in result.attempts if item.strategy == STRATEGY_LOGIN
        )
        self.assertIn("已关闭协议登录兜底", login_attempt.message)

    def test_login_is_skipped_without_password(self):
        fake = _FakeSession()
        manager = _manager(fake)

        with mock.patch.object(manager, "refresh_by_login") as login:
            result = manager.refresh_account(_Account(password=""))

        self.assertFalse(result.success)
        login.assert_not_called()
        self.assertEqual(fake.calls, [])
        self.assertIn("没有密码", result.error_message)

    def test_stale_access_token_is_not_carried_into_a_failed_result(self):
        """失败那条路手里的废 AT 不能进最终结果,否则会把库里的旧 AT 覆盖掉。"""
        fake = _FakeSession()
        manager = _manager(fake)
        stale = TokenRefreshResult(
            success=False, access_token="at-stale", error_message="RT 刷新未果"
        )

        with mock.patch.object(manager, "refresh_by_oauth_token", return_value=stale), \
                mock.patch.object(
                    manager, "refresh_by_session_exchange", return_value=_exchange_failure()
                ), \
                mock.patch.object(manager, "refresh_by_login", return_value=_login_failure()):
            result = manager.refresh_account(
                _Account(refresh_token="rt-dead", session_token="st-1")
            )

        self.assertFalse(result.success)
        self.assertEqual(result.access_token, "")


class SessionTokenReplayTests(unittest.TestCase):
    """``refresh_by_session_token``:回放语义 + 验货,一步都不能省。"""

    def test_access_token_identical_to_previous_is_not_a_success(self):
        """会话端点把旧 AT 原样回放 —— 这是最常见的假成功,不能算刷新成功。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-old"})
        )
        manager = _manager(fake)

        result = manager.refresh_by_session_token(
            "st-1", previous_access_token="at-old"
        )

        self.assertFalse(result.success)
        self.assertEqual(result.strategy, STRATEGY_SESSION)
        self.assertIn("与刷新前相同", result.error_message)
        # 旧值一比对就能定性,不必再打 /me
        self.assertEqual(fake.me_calls(), [])

    def test_rotated_but_rejected_access_token_fails(self):
        """换出来的是新 AT,但上游已经把它作废了 —— 同样不算成功。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-rotated"}),
            me_response=_FakeResponse(401, {"error": {"code": "token_invalidated"}}),
        )
        manager = _manager(fake)

        result = manager.refresh_by_session_token("st-1", previous_access_token="at-old")

        self.assertFalse(result.success)
        self.assertIn("校验未通过", result.error_message)
        self.assertEqual(len(fake.me_calls()), 1)

    def test_rotated_and_valid_access_token_succeeds(self):
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-new"}),
            me_response=_FakeResponse(200, {"id": "user-1"}),
        )
        manager = _manager(fake)

        result = manager.refresh_by_session_token("st-1", previous_access_token="at-old")

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_SESSION)
        self.assertEqual(result.access_token, "at-new")

    def test_probe_failure_does_not_condemn_the_new_token(self):
        """验证请求自己没跑通(网络抖动)不能当凭证失效 —— 那比误判成功更坑。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-new"}),
            me_error=RuntimeError("连接被重置"),
        )
        manager = _manager(fake)

        result = manager.refresh_by_session_token("st-1", previous_access_token="at-old")

        self.assertTrue(result.success)
        self.assertEqual(result.access_token, "at-new")

    def test_rotated_session_cookie_is_carried_back(self):
        """服务端轮换了 session cookie 就得带回去落库。"""
        fake = _FakeSession(
            session_response=_FakeResponse(200, {"accessToken": "at-new"}),
            me_response=_FakeResponse(200, {"id": "user-1"}),
        )
        manager = _manager(fake)
        original_get = fake.get

        def _get(url, **kwargs):
            response = original_get(url, **kwargs)
            if _SESSION_PATH in url:
                fake.cookies.set("__Secure-next-auth.session-token", "st-rotated")
            return response

        fake.get = _get

        result = manager.refresh_by_session_token("st-1", previous_access_token="at-old")

        self.assertTrue(result.success)
        self.assertEqual(result.session_token, "st-rotated")

    def test_http_error_fails(self):
        fake = _FakeSession(session_response=_FakeResponse(403, text="forbidden"))
        manager = _manager(fake)

        result = manager.refresh_by_session_token("st-1")

        self.assertFalse(result.success)
        self.assertIn("HTTP 403", result.error_message)

    def test_missing_access_token_in_payload_fails(self):
        fake = _FakeSession(session_response=_FakeResponse(200, {}))
        manager = _manager(fake)

        result = manager.refresh_by_session_token("st-1")

        self.assertFalse(result.success)
        self.assertIn("未找到 accessToken", result.error_message)


class SessionExchangeTests(unittest.TestCase):
    """``refresh_by_session_exchange``:恢复会话 + 授权链换全新 AT / RT。"""

    def _run(self, manager, flow_cls, **kwargs):
        with mock.patch("platforms.chatgpt.protocol.AuthFlow", flow_cls), mock.patch(
            "platforms.chatgpt.protocol_log_relay.mirror_protocol_logs",
            contextlib.nullcontext,
        ):
            return manager.refresh_by_session_exchange("st-1", **kwargs)

    def test_fresh_tokens_win(self):
        seen = {}

        class _FakeFlow:
            def __init__(self, config, **kwargs):
                self.result = _AuthResult()
                seen["env"] = kwargs.get("env_overrides") or {}

            def from_existing_credentials(self, session_token, access_token, device_id):
                seen["credentials"] = (session_token, access_token, device_id)
                self.result.access_token = "at-old"

            def oauth_codex_rt_exchange(self, mail_provider=None):
                seen["provider"] = mail_provider
                self.result.access_token = "at-new"
                self.result.refresh_token = "rt-new"
                self.result.session_token = "st-new"
                return True

        manager = _manager(_FakeSession())
        result = self._run(
            manager, _FakeFlow, access_token="at-old", previous_access_token="at-old"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.strategy, STRATEGY_SESSION_EXCHANGE)
        self.assertEqual(result.access_token, "at-new")
        self.assertEqual(result.refresh_token, "rt-new")
        self.assertEqual(result.session_token, "st-new")
        self.assertEqual(seen["credentials"][0], "st-1")
        # 会话复用必须清掉 prompt=login,否则第一次授权必然被打回 /log-in
        self.assertEqual(seen["env"].get("OAUTH_CODEX_PROMPT"), "")

    def test_same_access_token_as_before_is_not_a_success(self):
        """授权链跑完还是那张旧 AT —— 会话没换出新凭证,不能算成功。"""

        class _FakeFlow:
            def __init__(self, config, **kwargs):
                self.result = _AuthResult()

            def from_existing_credentials(self, session_token, access_token, device_id):
                self.result.access_token = "at-old"
                self.result.session_token = "st-1"

            def oauth_codex_rt_exchange(self, mail_provider=None):
                return False

        manager = _manager(_FakeSession())
        result = self._run(
            manager, _FakeFlow, access_token="at-old", previous_access_token="at-old"
        )

        self.assertFalse(result.success)
        self.assertIn("与刷新前相同", result.error_message)

    def test_expired_session_is_reported(self):
        class _FakeFlow:
            def __init__(self, config, **kwargs):
                self.result = _AuthResult()

            def from_existing_credentials(self, session_token, access_token, device_id):
                return None

            def oauth_codex_rt_exchange(self, mail_provider=None):
                raise AssertionError("会话已失效就不该继续跑授权链")

        manager = _manager(_FakeSession())
        result = self._run(manager, _FakeFlow, access_token="at-old")

        self.assertFalse(result.success)
        self.assertIn("已失效", result.error_message)

    def test_credentials_are_kept_when_the_tail_blows_up(self):
        """授权链末段炸了,但 AT 已经到手 —— 不该连凭证一起扔掉。"""

        class _FakeFlow:
            def __init__(self, config, **kwargs):
                self.result = _AuthResult()

            def from_existing_credentials(self, session_token, access_token, device_id):
                self.result.access_token = "at-old"

            def oauth_codex_rt_exchange(self, mail_provider=None):
                self.result.access_token = "at-new"
                self.result.refresh_token = "rt-new"
                raise RuntimeError("拉 session 失败")

        manager = _manager(_FakeSession())
        result = self._run(
            manager, _FakeFlow, access_token="at-old", previous_access_token="at-old"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.access_token, "at-new")
        self.assertEqual(result.refresh_token, "rt-new")

    def test_replayed_token_rejected_by_upstream_is_not_a_success(self):
        """授权链没换出 code 时,手上那张 AT 只是会话回放 —— 被上游拒绝就不算成功。"""

        class _FakeFlow:
            def __init__(self, config, **kwargs):
                self.result = _AuthResult()

            def from_existing_credentials(self, session_token, access_token, device_id):
                self.result.access_token = "at-old"

            def oauth_codex_rt_exchange(self, mail_provider=None):
                self.result.access_token = "at-replayed"
                return False

        fake = _FakeSession(
            me_response=_FakeResponse(401, {"error": {"code": "token_invalidated"}})
        )
        manager = _manager(fake)
        result = self._run(
            manager, _FakeFlow, access_token="at-old", previous_access_token="at-old"
        )

        self.assertFalse(result.success)
        self.assertIn("校验未通过", result.error_message)
        self.assertEqual(len(fake.me_calls()), 1)

    def test_without_session_token_nothing_is_sent(self):
        fake = _FakeSession()
        manager = _manager(fake)

        result = manager.refresh_by_session_exchange("", access_token="at-old")

        self.assertFalse(result.success)
        self.assertIn("没有 session_token", result.error_message)
        self.assertEqual(fake.calls, [])

    def test_device_id_is_derived_from_email_when_missing(self):
        seen = {}

        class _FakeFlow:
            def __init__(self, config, **kwargs):
                self.result = _AuthResult()

            def from_existing_credentials(self, session_token, access_token, device_id):
                seen["device_id"] = device_id
                self.result.access_token = "at-new"

            def oauth_codex_rt_exchange(self, mail_provider=None):
                return True

        manager = _manager(_FakeSession())
        self._run(manager, _FakeFlow, email="demo@example.com")

        self.assertTrue(seen["device_id"])
        # 同一个号每次刷新必须是同一台"设备"
        self.assertEqual(seen["device_id"], manager._derive_device_id("demo@example.com"))


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

    def test_session_exchange_uses_the_resolved_provider(self):
        """换新被打回 /log-in 时要能收码,所以也得拿到惰性解析出来的通道。"""
        provider = object()
        calls = []

        def resolver():
            calls.append(1)
            return provider, ""

        manager = _manager(_FakeSession(), mail_provider_resolver=resolver)
        seen = {}

        class _FakeFlow:
            def __init__(self, config, **kwargs):
                self.result = _AuthResult()

            def from_existing_credentials(self, session_token, access_token, device_id):
                self.result.access_token = "at-new"

            def oauth_codex_rt_exchange(self, mail_provider=None):
                seen["provider"] = mail_provider
                return True

        with mock.patch("platforms.chatgpt.protocol.AuthFlow", _FakeFlow), mock.patch(
            "platforms.chatgpt.protocol_log_relay.mirror_protocol_logs",
            contextlib.nullcontext,
        ):
            result = manager.refresh_by_session_exchange("st-1")

        self.assertTrue(result.success)
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
                captured["account"] = account
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

    def test_lazy_resolver_is_injected_when_rt_could_fall_back(self):
        captured = self._run(
            password="pw",
            extra={"refresh_token": "rt-1", "access_token": "at-old"},
        )
        self.assertIsNotNone(captured.get("mail_provider_resolver"))

    def test_resolver_is_skipped_when_login_fallback_is_disabled(self):
        captured = self._run(
            password="pw",
            extra={"refresh_token": "rt-1"},
            allow_login=False,
        )
        self.assertIsNone(captured.get("mail_provider_resolver"))

    def test_resolver_is_skipped_without_password(self):
        captured = self._run(password="", extra={"refresh_token": "rt-1"})
        self.assertIsNone(captured.get("mail_provider_resolver"))

    def test_session_and_device_credentials_are_forwarded(self):
        """Session 换新要的 session_token / access_token / device_id 都得带上。"""
        captured = self._run(
            password="",
            token="at-from-token-column",
            extra={
                "session_token": "st-1",
                "device_id": "dev-1",
            },
        )
        account = captured["account"]
        self.assertEqual(account.session_token, "st-1")
        self.assertEqual(account.device_id, "dev-1")
        self.assertEqual(account.access_token, "at-from-token-column")


class RefreshTargetSelectionTests(unittest.TestCase):
    """选号判据跟着编排一起放宽:有 session_token 的号也能刷。

    ``account_can_refresh`` 只用到 ``get_extra() / email / password``,这里用
    鸭子类型替身,避免为了跑测试去建数据库引擎。
    """

    class _Model:
        def __init__(self, *, email="demo@example.com", password="", extra=None):
            self.email = email
            self.password = password
            self._extra = dict(extra or {})

        def get_extra(self):
            return dict(self._extra)

    def test_session_only_account_is_refreshable(self):
        from services.chatgpt_token_refresh import account_can_refresh

        model = self._Model(extra={"session_token": "st-1"})
        self.assertTrue(account_can_refresh(model))

    def test_refresh_token_account_is_refreshable(self):
        from services.chatgpt_token_refresh import account_can_refresh

        model = self._Model(extra={"refresh_token": "rt-1"})
        self.assertTrue(account_can_refresh(model))

    def test_email_password_account_is_refreshable(self):
        from services.chatgpt_token_refresh import account_can_refresh

        model = self._Model(password="pw", extra={"session_token": "st-1"})
        self.assertTrue(account_can_refresh(model))

    def test_account_without_any_credential_is_not_refreshable(self):
        from services.chatgpt_token_refresh import account_can_refresh

        model = self._Model(password="", extra={"id_token": "id-1"})
        self.assertFalse(account_can_refresh(model))


if __name__ == "__main__":
    unittest.main()
