"""CLIProxyAPI 只读状态同步。"""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from platforms.chatgpt.status_probe import CODEX_USER_AGENT, extract_chatgpt_account_id
from services.chatgpt_account_state import is_account_deactivated_message

DEFAULT_CLIPROXYAPI_BASE_URL = "http://127.0.0.1:8317"
SYNC_RETRY_ATTEMPTS = 3
SYNC_RETRY_DELAY_SECONDS = 0.4
BATCH_PROBE_DELAY_SECONDS = 0.12
# 两个 AT 的过期时间差在这个窗口内就算同一份凭证 —— 同一秒解出来的 exp
# 不该因为时区/毫秒截断被判成"谁更新"
VERSION_TOLERANCE_SECONDS = 60

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_config_value(key: str, default: str = "") -> str:
    try:
        from core.config_store import config_store

        value = str(config_store.get(key, "") or "").strip()
        return value or default
    except Exception:
        return default


def _base_url(api_url: str | None = None) -> str:
    """CPA 面板地址。`cpa_api_url` 是主配置，旧的 `cliproxyapi_base_url` 兜底。"""
    return str(
        api_url
        or _get_config_value("cpa_api_url")
        or _get_config_value("cliproxyapi_base_url", DEFAULT_CLIPROXYAPI_BASE_URL)
        or DEFAULT_CLIPROXYAPI_BASE_URL
    ).rstrip("/")


def _api_key(api_key: str | None = None) -> str:
    return str(
        api_key
        or _get_config_value("cpa_api_key")
        or _get_config_value("cliproxyapi_management_key", "cliproxyapi")
        or "cliproxyapi"
    ).strip()


def _headers(api_key: str | None = None) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {_api_key(api_key)}",
        "Content-Type": "application/json",
    }


def _parse_json_text(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_header_error_json(headers: dict[str, Any]) -> dict[str, Any]:
    raw = headers.get("X-Error-Json") or headers.get("x-error-json") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    raw = str(raw or "").strip()
    if not raw:
        return {}
    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
    except Exception:
        return {}
    return _parse_json_text(decoded)


def _extract_error_code(headers: dict[str, Any], body_json: dict[str, Any], header_error_json: dict[str, Any]) -> str:
    for key in ("X-Openai-Ide-Error-Code", "x-openai-ide-error-code"):
        value = headers.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        if str(value or "").strip():
            return str(value).strip()
    candidates = [
        ((body_json.get("error") or {}).get("code") if isinstance(body_json.get("error"), dict) else ""),
        ((header_error_json.get("error") or {}).get("code") if isinstance(header_error_json.get("error"), dict) else ""),
    ]
    for candidate in candidates:
        if str(candidate or "").strip():
            return str(candidate).strip()
    return ""


def _extract_error_message(body_json: dict[str, Any], header_error_json: dict[str, Any], body_text: str, status_code: int) -> str:
    candidates = [
        ((body_json.get("error") or {}).get("message") if isinstance(body_json.get("error"), dict) else ""),
        ((header_error_json.get("error") or {}).get("message") if isinstance(header_error_json.get("error"), dict) else ""),
        body_json.get("message", ""),
        body_text.strip(),
    ]
    for candidate in candidates:
        if str(candidate or "").strip():
            return str(candidate).strip()[:500]
    return f"HTTP {status_code}"


def _request_json(method: str, path: str, *, api_url: str | None = None, api_key: str | None = None, json_body: dict | None = None) -> Any:
    import requests
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    target = f"{_base_url(api_url)}{path}"
    try:
        response = requests.request(
            method,
            target,
            headers=_headers(api_key),
            json=json_body,
            timeout=30,
            verify=False,
        )
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"CLIProxyAPI 无法连接，请确认服务已启动或 API URL 是否正确：{_base_url(api_url)}") from exc
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(f"CLIProxyAPI 请求超时：{_base_url(api_url)}") from exc
    response.raise_for_status()
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return response.text


def _is_retryable_sync_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    markers = (
        "无法连接",
        "请求超时",
        "connection",
        "timeout",
        "timed out",
    )
    return any(marker in text for marker in markers)


def _retry_sync_call(func, *, attempts: int = SYNC_RETRY_ATTEMPTS):
    last_error = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts or not _is_retryable_sync_error(exc):
                raise
            time.sleep(SYNC_RETRY_DELAY_SECONDS)
    if last_error is not None:
        raise last_error
    raise RuntimeError("sync retry failed without captured error")


def list_auth_files(*, api_url: str | None = None, api_key: str | None = None) -> list[dict[str, Any]]:
    data = _request_json("GET", "/v0/management/auth-files", api_url=api_url, api_key=api_key)
    files = data.get("files", []) if isinstance(data, dict) else []
    return [item for item in files if isinstance(item, dict)]


# ── 版本对比 ────────────────────────────────────────────────
# 两边谁更新，靠"凭证本身的过期时间"说话：AT 的 exp 直接决定这份凭证还能用
# 多久，比文件的 mtime 可靠（上传时 mtime 会被重写，exp 不会）。exp 相同或
# 缺失时才退回刷新时间。所有比较都是纯函数，不写库、不发请求。

VERSION_DIRECTION_LABELS = {
    "missing_remote": "远端缺失",
    "missing_local": "本地缺失",
    "local_newer": "本地较新",
    "remote_newer": "远端较新",
    "in_sync": "已一致",
    "unknown": "无法比较",
}


def _decode_jwt_exp(token: Any) -> int:
    """解 JWT payload 里的 `exp`（秒级时间戳），解不出返回 0。"""
    text = str(token or "").strip()
    if not text or text.count(".") < 2:
        return 0
    payload = text.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8", errors="ignore"))
    except Exception:
        return 0
    if not isinstance(decoded, dict):
        return 0
    exp = decoded.get("exp")
    if isinstance(exp, bool):
        return 0
    if isinstance(exp, (int, float)) and exp > 0:
        return int(exp)
    if isinstance(exp, str) and exp.strip().isdigit():
        return int(exp.strip())
    return 0


def _parse_time_value(value: Any) -> datetime | None:
    """把远端/本地各种时间写法统一成 aware datetime。

    吃 `2026-01-01T12:00:00+08:00`、`...Z`、`2026-01-01 12:00:00`、epoch 秒、
    epoch 毫秒；解析不出来返回 None（调用方据此退回其它判据）。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # 毫秒
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except Exception:
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_time_value(int(text))

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        # CPA 写的是 +08:00 的墙钟时间，裸串按东八区理解（与 cpa_upload 一致）
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed


def _at_expires_at(access_token: Any) -> datetime | None:
    exp = _decode_jwt_exp(access_token)
    if not exp:
        return None
    try:
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except Exception:
        return None


def _to_iso(value: datetime | None) -> str:
    return value.isoformat() if isinstance(value, datetime) else ""


def _humanize_delta(delta_seconds: float) -> str:
    seconds = abs(int(delta_seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时"
    days = hours // 24
    return f"{days} 天 {hours % 24} 小时"


def build_version_compare(local: dict[str, Any] | None, remote: dict[str, Any] | None) -> dict[str, Any]:
    """比较本地账号与远端 auth-file 的版本，只算不写。

    `local` / `remote` 支持两种形态：直接给 token 串，或给带
    `access_token` / `expired` / `at_expires_at` / `last_refresh` 的 dict。
    """
    local_data = local if isinstance(local, dict) else {"access_token": local}
    remote_data = remote if isinstance(remote, dict) else {"access_token": remote}

    local_token = str(local_data.get("access_token") or "").strip()
    remote_token = str(remote_data.get("access_token") or "").strip()
    # 远端条目不存在（或调用方显式说 present=False）时没什么可比的
    remote_present = bool(remote_data) and remote_data.get("present", True) is not False

    local_at = _parse_time_value(local_data.get("at_expires_at")) or _at_expires_at(local_token)
    remote_at = (
        _parse_time_value(remote_data.get("at_expires_at"))
        or _parse_time_value(remote_data.get("expired"))
        or _at_expires_at(remote_token)
    )
    local_refresh = _parse_time_value(local_data.get("last_refresh"))
    remote_refresh = _parse_time_value(remote_data.get("last_refresh"))

    result: dict[str, Any] = {
        "local_at_expires_at": _to_iso(local_at),
        "remote_at_expires_at": _to_iso(remote_at),
        "local_last_refresh": _to_iso(local_refresh),
        "remote_last_refresh": _to_iso(remote_refresh),
        "remote_has_credentials": bool(remote_token),
        "direction": "unknown",
        "detail": "",
    }

    if not remote_present:
        result["direction"] = "missing_remote"
        result["detail"] = "远端没有这个账号的 auth-file"
        return result

    if not local_token:
        result["direction"] = "missing_local"
        result["detail"] = "本地没有可用的 access_token"
        return result

    if local_at and remote_at:
        delta = (local_at - remote_at).total_seconds()
        if abs(delta) <= VERSION_TOLERANCE_SECONDS:
            result["direction"] = "in_sync"
            result["detail"] = "两边 AT 过期时间一致"
        elif delta > 0:
            result["direction"] = "local_newer"
            result["detail"] = f"本地 AT 晚 {_humanize_delta(delta)} 过期"
        else:
            result["direction"] = "remote_newer"
            result["detail"] = f"远端 AT 晚 {_humanize_delta(delta)} 过期"
        return result

    # AT 的 exp 至少有一边解不出来，退回刷新时间
    if local_refresh and remote_refresh:
        delta = (local_refresh - remote_refresh).total_seconds()
        if abs(delta) <= VERSION_TOLERANCE_SECONDS:
            result["direction"] = "in_sync"
            result["detail"] = "两边刷新时间一致"
        elif delta > 0:
            result["direction"] = "local_newer"
            result["detail"] = f"本地刷新时间晚 {_humanize_delta(delta)}"
        else:
            result["direction"] = "remote_newer"
            result["detail"] = f"远端刷新时间晚 {_humanize_delta(delta)}"
        return result

    result["detail"] = "两边都取不到可比的时间信息"
    return result


def _status_rank(status: str) -> int:
    order = {
        "active": 0,
        "refreshing": 1,
        "pending": 2,
        "error": 3,
        "disabled": 4,
    }
    return order.get(str(status or "").strip().lower(), 9)


def _match_auth_file(account: Any, files: list[dict[str, Any]]) -> dict[str, Any] | None:
    email = str(getattr(account, "email", "") or "").strip().lower()
    if not email:
        return None
    candidates = []
    for item in files:
        provider = str(item.get("provider") or item.get("type") or "").strip().lower()
        item_email = str(item.get("email") or "").strip().lower()
        item_name = str(item.get("name") or "").strip().lower()
        if provider != "codex":
            continue
        if item_email == email or item_name == f"{email}.json":
            candidates.append(item)
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            _status_rank(item.get("status", "")),
            str(item.get("updated_at") or item.get("modtime") or item.get("created_at") or ""),
        ),
        reverse=False,
    )
    return candidates[0]


def _probe_remote_auth(auth_index: str, account_id: str, *, api_url: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    checked_at = _utcnow_iso()
    if not auth_index:
        return {
            "last_probe_at": checked_at,
            "last_probe_status_code": 0,
            "last_probe_error_code": "",
            "last_probe_message": "缺少 auth_index，无法探测远端额度状态",
            "remote_state": "probe_skipped",
        }
    if not account_id:
        return {
            "last_probe_at": checked_at,
            "last_probe_status_code": 0,
            "last_probe_error_code": "",
            "last_probe_message": "缺少 Chatgpt-Account-Id，无法严格探测远端额度状态",
            "remote_state": "probe_skipped",
        }

    data = _request_json(
        "POST",
        "/v0/management/api-call",
        api_url=api_url,
        api_key=api_key,
        json_body={
            "authIndex": auth_index,
            "method": "GET",
            "url": "https://chatgpt.com/backend-api/wham/usage",
            "header": {
                "Authorization": "Bearer $TOKEN$",
                "Content-Type": "application/json",
                "User-Agent": CODEX_USER_AGENT,
                "Chatgpt-Account-Id": account_id,
            },
        },
    )

    upstream_status = int((data or {}).get("status_code") or 0)
    headers = (data or {}).get("header") or {}
    body_text = str((data or {}).get("body") or "")
    body_json = _parse_json_text(body_text)
    header_error_json = _parse_header_error_json(headers)
    error_code = _extract_error_code(headers, body_json, header_error_json)
    message = _extract_error_message(body_json, header_error_json, body_text, upstream_status)

    remote_state = "probe_failed"
    if upstream_status == 200:
        remote_state = "usable"
    elif upstream_status == 401:
        remote_state = "access_token_invalidated" if error_code == "token_invalidated" else "unauthorized"
    elif is_account_deactivated_message(error_code, message):
        remote_state = "account_deactivated"
    elif upstream_status in (402, 403):
        remote_state = "payment_required"
    elif upstream_status == 429:
        remote_state = "quota_exhausted"

    return {
        "last_probe_at": checked_at,
        "last_probe_status_code": upstream_status,
        "last_probe_error_code": error_code,
        "last_probe_message": message,
        "remote_state": remote_state,
    }


def _local_version_inputs(account: Any) -> dict[str, Any]:
    """从账号（ORM 行或 duck-typed 快照）里凑出本地侧参与比版本的字段。"""
    extra = getattr(account, "extra", None)
    if not isinstance(extra, dict):
        getter = getattr(account, "get_extra", None)
        extra = getter() if callable(getter) else {}
        if not isinstance(extra, dict):
            extra = {}

    access_token = str(extra.get("access_token") or getattr(account, "token", "") or "").strip()
    refresh = extra.get("chatgpt_token_refresh")
    last_refresh = refresh.get("at") if isinstance(refresh, dict) else ""
    if not last_refresh:
        updated_at = getattr(account, "updated_at", None)
        last_refresh = updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at
    return {
        "access_token": access_token,
        "last_refresh": str(last_refresh or "").strip(),
    }


def _with_version_aliases(compare: dict[str, Any]) -> dict[str, Any]:
    """把 compare 的 `direction` / `detail` 再以 `version_*` 名字暴露一份。

    账号详情页（frontend `Accounts.tsx`）读的是 `version_direction` /
    `version_detail`，而同步逻辑内部统一用 `direction` / `detail`。
    """
    merged = dict(compare)
    merged["version_direction"] = str(compare.get("direction") or "unknown")
    merged["version_detail"] = str(compare.get("detail") or "")
    return merged


def _build_remote_sync_result(
    account: Any,
    matched: dict[str, Any] | None,
    synced_at: str,
    *,
    api_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    if not matched:
        compare = _with_version_aliases(build_version_compare(_local_version_inputs(account), {"present": False}))
        return {
            "uploaded": False,
            "last_synced_at": synced_at,
            "message": "未在 CLIProxyAPI 找到匹配的 Codex auth-file",
            "remote_state": "not_found",
            "base_url": _base_url(api_url),
            "remote_has_credentials": False,
            **compare,
        }

    account_id = extract_chatgpt_account_id(account)
    remote = {
        "uploaded": True,
        "last_synced_at": synced_at,
        "message": "",
        "base_url": _base_url(api_url),
        "auth_index": str(matched.get("auth_index") or "").strip(),
        "name": str(matched.get("name") or "").strip(),
        "provider": str(matched.get("provider") or matched.get("type") or "").strip(),
        "status": str(matched.get("status") or "").strip(),
        "status_message": str(matched.get("status_message") or "").strip(),
        "unavailable": bool(matched.get("unavailable")),
        "disabled": bool(matched.get("disabled")),
        "last_refresh": str(matched.get("last_refresh") or "").strip(),
        "next_retry_after": str(matched.get("next_retry_after") or "").strip(),
        "remote_plan_type": str(((matched.get("id_token") or {}).get("plan_type") if isinstance(matched.get("id_token"), dict) else "") or "").strip(),
        "chatgpt_subscription_active_until": str(((matched.get("id_token") or {}).get("chatgpt_subscription_active_until") if isinstance(matched.get("id_token"), dict) else "") or "").strip(),
    }
    remote.update(
        _with_version_aliases(
            build_version_compare(
                _local_version_inputs(account),
                {
                    "present": True,
                    "access_token": matched.get("access_token"),
                    "expired": matched.get("expired"),
                    "last_refresh": matched.get("last_refresh"),
                },
            )
        )
    )
    try:
        remote.update(
            _retry_sync_call(
                lambda: _probe_remote_auth(remote["auth_index"], account_id, api_url=api_url, api_key=api_key)
            )
        )
    except Exception as exc:
        remote.update(
            {
                "last_probe_at": synced_at,
                "last_probe_status_code": 0,
                "last_probe_error_code": "",
                "last_probe_message": str(exc),
                "remote_state": "unreachable",
                "message": str(exc),
            }
        )
        return remote
    if remote["status"] == "error" and remote["status_message"]:
        remote["message"] = remote["status_message"]
    elif remote["last_probe_message"]:
        remote["message"] = remote["last_probe_message"]
    return remote


def sync_chatgpt_cliproxyapi_status(
    account: Any,
    *,
    api_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    synced_at = _utcnow_iso()
    try:
        files = _retry_sync_call(lambda: list_auth_files(api_url=api_url, api_key=api_key))
    except Exception as exc:
        return {
            "uploaded": False,
            "last_synced_at": synced_at,
            "message": str(exc),
            "remote_state": "unreachable",
            "base_url": _base_url(api_url),
        }
    matched = _match_auth_file(account, files)
    return _build_remote_sync_result(account, matched, synced_at, api_url=api_url, api_key=api_key)


def sync_chatgpt_cliproxyapi_status_batch(
    accounts: list[Any],
    *,
    api_url: str | None = None,
    api_key: str | None = None,
) -> dict[int, dict[str, Any]]:
    synced_at = _utcnow_iso()
    results: dict[int, dict[str, Any]] = {}
    if not accounts:
        return results

    try:
        files = _retry_sync_call(lambda: list_auth_files(api_url=api_url, api_key=api_key))
    except Exception as exc:
        fallback = {
            "uploaded": False,
            "last_synced_at": synced_at,
            "message": str(exc),
            "remote_state": "unreachable",
            "base_url": _base_url(api_url),
        }
        for account in accounts:
            account_id = getattr(account, "id", None)
            if account_id is not None:
                results[int(account_id)] = dict(fallback)
        logger.warning("CLIProxyAPI 批量同步失败：无法获取 auth-files, accounts=%s, error=%s", len(accounts), exc)
        return results

    for index, account in enumerate(accounts):
        account_id = getattr(account, "id", None)
        if account_id is None:
            continue
        matched = _match_auth_file(account, files)
        results[int(account_id)] = _build_remote_sync_result(account, matched, synced_at, api_url=api_url, api_key=api_key)
        if index < len(accounts) - 1 and matched:
            time.sleep(BATCH_PROBE_DELAY_SECONDS)

    unreachable = sum(1 for item in results.values() if str(item.get("remote_state") or "").strip().lower() == "unreachable")
    not_found = sum(1 for item in results.values() if str(item.get("remote_state") or "").strip().lower() == "not_found")
    logger.info(
        "CLIProxyAPI 批量同步完成：accounts=%s, unreachable=%s, not_found=%s, base_url=%s",
        len(results),
        unreachable,
        not_found,
        _base_url(api_url),
    )
    return results
