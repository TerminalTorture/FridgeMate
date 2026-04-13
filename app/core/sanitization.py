from __future__ import annotations

import re
from typing import Any

_REDACTED = "***REDACTED***"
_SECRET_KEY_MARKERS = (
    "api_key",
    "token",
    "secret",
    "password",
    "authorization",
    "webhook",
    "credential",
    "bearer",
)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (_REDACTED if _looks_sensitive_key(str(key)) else redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return _BEARER_PATTERN.sub("Bearer " + _REDACTED, value)
    return value
