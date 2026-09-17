"""刷新 Token 的库侧胶水：挑号、跑刷新、把结果写回账号表。

引擎（``platforms.chatgpt.token_refresh``）刻意不认识数据库，这里负责把
``accounts`` 表的一行翻译成引擎要的入参，再把拿到的凭证塞回 ``extra_json``。
每个号的刷新过程会在 extra 里留一份 ``chatgpt_token_refresh`` 留痕，方便事后
查是哪条策略成的、失败又卡在哪。

与补 RT（``services.chatgpt_rt_backfill``）的分工：
    - 补 RT 只认 refresh_token，目标是把缺 RT 的老号补齐，已有 RT 的号默认跳过。
    - 刷新 Token 的目标是拿到**新的 access_token**，任何号都可以刷，RT 只是
      它最省事的一条路径。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from sqlmodel import Session

from core.db import AccountModel
from platforms.chatgpt.token_refresh import (
    STRATEGY_LOGIN,
    TokenRefreshManager,
    TokenRefreshResult,
    strategy_label,
)
from services.chatgpt_account_selection import select_chatgpt_accounts

logger = logging.getLogger(__name__)

# 默认的 OAuth client_id，与 plugin/actions 里保持一致
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def account_can_refresh(model: AccountModel) -> bool:
    """这个号有没有任何一条可用的刷新路径。

    几条路只要有一条有材料就算可刷：RT、session_token、或者邮箱 + 密码。
    """
    extra = model.get_extra()
    if str(extra.get("refresh_token") or extra.get("refreshToken") or "").strip():
        return True
    if str(extra.get("session_token") or extra.get("sessionToken") or "").strip():
        return True
    return bool(str(model.email or "").strip() and str(model.password or "").strip())


def select_refresh_targets(
    session: Session,
    *,
    account_ids: Optional[Iterable[int]] = None,
    all_filtered: bool = False,
    email: str = "",
    status: str = "",
    plus_status: str = "",
    only_refreshable: bool = True,
) -> tuple[list[AccountModel], list[int]]:
    """挑出要刷新 Token 的号，返回 ``(账号列表, 找不到的 id)``。

    ``only_refreshable`` 默认开着：几条路都没材料的号（没 RT、没 session、
    也没密码）跑了必然失败，没必要占着任务队列。
    """
    return select_chatgpt_accounts(
        session,
        account_ids=account_ids,
        all_filtered=all_filtered,
        email=email,
        status=status,
        plus_status=plus_status,
        keep=account_can_refresh if only_refreshable else None,
    )


def refresh_account_data(
    *,
    email: str,
    password: str = "",
    extra: Optional[dict] = None,
    token: str = "",
    config: Optional[dict] = None,
    proxy: Optional[str] = None,
    allow_login: bool = True,
    log_fn: Optional[Callable[[str], None]] = None,
    task_control=None,
    attempt_id=None,
) -> TokenRefreshResult:
    """按账号字段刷新 Token，不落库（落库交给 ``apply_refresh_result``）。

    只收纯数据不收 ORM 对象：协议登录那条路要跑几十秒网络请求，调用方得以
    在这期间把数据库连接还回池子里。

    ``task_control`` 传的是后台任务的停止/跳过开关，会一路交到邮箱的等码循环
    里 —— 协议登录等验证码是整个流程里最长的一段，不接开关就停不下来。
    """
    extra = dict(extra or {})
    config = dict(config or _load_config())
    log = log_fn or logger.info

    def _resolve_mail_provider():
        """惰性解析收件通道，只在真要走协议登录那一刻才连邮箱。

        RT/Session 能成的话根本用不上邮箱，没必要为了一个大概率不跑的分支先连
        一遍收件服务；但 Session 换新被打回 /log-in、或降级到协议登录那一刻，
        又必须拿得到通道 —— 所以交出去的是工厂，不是现成的 provider。
        """
        from services.chatgpt_otp_mailbox import resolve_otp_mail_provider

        return resolve_otp_mail_provider(
            email,
            account_extra=extra,
            config=config,
            proxy=proxy,
            log_fn=log,
            task_control=task_control,
            attempt_id=attempt_id,
        )

    class _Account:
        pass

    account = _Account()
    account.email = email
    account.password = password
    account.access_token = str(extra.get("access_token") or token or "")
    account.refresh_token = str(extra.get("refresh_token") or extra.get("refreshToken") or "")
    account.session_token = str(extra.get("session_token") or extra.get("sessionToken") or "")
    account.id_token = str(extra.get("id_token") or "")
    account.device_id = str(extra.get("device_id") or "")
    account.client_id = str(extra.get("client_id") or DEFAULT_CLIENT_ID)
    account.totp_secret = str(extra.get("totp_secret") or "")

    return TokenRefreshManager(
        proxy_url=proxy,
        extra_config=config,
        # 只要还有可能走到协议登录，就把收件通道的解析权交出去（惰性，
        # RT/Session 成功时一次都不会调用）
        mail_provider_resolver=(
            _resolve_mail_provider if (allow_login and password) else None
        ),
        allow_login=allow_login,
        log_fn=log,
    ).refresh_account(account)


def build_extra_patch(result: TokenRefreshResult) -> dict[str, Any]:
    """把刷新结果整理成可以合并进 ``extra_json`` 的补丁。

    只写非空字段：任何一条路拿不到 RT 时都不该用空串覆盖掉库里原有的
    refresh_token，那等于把号弄坏。
    """
    patch: dict[str, Any] = {}
    for key in ("access_token", "refresh_token", "session_token", "id_token"):
        value = str(getattr(result, key, "") or "").strip()
        if value:
            patch[key] = value
    if result.cookie_header:
        patch["cookies"] = result.cookie_header
    if result.refresh_token:
        # 号已经有 RT 了，别再被当成 access_token_only 方案的产物
        patch["chatgpt_has_refresh_token_solution"] = True
    patch["chatgpt_token_refresh"] = {
        "ok": result.success,
        "strategy": result.strategy,
        "strategy_label": strategy_label(result.strategy) if result.strategy else "",
        "message": result.summary(),
        "expires_at": result.expires_at.isoformat() if result.expires_at else "",
        "attempts": [
            {"strategy": item.strategy, "ok": item.ok, "message": item.message}
            for item in result.attempts
        ],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    return patch


def apply_refresh_result(
    model: AccountModel,
    result: TokenRefreshResult,
    *,
    session: Optional[Session] = None,
    commit: bool = False,
) -> dict[str, Any]:
    """把刷新结果落到账号行上，返回实际写入的补丁。

    ``token`` 列是 access_token 的冗余镜像（账号列表页读这一列），刷出新 AT
    必须同步更新，否则列表页会一直显示旧的。
    """
    patch = build_extra_patch(result)
    extra = model.get_extra()
    extra.update(patch)
    model.set_extra(extra)
    if patch.get("access_token"):
        model.token = patch["access_token"]
    model.updated_at = datetime.now(timezone.utc)
    if session is not None:
        session.add(model)
        if commit:
            session.commit()
    return patch


def _load_config() -> dict:
    from core.config_store import config_store

    return config_store.get_all() or {}
