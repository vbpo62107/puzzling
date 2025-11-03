import importlib
import sys
from types import SimpleNamespace

import pytest


class DummyBot:
    def __init__(self) -> None:
        self.sent_messages = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append((chat_id, text))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def security_env(monkeypatch, tmp_path):
    def _setup(whitelist: str = "", super_admin: str = ""):
        log_dir = tmp_path / "logs"
        token_dir = tmp_path / "tokens"
        log_dir.mkdir(parents=True, exist_ok=True)
        token_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("LOG_DIRECTORY", str(log_dir))
        monkeypatch.setenv("USER_STORE_PATH", str(tmp_path / "users.json"))
        monkeypatch.setenv("GOOGLE_TOKEN_DIR", str(token_dir))
        monkeypatch.setenv("BOT_WHITELIST_IDS", whitelist)
        monkeypatch.setenv("SUPER_ADMIN_IDS", super_admin)

        for module_name in ["permissions", "monitoring", "security.interceptor", "security"]:
            sys.modules.pop(module_name, None)

        monitoring = importlib.import_module("monitoring")
        monitoring.setup_logging()
        permissions = importlib.import_module("permissions")
        interceptor_module = importlib.import_module("security.interceptor")
        return permissions, interceptor_module

    return _setup


@pytest.mark.anyio("asyncio")
async def test_denies_when_user_not_whitelisted(security_env):
    _, interceptor_module = security_env(whitelist="123", super_admin="")
    interceptor = interceptor_module.SecurityInterceptor()

    bot = DummyBot()
    context = SimpleNamespace(bot=bot)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=456),
        effective_chat=SimpleNamespace(id=456),
    )

    handler_called = False

    async def handler(update, context):  # pragma: no cover - signature defined by Telegram
        nonlocal handler_called
        handler_called = True

    wrapped = interceptor.wrap("upload", handler)
    await wrapped(update, context)

    assert handler_called is False
    assert bot.sent_messages == [
        (456, "❌ 您尚未加入白名单，无法使用此功能。请联系管理员申请访问。")
    ]


@pytest.mark.anyio("asyncio")
async def test_allows_whitelisted_user_without_token_for_optional_command(security_env):
    _, interceptor_module = security_env(whitelist="456", super_admin="")
    interceptor = interceptor_module.SecurityInterceptor()

    bot = DummyBot()
    context = SimpleNamespace(bot=bot)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=456),
        effective_chat=SimpleNamespace(id=456),
    )

    handler_called = False

    async def handler(update, context):  # pragma: no cover - signature defined by Telegram
        nonlocal handler_called
        handler_called = True

    wrapped = interceptor.wrap("auth", handler)
    await wrapped(update, context)

    assert handler_called is True
    assert bot.sent_messages == []


def test_super_admin_ids_auto_whitelisted(security_env):
    permissions, _ = security_env(whitelist="", super_admin="789")
    manager = permissions.get_permission_manager()

    status = manager.check_authorization(789)
    assert status.whitelisted is True
