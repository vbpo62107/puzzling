from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from typing import List
from unittest.mock import patch


class DummyMessage:
    def __init__(self) -> None:
        self.sent: List[str] = []

    async def reply_text(self, text: str) -> None:
        self.sent.append(text)


class DummyBot:
    def __init__(self) -> None:
        self.sent_messages: List[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append((chat_id, text))


class StatusCommandPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.log_dir = self.tmpdir.name
        os.environ["LOG_DIRECTORY"] = self.log_dir
        os.environ["USER_STORE_PATH"] = os.path.join(self.log_dir, "users.json")
        os.environ["TELEGRAM_BOT_TOKEN"] = "dummy-token"
        os.environ["GOOGLE_CLIENT_ID"] = "dummy-client"
        os.environ["GOOGLE_CLIENT_SECRET"] = "dummy-secret"
        self.addCleanup(lambda: os.environ.pop("LOG_DIRECTORY", None))
        self.addCleanup(lambda: os.environ.pop("USER_STORE_PATH", None))
        self.addCleanup(lambda: os.environ.pop("TELEGRAM_BOT_TOKEN", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_ID", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_SECRET", None))

        for module_name in ["permissions", "monitoring", "handlers.status_handler"]:
            if module_name in sys.modules:
                del sys.modules[module_name]

        import importlib

        self.monitoring = importlib.import_module("monitoring")
        self.monitoring.setup_logging()
        self.permissions = importlib.import_module("permissions")
        self.status_handler = importlib.import_module("handlers.status_handler")

        self.addCleanup(logging.shutdown)

    async def test_status_denied_for_regular_user(self) -> None:
        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=321),
            effective_chat=SimpleNamespace(id=654),
            message=message,
        )
        context = SimpleNamespace(bot=DummyBot())

        await self.status_handler.status(update, context)

        self.assertEqual(message.sent, ["❌ 权限不足，无法执行该操作。"])

    async def test_status_allows_admin_and_shows_stats(self) -> None:
        self.permissions.set_user_role(987, "admin")

        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=987),
            effective_chat=SimpleNamespace(id=111),
            message=message,
        )
        context = SimpleNamespace(bot=DummyBot())

        with patch.object(
            self.status_handler, "get_user_status", return_value={"filename": "demo.zip", "stage": "上传中", "progress": 42}
        ), patch.object(
            self.status_handler,
            "get_today_stats",
            return_value={"date": "2024-01-02", "upload_count": 5, "total_size_mb": 128},
        ):
            await self.status_handler.status(update, context)

        self.assertEqual(len(message.sent), 1)
        payload = message.sent[0]
        self.assertIn("📊 当前上传任务状态：", payload)
        self.assertIn("• 文件：demo.zip", payload)
        self.assertIn("📊 今日运行统计：", payload)
        self.assertIn("• 上传次数：5", payload)

    async def test_my_status_returns_personal_progress(self) -> None:
        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=4321),
            effective_chat=SimpleNamespace(id=4321),
            message=message,
        )
        context = SimpleNamespace(bot=DummyBot())

        with patch.object(
            self.status_handler, "get_user_status", return_value={"filename": "personal.bin", "stage": "处理中", "progress": 75}
        ):
            await self.status_handler.my_status(update, context)

        self.assertEqual(len(message.sent), 1)
        payload = message.sent[0]
        self.assertIn("📊 当前上传任务状态：", payload)
        self.assertIn("• 文件：personal.bin", payload)
        self.assertIn("75%", payload)


if __name__ == "__main__":
    unittest.main()
