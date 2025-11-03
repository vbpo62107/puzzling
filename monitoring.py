from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional

RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "7"))
LOG_DIR = Path(os.getenv("LOG_DIRECTORY", "logs")).expanduser()

_LAST_CLEANUP: Optional[date] = None


def _ensure_log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def _get_log_path(log_date: Optional[date] = None) -> Path:
    target_date = (log_date or date.today()).isoformat()
    return _ensure_log_dir() / f"{target_date}.jsonl"


def _cleanup_old_logs(today: Optional[date] = None) -> None:
    global _LAST_CLEANUP
    current_day = today or date.today()
    if _LAST_CLEANUP == current_day:
        return
    _LAST_CLEANUP = current_day

    if RETENTION_DAYS <= 0:
        return

    cutoff_date = current_day - timedelta(days=max(RETENTION_DAYS - 1, 0))
    for path in _ensure_log_dir().glob("*.jsonl"):
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if file_date < cutoff_date:
            try:
                path.unlink()
            except FileNotFoundError:  # pragma: no cover - best effort cleanup
                continue


def _write_log_entry(category: str, payload: Dict[str, Any]) -> None:
    timestamp = datetime.now(timezone.utc)
    record = {
        "timestamp": timestamp.isoformat(),
        "category": category,
        **payload,
    }
    path = _get_log_path()
    with path.open("a", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    _cleanup_old_logs(timestamp.date())


def setup_logging(level_name: str = "INFO") -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    root.addHandler(console_handler)

    for logger_name in ("system", "activity", "stats"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.propagate = True


def log_activity(
    user_id: int,
    role: str,
    command: str,
    *,
    source: str = "",
    verification: Optional[str] = None,
    duration_ms: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    logging.getLogger("activity").info(
        "user=%s role=%s command=%s source=%s verification=%s duration_ms=%s metadata=%s",
        user_id,
        role,
        command,
        source,
        verification,
        duration_ms,
        metadata,
    )
    payload: Dict[str, Any] = {
        "user": {"id": user_id, "role": role},
        "command": command,
        "source": source or None,
        "verification": verification,
        "duration_ms": duration_ms,
    }
    if metadata:
        payload["metadata"] = metadata
    _write_log_entry("activity", payload)


def log_system_error(message: str, exc: Optional[BaseException] = None) -> None:
    logger = logging.getLogger("system")
    if exc:
        logger.exception("%s", message, exc_info=exc)
        payload: Dict[str, Any] = {
            "level": "ERROR",
            "message": message,
            "exception": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
    else:
        logger.error("%s", message)
        payload = {"level": "ERROR", "message": message}
    _write_log_entry("system", payload)


def log_system_info(message: str) -> None:
    logging.getLogger("system").info("%s", message)
    _write_log_entry("system", {"level": "INFO", "message": message})


def trigger_admin_alert(message: str) -> None:
    """Raise an operational alert for administrators via the system logger."""

    logging.getLogger("system").warning("[ADMIN ALERT] %s", message)
    _write_log_entry(
        "system",
        {"level": "WARNING", "message": message, "tag": "admin_alert"},
    )


@dataclass
class DailyStats:
    day: date
    upload_count: int = 0
    total_size_mb: float = 0.0


_stats = DailyStats(day=date.today())


def _ensure_today() -> None:
    global _stats
    today = date.today()
    if _stats.day != today:
        logging.getLogger("stats").info(
            "Rollover daily stats: uploads=%s, total_size_mb=%.2f",
            _stats.upload_count,
            _stats.total_size_mb,
        )
        _write_log_entry(
            "stats",
            {
                "event": "rollover",
                "uploads": _stats.upload_count,
                "total_size_mb": round(_stats.total_size_mb, 2),
            },
        )
        _stats = DailyStats(day=today)


def record_upload(user_id: int, role: str, file_size_mb: float, filename: str) -> None:
    _ensure_today()
    _stats.upload_count += 1
    _stats.total_size_mb += file_size_mb
    logging.getLogger("stats").info(
        "[user=%s][role=%s] upload size=%.2fMB file=%s",
        user_id,
        role,
        file_size_mb,
        filename,
    )
    _write_log_entry(
        "stats",
        {
            "user": {"id": user_id, "role": role},
            "event": "upload",
            "size_mb": round(file_size_mb, 2),
            "filename": filename,
        },
    )


def get_today_stats() -> Dict[str, object]:
    _ensure_today()
    return {
        "date": _stats.day.isoformat(),
        "upload_count": _stats.upload_count,
        "total_size_mb": round(_stats.total_size_mb, 2),
    }


def _iter_log_files() -> List[Path]:
    if not _ensure_log_dir().exists():
        return []
    return sorted(LOG_DIR.glob("*.jsonl"))


def _read_recent_entries(category: str, limit: int) -> Iterable[str]:
    dq: Deque[str] = deque(maxlen=limit)
    for path in _iter_log_files():
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("category") != category:
                    continue
                dq.append(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return dq


def tail_logs(log_type: str = "system", lines: int = 50) -> str:
    category = log_type if log_type in {"system", "activity", "stats"} else "system"
    content = "\n".join(_read_recent_entries(category, lines))
    if not content:
        return "（暂无日志记录）"
    return content
