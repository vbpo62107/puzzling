from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from telegram.ext import CallbackContext

from creds import get_google_token_base_dir
from monitoring import log_activity, log_system_info, trigger_admin_alert
from security.token_store import (
    TokenLoadResult,
    TokenState,
    token_store,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 10
DEFAULT_REFRESH_AHEAD = timedelta(hours=1)


@dataclass
class _MaintenanceMetrics:
    processed: int = 0
    refresh_attempts: int = 0
    refreshed: int = 0
    refresh_failures: int = 0
    quarantined: int = 0
    skipped: int = 0

    @property
    def success_rate(self) -> float:
        if not self.refresh_attempts:
            return 1.0
        return self.refreshed / self.refresh_attempts


def _collect_token_ids() -> List[int]:
    store = token_store()
    user_ids = set(store._cache.keys())  # type: ignore[attr-defined]

    base_dir = get_google_token_base_dir()
    if base_dir.exists():
        for path in base_dir.glob("token_*.json"):
            suffix = path.stem.split("token_")[-1]
            if suffix.isdigit():
                user_ids.add(int(suffix))

    return sorted(user_ids)


def _select_batch(
    user_ids: Sequence[int],
    cursor: int,
    batch_size: int,
) -> Tuple[List[int], int]:
    if not user_ids:
        return [], cursor

    total = len(user_ids)
    start = cursor % total if total else 0
    count = min(batch_size, total)

    selected: List[int] = []
    index = start
    for _ in range(count):
        selected.append(user_ids[index])
        index = (index + 1) % total

    return selected, index


def _normalize_expiry(expiry: Optional[datetime]) -> Optional[datetime]:
    if expiry is None:
        return None
    if expiry.tzinfo is None:
        return expiry.replace(tzinfo=timezone.utc)
    return expiry.astimezone(timezone.utc)


def _should_refresh(result: TokenLoadResult, refresh_ahead: timedelta) -> bool:
    gauth = result.gauth
    if gauth is None:
        return False

    if getattr(gauth, "access_token_expired", False):
        return True

    credentials = getattr(gauth, "credentials", None)
    expiry = _normalize_expiry(getattr(credentials, "token_expiry", None))
    if expiry is None:
        return False

    now = datetime.now(timezone.utc)
    return expiry - now <= refresh_ahead


def _handle_result(
    user_id: int,
    result: TokenLoadResult,
    refresh_ahead: timedelta,
    metrics: _MaintenanceMetrics,
) -> TokenLoadResult:
    store = token_store()

    if result.state is TokenState.ABSENT:
        return result

    refresh_needed = result.state is TokenState.EXPIRED or _should_refresh(result, refresh_ahead)
    if refresh_needed and result.gauth is not None:
        metrics.refresh_attempts += 1
        refreshed = store.refresh(user_id, result.gauth)
        if refreshed.quarantined_to:
            metrics.quarantined += 1
        if refreshed.state is TokenState.VALID and refreshed.refreshed:
            metrics.refreshed += 1
        elif refreshed.state is TokenState.REFRESH_FAILED:
            metrics.refresh_failures += 1
        result = refreshed
    elif refresh_needed:
        metrics.refresh_failures += 1

    if result.state in {TokenState.CORRUPTED, TokenState.REFRESH_FAILED}:
        if result.quarantined_to is None:
            quarantined = store.quarantine(user_id, result.error or result.state.value)
            if quarantined:
                metrics.quarantined += 1
        else:
            metrics.quarantined += 1
    elif result.quarantined_to:
        metrics.quarantined += 1

    return result


def _process_user(
    user_id: int,
    refresh_ahead: timedelta,
    metrics: _MaintenanceMetrics,
) -> Optional[TokenLoadResult]:
    store = token_store()
    token_path = store.get_token_path(user_id)
    lock_path = token_path.with_suffix(token_path.suffix + ".maint.lock")

    try:
        with store._file_lock(lock_path, timeout=5.0):  # type: ignore[attr-defined]
            result = store.prepare_gauth(user_id)
            metrics.processed += 1
            final_result = _handle_result(user_id, result, refresh_ahead, metrics)
            return final_result
    except TimeoutError:
        metrics.skipped += 1
        logger.debug("Skipped maintenance for user %s due to active lock", user_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        metrics.refresh_failures += 1
        logger.exception("Token maintenance failed for user %s: %s", user_id, exc)
    return None


def _serialize_metrics(
    metrics: _MaintenanceMetrics,
    total_users: int,
    details: Iterable[TokenLoadResult],
) -> Dict[str, object]:
    return {
        "processed": metrics.processed,
        "total_users": total_users,
        "refresh_attempts": metrics.refresh_attempts,
        "refresh_success": metrics.refreshed,
        "refresh_failures": metrics.refresh_failures,
        "quarantined": metrics.quarantined,
        "skipped": metrics.skipped,
        "success_rate": round(metrics.success_rate, 4),
        "details": [result.as_metadata() for result in details if result is not None],
    }


def run_token_health_check(context: CallbackContext) -> None:
    job = getattr(context, "job", None)
    job_data: Dict[str, object] = getattr(job, "data", {}) or {}

    batch_size = int(job_data.get("batch_size", DEFAULT_BATCH_SIZE))
    refresh_ahead = job_data.get("refresh_ahead", DEFAULT_REFRESH_AHEAD)
    if not isinstance(refresh_ahead, timedelta):
        refresh_ahead = timedelta(seconds=float(refresh_ahead))

    cursor = int(job_data.get("cursor", 0))

    user_ids = _collect_token_ids()
    batch, next_cursor = _select_batch(user_ids, cursor, max(1, batch_size))

    metrics = _MaintenanceMetrics()
    results: List[TokenLoadResult] = []
    for user_id in batch:
        outcome = _process_user(user_id, refresh_ahead, metrics)
        if outcome is not None:
            results.append(outcome)

    job_data.update({
        "batch_size": batch_size,
        "refresh_ahead": refresh_ahead,
        "cursor": next_cursor,
    })
    if job is not None:
        job.data = job_data

    metrics_payload = _serialize_metrics(metrics, len(user_ids), results)
    message = (
        "🔐 Token health check completed: processed={processed} refreshed={refreshed} "
        "failures={refresh_failures} quarantined={quarantined} skipped={skipped}"
    ).format(
        processed=metrics.processed,
        refreshed=metrics.refreshed,
        refresh_failures=metrics.refresh_failures,
        quarantined=metrics.quarantined,
        skipped=metrics.skipped,
    )

    log_system_info(message, metadata=metrics_payload)
    log_activity(
        0,
        "system",
        "token_health_check",
        source="maintenance",
        metadata={"maintenance": metrics_payload},
        maintenance_metrics=metrics_payload,
    )

    if metrics.refresh_failures or metrics.quarantined:
        alert_message = (
            "Token health check detected issues: failures={failures}, quarantined={quarantined}."
        ).format(
            failures=metrics.refresh_failures,
            quarantined=metrics.quarantined,
        )
        trigger_admin_alert(alert_message)


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_REFRESH_AHEAD",
    "run_token_health_check",
]

