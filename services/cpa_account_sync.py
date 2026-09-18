"""本地账号 ↔ CPA 面板的版本对比与双向同步。

和另外两个同名兄弟的分工：

- `services/cliproxyapi_sync.py`：只读。拉远端清单 + 借远端通道探测额度状态。
- `services/chatgpt_sync.py`：记账层。把同步/上传结果写进 `extra.sync_statuses`，
  以及"回填"（只补远端没有的）。
- **本模块**：对比 + 双向。按 AT 过期时间（缺则刷新时间）判断哪边更新，
  谁新听谁的 —— 本地新就推上去，远端新就拉回来。

远端凭据直接取 auth-files 列表条目里的 `access_token` / `refresh_token`；
条目里没带就只报告不可拉取，不去猜下载接口。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlmodel import Session

from services.cliproxyapi_sync import (
    BATCH_PROBE_DELAY_SECONDS,
    VERSION_DIRECTION_LABELS,
    _match_auth_file,
    build_version_compare,
    list_auth_files,
)
from services.chatgpt_sync import (
    _get_account_extra,
    _local_probe_uploadable,
    _resolve_cliproxy_target,
    _utcnow,
    _utcnow_iso,
    build_chatgpt_sync_account,
    get_cliproxy_sync_state,
    update_account_model_cliproxy_sync,
    upload_account_model_to_cpa,
)

SYNC_MODES = ("auto", "push_only", "pull_only")
# 远端已经硬失效的号，拉回来只会把本地也弄脏
_PULL_BLOCKED_REMOTE_STATES = ("access_token_invalidated", "unauthorized", "account_deactivated")

logger = logging.getLogger(__name__)


def _local_inputs(account: Any) -> dict[str, Any]:
    extra = _get_account_extra(account)
    access_token = str(extra.get("access_token") or getattr(account, "token", "") or "").strip()
    refresh = extra.get("chatgpt_token_refresh")
    last_refresh = refresh.get("at") if isinstance(refresh, dict) else ""
    if not last_refresh:
        updated_at = getattr(account, "updated_at", None)
        last_refresh = updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at
    return {"access_token": access_token, "last_refresh": str(last_refresh or "").strip()}


def _remote_inputs(matched: dict[str, Any] | None) -> dict[str, Any]:
    if not matched:
        return {"present": False}
    return {
        "present": True,
        "access_token": matched.get("access_token"),
        "expired": matched.get("expired"),
        "last_refresh": matched.get("last_refresh"),
    }


def _remote_credentials(matched: dict[str, Any] | None) -> dict[str, str]:
    if not matched:
        return {}
    credentials = {}
    for key in ("access_token", "refresh_token", "id_token", "account_id"):
        value = str(matched.get(key) or "").strip()
        if value:
            credentials[key] = value
    return credentials


def _direction_label(direction: str) -> str:
    return VERSION_DIRECTION_LABELS.get(direction, direction or "unknown")


def _decide_action(direction: str, mode: str) -> str:
    """按方向与模式决定动作：push / pull / skip。"""
    if direction in ("local_newer", "missing_remote"):
        return "push" if mode in ("auto", "push_only") else "skip"
    if direction in ("remote_newer", "missing_local"):
        return "pull" if mode in ("auto", "pull_only") else "skip"
    return "skip"


def _record_sync_action(account: Any, action: str, direction: str, ok: bool, message: str) -> None:
    """在 `extra.sync_statuses.cpa_sync` 里留一条本次动作的记账。"""
    extra = _get_account_extra(account)
    sync_statuses = extra.get("sync_statuses")
    if not isinstance(sync_statuses, dict):
        sync_statuses = {}
    state = sync_statuses.get("cpa_sync")
    if not isinstance(state, dict):
        state = {}
    state.update(
        {
            "last_action": action,
            "direction": direction,
            "last_attempt_ok": bool(ok),
            "last_message": message,
            "last_attempt_at": _utcnow_iso(),
        }
    )
    sync_statuses["cpa_sync"] = state
    extra["sync_statuses"] = sync_statuses
    _write_extra(account, extra)


def _write_extra(account: Any, extra: dict[str, Any]) -> None:
    setter = getattr(account, "set_extra", None)
    if callable(setter):
        setter(extra)
        return
    current = getattr(account, "extra", None)
    if isinstance(current, dict):
        current.clear()
        current.update(extra)


def _apply_remote_credentials(account: Any, credentials: dict[str, str]) -> list[str]:
    """把远端凭据写回本地，返回实际更新的字段名。

    空值一律不覆盖 —— 远端没给 RT 不该把本地好端端的 RT 冲掉。
    """
    if not credentials:
        return []
    extra = _get_account_extra(account)
    updated: list[str] = []
    for key in ("access_token", "refresh_token", "id_token"):
        value = str(credentials.get(key) or "").strip()
        if value and str(extra.get(key) or "").strip() != value:
            extra[key] = value
            updated.append(key)
    if credentials.get("access_token"):
        setattr(account, "token", credentials["access_token"])
    if updated:
        _write_extra(account, extra)
    return updated


def _push(account: Any, *, session: Session | None, api_url: str | None, api_key: str | None, commit: bool) -> dict[str, Any]:
    from platforms.chatgpt.status_probe import probe_local_chatgpt_status

    sync_account = build_chatgpt_sync_account(account)
    probe = probe_local_chatgpt_status(sync_account, proxy=None)
    if not _local_probe_uploadable(probe):
        auth = probe.get("auth") if isinstance(probe.get("auth"), dict) else {}
        state = str(auth.get("state") or "unknown").strip()
        detail = str(auth.get("message") or "").strip()
        message = f"本地状态不可上传（{state}）：{detail}" if detail else f"本地状态不可上传（{state}）"
        return {"ok": False, "skipped": True, "message": message}

    ok, message = upload_account_model_to_cpa(
        account, session=session, api_url=api_url, api_key=api_key, commit=False
    )
    if not ok:
        return {"ok": False, "skipped": False, "message": message}

    # "传了"不等于"成了"：再拉一次远端确认，顺便把新的同步状态落库
    verified = _resync(account, api_url=api_url, api_key=api_key)
    update_account_model_cliproxy_sync(account, verified, session=session, commit=False)
    if not verified.get("uploaded"):
        return {"ok": False, "skipped": False, "message": verified.get("message") or "上传后远端仍未发现 auth-file"}
    return {"ok": True, "skipped": False, "message": "已推送到 CPA，远端状态=" + str(verified.get("remote_state") or "unknown")}


def _resync(account: Any, *, api_url: str | None, api_key: str | None) -> dict[str, Any]:
    from services.cliproxyapi_sync import sync_chatgpt_cliproxyapi_status

    return sync_chatgpt_cliproxyapi_status(build_chatgpt_sync_account(account), api_url=api_url, api_key=api_key)


def _pull(
    account: Any,
    matched: dict[str, Any] | None,
    *,
    session: Session | None,
    api_url: str | None,
    api_key: str | None,
) -> dict[str, Any]:
    credentials = _remote_credentials(matched)
    if not credentials.get("access_token"):
        return {
            "ok": False,
            "skipped": True,
            "message": "远端 auth-file 未返回 access_token，无法拉取；可在 CPA 面板导出该文件后手工导入",
        }

    current_state = str(get_cliproxy_sync_state(account).get("remote_state") or "").strip().lower()
    if current_state in _PULL_BLOCKED_REMOTE_STATES:
        return {"ok": False, "skipped": True, "message": f"远端状态为 {current_state}，拒绝把失效凭据拉回本地"}

    updated = _apply_remote_credentials(account, credentials)
    # 拉完重新同步一次，把新的 AT 状态（额度/失效）落到 sync_statuses
    update_account_model_cliproxy_sync(account, _resync(account, api_url=api_url, api_key=api_key), session=session, commit=False)
    if not updated:
        return {"ok": True, "skipped": True, "message": "远端凭据与本地一致，无需覆盖"}
    return {"ok": True, "skipped": False, "message": f"已从 CPA 拉取并更新本地字段：{', '.join(updated)}"}


def sync_chatgpt_account_with_cpa(
    account: Any,
    *,
    session: Session | None = None,
    mode: str = "auto",
    files: list[dict[str, Any]] | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """按版本对比结果同步单个账号。`files` 传入可复用批量拉到的远端清单。"""
    mode = mode if mode in SYNC_MODES else "auto"
    api_url, api_key = _resolve_cliproxy_target(api_url=api_url, api_key=api_key)

    result: dict[str, Any] = {
        "id": getattr(account, "id", None),
        "email": getattr(account, "email", ""),
        "action": "skip",
        "ok": True,
        "direction": "unknown",
        "direction_label": _direction_label("unknown"),
        "detail": "",
        "message": "",
        # 注意不要把 AT 原文带进返回值 —— 这个结构会原样回给前端
        "local": {
            "has_access_token": bool(_local_inputs(account).get("access_token")),
            "last_refresh": _local_inputs(account).get("last_refresh") or "",
        },
        "remote": {},
    }

    if files is None:
        try:
            files = list_auth_files(api_url=api_url, api_key=api_key)
        except Exception as exc:
            result.update({"action": "fail", "ok": False, "direction": "unreachable", "message": str(exc)})
            return result

    matched = _match_auth_file(account, files)
    local_inputs = _local_inputs(account)
    compare = build_version_compare(local_inputs, _remote_inputs(matched))
    direction = str(compare.get("direction") or "unknown")
    result["direction"] = direction
    result["direction_label"] = _direction_label(direction)
    result["detail"] = str(compare.get("detail") or "")
    result["local"] = {
        "has_access_token": bool(local_inputs.get("access_token")),
        "at_expires_at": compare.get("local_at_expires_at") or "",
        "last_refresh": compare.get("local_last_refresh") or "",
    }
    result["remote"] = {
        "name": str((matched or {}).get("name") or ""),
        "status": str((matched or {}).get("status") or ""),
        "has_credentials": bool(compare.get("remote_has_credentials")),
        "at_expires_at": compare.get("remote_at_expires_at") or "",
        "last_refresh": compare.get("remote_last_refresh") or "",
    }

    action = _decide_action(direction, mode)
    if action == "skip":
        message = result["detail"] or "两边一致，无需同步"
        if direction in ("local_newer", "remote_newer") and mode != "auto":
            message = f"{message}（当前模式 {mode}，未动作）"
        result.update({"action": "skip", "ok": True, "message": message})
        _record_sync_action(account, "skip", direction, True, message)
        _persist(account, session=session, commit=commit)
        return result

    try:
        if action == "push":
            outcome = _push(account, session=session, api_url=api_url, api_key=api_key, commit=False)
        else:
            outcome = _pull(account, matched, session=session, api_url=api_url, api_key=api_key)
    except Exception as exc:
        outcome = {"ok": False, "skipped": False, "message": f"{action} 异常: {exc}"}

    result.update(
        {
            "action": action if not outcome.get("skipped") else "skip",
            "ok": bool(outcome.get("ok")),
            "message": str(outcome.get("message") or ""),
        }
    )
    _record_sync_action(account, str(result["action"]), direction, bool(result["ok"]), result["message"])
    _persist(account, session=session, commit=commit)
    return result


def _persist(account: Any, *, session: Session | None, commit: bool) -> None:
    if session is None:
        return
    setattr(account, "updated_at", _utcnow())
    session.add(account)
    if commit:
        session.commit()
        session.refresh(account)


def build_cpa_sync_report(
    accounts: Iterable[Any],
    *,
    api_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """只读对比报告：拉一次远端清单，逐个算版本，不探测、不写库。"""
    account_list = list(accounts)
    api_url, api_key = _resolve_cliproxy_target(api_url=api_url, api_key=api_key)
    summary: dict[str, Any] = {
        "total": len(account_list),
        "reachable": True,
        "base_url": str(api_url or ""),
        "message": "",
        "counts": {key: 0 for key in VERSION_DIRECTION_LABELS},
        "items": [],
    }
    if not account_list:
        return summary

    try:
        files = list_auth_files(api_url=api_url, api_key=api_key)
    except Exception as exc:
        summary.update({"reachable": False, "message": str(exc)})
        for account in account_list:
            summary["items"].append(
                {
                    "id": getattr(account, "id", None),
                    "email": getattr(account, "email", ""),
                    "direction": "unreachable",
                    "direction_label": "面板不可达",
                    "detail": str(exc),
                    "local": _local_inputs(account),
                    "remote": {},
                }
            )
        return summary

    summary["remote_auth_file_count"] = len(files)
    for account in account_list:
        matched = _match_auth_file(account, files)
        compare = build_version_compare(_local_inputs(account), _remote_inputs(matched))
        direction = str(compare.get("direction") or "unknown")
        summary["counts"][direction] = int(summary["counts"].get(direction, 0)) + 1
        summary["items"].append(
            {
                "id": getattr(account, "id", None),
                "email": getattr(account, "email", ""),
                "direction": direction,
                "direction_label": _direction_label(direction),
                "detail": str(compare.get("detail") or ""),
                "local": {
                    "at_expires_at": compare.get("local_at_expires_at") or "",
                    "last_refresh": compare.get("local_last_refresh") or "",
                },
                "remote": {
                    "name": str((matched or {}).get("name") or ""),
                    "status": str((matched or {}).get("status") or ""),
                    "has_credentials": bool(compare.get("remote_has_credentials")),
                    "at_expires_at": compare.get("remote_at_expires_at") or "",
                    "last_refresh": compare.get("remote_last_refresh") or "",
                },
            }
        )
    return summary


def sync_chatgpt_accounts_with_cpa(
    accounts: Iterable[Any],
    *,
    session: Session | None = None,
    mode: str = "auto",
    api_url: str | None = None,
    api_key: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """批量同步：远端清单只拉一次，逐个账号对比 + 动作。"""
    account_list = list(accounts)
    api_url, api_key = _resolve_cliproxy_target(api_url=api_url, api_key=api_key)
    summary: dict[str, Any] = {
        "total": len(account_list),
        "pushed": 0,
        "pulled": 0,
        "skipped": 0,
        "failed": 0,
        "unreachable": False,
        "items": [],
    }
    if not account_list:
        return summary

    try:
        files = list_auth_files(api_url=api_url, api_key=api_key)
    except Exception as exc:
        summary["unreachable"] = True
        summary["message"] = str(exc)
        for account in account_list:
            summary["items"].append(
                {
                    "id": getattr(account, "id", None),
                    "email": getattr(account, "email", ""),
                    "action": "fail",
                    "ok": False,
                    "direction": "unreachable",
                    "message": str(exc),
                }
            )
        summary["failed"] = len(account_list)
        logger.warning("CPA 账号同步失败：无法获取 auth-files, accounts=%s, error=%s", len(account_list), exc)
        return summary

    for index, account in enumerate(account_list):
        try:
            item = sync_chatgpt_account_with_cpa(
                account,
                session=session,
                mode=mode,
                files=files,
                api_url=api_url,
                api_key=api_key,
                commit=False,
            )
        except Exception as exc:
            item = {
                "id": getattr(account, "id", None),
                "email": getattr(account, "email", ""),
                "action": "fail",
                "ok": False,
                "direction": "unknown",
                "message": str(exc),
            }
        action = str(item.get("action") or "skip")
        if action == "push" and item.get("ok"):
            summary["pushed"] += 1
        elif action == "pull" and item.get("ok"):
            summary["pulled"] += 1
        elif action == "fail" or not item.get("ok"):
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
        summary["items"].append(item)
        if index < len(account_list) - 1:
            time.sleep(BATCH_PROBE_DELAY_SECONDS)

    if session is not None and commit:
        session.commit()
    logger.info(
        "CPA 账号同步完成：accounts=%s, pushed=%s, pulled=%s, skipped=%s, failed=%s",
        len(account_list),
        summary["pushed"],
        summary["pulled"],
        summary["skipped"],
        summary["failed"],
    )
    return summary
