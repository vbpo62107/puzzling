from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


class TokenCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.token_dir = Path(self.tmpdir.name)

        os.environ["GOOGLE_TOKEN_DIR"] = self.tmpdir.name
        os.environ["TELEGRAM_BOT_TOKEN"] = "dummy-token"
        os.environ["GOOGLE_CLIENT_ID"] = "dummy-client"
        os.environ["GOOGLE_CLIENT_SECRET"] = "dummy-secret"

        self.addCleanup(lambda: os.environ.pop("GOOGLE_TOKEN_DIR", None))
        self.addCleanup(lambda: os.environ.pop("TELEGRAM_BOT_TOKEN", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_ID", None))
        self.addCleanup(lambda: os.environ.pop("GOOGLE_CLIENT_SECRET", None))

        for module_name in ("token_cleanup", "creds"):
            if module_name in sys.modules:
                del sys.modules[module_name]

        import importlib

        self.token_cleanup = importlib.import_module("token_cleanup")

    def _create_token(self, name: str, payload: dict | None = None) -> Path:
        path = self.token_dir / name
        if payload is None:
            path.write_text("", encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_quick_scan_removes_empty_and_invalid_tokens(self) -> None:
        healthy = self._create_token("token_1.json", {"token_expiry": "2999-01-01T00:00:00Z"})
        empty = self._create_token("token_2.json")
        broken = (self.token_dir / "token_3.json")
        broken.write_text("not-json", encoding="utf-8")

        report = self.token_cleanup.scan_tokens(mode="quick")

        self.assertEqual(report.total_files, 3)
        self.assertEqual(report.deleted_count, 2)
        deleted_paths = {issue.path for issue in report.deleted_files}
        self.assertIn(empty, deleted_paths)
        self.assertIn(broken, deleted_paths)
        self.assertTrue(healthy.exists())
        for issue in report.deleted_files:
            self.assertIn(issue.reason, {"empty file", "invalid JSON"})

    def test_full_scan_checks_age_and_naming(self) -> None:
        os.environ["TOKEN_MAX_AGE_DAYS"] = "1"
        self.addCleanup(lambda: os.environ.pop("TOKEN_MAX_AGE_DAYS", None))

        healthy = self._create_token("token_healthy.json", {"token_expiry": "2999-01-01T00:00:00Z"})
        old = self._create_token("token_old.json", {"token_expiry": "2999-01-01T00:00:00Z"})
        weird = self._create_token("credentials.json", {"token_expiry": "2999-01-01T00:00:00Z"})
        expired = self._create_token("token_expired.json", {"token_expiry": "2000-01-01T00:00:00Z"})

        cutoff = time.time() - 86400 * 5
        os.utime(old, (cutoff, cutoff))

        report = self.token_cleanup.scan_tokens(mode="full")

        self.assertEqual(report.total_files, 4)
        self.assertEqual(report.deleted_count, 3)
        deleted = {issue.path: issue.reason for issue in report.deleted_files}
        self.assertIn(old, deleted)
        self.assertIn("file older than", deleted[old])
        self.assertIn(weird, deleted)
        self.assertIn("unexpected filename pattern", deleted[weird])
        self.assertIn(expired, deleted)
        self.assertIn("token expired", deleted[expired])
        self.assertTrue(healthy.exists())
        self.assertEqual(report.kept_files, 1)

    def test_full_scan_handles_non_dict_json(self) -> None:
        array_token = self._create_token("token_array.json")
        array_token.write_text("[1, 2, 3]", encoding="utf-8")

        string_token = self._create_token("token_string.json")
        string_token.write_text('"hello"', encoding="utf-8")

        report = self.token_cleanup.scan_tokens(mode="full")

        self.assertEqual(report.total_files, 2)
        self.assertEqual(report.deleted_count, 2)
        reasons = {issue.path: issue.reason for issue in report.deleted_files}
        self.assertIn(array_token, reasons)
        self.assertIn("unexpected JSON structure", reasons[array_token])
        self.assertIn(string_token, reasons)
        self.assertIn("unexpected JSON structure", reasons[string_token])


if __name__ == "__main__":
    unittest.main()
