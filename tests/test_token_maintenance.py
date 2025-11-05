from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from security.token_store import TokenLoadResult, TokenState


class _DummyCredentials:
    def __init__(self, expiry: datetime) -> None:
        self.token_expiry = expiry
        self.invalid = False


class _DummyGAuth:
    def __init__(self, expiry: datetime, expired: bool = False) -> None:
        self.credentials = _DummyCredentials(expiry)
        self.access_token_expired = expired


class _DummyStore:
    def __init__(
        self,
        base_dir: Path,
        prepare_map: dict[int, TokenLoadResult],
        refresh_map: dict[int, TokenLoadResult],
        *,
        lock_failures: set[int] | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._prepare_map = prepare_map
        self._refresh_map = refresh_map
        self._lock_failures = lock_failures or set()
        self._cache = {user_id: object() for user_id in prepare_map}
        self.prepare_calls: list[int] = []
        self.refresh_calls: list[int] = []
        self.quarantine_calls: list[tuple[int, str]] = []

    def prepare_gauth(self, user_id: int) -> TokenLoadResult:
        self.prepare_calls.append(user_id)
        return self._prepare_map[user_id]

    def refresh(self, user_id: int, gauth) -> TokenLoadResult:
        self.refresh_calls.append(user_id)
        return self._refresh_map[user_id]

    def quarantine(self, user_id: int, reason: str):
        path = self._base_dir / "quarantine" / f"token_{user_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.quarantine_calls.append((user_id, reason))
        return path

    def get_token_path(self, user_id: int) -> Path:
        return self._base_dir / f"token_{user_id}.json"

    @contextlib.contextmanager
    def _file_lock(self, path: Path, timeout: float = 5.0):
        for user_id in self._lock_failures:
            if f"token_{user_id}" in path.name:
                raise TimeoutError("locked")
        yield


class TokenMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tempdir.name)
        for user_id in range(1, 6):
            (self.base_dir / f"token_{user_id}.json").write_text("{}", encoding="utf-8")

        self.log_activity = MagicMock()
        self.log_system_info = MagicMock()
        self.alert = MagicMock()

        patches = [
            patch("security.maintenance.log_activity", self.log_activity),
            patch("security.maintenance.log_system_info", self.log_system_info),
            patch("security.maintenance.trigger_admin_alert", self.alert),
            patch("security.maintenance.get_google_token_base_dir", return_value=self.base_dir),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_health_check_reports_and_quarantines(self) -> None:
        now = datetime.now(timezone.utc)

        gauth_refresh = _DummyGAuth(now + timedelta(minutes=5))
        gauth_expired = _DummyGAuth(now - timedelta(minutes=1), expired=True)
        gauth_ok = _DummyGAuth(now + timedelta(days=2))

        prepare_map = {
            1: TokenLoadResult(
                user_id=1,
                path=self.base_dir / "token_1.json",
                state=TokenState.VALID,
                gauth=gauth_refresh,
            ),
            2: TokenLoadResult(
                user_id=2,
                path=self.base_dir / "token_2.json",
                state=TokenState.CORRUPTED,
                gauth=None,
                error="invalid",
            ),
            3: TokenLoadResult(
                user_id=3,
                path=self.base_dir / "token_3.json",
                state=TokenState.EXPIRED,
                gauth=gauth_expired,
            ),
            4: TokenLoadResult(
                user_id=4,
                path=self.base_dir / "token_4.json",
                state=TokenState.VALID,
                gauth=gauth_ok,
            ),
            5: TokenLoadResult(
                user_id=5,
                path=self.base_dir / "token_5.json",
                state=TokenState.VALID,
                gauth=_DummyGAuth(now + timedelta(days=1)),
            ),
        }

        refresh_map = {
            1: TokenLoadResult(
                user_id=1,
                path=self.base_dir / "token_1.json",
                state=TokenState.VALID,
                gauth=gauth_refresh,
                refreshed=True,
            ),
            3: TokenLoadResult(
                user_id=3,
                path=self.base_dir / "token_3.json",
                state=TokenState.REFRESH_FAILED,
                gauth=None,
                error="refresh_failed",
            ),
        }

        store = _DummyStore(
            self.base_dir,
            prepare_map,
            refresh_map,
            lock_failures={5},
        )

        context = SimpleNamespace(
            job=SimpleNamespace(
                data={
                    "batch_size": 5,
                    "refresh_ahead": timedelta(minutes=30),
                    "cursor": 0,
                }
            )
        )

        from security import maintenance

        with patch("security.maintenance.token_store", return_value=store):
            maintenance.run_token_health_check(context)

        # Ensure cursor persists so polling loop is unaffected
        self.assertEqual(context.job.data["cursor"], 0)

        # Refresh attempted for users 1 and 3 only
        self.assertCountEqual(store.refresh_calls, [1, 3])
        self.assertTrue(any(call[0] == 2 for call in store.quarantine_calls))
        self.assertTrue(any(call[0] == 3 for call in store.quarantine_calls))

        self.log_system_info.assert_called_once()
        system_args, system_kwargs = self.log_system_info.call_args
        self.assertIn("processed=4", system_args[0])
        metadata = system_kwargs.get("metadata")
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata["processed"], 4)
        self.assertEqual(metadata["refresh_failures"], 1)
        self.assertEqual(metadata["quarantined"], 2)
        self.assertEqual(metadata["skipped"], 1)

        self.log_activity.assert_called_once()
        _, activity_kwargs = self.log_activity.call_args
        maintenance_metrics = activity_kwargs["maintenance_metrics"]
        self.assertEqual(maintenance_metrics["refresh_success"], 1)
        self.assertEqual(maintenance_metrics["refresh_attempts"], 2)

        self.alert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
