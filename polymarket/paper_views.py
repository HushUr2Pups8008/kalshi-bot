"""Dicts the paper ranker already understands, built from public Polymarket reads."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from config import MARKET_SERIES_BLOCKLIST_PREFIXES
from polymarket.normalizer import _long_book_prices, _quote_cents
from polymarket.read_only import PolymarketOrderBlocked
from utils.event_news_grok_flags import EVENT_NEWS_BAND_FLOOR_CENTS
from utils.logger import get_logger

log = get_logger("polymarket_paper")

_OPEN = "MARKET_STATUS_OPEN"
_CERTAIN_ASK_CENTS = 91


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


def _blocked_name(name: str) -> bool:
    upper = str(name or "").split("?", 1)[0].strip().upper()
    return any(upper.startswith(prefix) for prefix in MARKET_SERIES_BLOCKLIST_PREFIXES)


def _list_asks(raw: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """List quotes only. A null top bid falls through to the side quote."""
    prices = _long_book_prices(dict(raw))
    if prices is not None:
        yes_ask, no_ask = prices[1], prices[3]
        return (yes_ask if type(yes_ask) is int else None, no_ask if type(no_ask) is int else None)
    yes_ask = None
    no_ask = None
    for side in raw.get("marketSides") or []:
        if not isinstance(side, dict):
            continue
        cents = _quote_cents(side.get("quote"))
        if side.get("long") is True:
            yes_ask = cents
        elif side.get("long") is False:
            no_ask = cents
    return yes_ask, no_ask


def _prefilter_reason(series: str, raw: Mapping[str, Any]) -> str | None:
    """Ask band, 91c gate, and the sports blocklist. A missing list ask fails closed."""
    if _blocked_name(series) or _blocked_name(str(raw.get("slug") or "")):
        return "blocklist"
    reasons: list[str] = []
    for ask in _list_asks(raw):
        if ask is None:
            continue
        if ask < EVENT_NEWS_BAND_FLOOR_CENTS:
            reasons.append("ask_band")
        elif ask >= _CERTAIN_ASK_CENTS:
            reasons.append("near_certain_ask")
        else:
            return None
    return reasons[0] if reasons else "no_list_ask"


def _book_read_cap(entry: Any) -> int:
    """Named slugs set the cap. A series with no slug list reads one book."""
    allowed = getattr(entry, "market_slugs", ()) or ()
    if isinstance(allowed, (tuple, list)) and len(allowed) > 0:
        return len(allowed)
    return 1


def _read_book(client: Any, series: str, slug: str) -> tuple[Any | None, int | None]:
    """One book read. The shared limiter already retried a 429."""
    try:
        return client.get_market_book(slug), None
    except PolymarketOrderBlocked:
        raise
    except Exception as exc:
        return None, _log_book_failure(series, slug, exc)


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
            log.warning("polymarket paper list failed series=%s error_type=%s", series, type(exc).__name__)
            continue
        ok = 0
        fail = 0
        reads = 0
        cap = _book_read_cap(entry)
        for raw in markets:
            if not isinstance(raw, dict):
                continue
            market_slug = str(raw.get("slug") or "").strip()
            reason = "market_slug" if (allowed := getattr(entry, "market_slugs", ())) and market_slug not in allowed else _prefilter_reason(series, raw)
            if reason is not None:
                log.info("polymarket paper skip series=%s reason=%s", series, reason)
                continue
            if reads >= cap:
                log.info("polymarket paper skip series=%s reason=book_cap", series)
                continue
            reads += 1
            book, status = _read_book(client, series, market_slug)
            if book is None:
                fail += 1
                if status == 429:
                    log.info("polymarket paper skip series=%s reason=book_rate_limited", series)
                continue
            view = paper_view(raw, series, book)
            if view is None or (view.get("yes_ask") is None and view.get("no_ask") is None):
                fail += 1
                log.info("polymarket paper skip series=%s reason=partial_book", series)
                continue
            ok += 1
            loaded.append(view)
        log.info("polymarket paper book series=%s ok=%s fail=%s", series, ok, fail)
    return loaded
