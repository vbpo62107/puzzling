from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class AuthLoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.log_dir = self.tmpdir.name
        os.environ["LOG_DIRECTORY"] = self.log_dir
        os.environ["USER_STORE_PATH"] = os.path.join(self.log_dir, "users.json")
        os.environ["GOOGLE_CLIENT_ID"] = "dummy-client"
        os.environ["GOOGLE_CLIENT_SECRET"] = "dummy-secret"
        os.environ["TELEGRAM_BOT_TOKEN"] = "dummy-token"
        self.addCleanup(lambda: os.environ.pop("LOG_DIRECTORY", None))
        self.addCleanup(lambda: os.environ.pop("USER_STORE_PATH", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_ID", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_SECRET", None))
        self.addCleanup(lambda: os.environ.pop("TELEGRAM_BOT_TOKEN", None))

        for module_name in [
            "permissions",
            "monitoring",
            "handlers.upload_handler",
            "handlers.file_handler",
        ]:
            if module_name in sys.modules:
                del sys.modules[module_name]

        import importlib

        self.monitoring = importlib.import_module("monitoring")
        self.monitoring.setup_logging()
        self.permissions = importlib.import_module("permissions")
        self.upload_handler = importlib.import_module("handlers.upload_handler")
        self.file_handler = importlib.import_module("handlers.file_handler")

        self.addCleanup(logging.shutdown)

    async def test_upload_handler_logs_missing_auth(self) -> None:
        user_id = 101
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(chat_id=user_id, text="/upload http://example.com"),
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch.object(
            self.upload_handler, "prepare_user_gauth", return_value=(None, True)
        ), patch.object(
            self.upload_handler, "log_activity"
        ) as mock_log_activity:
            await self.upload_handler.upload(update, context)

        expected_prompt = (
            f"❌ 用户 ID {user_id} 的授权凭证已失效并被清理，请发送 /auth 重新授权。"
        )
        context.bot.send_message.assert_awaited_once_with(
            chat_id=user_id, text=expected_prompt
        )
        mock_log_activity.assert_called_once_with(
            user_id,
            "user",
            "auth_missing",
            source="handlers.upload",
            verification="token_corrupt",
            metadata={"corrupt": True},
        )

    async def test_file_handler_logs_missing_auth(self) -> None:
        user_id = 202
        dummy_document = SimpleNamespace(file_id="abc", get_file=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(
                chat_id=user_id,
                document=dummy_document,
                photo=None,
            ),
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch.object(
            self.file_handler, "prepare_user_gauth", return_value=(None, False)
        ), patch.object(
            self.file_handler, "log_activity"
        ) as mock_log_activity:
            await self.file_handler.handle_file_message(update, context)

        expected_prompt = "❌ 未能加载您的授权凭证，请重新发送 /auth 完成授权。"
        context.bot.send_message.assert_awaited_once_with(
            chat_id=user_id, text=expected_prompt
        )
        mock_log_activity.assert_called_once_with(
            user_id,
            "user",
            "auth_missing",
            source="handlers.file",
            verification="token_invalid",
            metadata={"corrupt": False},
        )


if __name__ == "__main__":
    unittest.main()
