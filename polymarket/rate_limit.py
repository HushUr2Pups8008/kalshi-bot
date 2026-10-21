"""One pace for every Polymarket call.

Retail general limit is 25 requests/s, shared by endpoint, source IP, and
Cloudflare location. Quote and RFQ limits are tighter. This process does
not call those endpoints.
https://docs.polymarket.us/api-reference/rate-limits.md
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

_INTERVAL = 1.0 / 25
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_lock = threading.Lock()
_next = 0.0


def wait_turn() -> None:
    global _next
    with _lock:
        now = time.monotonic()
        delay = max(0.0, _next - now)
        _next = max(now, _next) + _INTERVAL
    if delay:
        time.sleep(delay)


def retry_wait_seconds(response: Any) -> float:
    """At least one second. A Retry-After above five seconds is capped."""
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    try:
        parsed = float(str(raw).strip())
    except (TypeError, ValueError):
        parsed = 1.0
    if parsed != parsed or parsed <= 0:
        parsed = 1.0
    return min(5.0, max(1.0, parsed))


def call_once(send: Callable[[], Any]) -> Any:
    """Send once. A 429 or 5xx waits, then tries once more. A 404 does not."""
    wait_turn()
    response = send()
    status = getattr(response, "status_code", getattr(response, "code", None))
    if isinstance(status, bool) or status not in _RETRY_STATUS:
        return response
    time.sleep(retry_wait_seconds(response))
    wait_turn()
    return send()
