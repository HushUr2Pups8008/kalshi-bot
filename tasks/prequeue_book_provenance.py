"""Read-only binary-book provenance collected immediately before queueing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, Protocol

from analysis import SignalAnalysis
from kalshi import KalshiMarket
from trading.orderbook import BinaryMarketBook
from trading.venue import Venue


class KalshiPrequeueBookReader(Protocol):
    """Minimal read-only Kalshi book client used by provenance collection."""

    def get_market_orderbook(self, ticker: str, *, depth: int = 100) -> BinaryMarketBook: ...


class PolymarketPrequeueBookReader(Protocol):
    """Minimal read-only Polymarket book client used by provenance collection."""

    def get_market_payload(self, market_id: str) -> Mapping[str, object]: ...

    def get_market_book(self, market_id: str) -> BinaryMarketBook: ...


@dataclass(frozen=True)
class PrequeueBookProvenanceResult:
    """Book identity and source facts attached to an already-approved candidate."""

    status: Literal["available", "unavailable"]
    venue: str | None
    requested_market_id: str | None
    native_market_id: str | None
    book_market_id: str | None
    book_observed_at: datetime | None
    book_payload_hash: str | None
    reason: str | None = None

    def as_metadata(self) -> dict[str, object]:
        """Return a JSON-safe representation for ``TradeCandidate.signal_meta``."""
        return {
            "status": self.status,
            "venue": self.venue,
            "requested_market_id": self.requested_market_id,
            "native_market_id": self.native_market_id,
            "book_market_id": self.book_market_id,
            "book_observed_at": (
                self.book_observed_at.isoformat() if self.book_observed_at is not None else None
            ),
            "book_payload_hash": self.book_payload_hash,
            "reason": self.reason,
        }


def unavailable_prequeue_book_provenance(
    analysis: SignalAnalysis,
    *,
    reason: str,
) -> PrequeueBookProvenanceResult:
    """Describe why no provenance was available without changing trade eligibility."""
    venue, requested_market_id, native_market_id = _request_identity(analysis)
    return _unavailable(
        venue=venue,
        requested_market_id=requested_market_id,
        native_market_id=native_market_id,
        reason=reason,
    )


async def fetch_prequeue_book_provenance(
    analysis: SignalAnalysis,
    *,
    kalshi_reader: KalshiPrequeueBookReader | None = None,
    polymarket_reader: PolymarketPrequeueBookReader | None = None,
) -> PrequeueBookProvenanceResult:
    """Fetch a single venue book without changing readiness, sizing, or orders."""
    venue, requested_market_id, native_market_id = _request_identity(analysis)

    if venue is Venue.KALSHI:
        if requested_market_id is None:
            return _unavailable(
                venue=venue,
                requested_market_id=None,
                native_market_id=None,
                reason="missing_kalshi_ticker",
            )
        if kalshi_reader is None:
            return _unavailable(
                venue=venue,
                requested_market_id=requested_market_id,
                native_market_id=native_market_id,
                reason="kalshi_reader_unavailable",
            )
        try:
            book = await asyncio.to_thread(
                kalshi_reader.get_market_orderbook,
                requested_market_id,
                depth=100,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _unavailable(
                venue=venue,
                requested_market_id=requested_market_id,
                native_market_id=native_market_id,
                reason=f"reader_error:{type(exc).__name__}",
            )
        if not _is_kalshi_book_for(book, requested_market_id):
            return _unavailable(
                venue=venue,
                requested_market_id=requested_market_id,
                native_market_id=native_market_id,
                reason="kalshi_book_identity_mismatch",
            )
        return _available(
            venue=venue,
            requested_market_id=requested_market_id,
            native_market_id=native_market_id,
            book=book,
        )

    if venue is Venue.POLYMARKET_US:
        if requested_market_id is None:
            return _unavailable(
                venue=venue,
                requested_market_id=None,
                native_market_id=native_market_id,
                reason="missing_polymarket_market_identity",
            )
        if polymarket_reader is None:
            return _unavailable(
                venue=venue,
                requested_market_id=requested_market_id,
                native_market_id=native_market_id,
                reason="polymarket_reader_unavailable",
            )
        try:
            expected_book_market_id = (
                await _polymarket_canonical_slug_for(polymarket_reader, native_market_id)
                if native_market_id is not None
                else requested_market_id
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _unavailable(
                venue=venue,
                requested_market_id=requested_market_id,
                native_market_id=native_market_id,
                reason=f"reader_error:{type(exc).__name__}",
            )
        if expected_book_market_id is None:
            return _unavailable(
                venue=venue,
                requested_market_id=requested_market_id,
                native_market_id=native_market_id,
                reason="polymarket_native_identity_mismatch",
            )
        try:
            book = await asyncio.to_thread(
                polymarket_reader.get_market_book,
                expected_book_market_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _unavailable(
                venue=venue,
                requested_market_id=requested_market_id,
                native_market_id=native_market_id,
                reason=f"reader_error:{type(exc).__name__}",
            )
        if not _is_polymarket_book_for(book, expected_book_market_id):
            return _unavailable(
                venue=venue,
                requested_market_id=requested_market_id,
                native_market_id=native_market_id,
                reason="polymarket_book_identity_mismatch",
            )
        return _available(
            venue=venue,
            requested_market_id=requested_market_id,
            native_market_id=native_market_id,
            book=book,
        )

    return _unavailable(
        venue=venue,
        requested_market_id=requested_market_id,
        native_market_id=native_market_id,
        reason="unsupported_venue",
    )


def _request_identity(analysis: SignalAnalysis) -> tuple[Venue | None, str | None, str | None]:
    market = analysis.market
    venue = _venue_for_market(market)
    ticker = _optional_text(getattr(market, "ticker", None))
    venue_market_id = _optional_text(getattr(market, "venue_market_id", None))

    if venue is Venue.KALSHI:
        return venue, ticker, ticker
    if venue is Venue.POLYMARKET_US:
        native_market_id = venue_market_id if _is_numeric_market_id(venue_market_id) else None
        return venue, native_market_id or ticker or venue_market_id, native_market_id
    return venue, ticker, venue_market_id


def _venue_for_market(market: object) -> Venue | None:
    venue = getattr(market, "venue", None)
    if isinstance(venue, Venue):
        return venue
    if venue is not None:
        try:
            return Venue(str(venue).strip().lower())
        except ValueError:
            return None
    if isinstance(market, KalshiMarket):
        return Venue.KALSHI
    return None


def _optional_text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _is_numeric_market_id(value: str | None) -> bool:
    return value is not None and value.isascii() and value.isdecimal()


def _is_kalshi_book_for(book: object, ticker: str) -> bool:
    return (
        isinstance(book, BinaryMarketBook)
        and book.venue is Venue.KALSHI
        and book.venue_market_id == ticker
    )


async def _polymarket_canonical_slug_for(
    reader: PolymarketPrequeueBookReader,
    native_market_id: str,
) -> str | None:
    resolver = getattr(reader, "get_market_payload", None)
    if not callable(resolver):
        return None
    payload = await asyncio.to_thread(resolver, native_market_id)
    if not isinstance(payload, Mapping):
        return None
    returned_native_market_id = _optional_text(payload.get("id"))
    canonical_slug = _optional_text(payload.get("slug"))
    if returned_native_market_id != native_market_id:
        return None
    return canonical_slug


def _is_polymarket_book_for(book: object, expected_market_id: str) -> bool:
    return (
        isinstance(book, BinaryMarketBook)
        and book.venue is Venue.POLYMARKET_US
        and book.venue_market_id == expected_market_id
    )


def _available(
    *,
    venue: Venue,
    requested_market_id: str,
    native_market_id: str | None,
    book: BinaryMarketBook,
) -> PrequeueBookProvenanceResult:
    return PrequeueBookProvenanceResult(
        status="available",
        venue=venue.value,
        requested_market_id=requested_market_id,
        native_market_id=native_market_id,
        book_market_id=book.venue_market_id,
        book_observed_at=book.as_of,
        book_payload_hash=book.raw_payload_hash,
    )


def _unavailable(
    *,
    venue: Venue | None,
    requested_market_id: str | None,
    native_market_id: str | None,
    reason: str,
) -> PrequeueBookProvenanceResult:
    return PrequeueBookProvenanceResult(
        status="unavailable",
        venue=venue.value if venue is not None else None,
        requested_market_id=requested_market_id,
        native_market_id=native_market_id,
        book_market_id=None,
        book_observed_at=None,
        book_payload_hash=None,
        reason=reason,
    )
