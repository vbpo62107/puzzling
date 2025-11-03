from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import List


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


class RequireRoleLoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.log_dir = Path(self.tmpdir.name)
        self.log_path = self.log_dir / f"{date.today().isoformat()}.jsonl"

        os.environ["LOG_DIRECTORY"] = self.tmpdir.name
        os.environ["USER_STORE_PATH"] = str(self.log_dir / "users.json")
        self.addCleanup(lambda: os.environ.pop("LOG_DIRECTORY", None))
        self.addCleanup(lambda: os.environ.pop("USER_STORE_PATH", None))

        for module_name in ["permissions", "monitoring"]:
            if module_name in sys.modules:
                del sys.modules[module_name]

        import importlib

        self.monitoring = importlib.import_module("monitoring")
        self.monitoring.setup_logging()
        self.permissions = importlib.import_module("permissions")

        self.addCleanup(logging.shutdown)

    async def test_require_role_logs_success(self) -> None:
        self.permissions.set_user_role(123, "admin")

        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(id=456),
            message=message,
        )
        context = SimpleNamespace(bot=DummyBot())

        calls: List[str] = []

        async def sample_handler(update, context, *args, **kwargs):
            calls.append("called")
            return "ok"

        wrapped = self.permissions.require_role("user")(sample_handler)

        result = await wrapped(update, context)

        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["called"])

        for handler in logging.getLogger("activity").handlers:
            handler.flush()

        self.assertTrue(self.log_path.exists())
        entries = [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        activity_entries = [entry for entry in entries if entry.get("category") == "activity"]
        self.assertTrue(activity_entries)
        last_entry = activity_entries[-1]
        self.assertEqual(last_entry["command"], "sample_handler")
        self.assertEqual(last_entry["verification"], "pass")
        self.assertEqual(last_entry["user"], {"id": 123, "role": "admin"})
        self.assertEqual(message.sent, [])

    async def test_require_role_logs_denial(self) -> None:
        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=789),
            effective_chat=SimpleNamespace(id=999),
            message=message,
        )
        context = SimpleNamespace(bot=DummyBot())

        calls: List[str] = []

        async def restricted_handler(update, context, *args, **kwargs):
            calls.append("called")
            return "ok"

        wrapped = self.permissions.require_role("admin")(restricted_handler)

        result = await wrapped(update, context)

        self.assertIsNone(result)
        self.assertEqual(calls, [])

        for handler in logging.getLogger("activity").handlers:
            handler.flush()

        self.assertTrue(self.log_path.exists())
        entries = [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        activity_entries = [entry for entry in entries if entry.get("category") == "activity"]
        self.assertTrue(activity_entries)
        last_entry = activity_entries[-1]
        self.assertEqual(last_entry["command"], "restricted_handler")
        self.assertEqual(last_entry["verification"], "deny")
        self.assertEqual(last_entry["user"], {"id": 789, "role": "user"})
        self.assertEqual(message.sent, ["❌ 权限不足，无法执行该操作。"])
        self.assertEqual(context.bot.sent_messages, [])


if __name__ == "__main__":
    unittest.main()
