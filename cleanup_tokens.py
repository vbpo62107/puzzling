#!/usr/bin/env python3
"""Command line entry point for running token cleanup scans."""

from __future__ import annotations

import argparse
import logging
from typing import Iterable

from monitoring import log_system_info, setup_logging
from puzzling.token_cleanup import TokenIssue, run_cleanup


def _format_issue(issue: TokenIssue) -> str:
    if issue.deleted_at is not None:
        timestamp = issue.deleted_at.astimezone().isoformat()
    else:
        timestamp = "preserved"
    return f"- {issue.masked_path} ({timestamp}): {issue.reason}"


def _format_report(report) -> str:
    lines = [report.summary(), f"Base directory: {report.base_dir}"]
    if report.deleted_files:
        lines.append("Deleted tokens:")
        lines.extend(_format_issue(issue) for issue in report.deleted_files)
    if report.skipped_files:
        lines.append("Preserved tokens:")
        lines.extend(_format_issue(issue) for issue in report.skipped_files)
    if report.errors:
        lines.append("Errors:")
        lines.extend(f"  * {error}" for error in report.errors)
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the token cleanup routine")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Perform the comprehensive scan instead of the quick check.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    setup_logging()

    report = run_cleanup(full=args.full)
    summary = report.summary()

    logging.info(summary)
    log_system_info(summary)

    details = _format_report(report)
    print(details)

    for issue in report.deleted_files:
        logging.warning(
            "Deleted token file %s (%s)", issue.masked_path, issue.reason
        )
    for error in report.errors:
        logging.error("Token cleanup error: %s", error)

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
