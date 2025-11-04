from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class SecurityAuditLoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        os.environ["LOG_DIRECTORY"] = self.tmpdir.name
        self.addCleanup(lambda: os.environ.pop("LOG_DIRECTORY", None))

        for module_name in ["monitoring", "security.interceptor"]:
            if module_name in sys.modules:
                del sys.modules[module_name]

        self.monitoring = importlib.import_module("monitoring")
        self.monitoring.setup_logging()
        self.interceptor = importlib.import_module("security.interceptor")

        self.addCleanup(self._reset_cleanup_state)
        self.addCleanup(importlib.reload, self.monitoring)

    def _reset_cleanup_state(self) -> None:
        if hasattr(self.monitoring, "_LAST_CLEANUP"):
            self.monitoring._LAST_CLEANUP = None

    async def test_allow_flow_records_single_audit_entry(self) -> None:
        entries = []

        def capture(category, payload, *, timestamp=None):
            entries.append((category, payload, timestamp))

        allow_decision = self.interceptor.AccessDecision(
            True, self.interceptor.AccessDecision.ALLOW, via="token"
        )

        class AllowManager:
            policy_version = "policy-v1"
            whitelist_version = "wl-v5"

            @staticmethod
            def evaluate_access(user_id, level):
                return allow_decision

        async def handler(update, context, *args, **kwargs):
            return "ok"

        wrapped = self.interceptor.secure(
            "test_command",
            self.interceptor.SecurityLevel.AUTHORIZED,
            manager=AllowManager(),
        )(handler)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=99, type="private"),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch.object(self.monitoring, "_write_log_entry", side_effect=capture), patch(
            "security.interceptor.uuid.uuid4", return_value=SimpleNamespace(hex="corr-123")
        ), patch("security.interceptor.time.perf_counter", side_effect=[100.0, 100.05]):
            result = await wrapped(update, context)

        self.assertEqual(result, "ok")
        self.assertEqual(len(entries), 1)
        category, payload, timestamp = entries[0]
        self.assertEqual(category, "security_audit")
        self.assertIsNotNone(timestamp)
        self.assertIn("T", payload["ts"])
        self.assertEqual(payload["corr_id"], "corr-123")
        self.assertEqual(payload["decision"], "allow")
        self.assertEqual(payload["reason"], self.interceptor.AccessDecision.ALLOW)
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["chat_type"], "private")
        self.assertEqual(payload["command"], "test_command")
        self.assertAlmostEqual(payload["duration_ms"], 50.0)
        self.assertEqual(payload["policy_version"], "policy-v1")
        self.assertEqual(payload["whitelist_version"], "wl-v5")

    async def test_deny_flow_records_single_audit_entry(self) -> None:
        entries = []

        def capture(category, payload, *, timestamp=None):
            entries.append((category, payload, timestamp))

        deny_decision = self.interceptor.AccessDecision(
            False, self.interceptor.AccessDecision.DENY_UNAUTHORIZED_TOKEN_MISSING
        )

        class DenyManager:
            policy_version = "policy-v1"
            whitelist_version = "wl-v5"

            @staticmethod
            def evaluate_access(user_id, level):
                return deny_decision

        async def handler(update, context, *args, **kwargs):  # pragma: no cover
            return "should not run"

        wrapped = self.interceptor.secure(
            "test_command",
            self.interceptor.SecurityLevel.AUTHORIZED,
            manager=DenyManager(),
        )(handler)

        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            effective_chat=SimpleNamespace(id=55, type="group"),
            effective_message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch.object(self.monitoring, "_write_log_entry", side_effect=capture), patch(
            "security.interceptor.uuid.uuid4", return_value=SimpleNamespace(hex="corr-999")
        ), patch("security.interceptor.time.perf_counter", side_effect=[200.0, 200.02]):
            result = await wrapped(update, context)

        self.assertIsNone(result)
        message.reply_text.assert_awaited()
        self.assertEqual(len(entries), 1)
        category, payload, timestamp = entries[0]
        self.assertEqual(category, "security_audit")
        self.assertIsNotNone(timestamp)
        self.assertEqual(payload["corr_id"], "corr-999")
        self.assertEqual(payload["decision"], "deny")
        self.assertEqual(
            payload["reason"], self.interceptor.AccessDecision.DENY_UNAUTHORIZED_TOKEN_MISSING
        )
        self.assertEqual(payload["user_id"], 77)
        self.assertEqual(payload["chat_type"], "group")
        self.assertEqual(payload["command"], "test_command")
        self.assertAlmostEqual(payload["duration_ms"], 20.0)
        self.assertEqual(payload["policy_version"], "policy-v1")
        self.assertEqual(payload["whitelist_version"], "wl-v5")
        self.assertTrue(payload["ts"].startswith(str(date.today())))


if __name__ == "__main__":
    unittest.main()
