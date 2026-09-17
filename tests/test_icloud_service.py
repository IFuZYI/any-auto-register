"""iCloud 业务层：凭据加密落库、额度控制、隐私邮箱去重与导入 MailAPI 号池。"""

import base64
import os

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from platforms.icloud.credentials import ICloudCredentials
from platforms.icloud.errors import ICloudError
from platforms.icloud.models import ImportedSession, PrivateEmail, SessionImportRequest


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())

    import core.db as db
    import core.config_store as config_store_module
    from core.secret_box import SecretBox
    import core.secret_box as secret_box_module
    from services import icloud_service

    engine = create_engine(f"sqlite:///{tmp_path / 'icloud.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(icloud_service, "engine", engine)
    # 导号池那条链路会穿过邮件导入策略和配置表，它们各自持有 engine 的引用，
    # 不一起换掉的话落库会写进别的测试的库里（读又读不到，表现为随机失败）。
    import services.mail_imports.providers as mail_import_providers

    monkeypatch.setattr(config_store_module, "engine", engine)
    monkeypatch.setattr(mail_import_providers, "engine", engine)
    monkeypatch.setattr(secret_box_module, "secret_box", SecretBox())
    monkeypatch.setattr(icloud_service, "secret_box", secret_box_module.secret_box)
    return icloud_service


class _StubWebClient:
    def __init__(self, *, imported=None, private_emails=(), generated=None):
        self.imported = imported
        self.private_emails = list(private_emails)
        self.generated = generated
        self.deleted = []

    def import_session(self, _request):
        return self.imported

    def list_private_emails(self, _credentials):
        return self.private_emails

    def generate_private_email(self, _credentials, *, label="", note=""):
        self.generated.label = label
        self.generated.note = note
        return self.generated

    def delete_private_email(self, _credentials, *, address, provider_id="", status=""):
        self.deleted.append(address)


@pytest.fixture
def stub_web_client(monkeypatch, service):
    from contextlib import contextmanager

    holder = {}

    @contextmanager
    def _factory(**_kwargs):
        yield holder["client"]

    monkeypatch.setattr(service, "web_client", _factory)
    return holder


def _imported(email="owner@icloud.com") -> ImportedSession:
    return ImportedSession(
        credentials=ICloudCredentials(
            region="global",
            dsid="123456",
            cookies="a=1",
            hme_service_url="https://hme.example.test",
            client_id="client-1",
            client_build_number="b",
            client_mastering_number="m",
            imap_password="app-specific",
        ),
        account_email=email,
        masked_dsid="12**56",
    )


def test_import_session_stores_encrypted_credentials(service, stub_web_client):
    stub_web_client["client"] = _StubWebClient(imported=_imported())

    account = service.import_session(SessionImportRequest(cookie_header="a=1"))

    assert account["email"] == "owner@icloud.com"
    assert account["credential_state"]["has_imap_credentials"] is True

    row = service.get_account(account["id"])
    assert "app-specific" not in row.credentials_cipher
    assert service.load_credentials(row).imap_password == "app-specific"


def test_reimport_keeps_existing_imap_password_when_not_resubmitted(service, stub_web_client):
    stub_web_client["client"] = _StubWebClient(imported=_imported())
    account = service.import_session(SessionImportRequest(cookie_header="a=1"))

    refreshed = _imported()
    refreshed.credentials.imap_password = ""
    refreshed.credentials.cookies = "a=2"
    stub_web_client["client"] = _StubWebClient(imported=refreshed)
    service.import_session(SessionImportRequest(cookie_header="a=2"))

    credentials = service.load_credentials(service.get_account(account["id"]))
    assert credentials.cookies == "a=2"
    assert credentials.imap_password == "app-specific"


def test_unreadable_credentials_report_a_relogin_hint(service, stub_web_client, monkeypatch):
    """密钥换掉后旧密文解不开，要给出"重新登录"而不是裸的 InvalidTag。"""
    stub_web_client["client"] = _StubWebClient(imported=_imported())
    account = service.import_session(SessionImportRequest(cookie_header="a=1"))

    import core.secret_box as secret_box_module

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(service, "secret_box", secret_box_module.SecretBox())

    row = service.get_account(account["id"])
    with pytest.raises(ICloudError) as excinfo:
        service.load_credentials(row)

    assert excinfo.value.code == "credentials_unreadable"
    assert "重新登录" in str(excinfo.value)
    # InvalidTag 的 str() 是空的，不能在消息尾巴上留一个空括号
    assert not str(excinfo.value).endswith("（）")

    # 账号列表不能因此整个 500，要能标出这一条坏掉了
    assert service.list_accounts()[0]["credential_state"] == {"credentials_unreadable": True}


def test_generate_alias_enforces_hourly_quota(service, stub_web_client):
    stub_web_client["client"] = _StubWebClient(imported=_imported())
    account = service.import_session(SessionImportRequest(cookie_header="a=1"))

    for index in range(service.HOURLY_ALIAS_LIMIT):
        stub_web_client["client"] = _StubWebClient(
            generated=PrivateEmail(address=f"alias{index}@icloud.com", provider_id=f"anon-{index}")
        )
        service.generate_alias(account["id"], label="任务")

    assert service.alias_quota(account["id"])["remaining"] == 0
    stub_web_client["client"] = _StubWebClient(generated=PrivateEmail(address="overflow@icloud.com"))
    with pytest.raises(ICloudError) as excinfo:
        service.generate_alias(account["id"])
    assert excinfo.value.code == "provider_rate_limited"


def test_sync_aliases_deduplicates_by_address(service, stub_web_client):
    stub_web_client["client"] = _StubWebClient(imported=_imported())
    account = service.import_session(SessionImportRequest(cookie_header="a=1"))

    stub_web_client["client"] = _StubWebClient(
        private_emails=[
            PrivateEmail(address="a@icloud.com", provider_id="1"),
            PrivateEmail(address="b@icloud.com", provider_id="2"),
        ]
    )
    first = service.sync_aliases(account["id"])
    second = service.sync_aliases(account["id"])

    assert (first["created"], first["updated"]) == (2, 0)
    assert (second["created"], second["updated"]) == (0, 2)
    assert len(service.list_aliases(account["id"])) == 2


def test_resolve_account_skips_disabled_accounts(service, stub_web_client):
    stub_web_client["client"] = _StubWebClient(imported=_imported())
    account = service.import_session(SessionImportRequest(cookie_header="a=1"))
    service.set_account_enabled(account["id"], False)

    with pytest.raises(ICloudError, match="还没有可用的 iCloud 主号"):
        service.resolve_account()
    with pytest.raises(ICloudError) as excinfo:
        service.resolve_account("owner@icloud.com")
    assert excinfo.value.code == "account_disabled"


# ------------------------------------------------- 隐私邮箱导入 MailAPI 号池


def test_normalize_public_base_url_keeps_only_scheme_and_host(service):
    assert (
        service.normalize_public_base_url("https://reg.example.com/some/path?x=1#f")
        == "https://reg.example.com"
    )
    assert service.normalize_public_base_url("http://192.168.1.9:8000") == "http://192.168.1.9:8000"
    for bad in ("", "   ", "reg.example.com", "javascript:alert(1)", "file:///etc/passwd"):
        assert service.normalize_public_base_url(bad) == ""


def test_build_alias_mailapi_lines_skips_aliases_without_share_token(service):
    lines, skipped = service.build_alias_mailapi_lines(
        [
            {"address": "A@icloud.com", "share_token": "tok-a"},
            {"address": "b@icloud.com", "share_token": ""},
        ],
        "https://reg.example.com/",
    )

    # 拼出来的就是邮箱导入里 `邮箱----mailapi_url` 那一行，地址统一小写
    assert lines == ["a@icloud.com----https://reg.example.com/m/tok-a"]
    assert skipped == ["b@icloud.com"]


def _import_aliases(service, stub_web_client, addresses):
    stub_web_client["client"] = _StubWebClient(imported=_imported())
    account = service.import_session(SessionImportRequest(cookie_header="a=1"))
    stub_web_client["client"] = _StubWebClient(
        private_emails=[
            PrivateEmail(address=address, provider_id=f"anon-{index}")
            for index, address in enumerate(addresses)
        ]
    )
    service.sync_aliases(account["id"])
    return account


def test_import_aliases_into_pool_creates_mailapi_accounts(service, stub_web_client):
    account = _import_aliases(service, stub_web_client, ["a@icloud.com", "b@icloud.com"])
    aliases = service.list_aliases(account["id"])

    result = service.import_aliases_to_mailapi_pool(
        [alias["id"] for alias in aliases], origin="https://reg.example.com"
    )

    assert (result["imported"], result["failed"], result["skipped"]) == (2, 0, 0)
    assert result["errors"] == []
    from core.db import OutlookAccountModel

    with Session(service.engine) as session:
        rows = session.exec(select(OutlookAccountModel)).all()
    assert {row.account_type for row in rows} == {"mailapi_url"}
    assert {row.email for row in rows} == {"a@icloud.com", "b@icloud.com"}
    # 免登录链接必须落在池子里，注册任务就是靠轮询它取码的
    assert {row.mailapi_url for row in rows} == {
        f"https://reg.example.com/m/{alias['share_token']}" for alias in aliases
    }
    # 视图跟着切到 MailAPI URL，否则这些号取不出来
    assert service.config_store.get(service.MAIL_IMPORT_SOURCE_KEY) == "mailapi"


def test_import_aliases_requires_a_public_base_url(service, stub_web_client):
    """地址拼不出来时宁可一条不导，也不能把半截链接写进号池。"""
    account = _import_aliases(service, stub_web_client, ["a@icloud.com"])
    aliases = service.list_aliases(account["id"])

    result = service.import_aliases_to_mailapi_pool([alias["id"] for alias in aliases])

    assert result["imported"] == 0
    assert "无法确定面板访问地址" in result["errors"][0]

    from core.db import OutlookAccountModel

    with Session(service.engine) as session:
        assert session.exec(select(OutlookAccountModel)).all() == []


def test_import_aliases_does_not_duplicate_what_is_already_in_the_pool(service, stub_web_client):
    account = _import_aliases(service, stub_web_client, ["a@icloud.com"])
    alias = service.list_aliases(account["id"])[0]

    first = service.import_aliases_to_mailapi_pool([alias["id"]], origin="https://reg.example.com")
    second = service.import_aliases_to_mailapi_pool([alias["id"]], origin="https://reg.example.com")

    assert first["imported"] == 1
    assert second["imported"] == 0
    assert any("早就在号池里" in message for message in second["errors"])

    from core.db import OutlookAccountModel

    with Session(service.engine) as session:
        assert len(session.exec(select(OutlookAccountModel)).all()) == 1


def test_import_aliases_reports_when_nothing_matches(service, stub_web_client):
    _import_aliases(service, stub_web_client, ["a@icloud.com"])

    result = service.import_aliases_to_mailapi_pool([999], origin="https://reg.example.com")

    assert result["total"] == 0
    assert "都不存在" in result["errors"][0]


def test_import_aliases_never_takes_another_accounts_aliases(service, stub_web_client):
    """带主号筛选时，勾选的行不能越界把别的账号的别名也导进去。"""
    stub_web_client["client"] = _StubWebClient(imported=_imported("owner@icloud.com"))
    owner = service.import_session(SessionImportRequest(cookie_header="a=1"))
    stub_web_client["client"] = _StubWebClient(imported=_imported("second@icloud.com"))
    second = service.import_session(SessionImportRequest(cookie_header="a=1"))

    stub_web_client["client"] = _StubWebClient(
        private_emails=[PrivateEmail(address="a@icloud.com", provider_id="1")]
    )
    service.sync_aliases(owner["id"])
    stub_web_client["client"] = _StubWebClient(
        private_emails=[PrivateEmail(address="b@icloud.com", provider_id="2")]
    )
    service.sync_aliases(second["id"])

    foreign_id = service.list_aliases(second["id"])[0]["id"]
    result = service.import_aliases_to_mailapi_pool(
        [foreign_id], account_id=owner["id"], origin="https://reg.example.com"
    )

    assert result["imported"] == 0
    assert "都不存在" in result["errors"][0]

    from core.db import OutlookAccountModel

    with Session(service.engine) as session:
        assert session.exec(select(OutlookAccountModel)).all() == []


def test_import_aliases_falls_back_to_the_configured_panel_url(service, stub_web_client):
    """地址栏没给（或给了个拼不起来的串）时，用设置里的「面板访问地址」。"""
    account = _import_aliases(service, stub_web_client, ["a@icloud.com"])
    alias = service.list_aliases(account["id"])[0]
    service.config_store.set("public_base_url", "https://panel.example.com/")

    result = service.import_aliases_to_mailapi_pool([alias["id"]], origin="not-a-url")

    from core.db import OutlookAccountModel

    with Session(service.engine) as session:
        row = session.exec(select(OutlookAccountModel)).one()
    assert result["imported"] == 1
    assert row.mailapi_url == f"https://panel.example.com/m/{alias['share_token']}"
