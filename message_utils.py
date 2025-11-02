from __future__ import annotations

from typing import Optional

PROGRESS_BAR_WIDTH = 12


_ICONS = {
    "info": "ℹ️",
    "success": "✅",
    "error": "❌",
    "progress": "🚀",
    "download": "📥",
    "upload": "☁️",
}


def wrap(kind: str, message: str) -> str:
    """Attach a leading emoji icon for consistent visual style."""
    icon = _ICONS.get(kind, "")
    if icon:
        return f"{icon} {message}"
    return message


def format_info(message: str) -> str:
    return wrap("info", message)


def format_success(message: str) -> str:
    return wrap("success", message)


def format_error(message: str) -> str:
    return wrap("error", message)


def format_download(message: str) -> str:
    return wrap("download", message)


def format_upload(message: str) -> str:
    return wrap("upload", message)


def format_progress(stage: str, progress: int, detail: Optional[str] = None) -> str:
    """
    Build a simulated progress message with a textual progress bar.

    Args:
        stage: Description of the current operation.
        progress: Percentage (0~100).
        detail: Optional extra line (e.g., filename or speed).
    """
    progress = max(0, min(100, int(progress)))
    filled = int(round(PROGRESS_BAR_WIDTH * progress / 100))
    bar = "█" * filled + "░" * (PROGRESS_BAR_WIDTH - filled)
    lines = [
        wrap("progress", stage),
        f"进度：{bar} {progress}%",
    ]
    if detail:
        lines.append(detail)
    return "\n".join(lines)
