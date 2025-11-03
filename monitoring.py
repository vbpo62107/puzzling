from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Deque, Dict, Iterable, Optional

RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "7"))
LOG_DIR = Path(os.getenv("LOG_DIRECTORY", "logs")).expanduser()


def _ensure_log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def _build_handler(filename: str) -> TimedRotatingFileHandler:
    handler = TimedRotatingFileHandler(
        _ensure_log_dir() / filename,
        when="midnight",
        interval=1,
        backupCount=RETENTION_DAYS,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    return handler


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

    system_logger = logging.getLogger("system")
    system_logger.setLevel(logging.INFO)
    system_logger.handlers.clear()
    system_logger.addHandler(_build_handler("system.log"))
    system_logger.propagate = False

    activity_logger = logging.getLogger("activity")
    activity_logger.setLevel(logging.INFO)
    activity_logger.handlers.clear()
    activity_logger.addHandler(_build_handler("activity.log"))
    activity_logger.propagate = False

    stats_logger = logging.getLogger("stats")
    stats_logger.setLevel(logging.INFO)
    stats_logger.handlers.clear()
    stats_logger.addHandler(_build_handler("stats.log"))
    stats_logger.propagate = False


def log_activity(user_id: int, role: str, action: str, detail: str = "") -> None:
    logging.getLogger("activity").info(
        "[user=%s][role=%s] %s %s", user_id, role, action, detail
    )


def log_system_error(message: str, exc: Optional[BaseException] = None) -> None:
    if exc:
        logging.getLogger("system").exception("%s", message, exc_info=exc)
    else:
        logging.getLogger("system").error("%s", message)


def log_system_info(message: str) -> None:
    logging.getLogger("system").info("%s", message)


def trigger_admin_alert(message: str) -> None:
    """Raise an operational alert for administrators via the system logger."""

    logging.getLogger("system").warning("[ADMIN ALERT] %s", message)


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


def get_today_stats() -> Dict[str, object]:
    _ensure_today()
    return {
        "date": _stats.day.isoformat(),
        "upload_count": _stats.upload_count,
        "total_size_mb": round(_stats.total_size_mb, 2),
    }


def _read_recent_lines(path: Path, limit: int) -> Iterable[str]:
    if not path.exists():
        return []
    dq: Deque[str] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            dq.append(line.rstrip("\n"))
    return dq


def tail_logs(log_type: str = "system", lines: int = 50) -> str:
    filename = {
        "system": "system.log",
        "activity": "activity.log",
        "stats": "stats.log",
    }.get(log_type, "system.log")
    log_path = _ensure_log_dir() / filename
    content = "\n".join(_read_recent_lines(log_path, lines))
    if not content:
        return "（暂无日志记录）"
    return content
