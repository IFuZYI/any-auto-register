"""Token 刷新模块

按代价从低到高排两条路,第一条成了就不往下走:

    ① RT 刷新(``refresh_by_oauth_token``):拿 refresh_token 走 OAuth
       ``/oauth/token``。一次 POST 就完事,不碰风控,而且能顺带换回新的 RT。

    ② 协议登录(``refresh_by_login``):RT 为空或已失效时的兜底。邮箱 + 密码
       重跑一遍协议登录链,库里有 TOTP 密钥就自动算码过 2FA。这条路要几十秒、
       可能要邮箱验证码,但产出最全(AT + RT + session)。

两条路都以「拿到新的 access_token」为最低目标 —— 这是本模块存在的意义,
顺带换到的 RT / session_token 一并带回,由调用方决定落库哪些。

关于 session_token:``refresh_by_session_token`` 与配套的 ``_probe_access_token``
仍然保留,但**已退出刷新编排**。``/api/auth/session`` 只是把会话 cookie 里
已经嵌着的 AT 回放出来,它自己不会去找 OpenAI 换新的 —— 拿到 200 也说明不了
凭证是新的,得靠比对旧值 + ``/backend-api/me`` 验货兜底,会话已失效时更是直接
403。既然走一遍登录链能稳稳拿到全套新凭证,就没必要再留着这条既慢又不可信的
中间路径。保留代码是给需要单点调用它的场景备用。
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any, Tuple, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from curl_cffi import requests as cffi_requests

from platforms.chatgpt.protocol.response_summary import describe_error

logger = logging.getLogger(__name__)

# 策略标识,落库留痕与前端展示都用这套
# (STRATEGY_SESSION 已不在编排里,保留给备用的 refresh_by_session_token 留痕)
STRATEGY_REFRESH_TOKEN = "refresh_token"
STRATEGY_SESSION = "session"
STRATEGY_LOGIN = "login"

_STRATEGY_LABELS = {
    STRATEGY_REFRESH_TOKEN: "RT 刷新",
    STRATEGY_SESSION: "Session 刷新",
    STRATEGY_LOGIN: "协议登录",
}


def strategy_label(strategy: str) -> str:
    return _STRATEGY_LABELS.get(strategy, strategy or "未知方式")


@dataclass
class RefreshAttempt:
    """一条策略的执行结果,失败原因要能直接展示给用户。"""

    strategy: str
    ok: bool
    message: str = ""


@dataclass
class TokenRefreshResult:
    """Token 刷新结果。

    ``success`` 只在拿到新的 access_token 时为 True。顺带刷新到的
    refresh_token / session_token 无论成败都带回来,调用方可以一起落库。
    """
    success: bool
    access_token: str = ""
    refresh_token: str = ""
    session_token: str = ""
    id_token: str = ""
    cookie_header: str = ""
    strategy: str = ""
    expires_at: Optional[datetime] = None
    error_message: str = ""
    attempts: list[RefreshAttempt] = field(default_factory=list)

    def summary(self) -> str:
        if self.success:
            extras = []
            if self.refresh_token:
                extras.append("含 RT")
            if self.session_token:
                extras.append("含 Session")
            suffix = f"({'、'.join(extras)})" if extras else ""
            return f"Token 刷新成功（{strategy_label(self.strategy)}）{suffix}".strip()
        return self.error_message or "Token 刷新失败"


class TokenRefreshManager:
    """Token 刷新管理器。

    优先级:RT 刷新 → 协议登录兜底。
    """

    # OpenAI OAuth 端点
    SESSION_URL = "https://chatgpt.com/api/auth/session"
    TOKEN_URL = "https://auth.openai.com/oauth/token"

    def __init__(
        self,
        proxy_url: Optional[str] = None,
        *,
        extra_config: Optional[dict] = None,
        mail_provider=None,
        mail_unavailable_reason: str = "",
        mail_provider_resolver: Optional[Callable[[], tuple]] = None,
        allow_login: bool = True,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            proxy_url: 代理 URL
            extra_config: 全局配置,协议登录要用(OTP 超时、接码参数等)
            mail_provider: 邮箱注入点,协议登录撞上邮箱验证码时用
            mail_unavailable_reason: 读不到收件箱时的原因,用于拼人话报错
            mail_provider_resolver: 惰性邮箱工厂,返回 ``(provider, 失败原因)``。
                只有真要走协议登录时才会被调用一次 —— RT 快路径压根用不上收件箱,
                不该为了一个大概率不跑的分支先去连一遍邮箱服务;而 RT 失效降级到
                协议登录那一刻,又必须拿得到通道,所以只能靠惰性解析同时满足这两头。
            allow_login: 关掉就不走协议登录兜底
            log_fn: 日志回调,后台任务用它把过程实时推给前端
        """
        self.proxy_url = proxy_url
        self.extra_config = dict(extra_config or {})
        self.mail_provider = mail_provider
        self.mail_unavailable_reason = mail_unavailable_reason
        self._mail_provider_resolver = mail_provider_resolver
        self.allow_login = allow_login
        self._log_fn = log_fn
        self.log = log_fn or logger.info
        from .constants import OAUTH_CLIENT_ID, OAUTH_REDIRECT_URI
        self._oauth_client_id = OAUTH_CLIENT_ID
        self._oauth_redirect_uri = OAUTH_REDIRECT_URI

    def _create_session(self) -> cffi_requests.Session:
        """创建 HTTP 会话"""
        session = cffi_requests.Session(impersonate="chrome120", proxy=self.proxy_url)
        return session

    def _resolve_mail_provider(self):
        """惰性取邮箱注入点,只在真要走协议登录时才连一次收件通道。"""
        if self.mail_provider is not None or self._mail_provider_resolver is None:
            return self.mail_provider

        resolver = self._mail_provider_resolver
        self._mail_provider_resolver = None   # 只解析一次,失败了也不反复重试
        try:
            provider, reason = resolver()
        except Exception as exc:
            provider, reason = None, f"解析收件通道失败: {exc}"

        self.mail_provider = provider
        if reason:
            self.mail_unavailable_reason = reason
        if provider is None:
            self.log(
                f"[刷新Token] 暂时读不到收件箱（{self.mail_unavailable_reason}），"
                "需要邮箱验证码时会失败"
            )
        else:
            self.log(f"[刷新Token] 收件通道: {getattr(provider, 'display_name', '邮箱')}")
        return provider

    # ── 策略一:OAuth Refresh Token ──

    def refresh_by_oauth_token(
        self,
        refresh_token: str,
        client_id: Optional[str] = None
    ) -> TokenRefreshResult:
        """
        使用 OAuth Refresh Token 刷新(最优先,一次 POST 就能出 AT + 新 RT)

        Args:
            refresh_token: OAuth 刷新令牌
            client_id: OAuth Client ID

        Returns:
            TokenRefreshResult: 刷新结果
        """
        result = TokenRefreshResult(success=False, strategy=STRATEGY_REFRESH_TOKEN)

        try:
            session = self._create_session()

            # 使用配置的 client_id 或默认值
            client_id = client_id or self._oauth_client_id

            # 构建请求体
            token_data = {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "redirect_uri": self._oauth_redirect_uri
            }

            response = session.post(
                self.TOKEN_URL,
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                    "accept": "application/json"
                },
                data=token_data,
                timeout=30
            )

            if response.status_code != 200:
                result.error_message = (
                    f"OAuth token 刷新失败: HTTP {response.status_code}"
                    f"（{describe_error(response.text)}）"
                )
                logger.warning(result.error_message)
                return result

            data = response.json()

            # 提取令牌
            access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token", refresh_token)
            expires_in = data.get("expires_in", 3600)

            if not access_token:
                result.error_message = "OAuth token 刷新失败: 未找到 access_token"
                logger.warning(result.error_message)
                return result

            # 计算过期时间
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            result.success = True
            result.access_token = access_token
            result.refresh_token = new_refresh_token
            result.id_token = str(data.get("id_token") or "")
            result.expires_at = expires_at

            logger.info(f"OAuth token 刷新成功，过期时间: {expires_at}")
            return result

        except Exception as e:
            result.error_message = f"OAuth token 刷新异常: {str(e)}"
            logger.error(result.error_message)
            return result

    # ── 备用策略:Session Token(已不在编排里) ──

    def refresh_by_session_token(
        self,
        session_token: str,
        *,
        previous_access_token: str = "",
    ) -> TokenRefreshResult:
        """
        使用 Session Token 刷新(只出 AT,不出 RT)

        **注意:这条策略已不在 ``refresh_account`` 的编排里**,保留仅供单点
        调用备用。调用方需自己承担下面这套验货成本,否则很容易把旧 AT 当新
        凭证收下。

        ``/api/auth/session`` 只是把 NextAuth 会话 cookie 里已经嵌着的 AT 回放
        出来,它自己不会去找 OpenAI 换新的 —— 会话里的 AT 早就过期或被作废时,
        这个接口照样返回 200 和一个**与刷新前一模一样**的旧 AT。所以"HTTP 200
        且有 accessToken"不足以判成功,得比对旧值再实打实验一次:

        - 换回来的 AT 与刷新前相同 → 会话没换出新凭证,判失败,交给上层降级;
        - 换回来的是新 AT → 拿它打 ``/backend-api/me`` 验,被上游明确拒绝
          (401/403)同样判失败;
        - 探测本身没跑通(网络抖动、5xx)不否定结果,只标记未验证。

        Args:
            session_token: 会话令牌
            previous_access_token: 刷新前的 access_token,用来判断有没有真换新

        Returns:
            TokenRefreshResult: 刷新结果
        """
        result = TokenRefreshResult(success=False, strategy=STRATEGY_SESSION)

        try:
            session = self._create_session()

            # 设置会话 Cookie
            session.cookies.set(
                "__Secure-next-auth.session-token",
                session_token,
                domain=".chatgpt.com",
                path="/"
            )

            # 请求会话端点
            response = session.get(
                self.SESSION_URL,
                headers={
                    "accept": "application/json",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
                timeout=30
            )

            if response.status_code != 200:
                result.error_message = f"Session token 刷新失败: HTTP {response.status_code}"
                logger.warning(result.error_message)
                return result

            data = response.json()

            # 提取 access_token
            access_token = str(data.get("accessToken") or "").strip()
            if not access_token:
                result.error_message = "Session token 刷新失败: 未找到 accessToken"
                logger.warning(result.error_message)
                return result

            # 提取过期时间
            expires_at = None
            expires_str = data.get("expires")
            if expires_str:
                try:
                    expires_at = datetime.fromisoformat(str(expires_str).replace("Z", "+00:00"))
                except Exception as exc:
                    logger.debug(f"session expires 解析失败: {expires_str} ({exc})")

            previous = str(previous_access_token or "").strip()
            if previous and access_token == previous:
                result.error_message = (
                    "Session token 刷新失败: 换回的 access_token 与刷新前相同，"
                    "会话里没有新凭证"
                )
                logger.warning(result.error_message)
                return result

            verdict, detail = self._probe_access_token(access_token)
            if verdict == "invalid":
                result.error_message = (
                    f"Session token 刷新失败: 换回的 access_token 校验未通过（{detail}）"
                )
                logger.warning(result.error_message)
                return result

            result.success = True
            result.access_token = access_token
            # 服务端可能轮换 session cookie,换了就带回去落库
            rotated = session.cookies.get("__Secure-next-auth.session-token", "")
            result.session_token = str(rotated or session_token)
            result.expires_at = expires_at

            if verdict == "unknown":
                logger.info(f"Session 换出 access_token 但有效性未验证: {detail}")
            logger.info(f"Session token 刷新成功，过期时间: {expires_at}")
            return result

        except Exception as e:
            result.error_message = f"Session token 刷新异常: {str(e)}"
            logger.error(result.error_message)
            return result

    # ── 策略二:协议登录兜底 ──

    def refresh_by_login(
        self,
        email: str,
        password: str,
        *,
        totp_secret: str = "",
    ) -> TokenRefreshResult:
        """邮箱 + 密码重跑协议登录链拿 AT(纯协议,不开浏览器)。

        RT 没有或已失效时才走这条。库里存了 TOTP 密钥就自动算码过 2FA;
        OpenAI 要邮箱验证码时靠注入的 ``mail_provider`` 收码。

        产出最全:AT + RT + session_token 一起带回来。
        """
        result = TokenRefreshResult(success=False, strategy=STRATEGY_LOGIN)

        if not email:
            result.error_message = "账号没有邮箱，无法协议登录"
            return result
        if not password:
            result.error_message = "库里没有密码，无法协议登录"
            return result

        from platforms.chatgpt.protocol import AuthFlow, Config
        from platforms.chatgpt.protocol_log_relay import mirror_protocol_logs
        from platforms.chatgpt.rt_backfill import MailboxUnavailableProvider

        flow: Optional[AuthFlow] = None
        failure = ""
        try:
            flow = AuthFlow(
                Config(proxy=self.proxy_url),
                sms_callback=self._build_sms_callback(),
                env_overrides=self._login_env_overrides(),
                account_callback=lambda _email: {
                    "password": password,
                    "totp_secret": totp_secret,
                },
            )
            if totp_secret:
                flow.result.totp_secret = totp_secret

            provider = self._resolve_mail_provider() or MailboxUnavailableProvider(
                email, self.mail_unavailable_reason
            )
            with mirror_protocol_logs(self._log_fn):
                flow.run_protocol_login(provider, email, password)
        except Exception as exc:
            # 中断请求(手动停止/跳过)必须原样抛出去,不能当成"这条策略失败了"
            from core.task_runtime import TaskInterruption

            if isinstance(exc, TaskInterruption):
                raise
            failure = str(exc) or exc.__class__.__name__
            logger.warning(f"协议登录报错: {failure}")

        # 即便末段抛异常,已经到手的凭证也不该扔掉 —— AT 是链路中段拿到的
        if flow is not None:
            auth = flow.result
            for attr in ("access_token", "refresh_token", "session_token", "id_token", "cookie_header"):
                value = str(getattr(auth, attr, "") or "").strip()
                if value:
                    setattr(result, attr, value)

        if result.access_token:
            result.success = True
            if failure:
                logger.info(f"协议登录末段报错但 AT 已到手: {failure}")
            return result

        result.error_message = failure or "协议登录跑完但没拿到 access_token"
        return result

    def _build_sms_callback(self):
        """登录链可能被打到 add-phone,配了接码就顺手过掉。"""
        try:
            from services.sms_service import build_phone_callback, resolve_sms_settings
        except Exception:
            return None

        settings = resolve_sms_settings(self.extra_config)
        return build_phone_callback(
            settings,
            log_fn=lambda message: self.log(f"[刷新Token][接码] {message}"),
            proxy=self.proxy_url,
        )

    def _login_env_overrides(self) -> dict:
        """协议登录的开关:要 AT 也要 RT,顺手把 RT 一起换回来。"""
        merged = {
            # 已有账号登录,别让协议层把"这邮箱已注册"当失败
            "WEBUI_ALLOW_LOGIN": "1",
            # 刷新场景一轮里可能要试两次 authorize,默认的"本轮只试一次"会吞掉第二次
            "OAUTH_CODEX_RT_EXCHANGE": "1",
            "OAUTH_CODEX_RT_ALLOW_RETRY": "1",
        }
        merged["OTP_TIMEOUT"] = str(self._otp_timeout())
        for config_key, env_key in (
            ("sms_per_phone_timeout", "OPENAI_PHONE_OTP_TIMEOUT"),
            ("sms_max_phone_attempts", "OPENAI_PHONE_MAX_ATTEMPTS"),
            ("sms_code_retries_per_phone", "OPENAI_PHONE_OTP_CODE_RETRIES"),
            ("chatgpt_phone_number", "OPENAI_PHONE_NUMBER"),
        ):
            value = str(self.extra_config.get(config_key) or "").strip()
            if value:
                merged[env_key] = value
        return merged

    def _otp_timeout(self) -> int:
        for key in ("mailbox_otp_timeout_seconds", "email_otp_timeout_seconds", "otp_timeout"):
            try:
                seconds = int(str(self.extra_config.get(key) or "").strip())
            except ValueError:
                continue
            if seconds > 0:
                return seconds
        return 180

    # ── 编排 ──

    def refresh_account(self, account) -> TokenRefreshResult:
        """
        刷新账号的 Token。

        优先级:
        1. OAuth Refresh Token 刷新(最快,能顺带换新 RT)
        2. 邮箱 + 密码协议登录(兜底,产出最全但最慢)

        Session 刷新(``refresh_by_session_token``)已不参与编排,保留备用。

        Args:
            account: 账号对象,需带 email / password / refresh_token /
                     client_id / totp_secret 等字段

        Returns:
            TokenRefreshResult: 刷新结果,``success`` 表示拿到了新的 access_token
        """
        email = str(getattr(account, "email", "") or "")
        final = TokenRefreshResult(success=False)

        def _absorb(partial: TokenRefreshResult, *, usable: bool) -> None:
            """把某条策略拿到的凭证并进最终结果,只覆盖非空值。

            一条路拿到的凭证(比如协议登录途中顺带换到的 RT / session_token),
            不该被后面失败返回的空值抹掉。

            ``usable=False`` 时不吸收 access_token:失败那条路手里往往正攥着
            一个已被上游作废的 AT,收进来会被 ``build_extra_patch`` 写回库里,
            把原先还能用的 AT 一起弄坏。其余凭证(RT/session/cookies)无论
            成败都值得留下。
            """
            for attr in ("refresh_token", "session_token", "id_token", "cookie_header"):
                value = str(getattr(partial, attr, "") or "").strip()
                if value:
                    setattr(final, attr, value)
            if not usable:
                return
            access_token = str(getattr(partial, "access_token", "") or "").strip()
            if access_token:
                final.access_token = access_token
            if partial.expires_at:
                final.expires_at = partial.expires_at

        refresh_token = str(getattr(account, "refresh_token", "") or "").strip()
        password = str(getattr(account, "password", "") or "").strip()
        totp_secret = str(getattr(account, "totp_secret", "") or "").strip()

        # ① RT 刷新
        if refresh_token:
            self.log(f"[刷新Token] 尝试 RT 刷新: {email}")
            partial = self.refresh_by_oauth_token(
                refresh_token=refresh_token,
                client_id=getattr(account, "client_id", None),
            )
            _absorb(partial, usable=partial.success)
            if partial.success:
                final.success = True
                final.strategy = STRATEGY_REFRESH_TOKEN
                final.attempts.append(RefreshAttempt(STRATEGY_REFRESH_TOKEN, True, "拿到 access_token"))
                self.log(f"[刷新Token] RT 刷新成功: {email}")
                return final
            final.attempts.append(
                RefreshAttempt(STRATEGY_REFRESH_TOKEN, False, partial.error_message)
            )
            self.log(f"[刷新Token] RT 刷新未果: {partial.error_message}")
        else:
            final.attempts.append(
                RefreshAttempt(STRATEGY_REFRESH_TOKEN, False, "库里没有 refresh_token，跳过")
            )

        # ② 协议登录兜底
        if not self.allow_login:
            final.attempts.append(RefreshAttempt(STRATEGY_LOGIN, False, "已关闭协议登录兜底"))
        elif not password:
            final.attempts.append(RefreshAttempt(STRATEGY_LOGIN, False, "库里没有密码，无法协议登录"))
        else:
            self.log(f"[刷新Token] RT 不通，改走协议登录: {email}")
            partial = self.refresh_by_login(email, password, totp_secret=totp_secret)
            _absorb(partial, usable=partial.success)
            if partial.success:
                final.success = True
                final.strategy = STRATEGY_LOGIN
                final.attempts.append(RefreshAttempt(STRATEGY_LOGIN, True, "拿到 access_token"))
                self.log(f"[刷新Token] 协议登录成功: {email}")
                return final
            final.attempts.append(RefreshAttempt(STRATEGY_LOGIN, False, partial.error_message))
            self.log(f"[刷新Token] 协议登录未果: {partial.error_message}")

        details = "；".join(
            f"{strategy_label(item.strategy)}：{item.message}"
            for item in final.attempts
            if not item.ok
        )
        final.error_message = f"Token 刷新失败（{details}）" if details else "Token 刷新失败"
        return final

    def _probe_access_token(self, access_token: str) -> Tuple[str, str]:
        """拿 access_token 打 ``/backend-api/me``,判定它到底还能不能用。

        返回 ``(判定, 说明)``,判定只有三种取值:

        - ``valid``:上游认这个 AT;
        - ``invalid``:上游明确拒绝(401/403),这个 AT 已经废了;
        - ``unknown``:探测本身没跑通(网络异常、5xx、429),不能据此否定 AT ——
          一次网络抖动就把好号判死,比误判成功更糟。
        """
        token = str(access_token or "").strip()
        if not token:
            return "invalid", "没有 access_token"

        from .status_probe import CHATGPT_ME_URL, CODEX_USER_AGENT

        try:
            session = self._create_session()
            response = session.get(
                CHATGPT_ME_URL,
                headers={
                    "authorization": f"Bearer {token}",
                    "accept": "application/json",
                    "user-agent": CODEX_USER_AGENT,
                },
                timeout=30
            )
        except Exception as exc:
            return "unknown", f"探测异常: {exc}"

        status = int(getattr(response, "status_code", 0) or 0)
        if status == 200:
            return "valid", ""
        if status in (401, 403):
            detail = describe_error(getattr(response, "text", "") or "")
            return "invalid", f"HTTP {status} {detail}".strip()
        return "unknown", f"HTTP {status}"

    def validate_token(self, access_token: str) -> Tuple[bool, Optional[str]]:
        """验证 Access Token 是否有效。

        ``unknown``(探测没跑通)按有效处理:这里只用来否定**上游明确拒绝**的
        AT,网络抖动不该被当成凭证失效。

        Args:
            access_token: 访问令牌

        Returns:
            Tuple[bool, Optional[str]]: (是否有效, 错误信息)
        """
        verdict, detail = self._probe_access_token(access_token)
        if verdict == "valid":
            return True, None
        if verdict == "unknown":
            return True, detail or None
        return False, detail or "Token 无效或已过期"
