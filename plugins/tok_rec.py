# This module identifies whether the received text
# looks like a Google OAuth device flow token.

import re

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")


def is_token(token: str) -> bool:
    candidate = (token or "").strip().split()[-1]
    if not candidate:
        return False
    if len(candidate) < 40:
        return False
    return bool(_TOKEN_PATTERN.match(candidate))
