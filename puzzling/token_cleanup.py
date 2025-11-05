"""Utilities for proactively cleaning Google credential tokens.

This module inspects the directory configured by ``GOOGLE_TOKEN_BASE_DIR`` and
removes obviously bad credential files.  It can be invoked in two modes:

``quick``
    Intended for startup checks.  Only obviously broken files (empty or invalid
    JSON) are removed.

``full``
    Performs the quick checks and additionally evaluates the file name pattern
    and token freshness/age in order to catch stale credentials.

The public ``scan_tokens`` helper returns a :class:`TokenScanReport` instance
with enough structured information for logging and alerting.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Literal, Optional

try:  # pragma: no cover - imported lazily for non-POSIX platforms
    import fcntl

    _FCNTL_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]
    _FCNTL_AVAILABLE = False

import time
import uuid

try:
    from creds import get_google_token_base_dir
except ImportError:  # pragma: no cover - fallback when creds is unavailable
    def get_google_token_base_dir(default: str | Path = "user_data") -> Path:
        return Path(default)


GOOGLE_TOKEN_BASE_DIR = get_google_token_base_dir()

logger = logging.getLogger(__name__)

ScanMode = Literal["quick", "full"]


@dataclass
class TokenIssue:
    """Details about an individual token file that required attention."""

    path: Path
    reason: str
    deleted_at: Optional[datetime] = None

    @property
    def masked_path(self) -> str:
        """Return a privacy-preserving identifier for the token file."""

        return mask_token_identifier(self.path)


@dataclass
class TokenScanReport:
    """Structured output describing the outcome of a scan."""

    base_dir: Path
    mode: ScanMode
    total_files: int = 0
    deleted_files: List[TokenIssue] = field(default_factory=list)
    skipped_files: List[TokenIssue] = field(default_factory=list)
    kept_files: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_files)

    def summary(self) -> str:
        return (
            f"Token cleanup ({self.mode}) scanned {self.total_files} files: "
            f"deleted={self.deleted_count}, skipped={len(self.skipped_files)}, kept={self.kept_files}, "
            f"errors={len(self.errors)}"
        )


_TOKEN_FILENAME_PATTERN = re.compile(r"^token(?:[._-][\w-]+)?\.json$", re.IGNORECASE)


def _iter_token_files(base_dir: Path) -> Iterable[Path]:
    if not base_dir.exists():
        logger.debug("Token base directory does not exist: %s", base_dir)
        return []
    return sorted(
        path
        for path in base_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".json"
    )


def _load_json(path: Path) -> tuple[Optional[dict], Optional[str]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle), None
    except json.JSONDecodeError as exc:
        logger.warning(
            "Invalid JSON in token %s: %s",
            mask_token_identifier(path),
            exc,
        )
        return None, "invalid JSON"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Unable to read token %s: %s",
            mask_token_identifier(path),
            exc,
        )
        return None, f"unreadable ({exc})"


def _parse_expiry(value: str) -> Optional[datetime]:
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _full_mode_checks(
    path: Path,
    data: Optional[dict],
    modified: datetime,
    now: datetime,
    max_age_days: Optional[int],
) -> List[str]:
    reasons: List[str] = []

    if not _TOKEN_FILENAME_PATTERN.match(path.name):
        reasons.append("unexpected filename pattern")

    if max_age_days is not None and max_age_days >= 0:
        cutoff = now - timedelta(days=max_age_days)
        if modified < cutoff:
            reasons.append(f"file older than {max_age_days} days")

    if data:
        expiry_raw = data.get("token_expiry") or data.get("expiry")
        if isinstance(expiry_raw, str):
            expiry = _parse_expiry(expiry_raw)
            if expiry is None:
                reasons.append("could not parse token_expiry")
            elif expiry <= now:
                reasons.append(f"token expired on {expiry.isoformat()}")
        elif expiry_raw is not None:
            reasons.append("token_expiry has unexpected type")

    return reasons


def _read_max_age_days() -> Optional[int]:
    raw = os.getenv("TOKEN_MAX_AGE_DAYS")
    if raw is None:
        return 180
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid TOKEN_MAX_AGE_DAYS value %r; falling back to default", raw)
        return 180


def _read_lock_timeout_seconds() -> float:
    raw = os.getenv("TOKEN_LOCK_TIMEOUT_SECONDS")
    if raw is None:
        return 0.05
    try:
        value = float(raw)
        if value < 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning(
            "Invalid TOKEN_LOCK_TIMEOUT_SECONDS value %r; falling back to default",
            raw,
        )
        return 0.05


def _try_lock_handle(handle, timeout: float) -> bool:
    if not _FCNTL_AVAILABLE:
        return True

    deadline = time.monotonic() + timeout
    sleep_interval = 0.01

    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if timeout == 0 or time.monotonic() >= deadline:
                return False
            time.sleep(min(sleep_interval, max(0.001, timeout)))
        except OSError:
            return False


def _delete_with_lock(token_file: Path, timeout: float) -> tuple[bool, Optional[str]]:
    try:
        with token_file.open("rb") as handle:
            if not _try_lock_handle(handle, timeout):
                return False, "lock unavailable"
            try:
                token_file.unlink()
            except FileNotFoundError:
                pass
            return True, None
    except FileNotFoundError:
        return True, None
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"delete failed ({exc})"


def _delete_with_rename(token_file: Path) -> tuple[bool, Optional[str]]:
    temp_name = token_file.with_name(
        f".{token_file.name}.cleanup-{uuid.uuid4().hex}"
    )
    try:
        token_file.rename(temp_name)
    except FileNotFoundError:
        return True, None
    except OSError as exc:
        if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM):
            return False, "lock unavailable"
        return False, f"rename failed ({exc})"

    try:
        temp_name.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - defensive
        return False, f"unlink renamed file failed ({exc})"

    return True, None


def scan_tokens(mode: ScanMode = "quick", base_dir: Optional[Path] = None) -> TokenScanReport:
    """Scan and optionally clean up token files.

    Args:
        mode: ``"quick"`` (default) removes only empty/invalid JSON tokens while
            ``"full"`` performs additional checks.
        base_dir: Override the directory to scan.  Primarily useful for tests.

    Returns:
        TokenScanReport summarising the performed actions.
    """

    if mode not in ("quick", "full"):
        raise ValueError(f"Unsupported scan mode: {mode}")

    resolved_base = Path(base_dir or GOOGLE_TOKEN_BASE_DIR).expanduser()
    report = TokenScanReport(base_dir=resolved_base, mode=mode)

    now = datetime.now(timezone.utc)
    max_age_days = _read_max_age_days() if mode == "full" else None
    lock_timeout = _read_lock_timeout_seconds()

    for token_file in _iter_token_files(resolved_base):
        try:
            stats = token_file.stat()
        except FileNotFoundError:
            continue

        report.total_files += 1

        reasons: List[str] = []
        data: Optional[dict] = None

        if stats.st_size == 0:
            reasons.append("empty file")
        else:
            data, json_error = _load_json(token_file)
            if json_error:
                reasons.append(json_error)

        if not reasons and mode == "full":
            modified = datetime.fromtimestamp(stats.st_mtime, tz=timezone.utc)
            reasons.extend(_full_mode_checks(token_file, data, modified, now, max_age_days))

        if reasons:
            reason_text = "; ".join(reasons)
            if _FCNTL_AVAILABLE:
                deleted, error = _delete_with_lock(token_file, lock_timeout)
            else:
                deleted, error = _delete_with_rename(token_file)

            if deleted and error is None:
                logger.warning(
                    "Removed token file %s (%s)",
                    mask_token_identifier(token_file),
                    reason_text,
                )
                report.deleted_files.append(
                    TokenIssue(path=token_file, reason=reason_text, deleted_at=now)
                )
            elif not deleted and error == "lock unavailable":
                skip_reason = (
                    f"{reason_text}; lock unavailable after {lock_timeout:.3f}s"
                    if lock_timeout > 0
                    else f"{reason_text}; lock unavailable"
                )
                logger.info(
                    "Skipped deleting token %s because it appears in use (%s)",
                    mask_token_identifier(token_file),
                    skip_reason,
                )
                report.skipped_files.append(
                    TokenIssue(path=token_file, reason=skip_reason)
                )
            elif deleted:
                logger.warning(
                    "Removed token file %s (%s) after rename fallback",
                    mask_token_identifier(token_file),
                    reason_text,
                )
                report.deleted_files.append(
                    TokenIssue(path=token_file, reason=reason_text, deleted_at=now)
                )
            else:
                error_text = (
                    f"Failed to delete token {mask_token_identifier(token_file)}: {error}"
                    if error
                    else f"Failed to delete token {mask_token_identifier(token_file)}"
                )
                logger.exception(error_text)
                report.errors.append(error_text)
        else:
            report.kept_files += 1

    if report.total_files == 0:
        logger.debug("No token files discovered under %s", resolved_base)

    return report


def run_cleanup(full: bool = False, base_dir: Optional[Path] = None) -> TokenScanReport:
    """Execute a token cleanup pass.

    Args:
        full: When ``True`` performs the comprehensive scan (equivalent to
            ``mode="full"``); otherwise runs the quick scan.
        base_dir: Optional override for the token directory, primarily useful
            in tests.

    Returns:
        A :class:`TokenScanReport` describing the actions taken, including
        timestamps for deleted files.
    """

    mode: ScanMode = "full" if full else "quick"
    return scan_tokens(mode=mode, base_dir=base_dir)


def mask_token_identifier(path: Path) -> str:
    """Return a masked identifier for a token path suitable for logging/output."""

    suffix = "".join(path.suffixes)
    hasher = hashlib.sha256()
    hasher.update(str(path).encode("utf-8"))
    digest = hasher.hexdigest()[:8]
    return f"token#{digest}{suffix}"


__all__ = [
    "scan_tokens",
    "run_cleanup",
    "TokenScanReport",
    "TokenIssue",
    "ScanMode",
    "mask_token_identifier",
]
