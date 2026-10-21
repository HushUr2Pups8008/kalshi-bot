"""Block every Polymarket path that can place, change, or fund an order.

Paper reads stay on GET. A blocked call raises before any socket or signature.
"""

from __future__ import annotations

from urllib.parse import unquote


class PolymarketOrderBlocked(RuntimeError):
    """The process must not call a Polymarket order, transfer, or wallet path."""


_BLOCKED_PARTS = (
    "/orders",
    "/order",
    "/rfq",
    "/deposit",
    "/withdraw",
    "/transfer",
    "/wallet",
    "/combo",
    "/cancel",
    "/modify",
    "/close-position",
    "/preview",
    "/batch",
)


def _decoded_path(endpoint: str) -> str:
    """Query-stripped path. Percent-encoding is decoded before the block list."""
    path = str(endpoint or "").split("?", 1)[0].strip()
    for _ in range(5):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    return path.lower()


def assert_polymarket_read_only(method: str, endpoint: str) -> None:
    """Raise unless this is a GET that cannot reach an order or funding path.

    A market slug that contains one of the blocked words fails closed.
    ``%6frders`` is ``orders`` and is blocked before any socket.
    """
    verb = str(method or "").strip().upper()
    path = _decoded_path(endpoint)
    if verb != "GET":
        raise PolymarketOrderBlocked(f"polymarket {verb or 'request'} is blocked")
    if any(part in path for part in _BLOCKED_PARTS):
        raise PolymarketOrderBlocked("polymarket order endpoint is blocked")
