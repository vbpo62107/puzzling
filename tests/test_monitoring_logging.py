from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


class StructuredLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        os.environ["LOG_DIRECTORY"] = self.tmpdir.name
        os.environ["LOG_RETENTION_DAYS"] = "2"
        self.addCleanup(lambda: os.environ.pop("LOG_DIRECTORY", None))
        self.addCleanup(lambda: os.environ.pop("LOG_RETENTION_DAYS", None))

        if "monitoring" in sys.modules:
            del sys.modules["monitoring"]

        import importlib

        self.monitoring = importlib.import_module("monitoring")
        self.monitoring.setup_logging()
        self.addCleanup(logging_shutdown_safe)

    def tearDown(self) -> None:
        # Ensure the module-level cleanup flag resets between tests
        if hasattr(self.monitoring, "_LAST_CLEANUP"):
            self.monitoring._LAST_CLEANUP = None

    def test_activity_log_emits_structured_entry(self) -> None:
        self.monitoring.log_activity(
            42,
            "user",
            "test_command",
            source="tests",
            verification="pass",
            duration_ms=12.5,
            metadata={"foo": "bar"},
        )

        log_path = Path(self.tmpdir.name) / f"{date.today().isoformat()}.jsonl"
        self.assertTrue(log_path.exists())
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])

        self.assertEqual(entry["category"], "activity")
        self.assertEqual(entry["user"], {"id": 42, "role": "user"})
        self.assertEqual(entry["command"], "test_command")
        self.assertEqual(entry["source"], "tests")
        self.assertEqual(entry["verification"], "pass")
        self.assertEqual(entry["duration_ms"], 12.5)
        self.assertEqual(entry["metadata"], {"foo": "bar"})
        self.assertIn("timestamp", entry)

        tailed = self.monitoring.tail_logs("activity", lines=10)
        parsed_tail = json.loads(tailed.splitlines()[-1])
        self.assertEqual(parsed_tail["command"], "test_command")

    def test_cleanup_removes_expired_files(self) -> None:
        old_date = date.today() - timedelta(days=5)
        old_path = Path(self.tmpdir.name) / f"{old_date.isoformat()}.jsonl"
        old_path.write_text("{\n}", encoding="utf-8")

        # reset cleanup flag so the next write triggers removal
        self.monitoring._LAST_CLEANUP = None
        self.monitoring.log_system_info("hello")

        self.assertFalse(old_path.exists())
        today_path = Path(self.tmpdir.name) / f"{date.today().isoformat()}.jsonl"
        entries = [json.loads(line) for line in today_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(entry["category"] == "system" for entry in entries))

    def test_system_error_logs_exception_details(self) -> None:
        error = ValueError("boom")

        self.monitoring.log_system_error("failed", exc=error)

        log_path = Path(self.tmpdir.name) / f"{date.today().isoformat()}.jsonl"
        contents = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(contents), 1)

        entry = json.loads(contents[-1])
        self.assertEqual(entry["category"], "system")
        self.assertEqual(entry["level"], "ERROR")
        self.assertEqual(entry["message"], "failed")
        self.assertEqual(entry["exception"]["type"], "ValueError")
        self.assertEqual(entry["exception"]["message"], "boom")


def logging_shutdown_safe() -> None:
    import logging

    logging.shutdown()


if __name__ == "__main__":
    unittest.main()
