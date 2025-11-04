from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from security.interceptor import DENIAL_MESSAGES, secure
from security.manager import AccessDecision, PermissionManager, SecurityLevel


EXPECTED_DENIAL_MESSAGES = {
    AccessDecision.DENY_UNAUTHORIZED_MISSING_USER:
        "❌ I couldn't verify who requested this. Please try again in a private chat.",
    AccessDecision.DENY_UNAUTHORIZED_TOKEN_MISSING:
        "❌ Please authenticate with /auth before using this command.",
    AccessDecision.DENY_UNAUTHORIZED_ADMIN_REQUIRED:
        "❌ This 🔴 command is reserved for admins. In group chats, run it in a private chat with the bot.",
    AccessDecision.DENY_NOT_WHITELISTED:
        "❌ You're not on the admin whitelist yet. Please contact an administrator.",
    AccessDecision.RATE_LIMITED:
        "❌ You're sending requests too quickly. Please slow down and try again.",
    AccessDecision.POLICY_ERROR_UNSUPPORTED_LEVEL:
        "❌ This request isn't supported. Please contact an administrator.",
}


class PermissionManagerAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.env_path = Path(self.tmpdir.name) / ".env"
        self.env_path.write_text("USER_WHITELIST=100\n", encoding="utf-8")

        os.environ["GOOGLE_TOKEN_DIR"] = self.tmpdir.name
        self.addCleanup(lambda: os.environ.pop("GOOGLE_TOKEN_DIR", None))

        self.manager = PermissionManager(env_path=self.env_path, cache_ttl_seconds=1)

    def test_denial_messages_are_frozen(self) -> None:
        self.assertDictEqual(DENIAL_MESSAGES, EXPECTED_DENIAL_MESSAGES)

    def test_missing_user_denied(self) -> None:
        decision = self.manager.evaluate_access(None, SecurityLevel.AUTHORIZED)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, AccessDecision.DENY_UNAUTHORIZED_MISSING_USER)
        self.assertEqual(DENIAL_MESSAGES[decision.reason], EXPECTED_DENIAL_MESSAGES[decision.reason])

    def test_whitelisted_non_admin_denied_admin_access(self) -> None:
        with patch("security.manager.has_permission", return_value=False):
            decision = self.manager.evaluate_access(100, SecurityLevel.ADMIN)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, AccessDecision.DENY_UNAUTHORIZED_ADMIN_REQUIRED)
        self.assertEqual(DENIAL_MESSAGES[decision.reason], EXPECTED_DENIAL_MESSAGES[decision.reason])

    def test_non_whitelisted_admin_denied_even_with_token(self) -> None:
        user_id = 200
        token_path = self.manager._token_base_dir / f"token_{user_id}.json"  # type: ignore[attr-defined]
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text("{}", encoding="utf-8")
        self.manager.register_token(user_id)

        with patch("security.manager.has_permission", return_value=True):
            decision = self.manager.evaluate_access(user_id, SecurityLevel.ADMIN)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, AccessDecision.DENY_NOT_WHITELISTED)
        self.assertEqual(DENIAL_MESSAGES[decision.reason], EXPECTED_DENIAL_MESSAGES[decision.reason])

    def test_token_missing_denied(self) -> None:
        user_id = 300

        decision = self.manager.evaluate_access(user_id, SecurityLevel.AUTHORIZED)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, AccessDecision.DENY_UNAUTHORIZED_TOKEN_MISSING)
        self.assertEqual(DENIAL_MESSAGES[decision.reason], EXPECTED_DENIAL_MESSAGES[decision.reason])

    def test_policy_error_for_unknown_level(self) -> None:
        user_id = 400
        unknown_level = SimpleNamespace(value="custom")

        with patch.object(PermissionManager, "_has_token_cached", return_value=True):
            decision = self.manager.evaluate_access(user_id, unknown_level)  # type: ignore[arg-type]

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, AccessDecision.POLICY_ERROR_UNSUPPORTED_LEVEL)
        self.assertEqual(DENIAL_MESSAGES[decision.reason], EXPECTED_DENIAL_MESSAGES[decision.reason])


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
        self.assertEqual(
            message.sent,
            [DENIAL_MESSAGES[AccessDecision.DENY_UNAUTHORIZED_ADMIN_REQUIRED]],
        )
        mock_log.assert_called_once_with(
            user_id,
            "user",
            "admin_command",
            source="security.interceptor",
            verification=AccessDecision.DENY_UNAUTHORIZED_ADMIN_REQUIRED,
        )
        context.bot.send_message.assert_not_called()


class RateLimiterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.env_path = Path(self.tmpdir.name) / ".env"
        # Provide broad whitelist coverage for tests exercising ADMIN behaviour.
        self.env_path.write_text("USER_WHITELIST=500,600,700\n", encoding="utf-8")

        os.environ["GOOGLE_TOKEN_DIR"] = self.tmpdir.name
        self.addCleanup(lambda: os.environ.pop("GOOGLE_TOKEN_DIR", None))

        self.rate_limits = {
            "auth": {
                "name": "auth:user",
                "limit": 1,
                "interval": 60,
                "cooldown_seconds": 10,
                "scope": "user",
                "levels": ["public"],
            },
            "upload": {
                "name": "upload:user",
                "limit": 1,
                "interval": 60,
                "cooldown_seconds": 10,
                "scope": "user",
                "levels": ["authorized"],
            },
            "transfer": {
                "name": "transfer:user",
                "limit": 1,
                "interval": 60,
                "cooldown_seconds": 10,
                "scope": "user",
                "levels": ["admin"],
            },
        }

    def _make_manager(self) -> PermissionManager:
        return PermissionManager(
            env_path=self.env_path,
            cache_ttl_seconds=1,
            rate_limits=self.rate_limits,
        )

    async def test_auth_rate_limit_enforced(self) -> None:
        user_id = 500
        manager = self._make_manager()

        calls: list[str] = []

        @secure("auth", SecurityLevel.PUBLIC, manager=manager)
        async def auth_handler(update, context):  # pragma: no cover - exercised in test
            calls.append("auth")
            return "ok"

        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=555),
            effective_message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch("security.interceptor.get_user_role", return_value="user"), patch(
            "security.interceptor.log_activity"
        ) as mock_log, patch(
            "security.interceptor.record_rate_limit_hit"
        ) as mock_metric:
            await auth_handler(update, context)
            await auth_handler(update, context)

        self.assertEqual(calls, ["auth"])
        self.assertEqual(
            message.sent,
            [DENIAL_MESSAGES[AccessDecision.RATE_LIMITED]],
        )
        mock_metric.assert_called_once()
        metric_args, _ = mock_metric.call_args
        self.assertEqual(metric_args[:2], ("auth", "auth:user"))
        self.assertEqual(metric_args[5]["command"], "auth")
        log_kwargs = mock_log.call_args.kwargs
        self.assertEqual(log_kwargs["verification"], AccessDecision.RATE_LIMITED)
        self.assertIn("metadata", log_kwargs)
        self.assertEqual(log_kwargs["metadata"].get("limit"), "auth:user")

    async def test_upload_rate_limit_deduplicates_notifications(self) -> None:
        user_id = 600
        manager = self._make_manager()

        calls: list[str] = []

        @secure("upload", SecurityLevel.AUTHORIZED, manager=manager)
        async def upload_handler(update, context):  # pragma: no cover - exercised in test
            calls.append("upload")
            return "ok"

        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=777),
            effective_message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch.object(manager, "_has_token_cached", return_value=True), patch(
            "security.interceptor.get_user_role", return_value="user"
        ), patch("security.interceptor.log_activity") as mock_log, patch(
            "security.interceptor.record_rate_limit_hit"
        ) as mock_metric:
            await upload_handler(update, context)
            await upload_handler(update, context)
            await upload_handler(update, context)

        self.assertEqual(calls, ["upload"])
        self.assertEqual(
            message.sent,
            [DENIAL_MESSAGES[AccessDecision.RATE_LIMITED]],
        )
        self.assertEqual(mock_metric.call_count, 2)
        self.assertEqual(mock_log.call_count, 2)

    async def test_transfer_rate_limit_enforced_for_admin(self) -> None:
        user_id = 700
        manager = self._make_manager()

        calls: list[str] = []

        @secure("transfer", SecurityLevel.ADMIN, manager=manager)
        async def transfer_handler(update, context):  # pragma: no cover
            calls.append("transfer")
            return "ok"

        message = DummyMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=999),
            effective_message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch("security.manager.has_permission", return_value=True), patch(
            "security.interceptor.get_user_role", return_value="admin"
        ), patch("security.interceptor.log_activity") as mock_log, patch(
            "security.interceptor.record_rate_limit_hit"
        ) as mock_metric:
            await transfer_handler(update, context)
            await transfer_handler(update, context)

        self.assertEqual(calls, ["transfer"])
        self.assertEqual(
            message.sent,
            [DENIAL_MESSAGES[AccessDecision.RATE_LIMITED]],
        )
        mock_metric.assert_called()
        self.assertEqual(mock_log.call_args.kwargs["verification"], AccessDecision.RATE_LIMITED)

if __name__ == "__main__":
    unittest.main()
