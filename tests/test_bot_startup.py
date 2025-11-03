from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import MagicMock, patch

import bot


class DummyReport:
    def __init__(self, deleted_count: int, mode: str = "quick") -> None:
        self.deleted_count = deleted_count
        self.mode = mode

    def summary(self) -> str:
        return "summary"


class BotStartupAlertThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        importlib.reload(bot)

    def test_default_threshold_is_ten(self) -> None:
        report = DummyReport(deleted_count=9)
        mock_app = MagicMock()

        with patch("bot.require_bot_credentials"), patch(
            "bot.run_cleanup", return_value=report
        ), patch("bot.build_application", return_value=mock_app), patch(
            "bot.log_system_info"
        ), patch(
            "bot.trigger_admin_alert"
        ) as mock_alert:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TOKEN_CLEANUP_ALERT_THRESHOLD", None)
                bot.main()

        mock_alert.assert_not_called()
        mock_app.run_polling.assert_called_once()

    def test_threshold_can_be_overridden_by_environment(self) -> None:
        report = DummyReport(deleted_count=3)
        mock_app = MagicMock()

        with patch("bot.require_bot_credentials"), patch(
            "bot.run_cleanup", return_value=report
        ), patch("bot.build_application", return_value=mock_app), patch(
            "bot.log_system_info"
        ), patch(
            "bot.trigger_admin_alert"
        ) as mock_alert:
            with patch.dict(os.environ, {"TOKEN_CLEANUP_ALERT_THRESHOLD": "3"}, clear=False):
                bot.main()

        mock_alert.assert_called_once()
        mock_app.run_polling.assert_called_once()


if __name__ == "__main__":
    unittest.main()
