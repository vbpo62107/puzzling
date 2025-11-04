#!/usr/bin/env python3
"""Command-line interface for querying structured monitoring logs."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence, Tuple

from monitoring import LogSearchRequest, parse_log_search_arguments, query_logs, summarize_logs

HELP_TEXT = """\
Usage: python tools/search_logs.py [CLI-options] [filters...]

Examples:
  python tools/search_logs.py --uid 123456 --category activity --since 2024-05-01 --summary --limit 20
  python tools/search_logs.py --summary-only --cmd cleanup --since 2024-05-20T00:00:00+08:00

Filter options mirror the /search_logs admin command:
  --uid <id[,id...]>        Filter by user ID(s).
  --cmd <name>              Filter by command name.
  --category <name>         Filter by log category (system/activity/stats).
  --level <name>            Filter by log level.
  --source <name>           Filter by source marker.
  --tag <name>              Filter by tag value.
  --contains <text>         Perform substring match across the JSON payload.
  --since <ISO-8601>        Only include entries at or after this timestamp.
  --until <ISO-8601>        Only include entries up to this timestamp.
  --limit <number>          Maximum entries to return (0 means unlimited).
  --order <asc|desc>        Sort results chronologically or reverse chronological (default: desc).
  --summary                 Include summary statistics based on the returned entries.
  --field <key=value>       Equality filter using dotted key notation (e.g. user.role=admin).

Use --summary-only to print just the summary without the raw entries.
"""


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search structured JSONL log files.",
        add_help=False,
    )
    parser.add_argument("--help", action="store_true", dest="show_help", help="Show this help message and exit.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output JSON payload instead of text.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output when used with --json.")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only display summary statistics (implies --summary).",
    )
    return parser


def _format_pairs(pairs: Sequence[Tuple[str, int]], limit: int = 5) -> str:
    if not pairs:
        return "-"
    display = [f"{(label or '-')}×{count}" for label, count in pairs[:limit]]
    if len(pairs) > limit:
        display.append("…")
    return ", ".join(display)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_cli_parser()
    parsed, remaining = parser.parse_known_args(list(argv) if argv is not None else None)

    if parsed.show_help or any(token in {"-h", "--help"} for token in remaining):
        print(HELP_TEXT)
        return 0

    try:
        request: LogSearchRequest = parse_log_search_arguments(remaining)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(HELP_TEXT, file=sys.stderr)
        return 2

    if parsed.summary_only:
        request.summary = True

    reverse = request.order != "asc"
    results = query_logs(request.query, reverse=reverse)
    limit_value = request.query.limit
    total_returned = len(results)
    summary_data = summarize_logs(results) if request.summary else None

    if parsed.as_json:
        payload = {
            "meta": {
                "order": request.order,
                "limit": limit_value,
                "count": total_returned,
                "summary_based_on": "returned_entries" if summary_data is not None else None,
            },
            "results": results,
        }
        if summary_data is not None:
            payload["summary"] = summary_data
        indent = 2 if parsed.pretty else None
        print(json.dumps(payload, ensure_ascii=False, indent=indent))
        return 0

    limit_text = "∞" if limit_value in (None, 0) else str(limit_value)
    order_text = "desc" if reverse else "asc"
    print(f"🔎 Matched entries: {total_returned} (order={order_text}, limit={limit_text})")
    if limit_value not in (None, 0) and total_returned == limit_value:
        print("⚠️ Limit reached; additional entries may exist.")

    if summary_data is not None:
        print("📊 Summary (based on returned entries):")
        if summary_data.get("time_range"):
            start, end = summary_data["time_range"]
            print(f"  • Time range: {start} ~ {end}")
        print(f"  • Total entries: {summary_data['total']}")
        if summary_data.get("categories"):
            print(f"  • Categories: {_format_pairs(summary_data['categories'])}")
        if summary_data.get("commands"):
            print(f"  • Commands: {_format_pairs(summary_data['commands'])}")
        if summary_data.get("levels"):
            print(f"  • Levels: {_format_pairs(summary_data['levels'])}")
        if summary_data.get("user_ids"):
            print(f"  • Users: {_format_pairs(summary_data['user_ids'])}")
        print(
            "  • Unique users: "
            f"{summary_data.get('unique_users', 0)}, unique commands: {summary_data.get('unique_commands', 0)}"
        )

    if not parsed.summary_only:
        if not results:
            print("ℹ️ No matching entries found.")
        for entry in results:
            print(json.dumps(entry, ensure_ascii=False, sort_keys=True))

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
