#!/usr/bin/env python3
"""CLI helper to filter structured bot logs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring import (  # noqa: E402
    build_log_query_parser,
    parse_field_filters,
    query_structured_logs,
    summarize_logs,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_log_query_parser(prog="search_logs")
    args = parser.parse_args(argv)

    extra_filters = parse_field_filters(args.field)
    query_limit = None if args.summary else (args.limit + 1 if args.limit is not None else None)

    results = query_structured_logs(
        log_type=args.log,
        user_id=args.uid,
        command=args.cmd,
        since=args.since,
        until=args.until,
        extra_filters=extra_filters,
        limit=query_limit,
    )

    if args.summary:
        stats = summarize_logs(results)
        time_range = stats["time_range"]
        start_ts = time_range[0] or "-"
        end_ts = time_range[1] or "-"
        print("📊 日志统计（当前筛选）")
        print(f"• 匹配条目：{stats['count']}")
        print(f"• 时间范围：{start_ts} ~ {end_ts}")
        if stats["top_users"]:
            users_text = ", ".join(f"{uid}×{cnt}" for uid, cnt in stats["top_users"])
            print(f"• 用户 TOP3：{users_text}")
        if stats["top_commands"]:
            cmd_text = ", ".join(f"{cmd}×{cnt}" for cmd, cnt in stats["top_commands"])
            print(f"• 指令 TOP5：{cmd_text}")
        if not results:
            print("• 没有符合条件的日志记录。")
        return 0

    if not results:
        print("🔍 未找到符合条件的日志记录。")
        return 0

    limit = args.limit if args.limit is not None else len(results)
    display = results[:limit]
    has_more = len(results) > len(display)
    print(f"🔍 已检索到 {len(display)} 条日志记录。")
    for entry in display:
        ts = entry.get("timestamp") or entry.get("time") or "-"
        uid = entry.get("user_id", "-")
        cmd = entry.get("command") or entry.get("action") or "-"
        summary = json.dumps(entry, ensure_ascii=False)
        print(f"• {ts} | uid={uid} | cmd={cmd}\n  {summary}")
    if has_more:
        print("… 已截断，使用 --limit 调整显示数量。")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
