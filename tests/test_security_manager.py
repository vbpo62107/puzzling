from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from security.interceptor import DENIAL_MESSAGES, secure
from security.manager import AccessDecision, PermissionManager, SecurityLevel


class PermissionManagerAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.env_path = Path(self.tmpdir.name) / ".env"
        self.env_path.write_text("USER_WHITELIST=100\n", encoding="utf-8")

        os.environ["GOOGLE_TOKEN_DIR"] = self.tmpdir.name
        self.addCleanup(lambda: os.environ.pop("GOOGLE_TOKEN_DIR", None))

        self.manager = PermissionManager(env_path=self.env_path, cache_ttl_seconds=1)

    def test_whitelisted_non_admin_denied_admin_access(self) -> None:
        with patch("security.manager.has_permission", return_value=False):
            decision = self.manager.evaluate_access(100, SecurityLevel.ADMIN)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "admin_required")

    def test_non_whitelisted_admin_denied_even_with_token(self) -> None:
        user_id = 200
        token_path = Path(self.tmpdir.name) / f"token_{user_id}.json"
        token_path.write_text("{}", encoding="utf-8")
        self.manager.register_token(user_id)

        with patch("security.manager.has_permission", return_value=True):
            decision = self.manager.evaluate_access(user_id, SecurityLevel.ADMIN)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, AccessDecision.NOT_IN_WHITELIST)


class DummyMessage:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.sent.append(text)


class InterceptorWhitelistAdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.env_path = Path(self.tmpdir.name) / ".env"
        self.env_path.write_text("USER_WHITELIST=100\n", encoding="utf-8")

        os.environ["GOOGLE_TOKEN_DIR"] = self.tmpdir.name
        self.addCleanup(lambda: os.environ.pop("GOOGLE_TOKEN_DIR", None))

        self.manager = PermissionManager(env_path=self.env_path, cache_ttl_seconds=1)

    async def test_interceptor_denies_whitelisted_non_admin(self) -> None:
        user_id = 100

        @secure("admin_command", SecurityLevel.ADMIN, manager=self.manager)
        async def protected(update, context):  # pragma: no cover - exercise via decorator
            return "ok"

        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=555),
            effective_message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch("security.manager.has_permission", return_value=False), patch(
            "security.interceptor.get_user_role", return_value="user"
        ), patch("security.interceptor.log_activity") as mock_log:
            result = await protected(update, context)

        self.assertIsNone(result)
        self.assertEqual(message.sent, [DENIAL_MESSAGES["admin_required"]])
        mock_log.assert_called_once_with(
            user_id,
            "user",
            "admin_command",
            source="security.interceptor",
            verification="admin_required",
        )
        context.bot.send_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
