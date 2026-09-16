"""ChatGPT 账号状态判定辅助逻辑。"""

from __future__ import annotations

from typing import Any


INVALID_ACCOUNT_STATUS = "invalid"

# 被判失效之前的状态，留一份才能原样恢复（用户手工设过的 subscribed 不该被
# 一次探测抹成 registered）
STATUS_BEFORE_INVALID_KEY = "chatgpt_status_before_invalid"

# 恢复时允许还原的目标状态；invalid 本身不在其中，避免恢复出一个循环
_RESTORABLE_STATUSES = ("registered", "trial", "subscribed", "expired")
DEFAULT_RESTORED_STATUS = "registered"

# 明确可用的判据。只有拿到正面证据才谈恢复：探测没跑通、结论不明一律不动，
# 否则一次网络抖动就能把刚判定的失效洗白
_HEALTHY_LOCAL_AUTH_STATES = ("access_token_valid",)
_HEALTHY_REMOTE_STATES = ("usable",)

# 恢复成功时返回的 reason，调用方只判真假，取值本身用于日志与留痕
RECOVERED_STATUS_REASON = "recovered"


def _lower_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _account_extra(account: Any) -> dict:
    """读出账号的 extra，AccountModel 与鸭子类型对象两种形态都认。"""
    getter = getattr(account, "get_extra", None)
    if callable(getter):
        extra = getter() or {}
        return extra if isinstance(extra, dict) else {}
    extra = getattr(account, "extra", None)
    return extra if isinstance(extra, dict) else {}


def _write_account_extra(account: Any, extra: dict) -> None:
    setter = getattr(account, "set_extra", None)
    if callable(setter):
        setter(extra)
        return
    current = getattr(account, "extra", None)
    if isinstance(current, dict):
        current.clear()
        current.update(extra)


def is_account_deactivated_message(error_code: Any = "", message: Any = "") -> bool:
    code = _lower_text(error_code)
    text = _lower_text(message)
    if code in {"account_deactivated", "account_deleted"}:
        return True
    markers = (
        "deleted or deactivated",
        "account has been deleted or deactivated",
        "you do not have an account because it has been deleted or deactivated",
    )
    return any(marker in text for marker in markers)


# 401/403 的响应体里 OpenAI 说封号的措辞不止一种，全部小写后按子串匹配。
# 比 is_account_deactivated_message 宽：那个只认"已删除/已停用"，这里连
# 违规、滥用、封禁一起认，用在"凭证被吊销到底是过期还是封号"这种场合。
_BANNED_BODY_MARKERS = (
    "account_deactivated",
    "accountdeactivated",
    "deactivated",
    "disabled",
    "suspended",
    "banned",
    "violat",
    "potential abuse",
    "terminated",
)


def looks_like_banned_response(body_text: Any) -> bool:
    """凭证被拒的响应体读起来像不像封号。"""
    text = _lower_text(body_text)
    return any(marker in text for marker in _BANNED_BODY_MARKERS)


PLUS_STATUS_UNCHECKED = "unchecked"


def account_plus_status(account: Any) -> str:
    """账号上记录的 Plus 试用结论，没查过算 unchecked。"""
    extra: Any = {}
    getter = getattr(account, "get_extra", None)
    if callable(getter):
        extra = getter() or {}
    else:
        extra = getattr(account, "extra", {}) or {}
    check = extra.get("plus_check") if isinstance(extra, dict) else None
    if not isinstance(check, dict):
        return PLUS_STATUS_UNCHECKED
    return _lower_text(check.get("status")) or PLUS_STATUS_UNCHECKED


def filter_accounts_by_plus_status(accounts: Any, plus_status: Any) -> list:
    wanted = _lower_text(plus_status)
    if not wanted:
        return list(accounts)
    return [account for account in accounts if account_plus_status(account) == wanted]


def classify_local_probe_state(probe: dict[str, Any] | None) -> str:
    if not isinstance(probe, dict):
        return ""

    auth = probe.get("auth") if isinstance(probe.get("auth"), dict) else {}
    codex = probe.get("codex") if isinstance(probe.get("codex"), dict) else {}

    auth_state = _lower_text(auth.get("state"))
    auth_status = int(auth.get("http_status") or 0)
    auth_error_code = auth.get("error_code")
    auth_message = auth.get("message")

    if auth_status == 401 or auth_state in {"access_token_invalidated", "unauthorized"}:
        return "auth_401"
    if is_account_deactivated_message(auth_error_code, auth_message):
        return "auth_deactivated"
    if auth_status == 403 and auth_state in {"account_deactivated", "banned_like"}:
        return "auth_403"

    codex_state = _lower_text(codex.get("state"))
    codex_status = int(codex.get("http_status") or 0)
    codex_error_code = codex.get("error_code")
    codex_message = codex.get("message")

    if codex_status == 401 or codex_state in {"access_token_invalidated", "unauthorized"}:
        return "codex_401"
    if is_account_deactivated_message(codex_error_code, codex_message):
        return "codex_deactivated"
    if codex_status == 403 and codex_state == "account_deactivated":
        return "codex_403"

    return ""


def classify_remote_sync_state(sync: dict[str, Any] | None) -> str:
    if not isinstance(sync, dict):
        return ""

    remote_state = _lower_text(sync.get("remote_state"))
    status_code = int(sync.get("last_probe_status_code") or 0)
    error_code = sync.get("last_probe_error_code")
    message = sync.get("last_probe_message") or sync.get("status_message") or sync.get("message")

    if status_code == 401 or remote_state in {"access_token_invalidated", "unauthorized"}:
        return "remote_401"
    if is_account_deactivated_message(error_code, message):
        return "remote_deactivated"
    if status_code == 403 and remote_state in {"account_deactivated", "banned_like"}:
        return "remote_403"

    return ""


def classify_local_probe_health(probe: dict[str, Any] | None) -> str:
    """本地探测有没有拿到"这个号确实可用"的正面证据。

    ``auth.state == access_token_valid`` 意味着 ``/backend-api/me`` 回了 200，
    凭证有效这件事已经被上游确认过。codex 段的额度、付费、探测失败都不影响这个
    结论 —— 那些不是"账号失效"，不该继续挂着 invalid。
    """
    if not isinstance(probe, dict):
        return ""

    auth = probe.get("auth") if isinstance(probe.get("auth"), dict) else {}
    if _lower_text(auth.get("state")) in _HEALTHY_LOCAL_AUTH_STATES:
        return "auth_ok"
    return ""


def classify_remote_sync_health(sync: dict[str, Any] | None) -> str:
    """远端同步有没有拿到"这个号确实可用"的正面证据。"""
    if not isinstance(sync, dict):
        return ""
    if _lower_text(sync.get("remote_state")) in _HEALTHY_REMOTE_STATES:
        return "remote_ok"
    return ""


def apply_chatgpt_status_policy(
    account: Any,
    *,
    local_probe: dict[str, Any] | None = None,
    remote_sync: dict[str, Any] | None = None,
) -> str:
    """按一次探测/同步的结论调整账号状态，返回"这次为什么动了状态"（没动则为空串）。

    两个方向都得有。判失效是单向的（401/403/停用 → invalid），但只有这一半的话，
    一个号因为一次 token 失效被标成 invalid 之后就再也回不来 —— 之后重新探测
    拿到 ``/me`` 200，标签依旧挂着"已失效"，页面上显示的和服务端事实相反。

    异常判定优先于健康判定：同一次探测里 auth 说正常、codex 说停用时，按停用算。
    """
    reason = classify_local_probe_state(local_probe) or classify_remote_sync_state(remote_sync)
    if reason:
        _mark_invalid(account)
        return reason

    healthy = classify_local_probe_health(local_probe) or classify_remote_sync_health(remote_sync)
    if healthy and _restore_status(account):
        return RECOVERED_STATUS_REASON
    return ""


def _mark_invalid(account: Any) -> None:
    """置失效，并把"失效前是什么状态"记进 extra，供之后恢复。

    已经是 invalid 时不覆盖那条记录，否则连点两次失效就把原始状态冲掉了。
    """
    current = _lower_text(getattr(account, "status", ""))
    if current and current != INVALID_ACCOUNT_STATUS:
        extra = _account_extra(account)
        extra[STATUS_BEFORE_INVALID_KEY] = current
        _write_account_extra(account, extra)
    setattr(account, "status", INVALID_ACCOUNT_STATUS)


def _restore_status(account: Any) -> bool:
    """把被判失效的账号恢复成失效前的状态，返回是否真的改了。

    只动 invalid：正常状态不该被每次探测重写一遍。
    """
    if _lower_text(getattr(account, "status", "")) != INVALID_ACCOUNT_STATUS:
        return False

    extra = _account_extra(account)
    previous = _lower_text(extra.pop(STATUS_BEFORE_INVALID_KEY, ""))
    _write_account_extra(account, extra)
    target = previous if previous in _RESTORABLE_STATUSES else DEFAULT_RESTORED_STATUS
    setattr(account, "status", target)
    return True
