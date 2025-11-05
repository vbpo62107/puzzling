from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional, Union


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def mask_user_id(user_id: Optional[Union[int, str]]) -> str:
    if user_id in (None, "", 0):
        return "user-anon"
    return f"user-{_digest(str(user_id))}"


def mask_token_path(token_path: Optional[Union[str, Path]]) -> str:
    if token_path is None:
        return "token-unknown"
    path_str = str(Path(token_path))
    return f"token-{_digest(path_str)}"


def token_log_extra(
    *,
    user_id: Optional[Union[int, str]] = None,
    token_path: Optional[Union[str, Path]] = None,
    reason: Optional[str] = None,
    quarantine_path: Optional[Union[str, Path]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {**kwargs}
    if user_id is not None:
        payload["masked_user_id"] = mask_user_id(user_id)
    if token_path is not None:
        payload["masked_token_path"] = mask_token_path(token_path)
    if reason:
        payload["token_event"] = reason
    if quarantine_path is not None:
        payload["quarantined_mask"] = mask_token_path(quarantine_path)
    return payload


__all__ = ["mask_user_id", "mask_token_path", "token_log_extra"]
