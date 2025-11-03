from __future__ import annotations

import argparse
import json
import logging
import os
from collections import Counter, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Deque, Dict, Iterable, Iterator, List, Mapping, Optional

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


# ---------------------------------------------------------------------------
# Structured JSON log helpers
# ---------------------------------------------------------------------------

STRUCTURED_DEFAULT = "activity"


def _structured_log_path(log_type: str = STRUCTURED_DEFAULT) -> Path:
    suffix = ".jsonl"
    if log_type.endswith(suffix):
        filename = log_type
    else:
        filename = f"{log_type}{suffix}"
    return _ensure_log_dir() / filename


def _parse_datetime(value: Optional[object]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iter_structured_entries(path: Path) -> Iterator[Dict[str, object]]:
    if not path.exists():
        return iter(())

    def _generator() -> Iterator[Dict[str, object]]:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    yield data

    return _generator()


def parse_field_filters(field_args: Iterable[str]) -> Dict[str, str]:
    filters: Dict[str, str] = {}
    for item in field_args:
        if not item:
            continue
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            filters[key] = value
    return filters


def query_structured_logs(
    log_type: str = STRUCTURED_DEFAULT,
    *,
    user_id: Optional[int] = None,
    command: Optional[str] = None,
    since: Optional[object] = None,
    until: Optional[object] = None,
    extra_filters: Optional[Mapping[str, object]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, object]]:
    path = _structured_log_path(log_type)
    since_dt = _parse_datetime(since)
    until_dt = _parse_datetime(until)
    filters = dict(extra_filters or {})
    results: List[Dict[str, object]] = []

    for entry in _iter_structured_entries(path):
        timestamp = entry.get("timestamp") or entry.get("time")
        entry_dt = _parse_datetime(timestamp)
        if since_dt and entry_dt and entry_dt < since_dt:
            continue
        if until_dt and entry_dt and entry_dt > until_dt:
            continue

        if user_id is not None and str(entry.get("user_id")) != str(user_id):
            continue
        if command and str(entry.get("command")) != command:
            continue

        match = True
        for key, value in filters.items():
            if str(entry.get(key)) != str(value):
                match = False
                break
        if not match:
            continue

        results.append(entry)
        if limit is not None and len(results) >= limit:
            break

    return results


def summarize_logs(entries: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    count = 0
    users: Counter = Counter()
    commands: Counter = Counter()
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None

    for entry in entries:
        count += 1
        uid = entry.get("user_id")
        cmd = entry.get("command")
        if uid is not None:
            users[str(uid)] += 1
        if cmd:
            commands[str(cmd)] += 1
        ts = entry.get("timestamp") or entry.get("time")
        dt = _parse_datetime(ts)
        if dt:
            if first_ts is None or dt < first_ts:
                first_ts = dt
            if last_ts is None or dt > last_ts:
                last_ts = dt

    top_users = users.most_common(3)
    top_commands = commands.most_common(5)
    return {
        "count": count,
        "time_range": (
            first_ts.isoformat() if first_ts else None,
            last_ts.isoformat() if last_ts else None,
        ),
        "top_users": top_users,
        "top_commands": top_commands,
    }


def build_log_query_parser(
    *, prog: Optional[str] = None, add_help: bool = True
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        add_help=add_help,
        description="Filter structured JSONL bot logs",
    )
    parser.add_argument(
        "--log",
        default=STRUCTURED_DEFAULT,
        help="Log type / filename prefix (default: activity)",
    )
    parser.add_argument("--uid", type=int, help="Filter by user ID", default=None)
    parser.add_argument("--cmd", help="Filter by command name", default=None)
    parser.add_argument("--since", help="ISO timestamp lower bound", default=None)
    parser.add_argument("--until", help="ISO timestamp upper bound", default=None)
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="key=value",
        help="Additional key=value filters (repeatable)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of entries to return",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Return summary statistics instead of raw entries",
    )
    return parser


__all__ = [
    "build_log_query_parser",
    "parse_field_filters",
    "query_structured_logs",
    "summarize_logs",
    "tail_logs",
]
