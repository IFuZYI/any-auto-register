"""iCloud 主号与隐私邮箱的业务层。

对外提供主号登录落库、Hide My Email 生成/同步/删除以及 IMAP 实时收件；
凭据在这里完成加解密，调用方永远拿不到原文。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from sqlmodel import Session, select

from core.config_store import config_store
from core.db import (
    ICloudAccountModel,
    ICloudAliasModel,
    OutlookAccountModel,
    engine,
    new_alias_share_token,
)
from core.secret_box import secret_box
from platforms.icloud import (
    ALIAS_STATUS_ACTIVE,
    ICloudCredentials,
    ICloudError,
    LoginRequest,
    LoginState,
    MailMessage,
    SessionImportRequest,
    fetch_inbox,
    login_manager,
    normalize_region,
    web_client,
)

logger = logging.getLogger(__name__)

# Apple 对每个主号限制每滚动小时最多成功生成 5 个隐私邮箱。
HOURLY_ALIAS_LIMIT = 5
DEFAULT_MESSAGE_LIMIT = 50

# 隐私邮箱免登录页的路径前缀。前端 `aliasMailUrl()` 拼的是同一个路径，两边必须一致，
# 不然导出的链接贴回来自己都打不开。
SHARED_MAIL_PATH_PREFIX = "/m/"

# 号池落库的 account_type。隐私邮箱走 URL 轮询取码，和 MailAPI URL 是同一类账号。
POOL_ACCOUNT_TYPE_MAILAPI_URL = "mailapi_url"
# 视图配置项，写进去后注册取号才会只取 mailapi_url 这一类（见 services/mail_imports/import_source.py）。
MAIL_IMPORT_SOURCE_KEY = "mail_import_source"
MAIL_IMPORT_SOURCE_MAILAPI = "mailapi"

# 同一主号的隐私邮箱生成必须串行，否则并发注册会互相挤占小时额度。
_ACCOUNT_LOCKS: dict[int, threading.Lock] = {}
_ACCOUNT_LOCKS_GUARD = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _account_lock(account_id: int) -> threading.Lock:
    with _ACCOUNT_LOCKS_GUARD:
        return _ACCOUNT_LOCKS.setdefault(int(account_id), threading.Lock())


# --------------------------------------------------------------------- 读取


def load_credentials(row: ICloudAccountModel) -> ICloudCredentials:
    try:
        payload = secret_box.decrypt_json(row.credentials_cipher)
    except Exception as exc:
        # 密文本身没坏，是解密密钥换了（最常见的原因是密钥没放在挂载卷里，
        # 重建容器时被重新生成）。抛原始的 InvalidTag 只会得到一个 500，
        # 用户看不出该怎么办——只能重新登录主号。
        raise ICloudError(
            "credentials_unreadable",
            f"iCloud 主号 {row.email} 的凭据无法解密（加密密钥已变更），请重新登录该主号",
        ) from exc
    return ICloudCredentials.from_dict(payload)


def get_account(account_id: int) -> ICloudAccountModel:
    with Session(engine) as session:
        row = session.get(ICloudAccountModel, int(account_id))
        if row is None:
            raise ICloudError("account_not_found", "iCloud 主号不存在")
        return row


def find_account_by_email(email: str) -> Optional[ICloudAccountModel]:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    with Session(engine) as session:
        return session.exec(
            select(ICloudAccountModel).where(ICloudAccountModel.email == normalized)
        ).first()


def resolve_account(email: str = "") -> ICloudAccountModel:
    """按邮箱定位主号；未指定时取第一个可用主号。"""
    if str(email or "").strip():
        row = find_account_by_email(email)
        if row is None:
            raise ICloudError("account_not_found", f"iCloud 主号不存在: {email}")
        if not row.enabled:
            raise ICloudError("account_disabled", f"iCloud 主号已停用: {row.email}")
        return row

    with Session(engine) as session:
        row = session.exec(
            select(ICloudAccountModel)
            .where(ICloudAccountModel.enabled == True)  # noqa: E712 - SQLModel 需要值比较
            .order_by(ICloudAccountModel.id)
        ).first()
    if row is None:
        raise ICloudError("account_not_found", "还没有可用的 iCloud 主号，请先完成 Apple ID 登录")
    return row


def list_accounts() -> list[dict[str, Any]]:
    with Session(engine) as session:
        rows = session.exec(select(ICloudAccountModel).order_by(ICloudAccountModel.id)).all()
        aliases = session.exec(select(ICloudAliasModel)).all()
    counts: dict[int, int] = {}
    for alias in aliases:
        counts[alias.account_id] = counts.get(alias.account_id, 0) + 1
    return [_account_to_dict(row, alias_count=counts.get(row.id or 0, 0)) for row in rows]


def _account_to_dict(row: ICloudAccountModel, *, alias_count: int = 0) -> dict[str, Any]:
    quota = alias_quota(row.id or 0)
    try:
        credential_state = load_credentials(row).public_state()
    except Exception:
        credential_state = {"credentials_unreadable": True}
    return {
        "id": row.id,
        "email": row.email,
        "display_name": row.display_name,
        "region": row.region,
        "status": row.status,
        "enabled": row.enabled,
        "alias_count": alias_count,
        "sync_error": row.sync_error,
        "last_sync_at": _as_utc(row.last_sync_at).isoformat() if row.last_sync_at else None,
        "created_at": _as_utc(row.created_at).isoformat() if row.created_at else None,
        "credential_state": credential_state,
        "quota": quota,
    }


def alias_quota(account_id: int) -> dict[str, Any]:
    """按滚动一小时窗口统计剩余生成额度。"""
    since = _utcnow() - timedelta(hours=1)
    with Session(engine) as session:
        recent = session.exec(
            select(ICloudAliasModel)
            .where(ICloudAliasModel.account_id == int(account_id))
            .where(ICloudAliasModel.created_at >= since.replace(tzinfo=None))
        ).all()
    used = len(recent)
    earliest = min((_as_utc(item.created_at) for item in recent), default=None)
    return {
        "limit": HOURLY_ALIAS_LIMIT,
        "used": used,
        "remaining": max(HOURLY_ALIAS_LIMIT - used, 0),
        "reset_at": (earliest + timedelta(hours=1)).isoformat() if earliest else None,
    }


def list_aliases(account_id: Optional[int] = None) -> list[dict[str, Any]]:
    with Session(engine) as session:
        query = select(ICloudAliasModel)
        if account_id is not None:
            query = query.where(ICloudAliasModel.account_id == int(account_id))
        rows = session.exec(query.order_by(ICloudAliasModel.id.desc())).all()
        emails = {
            row.id: row.email
            for row in session.exec(select(ICloudAccountModel)).all()
        }
    return [_alias_to_dict(row, emails.get(row.account_id, "")) for row in rows]


def _alias_to_dict(row: ICloudAliasModel, account_email: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "account_email": account_email,
        "address": row.address,
        "label": row.label,
        "note": row.note,
        "status": row.status,
        "provider_id": row.provider_id,
        "share_token": row.share_token or "",
        "created_at": _as_utc(row.created_at).isoformat() if row.created_at else None,
    }


# ----------------------------------------------------------------- 主号登录


def start_login(request: LoginRequest, *, proxy: str | None = None) -> LoginState:
    return login_manager().start(request, proxy=proxy)


def login_state(login_id: str) -> LoginState:
    return login_manager().state(login_id)


def verify_login(login_id: str, code: str) -> LoginState:
    return login_manager().verify(login_id, code)


def resend_login_code(login_id: str) -> LoginState:
    return login_manager().resend(login_id)


def send_login_sms(login_id: str, phone_id: int, mode: str = "") -> LoginState:
    return login_manager().send_sms(login_id, phone_id, mode)


def cancel_login(login_id: str) -> None:
    login_manager().cancel(login_id)


def complete_login(state: LoginState, *, proxy: str | None = None) -> dict[str, Any]:
    """把已完成的登录会话导入为主号，随后销毁内存中的会话。"""
    if state.session is None:
        raise ICloudError("login_incomplete", "登录尚未完成，无法保存主号")
    try:
        account = import_session(
            state.session,
            email=state.email,
            display_name=state.display_name,
            proxy=proxy,
        )
    finally:
        cancel_login(state.login_id)
    return account


def import_session(
    request: SessionImportRequest,
    *,
    email: str = "",
    display_name: str = "",
    proxy: str | None = None,
) -> dict[str, Any]:
    """校验并保存一份 iCloud Web Session（应用内登录与手工 Cookie 导入共用）。"""
    with web_client(proxy=proxy) as client:
        imported = client.import_session(request)

    resolved_email = str(email or "").strip().lower() or imported.account_email
    if not resolved_email:
        raise ICloudError("invalid_config", "无法确定主号邮箱，请手工填写 Apple ID")

    with Session(engine) as session:
        row = session.exec(
            select(ICloudAccountModel).where(ICloudAccountModel.email == resolved_email)
        ).first()
        credentials = imported.credentials
        if row is None:
            row = ICloudAccountModel(email=resolved_email)
        else:
            # 重新登录时保留已有 IMAP 凭据，除非本次显式提交了新的配置。
            credentials = load_credentials(row).merged_with(credentials)

        row.display_name = str(display_name or "").strip() or row.display_name
        row.region = normalize_region(credentials.region)
        row.status = "active"
        row.enabled = True
        row.sync_error = ""
        row.credentials_cipher = secret_box.encrypt_json(credentials.to_dict())
        row.updated_at = _utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _account_to_dict(row)


def set_account_enabled(account_id: int, enabled: bool) -> dict[str, Any]:
    with Session(engine) as session:
        row = session.get(ICloudAccountModel, int(account_id))
        if row is None:
            raise ICloudError("account_not_found", "iCloud 主号不存在")
        row.enabled = bool(enabled)
        row.updated_at = _utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _account_to_dict(row)


def delete_account(account_id: int) -> None:
    with Session(engine) as session:
        row = session.get(ICloudAccountModel, int(account_id))
        if row is None:
            raise ICloudError("account_not_found", "iCloud 主号不存在")
        for alias in session.exec(
            select(ICloudAliasModel).where(ICloudAliasModel.account_id == row.id)
        ).all():
            session.delete(alias)
        session.delete(row)
        session.commit()


# ------------------------------------------------------------- 隐私邮箱管理


def generate_alias(account_id: int, *, label: str = "", note: str = "", proxy: str | None = None) -> dict[str, Any]:
    """生成并保留一个隐私邮箱地址。"""
    with _account_lock(account_id):
        row = get_account(account_id)
        if not row.enabled:
            raise ICloudError("account_disabled", f"iCloud 主号已停用: {row.email}")
        quota = alias_quota(account_id)
        if quota["remaining"] <= 0:
            raise ICloudError(
                "provider_rate_limited",
                f"主号 {row.email} 本小时的隐私邮箱生成额度已用完（{HOURLY_ALIAS_LIMIT} 个/小时）",
            )

        credentials = load_credentials(row)
        with web_client(proxy=proxy) as client:
            private_email = client.generate_private_email(credentials, label=label, note=note)
        return _upsert_alias(account_id, private_email.to_dict(), row.email)


def sync_aliases(account_id: int, *, proxy: str | None = None) -> dict[str, Any]:
    """从 iCloud 拉取主号名下全部历史隐私邮箱，按地址去重合并到本地。"""
    row = get_account(account_id)
    credentials = load_credentials(row)
    try:
        with web_client(proxy=proxy) as client:
            private_emails = client.list_private_emails(credentials)
    except ICloudError as exc:
        _record_sync_error(account_id, str(exc))
        raise

    created = updated = 0
    for private_email in private_emails:
        _, is_new = _upsert_alias(account_id, private_email.to_dict(), row.email, return_flag=True)
        created += int(is_new)
        updated += int(not is_new)

    _record_sync_error(account_id, "")
    return {
        "account_id": account_id,
        "fetched": len(private_emails),
        "created": created,
        "updated": updated,
        "synced_at": _utcnow().isoformat(),
    }


def delete_alias(alias_id: int, *, remote: bool = True, proxy: str | None = None) -> None:
    with Session(engine) as session:
        alias = session.get(ICloudAliasModel, int(alias_id))
        if alias is None:
            raise ICloudError("alias_not_found", "隐私邮箱不存在")
        account = session.get(ICloudAccountModel, alias.account_id)
        snapshot = (alias.address, alias.provider_id, alias.status)

    if remote and account is not None:
        credentials = load_credentials(account)
        with web_client(proxy=proxy) as client:
            client.delete_private_email(
                credentials, address=snapshot[0], provider_id=snapshot[1], status=snapshot[2]
            )

    with Session(engine) as session:
        alias = session.get(ICloudAliasModel, int(alias_id))
        if alias is not None:
            session.delete(alias)
            session.commit()


def delete_aliases(
    alias_ids: list[int], *, remote: bool = True, proxy: str | None = None
) -> dict[str, Any]:
    """批量删除隐私邮箱。

    逐条走单条删除的老路，一条失败不拖累后面的：上游偶发拒绝、个别地址已经在
    Apple 那边不存在都很常见，整批回滚只会让用户反复重试。
    """
    deleted: list[int] = []
    failed: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_id in alias_ids:
        alias_id = int(raw_id)
        if alias_id in seen:
            continue
        seen.add(alias_id)
        try:
            delete_alias(alias_id, remote=remote, proxy=proxy)
        except ICloudError as error:
            failed.append({"alias_id": alias_id, "code": error.code, "message": str(error)})
        except Exception as error:  # noqa: BLE001 - 批量里不能让单条异常吞掉剩下的
            logger.exception("批量删除隐私邮箱 %s 失败", alias_id)
            failed.append({"alias_id": alias_id, "code": "unknown", "message": str(error)})
        else:
            deleted.append(alias_id)
    return {"deleted": deleted, "failed": failed}


# ------------------------------------------------------- 隐私邮箱导入 MailAPI 池


def normalize_public_base_url(value: Any) -> str:
    """把面板访问地址收敛成 ``scheme://host[:port]``，认不出就返回空串。

    只接受 http/https 且有主机名的地址。导出的免登录链接是要贴进号池、
    以后由注册任务去轮询的，存进去一个拼不起来的串比不存更糟。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def resolve_public_base_url(origin: Any = "") -> str:
    """决定免登录链接用哪个面板地址。

    前端知道用户实际是从哪个地址打开面板的（反代、内网穿透、换端口都算），
    所以优先用它传来的 origin；只有它没给或给得不合法时才退回配置项
    ``public_base_url``。
    """
    return normalize_public_base_url(origin) or normalize_public_base_url(
        config_store.get("public_base_url", "")
    )


def build_alias_mailapi_lines(
    aliases: Iterable[dict[str, Any]], base_url: str
) -> tuple[list[str], list[str]]:
    """把别名行拼成 ``隐私邮箱----<面板地址>/m/<share_token>``，与前端导出一致。

    返回 ``(lines, skipped)``。没有 share_token 的老别名拼不出链接——这正是
    前端 ``countExportableAliases`` 要整行跳过的那些；写成末尾空着的
    ``邮箱----`` 只会让对面的导入器报格式错。
    """
    prefix = str(base_url or "").strip().rstrip("/")
    lines: list[str] = []
    skipped: list[str] = []
    for alias in aliases:
        address = str(alias.get("address") or "").strip().lower()
        token = str(alias.get("share_token") or "").strip()
        if not address or not token or not prefix:
            skipped.append(address)
            continue
        lines.append(f"{address}----{prefix}{SHARED_MAIL_PATH_PREFIX}{token}")
    return lines, skipped


def _list_pool_emails() -> set[str]:
    with Session(engine) as session:
        return {
            str(email or "").strip().lower()
            for email in session.exec(select(OutlookAccountModel.email)).all()
            if str(email or "").strip()
        }


def _resolve_pool_alias_ids(
    alias_ids: Optional[list[int]], account_id: Optional[int]
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """挑出要入池的别名；一条都没挑到时，把原因一并带回去。

    传了 ``account_id`` 就只在这位主号的别名里挑：勾选的行可能已经被"全部主号"
    之外的筛选换掉了，不能拿它去越界取别人的别名。
    """
    aliases = list_aliases(account_id)
    wanted: list[int] = []
    seen: set[int] = set()
    for raw_id in alias_ids or []:
        try:
            alias_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if alias_id not in seen:
            seen.add(alias_id)
            wanted.append(alias_id)

    if wanted:
        picked = set(wanted)
        aliases = [alias for alias in aliases if int(alias["id"]) in picked]
        if not aliases:
            return [], "选中的隐私邮箱都不存在，可能已经被删掉了"
    elif not aliases:
        return [], "还没有隐私邮箱，先在「隐私邮箱」里生成或同步一批"
    return aliases, None


def _apply_mail_import_source() -> None:
    """把导入视图切到 MailAPI URL。

    池子里混着 OAuth 号和 MailAPI URL 号，取号是按视图筛 account_type 的
    （``services/mail_imports/import_source.py``）：视图还停在 Outlook 的话，
    这些刚导进去的隐私邮箱一个都不会被取到，用户只会看到注册任务"没有可用邮箱"。
    """
    try:
        from services.mail_imports import normalize_mail_import_source

        current = normalize_mail_import_source(
            config_store.get(MAIL_IMPORT_SOURCE_KEY, ""),
            config_store.get("mail_provider", ""),
        )
        if current != MAIL_IMPORT_SOURCE_MAILAPI:
            config_store.set(MAIL_IMPORT_SOURCE_KEY, MAIL_IMPORT_SOURCE_MAILAPI)
    except Exception:  # noqa: BLE001 - 切视图失败不该让已经入池的账号白导
        logger.exception("切换 mail_import_source 失败")


def import_aliases_to_mailapi_pool(
    alias_ids: Optional[list[int]] = None,
    *,
    account_id: Optional[int] = None,
    origin: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """把隐私邮箱导入 MailAPI URL 账号池，等价于「导出 mail_url 再导入」。

    走的是邮箱导入那条老路（``MicrosoftMailImportStrategy``），所以查重、
    ``account_type=mailapi_url`` 落库这些规则都跟手工导入一模一样；差别只在
    内容由后端按 share_token 现拼，省掉导出再导入那一步。
    """
    aliases, reason = _resolve_pool_alias_ids(alias_ids, account_id)
    if reason:
        return {
            "total": 0,
            "imported": 0,
            "skipped": 0,
            "failed": 0,
            "skipped_aliases": [],
            "errors": [reason],
            "source": MAIL_IMPORT_SOURCE_MAILAPI,
        }

    base_url = resolve_public_base_url(origin)
    if not base_url:
        return {
            "total": len(aliases),
            "imported": 0,
            "skipped": 0,
            "failed": 0,
            "skipped_aliases": [],
            "errors": [
                "无法确定面板访问地址，拼不出免登录邮件链接。"
                "请从浏览器地址栏打开面板后再试，或在设置里填写「面板访问地址」。"
            ],
            "source": MAIL_IMPORT_SOURCE_MAILAPI,
        }

    lines, skipped = build_alias_mailapi_lines(aliases, base_url)
    if not lines:
        return {
            "total": len(aliases),
            "imported": 0,
            "skipped": len(skipped),
            "failed": 0,
            "skipped_aliases": skipped,
            "errors": ["选中的隐私邮箱都还没有免登录邮件链接，导不出可导入的内容"],
            "source": MAIL_IMPORT_SOURCE_MAILAPI,
        }

    from services.mail_imports import MailImportExecuteRequest, mail_import_registry

    strategy = mail_import_registry.get("microsoft")
    existing = _list_pool_emails()
    response = strategy.execute(
        MailImportExecuteRequest(
            type="microsoft",
            content="\n".join(lines),
            enabled=bool(enabled),
            # 导入完成后单独切视图：这一批可能一条都没进池（全被查重挡下），
            # 那种情况不该动用户当前的设置
            bind_to_config=False,
        )
    )

    imported_emails = {
        str(item.get("email") or "").strip().lower()
        for item in (response.meta.get("accounts") or [])
    }
    errors = list(response.errors)
    # 已经在池子里的不算失败，但得如实说一声，不然用户会以为点了没反应
    already = len(
        [
            email
            for email in (line.split("----", 1)[0] for line in lines)
            if email.strip().lower() in existing and email.strip().lower() not in imported_emails
        ]
    )
    if already:
        errors.append(f"{already} 个隐私邮箱早就在号池里了，没有重复导入")

    if response.summary.success:
        _apply_mail_import_source()

    return {
        "total": response.summary.total,
        "imported": response.summary.success,
        "skipped": len(skipped),
        "failed": response.summary.failed,
        "skipped_aliases": skipped,
        "errors": errors,
        "source": MAIL_IMPORT_SOURCE_MAILAPI,
    }


def _upsert_alias(
    account_id: int,
    payload: dict[str, Any],
    account_email: str,
    *,
    return_flag: bool = False,
):
    address = str(payload.get("address") or "").strip().lower()
    with Session(engine) as session:
        row = session.exec(
            select(ICloudAliasModel).where(ICloudAliasModel.address == address)
        ).first()
        is_new = row is None
        if row is None:
            row = ICloudAliasModel(account_id=int(account_id), address=address)
        row.account_id = int(account_id)
        row.label = str(payload.get("label") or "") or row.label
        row.note = str(payload.get("note") or "") or row.note
        row.status = str(payload.get("status") or ALIAS_STATUS_ACTIVE)
        row.provider_id = str(payload.get("provider_id") or "") or row.provider_id
        row.share_token = row.share_token or new_alias_share_token()
        row.updated_at = _utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        result = _alias_to_dict(row, account_email)
    return (result, is_new) if return_flag else result


def _record_sync_error(account_id: int, message: str) -> None:
    with Session(engine) as session:
        row = session.get(ICloudAccountModel, int(account_id))
        if row is None:
            return
        row.sync_error = message
        row.status = "error" if message else "active"
        row.last_sync_at = _utcnow()
        row.updated_at = _utcnow()
        session.add(row)
        session.commit()


# --------------------------------------------------------------------- 收件


def fetch_account_messages(
    account_id: int, *, limit: int = DEFAULT_MESSAGE_LIMIT, recipient: str = ""
) -> list[MailMessage]:
    row = get_account(account_id)
    credentials = load_credentials(row)
    if not credentials.has_imap:
        raise ICloudError(
            "invalid_config", f"主号 {row.email} 还没有配置 IMAP 应用专用密码，无法收件"
        )
    return fetch_inbox(credentials, row.email, limit=limit, recipient=recipient)


def fetch_alias_messages(alias_id: int, *, limit: int = DEFAULT_MESSAGE_LIMIT) -> list[MailMessage]:
    with Session(engine) as session:
        alias = session.get(ICloudAliasModel, int(alias_id))
        if alias is None:
            raise ICloudError("alias_not_found", "隐私邮箱不存在")
        account_id, address = alias.account_id, alias.address
    return fetch_account_messages(account_id, limit=limit, recipient=address)


def fetch_latest_shared_message(
    share_token: str, *, limit: int = DEFAULT_MESSAGE_LIMIT
) -> tuple[str, Optional[MailMessage]]:
    """按分享 token 取该隐私邮箱的最新一封邮件。

    给免登录页面用，所以只认 token、只回一封，拿不到地址以外的任何账号信息。
    """
    token = str(share_token or "").strip()
    if not token:
        raise ICloudError("alias_not_found", "隐私邮箱不存在")
    with Session(engine) as session:
        alias = session.exec(
            select(ICloudAliasModel).where(ICloudAliasModel.share_token == token)
        ).first()
        if alias is None:
            raise ICloudError("alias_not_found", "隐私邮箱不存在")
        account_id, address = alias.account_id, alias.address
    messages = fetch_account_messages(account_id, limit=limit, recipient=address)
    return address, messages[0] if messages else None
