from __future__ import annotations

import json
import logging
import os
import heapq
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

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

    for logger_name in ("system", "activity", "stats", "auth", "rate_limit"):
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


def record_rate_limit_hit(
    command: str,
    limit_name: Optional[str],
    user_id: Optional[int],
    role: Optional[str],
    retry_after: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    logger = logging.getLogger("rate_limit")
    logger.warning(
        "Rate limit triggered command=%s limit=%s user=%s role=%s retry_after=%s extra=%s",
        command,
        limit_name,
        user_id,
        role,
        retry_after,
        extra,
    )

    payload: Dict[str, Any] = {
        "command": command,
        "limit": limit_name,
        "user": {"id": user_id, "role": role},
        "retry_after": retry_after,
    }
    if extra:
        payload["metadata"] = extra

    _write_log_entry("rate_limit", payload)


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


@dataclass
class LogQuery:
    """Container for structured log search filters."""

    categories: Optional[Set[str]] = None
    user_ids: Optional[Set[int]] = None
    commands: Optional[Set[str]] = None
    levels: Optional[Set[str]] = None
    sources: Optional[Set[str]] = None
    tags: Optional[Set[str]] = None
    contains: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: Optional[int] = None
    extra_filters: Dict[str, Set[str]] = field(default_factory=dict)


@dataclass
class LogSearchRequest:
    """Represents a parsed log search operation."""

    query: LogQuery
    order: str = "desc"
    summary: bool = False


@dataclass
class _NormalizedQuery:
    categories: Optional[Set[str]]
    user_ids: Optional[Set[int]]
    commands: Optional[Set[str]]
    levels: Optional[Set[str]]
    sources: Optional[Set[str]]
    tags: Optional[Set[str]]
    contains: Optional[str]
    since: Optional[datetime]
    until: Optional[datetime]
    limit: Optional[int]
    extra_filters: Dict[str, Set[str]]


def _ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_datetime_spec(value: str) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise ValueError(f"无法解析时间：{value}")
    return parsed


def _normalize_string_set(values: Optional[Set[str]]) -> Optional[Set[str]]:
    if not values:
        return None
    normalized = {item.strip().lower() for item in values if item.strip()}
    return normalized or None


def _normalize_query(query: LogQuery) -> _NormalizedQuery:
    return _NormalizedQuery(
        categories=_normalize_string_set(query.categories),
        user_ids=set(query.user_ids) if query.user_ids else None,
        commands=_normalize_string_set(query.commands),
        levels=_normalize_string_set(query.levels),
        sources=_normalize_string_set(query.sources),
        tags=_normalize_string_set(query.tags),
        contains=query.contains.lower() if query.contains else None,
        since=_ensure_utc(query.since),
        until=_ensure_utc(query.until),
        limit=None if query.limit in {None, 0} else max(0, query.limit),
        extra_filters={
            key.strip(): {val for val in values if val != ""}
            for key, values in query.extra_filters.items()
            if key.strip() and values
        },
    )


def _extract_field(entry: Dict[str, Any], dotted_key: str) -> Any:
    value: Any = entry
    for part in dotted_key.split('.'):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _stringify_filter_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _match_entry(entry: Dict[str, Any], filters: _NormalizedQuery) -> Optional[datetime]:
    category_value = entry.get("category")
    if filters.categories is not None:
        category_key = category_value.lower() if isinstance(category_value, str) else ""
        if category_key not in filters.categories:
            return None

    timestamp = _parse_timestamp(entry.get("timestamp"))
    if timestamp is None:
        return None
    if filters.since and timestamp < filters.since:
        return None
    if filters.until and timestamp > filters.until:
        return None

    if filters.user_ids is not None:
        user_obj = entry.get("user")
        user_id: Optional[int] = None
        if isinstance(user_obj, dict):
            user_raw = user_obj.get("id")
            try:
                user_id = int(user_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                user_id = None
        if user_id is None or user_id not in filters.user_ids:
            return None

    if filters.commands is not None:
        command_value = entry.get("command")
        command_key = command_value.lower() if isinstance(command_value, str) else ""
        if command_key not in filters.commands:
            return None

    if filters.levels is not None:
        level_value = entry.get("level")
        level_key = level_value.lower() if isinstance(level_value, str) else ""
        if level_key not in filters.levels:
            return None

    if filters.sources is not None:
        source_value = entry.get("source")
        source_key = source_value.lower() if isinstance(source_value, str) else ""
        if source_key not in filters.sources:
            return None

    if filters.tags is not None:
        tag_value = entry.get("tag")
        tag_key = tag_value.lower() if isinstance(tag_value, str) else ""
        if tag_key not in filters.tags:
            return None

    if filters.contains is not None:
        haystack = json.dumps(entry, ensure_ascii=False, sort_keys=True).lower()
        if filters.contains not in haystack:
            return None

    for key, allowed_values in filters.extra_filters.items():
        value = _extract_field(entry, key)
        candidate = _stringify_filter_value(value)
        if candidate not in allowed_values:
            return None

    return timestamp


def _iter_matching_entries(filters: _NormalizedQuery) -> Iterator[Tuple[Dict[str, Any], datetime]]:
    for path in _iter_log_files():
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                timestamp = _match_entry(entry, filters)
                if timestamp is None:
                    continue
                yield entry, timestamp


def query_logs(query: LogQuery, *, reverse: bool = True) -> List[Dict[str, Any]]:
    filters = _normalize_query(query)
    if filters.limit is not None and filters.limit <= 0:
        return []

    if reverse:
        if filters.limit is not None:
            heap: List[Tuple[float, Dict[str, Any]]] = []
            for entry, timestamp in _iter_matching_entries(filters):
                ts_value = timestamp.timestamp()
                if len(heap) < filters.limit:
                    heapq.heappush(heap, (ts_value, entry))
                elif ts_value > heap[0][0]:
                    heapq.heapreplace(heap, (ts_value, entry))
            return [entry for _, entry in sorted(heap, key=lambda item: item[0], reverse=True)]

        records: List[Tuple[float, Dict[str, Any]]] = [
            (timestamp.timestamp(), entry)
            for entry, timestamp in _iter_matching_entries(filters)
        ]
        records.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in records]

    results: List[Dict[str, Any]] = []
    for entry, _timestamp in _iter_matching_entries(filters):
        results.append(entry)
        if filters.limit is not None and len(results) >= filters.limit:
            break
    return results


def summarize_logs(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    categories: Counter[str] = Counter()
    commands: Counter[str] = Counter()
    levels: Counter[str] = Counter()
    users: Counter[str] = Counter()
    total = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    for entry in entries:
        total += 1
        categories[str(entry.get("category", "unknown"))] += 1
        command_value = entry.get("command")
        if command_value:
            commands[str(command_value)] += 1
        level_value = entry.get("level")
        if level_value:
            levels[str(level_value)] += 1
        user_obj = entry.get("user")
        if isinstance(user_obj, dict) and user_obj.get("id") is not None:
            users[str(user_obj["id"])] += 1

        timestamp = _parse_timestamp(entry.get("timestamp"))
        if timestamp is not None:
            if first_seen is None or timestamp < first_seen:
                first_seen = timestamp
            if last_seen is None or timestamp > last_seen:
                last_seen = timestamp

    time_range: Optional[Tuple[str, str]] = None
    if first_seen and last_seen:
        time_range = (first_seen.isoformat(), last_seen.isoformat())
    elif first_seen:
        time_range = (first_seen.isoformat(), first_seen.isoformat())

    return {
        "total": total,
        "time_range": time_range,
        "categories": categories.most_common(),
        "commands": commands.most_common(),
        "levels": levels.most_common(),
        "user_ids": users.most_common(),
        "unique_users": len(users),
        "unique_commands": len(commands),
    }


def _split_multi_values(value: str) -> List[str]:
    return [part for part in (segment.strip() for segment in value.replace(",", " ").split()) if part]


def _merge_str_set(current: Optional[Set[str]], values: Iterable[str]) -> Set[str]:
    cleaned = [val for val in values if val]
    if not cleaned:
        raise ValueError("参数值不能为空。")
    container = set(current) if current else set()
    container.update(cleaned)
    return container


def _merge_int_set(current: Optional[Set[int]], values: Iterable[int]) -> Set[int]:
    container = set(current) if current else set()
    container.update(values)
    return container


def _parse_int_values(raw: str) -> Set[int]:
    result: Set[int] = set()
    for fragment in _split_multi_values(raw):
        try:
            result.add(int(fragment))
        except ValueError as exc:
            raise ValueError(f"用户 ID 必须为数字：{fragment}") from exc
    if not result:
        raise ValueError("请提供至少一个有效的用户 ID。")
    return result


def _parse_field_expression(expr: str) -> Tuple[str, str]:
    if "=" not in expr:
        raise ValueError("--field 参数格式应为 key=value。")
    key, value = expr.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise ValueError("--field 参数的键不能为空。")
    if value == "":
        raise ValueError("--field 参数的值不能为空。")
    return key, value


def _consume_value(tokens: Sequence[str], index: int) -> Tuple[str, int]:
    token = tokens[index]
    if "=" in token:
        _, value = token.split("=", 1)
        return value, index
    next_index = index + 1
    if next_index >= len(tokens):
        raise ValueError(f"参数 {token} 需要一个值。")
    return tokens[next_index], next_index


def parse_log_search_arguments(args: Sequence[str]) -> LogSearchRequest:
    tokens = list(args)
    query = LogQuery()
    order = "desc"
    summary = False

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in {"--uid", "-u"} or token.startswith("--uid="):
            value, idx = _consume_value(tokens, idx)
            query.user_ids = _merge_int_set(query.user_ids, _parse_int_values(value))
        elif token in {"--cmd", "--command"} or token.startswith("--cmd="):
            value, idx = _consume_value(tokens, idx)
            query.commands = _merge_str_set(query.commands, _split_multi_values(value))
        elif token in {"--category", "--cat"} or token.startswith("--category="):
            value, idx = _consume_value(tokens, idx)
            query.categories = _merge_str_set(query.categories, _split_multi_values(value))
        elif token in {"--level"} or token.startswith("--level="):
            value, idx = _consume_value(tokens, idx)
            query.levels = _merge_str_set(query.levels, _split_multi_values(value))
        elif token in {"--source"} or token.startswith("--source="):
            value, idx = _consume_value(tokens, idx)
            query.sources = _merge_str_set(query.sources, _split_multi_values(value))
        elif token in {"--tag"} or token.startswith("--tag="):
            value, idx = _consume_value(tokens, idx)
            query.tags = _merge_str_set(query.tags, _split_multi_values(value))
        elif token == "--contains" or token.startswith("--contains="):
            value, idx = _consume_value(tokens, idx)
            if not value:
                raise ValueError("--contains 参数不能为空。")
            query.contains = value
        elif token == "--since" or token.startswith("--since="):
            value, idx = _consume_value(tokens, idx)
            query.since = parse_datetime_spec(value)
        elif token == "--until" or token.startswith("--until="):
            value, idx = _consume_value(tokens, idx)
            query.until = parse_datetime_spec(value)
        elif token == "--limit" or token.startswith("--limit="):
            value, idx = _consume_value(tokens, idx)
            try:
                limit_value = int(value)
            except ValueError as exc:
                raise ValueError("--limit 参数必须为整数。") from exc
            if limit_value < 0:
                raise ValueError("--limit 参数必须为非负整数。")
            query.limit = limit_value
        elif token == "--summary":
            summary = True
        elif token == "--order" or token.startswith("--order="):
            value, idx = _consume_value(tokens, idx)
            normalized_order = value.lower()
            if normalized_order not in {"asc", "desc"}:
                raise ValueError("--order 仅支持 asc 或 desc。")
            order = normalized_order
        elif token == "--field" or token.startswith("--field="):
            value, idx = _consume_value(tokens, idx)
            key, val = _parse_field_expression(value)
            query.extra_filters.setdefault(key, set()).add(val)
        elif token in {"-h", "--help"}:
            raise ValueError("此命令不支持 --help，请在 CLI 中查看帮助。")
        else:
            raise ValueError(f"未识别的参数：{token}")
        idx += 1

    return LogSearchRequest(query=query, order=order, summary=summary)
