from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from observer.events import ObserverEvent


_REDACTED = "[redacted]"

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "auth",
    "bearer",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)

_ACCOUNT_KEY_PARTS = (
    "account",
    "balance",
    "balances",
    "fill",
    "fills",
    "order_history",
    "position",
    "positions",
    "subaccount",
    "withdrawal",
    "withdrawals",
)

_INLINE_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(token|secret|password|api[_-]?key|private[_-]?key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/\-=]+"),
)


def _key_is_restricted(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS + _ACCOUNT_KEY_PARTS)


def _sanitize_string(value: str) -> str:
    sanitized = value
    for pattern in _INLINE_SECRET_PATTERNS:
        sanitized = pattern.sub(lambda match: f"{match.group(1)}={_REDACTED}", sanitized)
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, Mapping):
        return {
            str(key): (_REDACTED if _key_is_restricted(str(key)) else _sanitize_value(nested_value))
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    return value


def sanitize_event(event: ObserverEvent) -> ObserverEvent:
    """Return a copy with secret-like and account-specific detail keys removed."""

    sanitized_details = {
        str(key): _sanitize_value(value)
        for key, value in event.details.items()
        if not _key_is_restricted(str(key))
    }
    return event.with_details(sanitized_details)
