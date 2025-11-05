from __future__ import annotations

import io
import logging
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List, Tuple
from unittest.mock import patch


class DummyMessage:
    def __init__(self) -> None:
        self.sent: List[str] = []

    async def reply_text(self, text: str) -> None:
        self.sent.append(text)


class DummyBot:
    def __init__(self) -> None:
        self.sent_messages: List[Tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_: object) -> None:
        self.sent_messages.append((chat_id, text))


class CleanupCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        os.environ["LOG_DIRECTORY"] = self.tmpdir.name
        os.environ["USER_STORE_PATH"] = os.path.join(self.tmpdir.name, "users.json")
        os.environ["SUPER_ADMIN_IDS"] = "999"
        os.environ["TELEGRAM_BOT_TOKEN"] = "dummy-token"
        os.environ["GOOGLE_CLIENT_ID"] = "dummy-client"
        os.environ["GOOGLE_CLIENT_SECRET"] = "dummy-secret"
        os.environ["GOOGLE_TOKEN_DIR"] = self.tmpdir.name

        self.addCleanup(lambda: os.environ.pop("LOG_DIRECTORY", None))
        self.addCleanup(lambda: os.environ.pop("USER_STORE_PATH", None))
        self.addCleanup(lambda: os.environ.pop("SUPER_ADMIN_IDS", None))
        self.addCleanup(lambda: os.environ.pop("TELEGRAM_BOT_TOKEN", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_ID", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_SECRET", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_TOKEN_DIR", None))

        for module_name in [
            "monitoring",
            "permissions",
            "handlers.admin_handler",
            "puzzling.token_cleanup",
        ]:
            sys.modules.pop(module_name, None)

        import importlib

        self.monitoring = importlib.import_module("monitoring")
        self.monitoring.setup_logging()
        self.permissions = importlib.import_module("permissions")
        self.admin_handler = importlib.import_module("handlers.admin_handler")
        self.token_cleanup = importlib.import_module("puzzling.token_cleanup")

        self.permissions.set_user_role(111, "admin")
        self.permissions.set_user_role(555, "super_admin")

        self.addCleanup(logging.shutdown)

    async def test_cleanup_command_logs_and_notifies(self) -> None:
        message = DummyMessage()
        bot = DummyBot()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=111),
            effective_chat=SimpleNamespace(id=222),
            message=message,
        )
        context = SimpleNamespace(bot=bot)

        report = self.token_cleanup.TokenScanReport(
            base_dir=Path(self.tmpdir.name),
            mode="full",
            total_files=3,
            kept_files=1,
        )
        report.deleted_files.append(
            self.token_cleanup.TokenIssue(
                path=Path(self.tmpdir.name) / "token_old.json",
                reason="expired",
                deleted_at=datetime.now(timezone.utc),
            )
        )
        report.skipped_files.append(
            self.token_cleanup.TokenIssue(
                path=Path(self.tmpdir.name) / "token_locked.json",
                reason="lock unavailable",
            )
        )

        with patch("handlers.admin_handler.run_cleanup", return_value=report) as mock_run, patch(
            "handlers.admin_handler.logging.info"
        ) as mock_log_info:
            await self.admin_handler.cleanup_tokens(update, context)

        mock_run.assert_called_once_with(full=True)
        self.assertTrue(mock_log_info.called)
        self.assertTrue(message.sent)
        self.assertIn("Token cleanup 已完成", message.sent[0])
        deleted_identifier = self.token_cleanup.mask_token_identifier(
            Path(self.tmpdir.name) / "token_old.json"
        )
        skipped_identifier = self.token_cleanup.mask_token_identifier(
            Path(self.tmpdir.name) / "token_locked.json"
        )
        combined_message = "\n".join(message.sent)
        self.assertIn(deleted_identifier, combined_message)
        self.assertIn(skipped_identifier, combined_message)

        sent_ids = {chat_id for chat_id, _ in bot.sent_messages}
        self.assertIn(999, sent_ids)
        self.assertIn(555, sent_ids)


class CleanupScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        os.environ["LOG_DIRECTORY"] = self.tmpdir.name
        os.environ["USER_STORE_PATH"] = os.path.join(self.tmpdir.name, "users.json")
        os.environ["TELEGRAM_BOT_TOKEN"] = "dummy-token"
        os.environ["GOOGLE_CLIENT_ID"] = "dummy-client"
        os.environ["GOOGLE_CLIENT_SECRET"] = "dummy-secret"
        os.environ["GOOGLE_TOKEN_DIR"] = self.tmpdir.name

        self.addCleanup(lambda: os.environ.pop("LOG_DIRECTORY", None))
        self.addCleanup(lambda: os.environ.pop("USER_STORE_PATH", None))
        self.addCleanup(lambda: os.environ.pop("TELEGRAM_BOT_TOKEN", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_ID", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_SECRET", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_TOKEN_DIR", None))

        for module_name in [
            "monitoring",
            "permissions",
            "cleanup_tokens",
            "puzzling.token_cleanup",
        ]:
            sys.modules.pop(module_name, None)

        import importlib

        self.monitoring = importlib.import_module("monitoring")
        self.monitoring.setup_logging()
        self.token_cleanup = importlib.import_module("puzzling.token_cleanup")
        self.cleanup_script = importlib.import_module("cleanup_tokens")

        self.addCleanup(logging.shutdown)

    def test_cli_emits_report_and_logs(self) -> None:
        report = self.token_cleanup.TokenScanReport(
            base_dir=Path(self.tmpdir.name),
            mode="quick",
            total_files=2,
            kept_files=2,
        )
        report.deleted_files.append(
            self.token_cleanup.TokenIssue(
                path=Path(self.tmpdir.name) / "token_invalid.json",
                reason="invalid JSON",
                deleted_at=datetime.now(timezone.utc),
            )
        )
        report.skipped_files.append(
            self.token_cleanup.TokenIssue(
                path=Path(self.tmpdir.name) / "token_locked.json",
                reason="lock unavailable",
            )
        )

        with patch("cleanup_tokens.run_cleanup", return_value=report) as mock_run, patch(
            "cleanup_tokens.logging.info"
        ) as mock_log_info:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = self.cleanup_script.main(["--full"])

        mock_run.assert_called_once_with(full=True)
        self.assertEqual(exit_code, 0)
        output = buffer.getvalue()
        self.assertIn("Token cleanup", output)
        deleted_identifier = self.token_cleanup.mask_token_identifier(
            Path(self.tmpdir.name) / "token_invalid.json"
        )
        skipped_identifier = self.token_cleanup.mask_token_identifier(
            Path(self.tmpdir.name) / "token_locked.json"
        )
        self.assertIn(deleted_identifier, output)
        self.assertIn(skipped_identifier, output)
        self.assertTrue(mock_log_info.called)


class CleanupScriptMinimalEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self._saved_env = {}
        for name in [
            "TELEGRAM_BOT_TOKEN",
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_TOKEN_DIR",
        ]:
            self._saved_env[name] = os.environ.pop(name, None)

        os.environ["GOOGLE_TOKEN_DIR"] = self.tmpdir.name

        def restore_env() -> None:
            for key, value in self._saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore_env)
        self.addCleanup(lambda: shutil.rmtree("logs", ignore_errors=True))

        for module_name in ["creds", "puzzling.token_cleanup", "cleanup_tokens"]:
            sys.modules.pop(module_name, None)

    def test_cleanup_main_succeeds_with_minimal_env(self) -> None:
        import importlib

        cleanup_script = importlib.import_module("cleanup_tokens")
        exit_code = cleanup_script.main([])

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
