"""远程 CPA 面板连接器。

这个插件以前是在本机 clone CLIProxyAPI 仓库、用 `go run` 起一个本地服务，
再由面板去管它的启停。现在改成**直连远程 CPA 面板**：地址与密钥统一取
ChatGPT 配置里的 `cpa_api_url` / `cpa_api_key`（旧键 `cliproxyapi_base_url` /
`cliproxyapi_management_key` 仍作兜底，老部署不会断）。

模块只负责「这个远端面板现在是什么状态」，不再有任何本地安装/进程管理代码。
"""

from __future__ import annotations

import logging
from typing import Any

SERVICE_NAME = "cliproxyapi"
SERVICE_LABEL = "CPA 面板"
MANAGEMENT_PATH = "/management.html"
AUTH_FILES_PATH = "/v0/management/auth-files"
PROBE_TIMEOUT_SECONDS = 8

logger = logging.getLogger(__name__)


def resolve_target(api_url: str | None = None, api_key: str | None = None) -> tuple[str, str]:
    """解析 CPA 目标地址与密钥，显式入参优先，其次配置（CPA 键优先于旧键）。"""
    from services.chatgpt_sync import _resolve_cliproxy_target

    resolved_url, resolved_key = _resolve_cliproxy_target(api_url=api_url, api_key=api_key)
    return str(resolved_url or "").strip(), str(resolved_key or "").strip()


def management_url(api_url: str | None = None) -> str:
    """远端管理页地址。面板就是 CPA 自带的 management.html。"""
    base = str(api_url or "").strip()
    if not base:
        base, _ = resolve_target()
    base = base.rstrip("/")
    return f"{base}{MANAGEMENT_PATH}" if base else ""


def _count_auth_files(files: Any) -> int:
    if not isinstance(files, list):
        return 0
    return sum(
        1
        for item in files
        if isinstance(item, dict)
        and str(item.get("provider") or item.get("type") or "").strip().lower() == "codex"
    )


def probe(api_url: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    """探一次远端面板，任何异常都吞成 message，不往上抛。

    返回 `reachable`（网络通）/ `auth_ok`（管理接口认这个 Key）两件事，
    前端拿这两个布尔拼状态标签。
    """
    import requests
    import urllib3

    url, key = resolve_target(api_url=api_url, api_key=api_key)
    if not url:
        return {
            "reachable": False,
            "auth_ok": False,
            "status_code": 0,
            "message": "未配置 CPA API URL，请到「设置 → ChatGPT → CPA 面板」填写",
            "auth_file_count": 0,
        }

    target = f"{url}{AUTH_FILES_PATH}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {key}",
    }
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        response = requests.get(target, headers=headers, timeout=PROBE_TIMEOUT_SECONDS, verify=False)
    except requests.exceptions.Timeout:
        return {
            "reachable": False,
            "auth_ok": False,
            "status_code": 0,
            "message": f"连接超时：{url}",
            "auth_file_count": 0,
        }
    except Exception as exc:
        return {
            "reachable": False,
            "auth_ok": False,
            "status_code": 0,
            "message": f"无法连接：{exc}",
            "auth_file_count": 0,
        }

    if response.status_code in (401, 403):
        return {
            "reachable": True,
            "auth_ok": False,
            "status_code": response.status_code,
            "message": "已连上面板，但 API Key 无效或无管理权限",
            "auth_file_count": 0,
        }
    if response.status_code >= 400:
        return {
            "reachable": True,
            "auth_ok": False,
            "status_code": response.status_code,
            "message": f"面板返回 HTTP {response.status_code}",
            "auth_file_count": 0,
        }

    try:
        data = response.json()
    except Exception:
        data = {}
    files = data.get("files", []) if isinstance(data, dict) else []
    return {
        "reachable": True,
        "auth_ok": True,
        "status_code": response.status_code,
        "message": "连接正常",
        "auth_file_count": _count_auth_files(files),
    }


def status_one(api_url: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    """单条状态快照，字段名尽量沿用旧插件的（running / last_error 等）。"""
    url, key = resolve_target(api_url=api_url, api_key=api_key)
    configured = bool(url and key)
    result = probe(api_url=url, api_key=key)
    ok = bool(result.get("reachable") and result.get("auth_ok"))
    return {
        "name": SERVICE_NAME,
        "label": SERVICE_LABEL,
        "kind": "remote",
        "url": url,
        "management_url": management_url(url),
        "management_key": key,
        "configured": configured,
        "running": ok,
        "auth_ok": bool(result.get("auth_ok")),
        "reachable": bool(result.get("reachable")),
        "auth_file_count": int(result.get("auth_file_count") or 0),
        "status_code": int(result.get("status_code") or 0),
        "message": str(result.get("message") or ""),
        # 旧字段，前端与外部脚本都读它，保留同名
        "last_error": "" if ok else str(result.get("message") or ""),
    }


def list_status() -> list[dict[str, Any]]:
    return [status_one()]


def test_connection(api_url: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    """主动测试按钮用：结果与状态快照同构，方便前端直接展示。"""
    return status_one(api_url=api_url, api_key=api_key)
