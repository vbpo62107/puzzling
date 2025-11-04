from __future__ import annotations

import logging
from pathlib import Path

from pydrive2.auth import GoogleAuth

from security.token_store import (
    TokenLoadResult,
    TokenState,
    configure_gauth as _configure_gauth,
    ensure_token_storage as _ensure_token_storage,
    prepare_gauth,
    refresh_gauth as _refresh_gauth,
    store_gauth as _store_gauth,
    token_store,
)

logger = logging.getLogger(__name__)


def ensure_token_storage(token_file: str | Path) -> None:
    """Proxy to :mod:`security.token_store` to prepare the token directory."""

    _ensure_token_storage(token_file)


def configure_gauth(gauth: GoogleAuth, token_file: str | Path) -> GoogleAuth:
    """Return a GoogleAuth instance configured for file-based credential storage."""

    return _configure_gauth(gauth, token_file)


def refresh_user_gauth(user_id: int, gauth: GoogleAuth) -> TokenLoadResult:
    """Refresh the stored credentials for ``user_id`` using the token store."""

    return _refresh_gauth(user_id, gauth)


def store_user_gauth(user_id: int, gauth: GoogleAuth) -> TokenLoadResult:
    """Persist credentials for ``user_id`` atomically via the token store."""

    return _store_gauth(user_id, gauth)


def prepare_user_gauth(user_id: int, token_file: str | Path) -> TokenLoadResult:
    """Prepare the GoogleAuth object for ``user_id`` with safety checks.

    The caller-provided ``token_file`` is ignored if it differs from the
    repository-defined location to prevent path traversal or cross-user access.
    """

    resolved_path = Path(token_file).expanduser()
    expected_path = token_store().get_token_path(user_id)
    if resolved_path != expected_path:
        logger.warning(
            "Ignoring overridden token path %s for user %s; enforcing %s",
            resolved_path,
            user_id,
            expected_path,
        )

    result = prepare_gauth(user_id)
    if result.state is TokenState.CORRUPTED:
        logger.warning("Token for user %s is corrupted or quarantined", user_id)
    elif result.state is TokenState.ABSENT:
        logger.info("Token for user %s is absent", user_id)
    elif result.state is TokenState.REFRESH_FAILED:
        logger.warning("Token refresh failed for user %s", user_id)
    return result


__all__ = [
    "TokenLoadResult",
    "TokenState",
    "configure_gauth",
    "ensure_token_storage",
    "refresh_user_gauth",
    "store_user_gauth",
    "prepare_user_gauth",
]
