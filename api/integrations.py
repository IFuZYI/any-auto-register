from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from core.db import AccountModel, engine
from services.external_apps import list_status, test_connection
from services.chatgpt_account_state import filter_accounts_by_plus_status
from services.chatgpt_sync import backfill_chatgpt_account_to_cpa, get_cliproxy_sync_state

router = APIRouter(prefix="/integrations", tags=["integrations"])


class AccountQuery(BaseModel):
    """选号条件。report / sync / backfill 三处共用同一套筛选语义。"""

    platforms: list[str] = Field(default_factory=lambda: ["chatgpt"])
    account_ids: list[int] = Field(default_factory=list)
    status: Optional[str] = None
    email: Optional[str] = None
    plus_status: Optional[str] = None
    limit: int = 0


class BackfillRequest(AccountQuery):
    pending_only: bool = False


class SyncRequest(AccountQuery):
    # auto: 谁新听谁的；push_only / pull_only: 只朝一个方向动
    mode: str = "auto"


def _select_accounts(session: Session, body: AccountQuery) -> list[AccountModel]:
    targets = set(body.platforms or [])
    q = select(AccountModel)
    if body.account_ids:
        q = q.where(AccountModel.id.in_(body.account_ids))
        if targets:
            q = q.where(AccountModel.platform.in_(targets))
    elif targets:
        q = q.where(AccountModel.platform.in_(targets))
    else:
        return []

    if body.status:
        q = q.where(AccountModel.status == body.status)
    if body.email:
        q = q.where(AccountModel.email.contains(body.email))

    rows = list(session.exec(q).all())
    if body.plus_status:
        rows = filter_accounts_by_plus_status(rows, body.plus_status)
    if body.limit and body.limit > 0:
        rows = rows[: body.limit]
    return rows


def _to_sync_account(model: AccountModel):
    """把 ORM 行拍平成同步层认的 duck-typed 快照。"""
    extra = model.get_extra()

    class _SyncAccount:
        pass

    account = _SyncAccount()
    account.id = model.id
    account.email = model.email
    account.user_id = model.user_id
    account.token = model.token
    account.extra = extra
    account.access_token = extra.get("access_token") or model.token
    account.refresh_token = extra.get("refresh_token", "")
    account.id_token = extra.get("id_token", "")
    account.session_token = extra.get("session_token", "")
    account.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
    account.cookies = extra.get("cookies", "")
    account.updated_at = model.updated_at
    return account


# ── 远程 CPA 面板状态 ──────────────────────────────────────
@router.get("/services")
def get_services():
    return {"items": list_status()}


@router.post("/services/{name}/test")
def test_service(name: str):
    if name != "cliproxyapi":
        return {"ok": False, "message": f"未知的插件: {name}"}
    return test_connection()


# ── 账号版本对比与同步 ─────────────────────────────────────
@router.post("/sync/report")
def sync_report(body: AccountQuery):
    """只读对比：本地 vs CPA 每个账号谁更新。不探测、不写库。"""
    from services.cpa_account_sync import build_cpa_sync_report

    with Session(engine) as s:
        rows = _select_accounts(s, body)
    return build_cpa_sync_report([_to_sync_account(row) for row in rows])


@router.post("/sync/accounts")
def sync_accounts(body: SyncRequest):
    """按对比结果把两边更新到最新版本。"""
    from services.cpa_account_sync import sync_chatgpt_accounts_with_cpa

    with Session(engine) as s:
        rows = _select_accounts(s, body)
        if not rows:
            return {"total": 0, "pushed": 0, "pulled": 0, "skipped": 0, "failed": 0, "unreachable": False, "items": []}
        return sync_chatgpt_accounts_with_cpa(rows, session=s, mode=body.mode, commit=True)


# ── 回填 ──────────────────────────────────────────────────
@router.post("/backfill")
def backfill_integrations(body: BackfillRequest):
    summary = {"total": 0, "success": 0, "failed": 0, "skipped": 0, "items": []}

    with Session(engine) as s:
        rows = _select_accounts(s, body)
        if not rows:
            return summary

        if body.pending_only:
            rows = [
                row for row in rows
                if row.platform != "chatgpt"
                or str(get_cliproxy_sync_state(row).get("remote_state") or "").strip().lower() == "not_found"
            ]

        for row in rows:
            item = {"platform": row.platform, "email": row.email, "results": []}
            try:
                results = []
                if row.platform == "chatgpt":
                    outcome = backfill_chatgpt_account_to_cpa(row, session=s, commit=True)
                    ok = bool(outcome.get("ok"))
                    skipped = bool(outcome.get("skipped"))
                    results.extend(outcome.get("results") or [])
                    if not results:
                        results.append({"name": "CLIProxyAPI", "ok": ok, "msg": outcome.get("message", "")})
                    if skipped:
                        summary["skipped"] += 1
                    elif ok:
                        summary["success"] += 1
                    else:
                        summary["failed"] += 1

                if not results:
                    item["results"].append({"name": "skip", "ok": False, "msg": "未配置对应导入目标"})
                    summary["failed"] += 1
                else:
                    item["results"] = results
            except Exception as e:
                s.rollback()
                item["results"].append({"name": "error", "ok": False, "msg": str(e)})
                summary["failed"] += 1
            summary["items"].append(item)
            summary["total"] += 1

    return summary
