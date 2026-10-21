"""Dicts the paper ranker already understands, built from public Polymarket reads."""

from __future__ import annotations

import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from polymarket.read_only import PolymarketOrderBlocked
from utils.logger import get_logger

log = get_logger("polymarket_paper")

_OPEN = "MARKET_STATUS_OPEN"
_TRANSIENT_BOOK_STATUS = frozenset({429, 500, 502, 503, 504})
_BOOK_RETRY_SECONDS = 1.0


def _cents_from_bid(price: Decimal) -> int | None:
    raw = (Decimal("1") - price) * Decimal(100)
    cents = int(raw.to_integral_value(rounding=ROUND_HALF_UP))
    if 1 <= cents <= 99:
        return cents
    return None


def book_asks(book: Any) -> tuple[int | None, int | None]:
    """YES ask is one minus the best NO bid. NO ask is one minus the best YES bid."""
    no_bids = getattr(book, "no_bids", ()) or ()
    yes_bids = getattr(book, "yes_bids", ()) or ()
    yes_ask = _cents_from_bid(no_bids[0].price) if no_bids else None
    no_ask = _cents_from_bid(yes_bids[0].price) if yes_bids else None
    return yes_ask, no_ask


def paper_view(raw: Mapping[str, Any], series: str, book: Any) -> dict[str, Any] | None:
    """Open slug only. The ranker reads status ``active`` and the yaml series name."""
    if str(raw.get("status") or "") != _OPEN:
        return None
    slug = str(raw.get("slug") or "").strip()
    if not slug:
        return None
    yes_ask, no_ask = book_asks(book)
    close = raw.get("closeTime") or raw.get("close_time") or raw.get("endDate") or ""
    volume = raw.get("volume")
    if volume in (None, ""):
        volume = raw.get("openInterest")
    name = str(series or "").strip().upper()
    return {
        "ticker": slug,
        "series": name,
        "series_ticker": name,
        "status": "active",
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "title": str(raw.get("title") or raw.get("question") or ""),
        "rules": str(raw.get("description") or ""),
        "close_time": str(close or ""),
        "volume": 0 if volume is None else volume,
        "venue": "polymarket_us",
    }


def _http_status(exc: BaseException) -> int | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def _book_endpoint(slug: str) -> str:
    """Path only. No host and no query string."""
    path = str(slug or "").split("?", 1)[0].strip()
    return f"/v1/markets/{path}/book"


def _log_book_failure(series: str, slug: str, exc: BaseException) -> int | None:
    status = _http_status(exc)
    log.warning(
        "polymarket paper book failed series=%s status=%s endpoint=%s error_type=%s",
        series,
        status if status is not None else "none",
        _book_endpoint(slug),
        type(exc).__name__,
    )
    return status


def _read_book(client: Any, series: str, slug: str) -> Any | None:
    """One book read. A transient status gets one retry after a one-second wait."""
    try:
        return client.get_market_book(slug)
    except PolymarketOrderBlocked:
        raise
    except Exception as exc:
        status = _log_book_failure(series, slug, exc)
        if status not in _TRANSIENT_BOOK_STATUS:
            return None
    time.sleep(_BOOK_RETRY_SECONDS)
    try:
        return client.get_market_book(slug)
    except PolymarketOrderBlocked:
        raise
    except Exception as exc:
        _log_book_failure(series, slug, exc)
        return None


def load_polymarket_paper_markets(client: Any, entries: Sequence[Any]) -> list[dict[str, Any]]:
    """Public event and book reads. An order-path block propagates."""
    loaded: list[dict[str, Any]] = []
    for entry in entries:
        series = str(getattr(entry, "series", "") or "").strip()
        slug = str(getattr(entry, "venue_market", "") or "").strip()
        try:
            markets = client.list_event_markets(slug)
        except PolymarketOrderBlocked:
            raise
        except Exception as exc:
            log.warning(
                "polymarket paper list failed series=%s error_type=%s",
                series,
                type(exc).__name__,
            )
            continue
        ok = 0
        fail = 0
        for raw in markets:
            if not isinstance(raw, dict):
                continue
            market_slug = str(raw.get("slug") or "").strip()
            book = _read_book(client, series, market_slug)
            if book is None:
                fail += 1
                continue
            ok += 1
            view = paper_view(raw, series, book)
            if view is not None:
                loaded.append(view)
        log.info(
            "polymarket paper book series=%s ok=%s fail=%s",
            series,
            ok,
            fail,
        )
    return loaded
