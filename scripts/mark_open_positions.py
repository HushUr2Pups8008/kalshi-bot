#!/usr/bin/env python3
"""Mark open paper positions to current market price (PROFIT-DRAWDOWN-001a).

The paper bankroll model deducts an open trade's full entry cost at placement,
so notional_bankroll understates true paper equity whenever open positions are
in the money. This tool fetches the CURRENT price for each unresolved position
(Kalshi via signed REST GET, Polymarket via the public client), values the held
side at its current market price, and reports true paper equity =
notional_bankroll + sum(current value of open positions).

READ-ONLY: issues only GET /markets/<ticker> (Kalshi) and the Polymarket public
get_market. Places no orders, writes nothing, mutates no state.

Usage:  python scripts/mark_open_positions.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Shared constant so the go-live gate's MTM marks and the report's trades can
# never silently read different DBs (PROFIT-DRAWDOWN-001c review note).
from utils.logger import get_logger  # noqa: E402
from utils.output_paths import PAPER_TRADES_DB as DB_PATH  # noqa: E402
from trading.fees import (  # noqa: E402
    DIRECT_ACCOUNT_PRECISION,
    KALSHI_GENERAL_2026_07_07,
    POLYMARKET_US_2026_07_01,
    FeeContext,
    FeeRole,
    FeeScheduleId,
    FeeUnscorableError,
    fee_coefficient_for,
    quote_fee,
)
from trading.venue import Venue, normalize_venue  # noqa: E402

log = get_logger("mark_open_positions")


class MarketProvider(Protocol):
    def get_market(self, venue: Venue, ticker: str): ...


def _replace_market_fields(market, **updates):
    try:
        return replace(market, **updates)
    except TypeError:
        return SimpleNamespace(**{**vars(market), **updates})


class PublicMarketProvider:
    """Lazy venue clients shared by every position in one report snapshot."""

    def __init__(self, *, as_of: datetime | None = None) -> None:
        self._kalshi = None
        self._polymarket = None
        self._as_of: datetime | None = None
        self._kalshi_event_terms: dict[str, object | None] = {}
        self._kalshi_series_fees: dict[
            str,
            tuple[Decimal | None, str | None, str | None, datetime | None],
        ] = {}
        if as_of is not None:
            self.bind_as_of(as_of)

    def bind_as_of(self, as_of: datetime) -> None:
        if as_of.tzinfo is None:
            raise ValueError("provider as_of must be timezone-aware")
        normalized = as_of.astimezone(timezone.utc)
        if self._as_of is not None and self._as_of != normalized:
            raise ValueError("provider cannot be reused across report timestamps")
        self._as_of = normalized

    def _with_kalshi_fee_provenance(self, market):
        market = _replace_market_fields(
            market,
            fee_multiplier=None,
            fee_type=None,
            fee_effective_at=None,
            fee_provenance_hash=None,
        )
        event_ticker = str(getattr(market, "event_ticker", None) or "").strip()
        if not event_ticker:
            return market
        if self._as_of is None:
            return market
        if event_ticker not in self._kalshi_event_terms:
            try:
                self._kalshi_event_terms[event_ticker] = (
                    self._kalshi.get_event_fee_terms(event_ticker, as_of=self._as_of)
                )
            except Exception as exc:
                log.warning(
                    "Kalshi event provenance unavailable event=%s error=%s",
                    event_ticker,
                    exc,
                )
                self._kalshi_event_terms[event_ticker] = None
        event_terms = self._kalshi_event_terms[event_ticker]
        if event_terms is None:
            return market
        series_ticker = str(getattr(event_terms, "series_ticker", "") or "").strip()
        if not series_ticker:
            return market
        if series_ticker not in self._kalshi_series_fees:
            metadata = self._kalshi.get_series(series_ticker)
            self._kalshi_series_fees[series_ticker] = (
                getattr(metadata, "fee_multiplier_decimal", None),
                str(getattr(metadata, "fee_type", None) or "").strip() or None,
                str(getattr(metadata, "raw_payload_hash", None) or "").strip() or None,
                getattr(metadata, "metadata_updated_at", None),
            )
        multiplier, fee_type, series_hash, series_updated_at = self._kalshi_series_fees[
            series_ticker
        ]
        if getattr(event_terms, "fee_multiplier_override", None) is not None:
            multiplier = event_terms.fee_multiplier_override
        if getattr(event_terms, "fee_type_override", None) is not None:
            fee_type = event_terms.fee_type_override
        event_hash = str(getattr(event_terms, "raw_payload_hash", None) or "").strip()
        if multiplier is None or not fee_type or not series_hash or not event_hash:
            return market
        provenance_hash = hashlib.sha256(
            f"{series_hash}:{event_hash}".encode("ascii")
        ).hexdigest()
        return _replace_market_fields(
            market,
            series_ticker=series_ticker,
            fee_multiplier=multiplier,
            fee_type=fee_type,
            fee_effective_at=(
                getattr(event_terms, "effective_at", None) or series_updated_at
            ),
            fee_provenance_hash=provenance_hash,
        )

    def get_market(self, venue: Venue, ticker: str):
        if venue is Venue.KALSHI:
            if self._kalshi is None:
                from kalshi.rest_client import KalshiRestClient

                self._kalshi = KalshiRestClient()
            market = self._kalshi.get_market(ticker)
            if market is None:
                return None
            market_identity_valid = (
                str(getattr(market, "ticker", "") or "").strip() == ticker
            )
            market = self._with_kalshi_fee_provenance(market)
            try:
                book = self._kalshi.get_market_orderbook(ticker, depth=100)
            except Exception as exc:
                log.warning("Kalshi book unavailable ticker=%s error=%s", ticker, exc)
                return _replace_market_fields(
                    market,
                    book_error=str(exc)[:200],
                    report_venue=(venue.value if market_identity_valid else None),
                    report_venue_market_id=(ticker if market_identity_valid else None),
                )
            if book.venue is not venue or book.venue_market_id != ticker:
                return _replace_market_fields(
                    market,
                    book_error="Kalshi book identity mismatch",
                    report_venue=None,
                    report_venue_market_id=None,
                )
            return _replace_market_fields(
                market,
                yes_bid_levels=tuple(
                    (level.price * Decimal("100"), level.quantity)
                    for level in book.yes_bids
                ),
                no_bid_levels=tuple(
                    (level.price * Decimal("100"), level.quantity)
                    for level in book.no_bids
                ),
                yes_bid_size=(book.yes_bids[0].quantity if book.yes_bids else None),
                no_bid_size=(book.no_bids[0].quantity if book.no_bids else None),
                book_as_of=book.as_of,
                book_payload_hash=book.raw_payload_hash,
                book_error=None,
                report_venue=(venue.value if market_identity_valid else None),
                report_venue_market_id=(ticker if market_identity_valid else None),
            )
        if venue is Venue.POLYMARKET_US:
            if self._polymarket is None:
                from polymarket.public_client import PolymarketPublicClient

                self._polymarket = PolymarketPublicClient()
            market = self._polymarket.get_market(ticker)
            if market is None:
                return None
            market_slug = str(getattr(market, "market_id", "") or "").strip()
            canonical_id = str(
                getattr(market, "venue_market_id", "") or ""
            ).strip()
            expected_market_id = canonical_id if ticker.isdigit() else market_slug
            market_identity_valid = (
                getattr(market, "venue", None) is venue
                and expected_market_id == ticker
            )
            try:
                book = self._polymarket.get_market_book(ticker)
            except Exception as exc:
                log.warning("Polymarket book unavailable ticker=%s error=%s", ticker, exc)
                return _replace_market_fields(
                    market,
                    book_error=str(exc)[:200],
                    report_venue=(venue.value if market_identity_valid else None),
                    report_venue_market_id=(ticker if market_identity_valid else None),
                )
            if book.venue is not venue or book.venue_market_id != market_slug:
                return _replace_market_fields(
                    market,
                    book_error="Polymarket book identity mismatch",
                    report_venue=None,
                    report_venue_market_id=None,
                )
            return _replace_market_fields(
                market,
                yes_bid_levels=tuple(
                    (level.price * Decimal("100"), level.quantity)
                    for level in book.yes_bids
                ),
                no_bid_levels=tuple(
                    (level.price * Decimal("100"), level.quantity)
                    for level in book.no_bids
                ),
                yes_bid_size=(book.yes_bids[0].quantity if book.yes_bids else None),
                no_bid_size=(book.no_bids[0].quantity if book.no_bids else None),
                book_as_of=book.as_of,
                book_payload_hash=book.raw_payload_hash,
                book_error=None,
                report_venue=(venue.value if market_identity_valid else None),
                report_venue_market_id=(ticker if market_identity_valid else None),
            )
        raise ValueError(f"unsupported venue: {venue!r}")


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _is_canonical_report_id(venue: Venue, venue_market_id: str) -> bool:
    if venue is Venue.KALSHI:
        return (
            venue_market_id.isascii()
            and venue_market_id.startswith("KX")
            and len(venue_market_id) > 2
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", venue_market_id)
            is not None
        )
    return venue_market_id.isascii() and venue_market_id.isdigit()


def _schedule_for_venue(venue: Venue) -> FeeScheduleId:
    if venue is Venue.KALSHI:
        return KALSHI_GENERAL_2026_07_07
    if venue is Venue.POLYMARKET_US:
        return POLYMARKET_US_2026_07_01
    raise FeeUnscorableError("unsupported venue")


def _schedule_descriptor(schedule: FeeScheduleId) -> dict:
    return {
        "name": schedule.name,
        "artifact_sha256": schedule.artifact_sha256,
        "supporting_artifact_sha256": list(schedule.supporting_artifact_sha256),
    }


def _unscorable_liquidation(
    *,
    venue: str,
    reason: str,
    as_of: str,
    gross_bid_cents: Decimal | None = None,
    gross_bid_value: Decimal = Decimal("0"),
    bid_depth: Decimal | None = None,
    fills: int = 0,
    schedule: FeeScheduleId | None = None,
) -> dict:
    return {
        "venue": venue,
        "gross_bid_cents": gross_bid_cents,
        "bid_depth": bid_depth,
        "fills": fills,
        "gross_bid_value": gross_bid_value,
        "estimated_exit_fee": Decimal("0"),
        "report_net_liquidation_value": Decimal("0"),
        "liquidation_status": "unscorable",
        "unscorable_reason": reason,
        "fee_schedule_name": schedule.name if schedule else None,
        "fee_schedule_hash": schedule.artifact_sha256 if schedule else None,
        "as_of": as_of,
    }


def _report_liquidation_quote(
    *,
    venue_text: str,
    venue_market_id: str,
    side: str,
    contracts: Decimal,
    market,
    fetch_error: str | None,
    as_of: datetime,
) -> dict:
    as_of_text = as_of.isoformat()
    try:
        venue = normalize_venue(venue_text)
    except ValueError:
        return _unscorable_liquidation(
            venue=venue_text or "unknown",
            reason="unsupported_venue",
            as_of=as_of_text,
        )
    schedule = _schedule_for_venue(venue)
    if not venue_market_id:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="missing_venue_market_id",
            as_of=as_of_text,
            schedule=schedule,
        )
    if not _is_canonical_report_id(venue, venue_market_id):
        return _unscorable_liquidation(
            venue=venue.value,
            reason="invalid_venue_market_id",
            as_of=as_of_text,
            schedule=schedule,
        )
    if fetch_error:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="fetch_error",
            as_of=as_of_text,
            schedule=schedule,
        )
    if market is None:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="market_unavailable",
            as_of=as_of_text,
            schedule=schedule,
        )
    market_report_venue = str(
        getattr(market, "report_venue", None) or ""
    ).strip()
    market_report_id = str(
        getattr(market, "report_venue_market_id", None) or ""
    ).strip()
    if market_report_venue != venue.value or market_report_id != venue_market_id:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="identity_mismatch",
            as_of=as_of_text,
            schedule=schedule,
        )
    if side not in {"yes", "no"}:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="unsupported_side",
            as_of=as_of_text,
            schedule=schedule,
        )
    if contracts <= 0:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="invalid_quantity",
            as_of=as_of_text,
            schedule=schedule,
        )

    if getattr(market, "book_error", None):
        return _unscorable_liquidation(
            venue=venue.value,
            reason="book_fetch_error",
            as_of=as_of_text,
            schedule=schedule,
        )

    raw_levels = getattr(market, f"{side}_bid_levels", None)
    bid_levels: list[tuple[Decimal, Decimal]] = []
    if raw_levels is None:
        bid_cents = _decimal(getattr(market, f"{side}_bid_cents", None))
        bid_size = _decimal(getattr(market, f"{side}_bid_size", None))
        if bid_cents is not None and bid_size is None:
            gross_value = contracts * bid_cents / Decimal("100")
            return _unscorable_liquidation(
                venue=venue.value,
                reason="missing_bid_depth",
                as_of=as_of_text,
                gross_bid_cents=bid_cents,
                gross_bid_value=gross_value,
                schedule=schedule,
            )
        if bid_cents is not None and bid_size is not None:
            bid_levels.append((bid_cents, bid_size))
    else:
        for raw_level in raw_levels:
            if not isinstance(raw_level, (list, tuple)) or len(raw_level) != 2:
                return _unscorable_liquidation(
                    venue=venue.value,
                    reason="malformed_bid_depth",
                    as_of=as_of_text,
                    schedule=schedule,
                )
            level_price = _decimal(raw_level[0])
            level_quantity = _decimal(raw_level[1])
            if (
                level_price is None
                or level_price <= 0
                or level_price >= 100
                or level_quantity is None
                or level_quantity <= 0
            ):
                return _unscorable_liquidation(
                    venue=venue.value,
                    reason="malformed_bid_depth",
                    as_of=as_of_text,
                    schedule=schedule,
                )
            bid_levels.append((level_price, level_quantity))
        if any(
            left_price < right_price
            for (left_price, _), (right_price, _) in zip(
                bid_levels, bid_levels[1:]
            )
        ):
            return _unscorable_liquidation(
                venue=venue.value,
                reason="malformed_bid_depth",
                as_of=as_of_text,
                schedule=schedule,
            )

    if not bid_levels:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="missing_held_side_bid",
            as_of=as_of_text,
            schedule=schedule,
        )

    bid_cents = bid_levels[0][0]
    bid_depth = sum((quantity for _, quantity in bid_levels), Decimal("0"))
    remaining = contracts
    fills: list[tuple[Decimal, Decimal]] = []
    gross_value = Decimal("0")
    for level_price, level_quantity in bid_levels:
        if remaining <= 0:
            break
        fill_quantity = min(remaining, level_quantity)
        fills.append((fill_quantity, level_price))
        gross_value += fill_quantity * level_price / Decimal("100")
        remaining -= fill_quantity
    if remaining > 0:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="insufficient_bid_depth",
            as_of=as_of_text,
            gross_bid_cents=bid_cents,
            gross_bid_value=gross_value,
            bid_depth=bid_depth,
            fills=len(fills),
            schedule=schedule,
        )

    coefficient = fee_coefficient_for(schedule, FeeRole.TAKER)
    if venue is Venue.KALSHI:
        multiplier = _decimal(getattr(market, "fee_multiplier", None))
        fee_type = str(getattr(market, "fee_type", None) or "").strip().lower()
        if multiplier is None or not fee_type:
            return _unscorable_liquidation(
                venue=venue.value,
                reason="missing_fee_provenance",
                as_of=as_of_text,
                gross_bid_cents=bid_cents,
                gross_bid_value=gross_value,
                bid_depth=bid_depth,
                fills=len(fills),
                schedule=schedule,
            )
        fee_effective_at = getattr(market, "fee_effective_at", None)
        provenance_hash = str(
            getattr(market, "fee_provenance_hash", None) or ""
        ).strip()
        provenance_is_sha256 = len(provenance_hash) == 64 and all(
            character in "0123456789abcdefABCDEF" for character in provenance_hash
        )
        invalid_effective_at = (
            not isinstance(fee_effective_at, datetime)
            or fee_effective_at.tzinfo is None
            or fee_effective_at.astimezone(timezone.utc) > as_of
            or fee_effective_at.astimezone(timezone.utc)
            < schedule.effective_from.astimezone(timezone.utc)
            or (
                schedule.effective_to is not None
                and fee_effective_at.astimezone(timezone.utc)
                >= schedule.effective_to.astimezone(timezone.utc)
            )
        )
        if not provenance_is_sha256 or invalid_effective_at:
            return _unscorable_liquidation(
                venue=venue.value,
                reason="fee_schedule_mismatch",
                as_of=as_of_text,
                gross_bid_cents=bid_cents,
                gross_bid_value=gross_value,
                bid_depth=bid_depth,
                fills=len(fills),
                schedule=schedule,
            )
        if fee_type != "quadratic":
            return _unscorable_liquidation(
                venue=venue.value,
                reason="unsupported_fee_type",
                as_of=as_of_text,
                gross_bid_cents=bid_cents,
                gross_bid_value=gross_value,
                bid_depth=bid_depth,
                fills=len(fills),
                schedule=schedule,
            )
        account_precision = DIRECT_ACCOUNT_PRECISION
    else:
        multiplier = Decimal("1")
        market_coefficient = _decimal(getattr(market, "fee_coefficient", None))
        if market_coefficient is None:
            return _unscorable_liquidation(
                venue=venue.value,
                reason="missing_fee_provenance",
                as_of=as_of_text,
                gross_bid_cents=bid_cents,
                gross_bid_value=gross_value,
                bid_depth=bid_depth,
                fills=len(fills),
                schedule=schedule,
            )
        fee_effective_at = getattr(market, "fee_effective_at", None)
        expected_coefficient = fee_coefficient_for(schedule, FeeRole.TAKER)
        invalid_effective_at = fee_effective_at is not None and (
            not isinstance(fee_effective_at, datetime)
            or fee_effective_at.tzinfo is None
            or fee_effective_at.astimezone(timezone.utc) > as_of
            or fee_effective_at.astimezone(timezone.utc)
            < schedule.effective_from.astimezone(timezone.utc)
            or (
                schedule.effective_to is not None
                and fee_effective_at.astimezone(timezone.utc)
                >= schedule.effective_to.astimezone(timezone.utc)
            )
        )
        if market_coefficient != expected_coefficient or invalid_effective_at:
            return _unscorable_liquidation(
                venue=venue.value,
                reason="fee_schedule_mismatch",
                as_of=as_of_text,
                gross_bid_cents=bid_cents,
                gross_bid_value=gross_value,
                bid_depth=bid_depth,
                fills=len(fills),
                schedule=schedule,
            )
        coefficient = market_coefficient
        account_precision = None

    total_fee = Decimal("0")
    accumulator = Decimal("0")
    order_id = f"report:{venue.value}:{venue_market_id}:{as_of.isoformat()}"
    try:
        for fill_quantity, fill_cents in fills:
            fill_revenue = fill_quantity * fill_cents / Decimal("100")
            quote = quote_fee(
                FeeContext(
                    schedule_id=schedule,
                    role=FeeRole.TAKER,
                    quantity=fill_quantity,
                    price=fill_cents / Decimal("100"),
                    signed_revenue=fill_revenue,
                    order_id=order_id,
                    accumulator=accumulator,
                    multiplier=multiplier,
                    coefficient=coefficient,
                    account_precision=account_precision,
                    timestamp=as_of,
                )
            )
            total_fee += quote.net_fee
            accumulator = quote.next_accumulator
    except FeeUnscorableError:
        return _unscorable_liquidation(
            venue=venue.value,
            reason="fee_quote_unscorable",
            as_of=as_of_text,
            gross_bid_cents=bid_cents,
            gross_bid_value=gross_value,
            bid_depth=bid_depth,
            fills=len(fills),
            schedule=schedule,
        )

    return {
        "venue": venue.value,
        "gross_bid_cents": bid_cents,
        "bid_depth": bid_depth,
        "fills": len(fills),
        "gross_bid_value": gross_value,
        "estimated_exit_fee": total_fee,
        "report_net_liquidation_value": gross_value - total_fee,
        "liquidation_status": "scorable",
        "unscorable_reason": None,
        "fee_schedule_name": schedule.name,
        "fee_schedule_hash": schedule.artifact_sha256,
        "as_of": as_of_text,
    }


def _kalshi_held_price_cents(market, side: str):
    """Current price (cents) of the held side. Mid when both bid/ask exist,
    else bid, else ask, else last (last is YES-referenced).

    The ``last`` fallback only counts when strictly inside (0, 100): an
    untraded/settling book reports last=0, and a held NO would otherwise be
    valued at 100-0 = full contract value — overstating equity exactly when
    the order book is emptiest. Out-of-band last -> unpriced ($0 to the
    go-live gate's MTM equity; PROFIT-DRAWDOWN-001c review finding)."""
    try:
        if side == "yes":
            bid, ask = market.yes_bid_cents, market.yes_ask_cents
        else:
            bid, ask = market.no_bid_cents, market.no_ask_cents
    except Exception:
        bid = ask = None
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    if bid is not None:
        return float(bid)
    if ask is not None:
        return float(ask)
    last = getattr(market, "last_price_cents", None)
    if last is not None and 0 < last < 100:
        return float(last) if side == "yes" else float(100 - last)
    return None


def _poly_held_price_cents(market, side: str):
    """Conservative mark for the held side (Polymarket exposes asks only).

    The held side's own ask is what a BUYER pays — liquidation value is
    bid-side. In a binary market the bid-equivalent is ``100 - opposite ask``
    (selling your side == buying the opposite at its ask). Marking at the held
    ask overstates equity by up to the full spread, which fed the go-live
    gate's MTM drawdown optimistically (PROFIT-DRAWDOWN-001c review finding).
    Use min(held ask, 100 - opposite ask) when both quotes exist; else
    whichever exists."""
    held = market.yes_ask_cents if side == "yes" else market.no_ask_cents
    opposite = market.no_ask_cents if side == "yes" else market.yes_ask_cents
    bid_equiv = (100 - opposite) if opposite is not None else None
    if held is not None and bid_equiv is not None:
        return min(float(held), float(bid_equiv))
    if bid_equiv is not None:
        return float(bid_equiv)
    return float(held) if held is not None else None


def _poly_snapshot_mark_cents(market_snapshot, side: str):
    """FIX-2 (PM feed-drop MTM-blind mitigation): last-known mark for a held
    Polymarket side from the entry-time ``market_snapshot`` JSON, used ONLY when
    the live market is transiently unpriceable.

    During the ~3-11h 2026-06-17 election-category feed drop, every held PM
    position's slug returned 'market not found', so the live mark was None and
    MTM equity read $0 for each -- distorting true paper equity / the go-live
    gate's drawdown criterion. The snapshot already persists both asks at trade
    time (PolymarketMarket.yes_ask_cents/no_ask_cents via
    paper_trader._market_to_jsonable), so the fallback needs ZERO new API calls
    and reuses the audited conservative ``_poly_held_price_cents`` mark.

    MEASUREMENT-ONLY: this feeds the daily report's MTM equity (and the go-live
    gate's drawdown read) and nothing else -- it touches no sizing/gating/EV
    input. Direction caveat: a stale ask can be richer than the current market
    and slightly OVERSTATE equity during a gap (the go-live gate's optimistic
    direction), but it is strictly better than the $0 it replaces (which
    UNDERSTATES) and the row is clearly labeled stale so the report distinguishes
    live vs snapshot marks.

    FAIL-SAFE: any missing/malformed snapshot returns None (-> the existing
    unknown_cost/$0 path), never raises into the read-only report path.
    """
    try:
        snap = json.loads(market_snapshot) if isinstance(market_snapshot, str) else None
        if not isinstance(snap, dict):
            return None
        safe_methods = {
            "pm_long_book_v1",
            "pm_named_sides_v1",
            "pm_named_outcomes_v1",
        }
        if snap.get("price_method") not in safe_methods:
            return None
        yes_ask = snap.get("yes_ask_cents")
        no_ask = snap.get("no_ask_cents")
        if yes_ask is None and no_ask is None:
            return None
        snap_obj = SimpleNamespace(yes_ask_cents=yes_ask, no_ask_cents=no_ask)
        return _poly_held_price_cents(snap_obj, side)
    except Exception as exc:
        log.warning("snapshot-fallback mark unavailable for held PM side: %s", exc)
        return None


def compute_open_position_marks(
    db_path: Path = DB_PATH,
    *,
    provider: MarketProvider | None = None,
    as_of: datetime | None = None,
) -> dict | None:
    """Price every unresolved position at current market and return a summary.

    Importable core of this tool (PROFIT-DRAWDOWN-001c): the daily performance
    report's go-live section consumes this so its drawdown criterion reflects
    TRUE paper equity (bankroll + current value of open positions) instead of
    the notional bankroll, which deducts each open trade's full entry cost at
    placement and therefore overstates drawdown whenever positions retain value.

    Read-only (GETs only; writes nothing). Returns None when the DB is missing.
    Per-position fetch failures are non-fatal: the position lands in
    ``unknown_cost`` / ``unpriced_count`` and is worth $0 in ``marked_value`` —
    callers that gate on equity stay conservative (unpriced == worthless).

    Returns dict: bankroll, total_cost, marked_value, unknown_cost,
    priced_count, unpriced_count, rows (per-position render dicts).
    """
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        state = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM bot_state")}
    except sqlite3.OperationalError:
        state = {}
    try:
        bankroll = float(state.get("notional_bankroll", 0) or 0)
    except (TypeError, ValueError):
        bankroll = 0.0
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    identity_select = (
        "venue_market_id" if "venue_market_id" in columns else "NULL AS venue_market_id"
    )
    rows = conn.execute(
        "SELECT ticker, venue, side, contracts, cost_dollars, market_snapshot, "
        f"{identity_select} "
        "FROM paper_trades WHERE resolved = 0 ORDER BY venue, ticker"
    ).fetchall()
    conn.close()

    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    if provider is None:
        provider = PublicMarketProvider(as_of=as_of)
    elif isinstance(provider, PublicMarketProvider):
        provider.bind_as_of(as_of)
    marked_cv = 0.0
    unknown_cost = 0.0
    total_cost = 0.0
    priced_count = 0
    unpriced_count = 0
    snapshot_fallback_count = 0
    gross_bid_value = Decimal("0")
    estimated_exit_fees = Decimal("0")
    report_net_value = Decimal("0")
    scorable_cost = Decimal("0")
    unscorable_cost = Decimal("0")
    unscorable_reasons: Counter[str] = Counter()
    fee_schedule_hashes: dict[str, dict] = {}
    by_venue: dict[str, dict] = {}
    out_rows: list[dict] = []

    for r in rows:
        ticker = r["ticker"]
        venue = (r["venue"] or "").lower()
        side = (r["side"] or "").lower()
        contracts = r["contracts"] or 0
        cost = float(r["cost_dollars"] or 0)
        total_cost += cost
        cents = None
        note = ""
        market = None
        fetch_error = None
        legacy_venue = (
            Venue.KALSHI
            if venue.startswith("kalshi") or ticker.startswith("KX")
            else Venue.POLYMARKET_US
        )
        try:
            market = provider.get_market(legacy_venue, ticker)
            if legacy_venue is Venue.KALSHI:
                cents = _kalshi_held_price_cents(market, side) if market else None
                if market is None:
                    note = "no market (closed/settled?)"
            else:
                cents = _poly_held_price_cents(market, side) if market else None
        except Exception as exc:  # pragma: no cover - live API surface
            note = "fetch error: %s" % str(exc)[:50]
            fetch_error = str(exc)

        canonical_id = str(r["venue_market_id"] or "").strip()
        report_market = market
        report_fetch_error = fetch_error
        try:
            canonical_venue = normalize_venue(venue)
        except ValueError:
            canonical_venue = None
        if (
            canonical_venue is not None
            and _is_canonical_report_id(canonical_venue, canonical_id)
            and (canonical_venue is not legacy_venue or canonical_id != ticker)
        ):
            report_market = None
            report_fetch_error = None
            try:
                report_market = provider.get_market(canonical_venue, canonical_id)
            except Exception as exc:  # pragma: no cover - live API surface
                report_fetch_error = str(exc)

        # FIX-2: PM-only, fallback-only. When the live mark is unavailable
        # (market transiently dropped from the feed, or a fetch error), recover
        # the last-known mark from the entry-time snapshot so MTM equity is not
        # blinded to $0 during a gap. A successful live mark always wins (we only
        # enter here when cents is None). Measurement-only; no trade decision.
        is_poly = legacy_venue is Venue.POLYMARKET_US
        if is_poly and cents is None:
            snapshot_cents = _poly_snapshot_mark_cents(r["market_snapshot"], side)
            if snapshot_cents is not None:
                cents = snapshot_cents
                note = "stale: last-known snapshot price (live market absent)"
                snapshot_fallback_count += 1

        value = None
        if cents is None:
            unknown_cost += cost
            unpriced_count += 1
        else:
            value = contracts * cents / 100.0
            marked_cv += value
            priced_count += 1
        report_quote = _report_liquidation_quote(
            venue_text=venue,
            venue_market_id=canonical_id,
            side=side,
            contracts=_decimal(contracts) or Decimal("0"),
            market=report_market,
            fetch_error=report_fetch_error,
            as_of=as_of,
        )
        book_as_of = getattr(report_market, "book_as_of", None)
        report_quote["book_as_of"] = (
            book_as_of.isoformat() if isinstance(book_as_of, datetime) else None
        )
        report_quote["book_payload_hash"] = getattr(
            report_market, "book_payload_hash", None
        )
        report_quote["fee_provenance_hash"] = (
            getattr(report_market, "fee_provenance_hash", None)
            or getattr(report_market, "source_payload_hash", None)
            or getattr(report_market, "raw_payload_hash", None)
        )
        report_venue = report_quote["venue"]
        venue_summary = by_venue.setdefault(
            report_venue,
            {
                "venue": report_venue,
                "open_cost": Decimal("0"),
                "scorable_cost": Decimal("0"),
                "gross_bid_value": Decimal("0"),
                "estimated_exit_fees": Decimal("0"),
                "report_net_liquidation_value": Decimal("0"),
                "unscorable_cost": Decimal("0"),
                "unscorable_reasons": Counter(),
            },
        )
        decimal_cost = _decimal(cost) or Decimal("0")
        venue_summary["open_cost"] += decimal_cost
        gross_bid_value += report_quote["gross_bid_value"]
        venue_summary["gross_bid_value"] += report_quote["gross_bid_value"]
        schedule_name = report_quote["fee_schedule_name"]
        schedule_hash = report_quote["fee_schedule_hash"]
        if schedule_name and schedule_hash:
            fee_schedule_hashes[report_venue] = _schedule_descriptor(
                _schedule_for_venue(normalize_venue(report_venue))
            )
        if report_quote["liquidation_status"] == "scorable":
            scorable_cost += decimal_cost
            estimated_exit_fees += report_quote["estimated_exit_fee"]
            report_net_value += report_quote["report_net_liquidation_value"]
            venue_summary["scorable_cost"] += decimal_cost
            venue_summary["estimated_exit_fees"] += report_quote[
                "estimated_exit_fee"
            ]
            venue_summary["report_net_liquidation_value"] += report_quote[
                "report_net_liquidation_value"
            ]
        else:
            reason = report_quote["unscorable_reason"]
            unscorable_cost += decimal_cost
            unscorable_reasons[reason] += 1
            venue_summary["unscorable_cost"] += decimal_cost
            venue_summary["unscorable_reasons"][reason] += 1
        out_rows.append({
            "ticker": ticker,
            "venue": venue,
            "side": side,
            "contracts": contracts,
            "cost": cost,
            "cents": cents,
            "value": value,
            "note": note,
            **report_quote,
        })

    by_venue_rows = []
    for venue_summary in by_venue.values():
        venue_summary["unrealized_fee_net_pnl"] = (
            venue_summary["report_net_liquidation_value"]
            - venue_summary["scorable_cost"]
        )
        venue_summary["unscorable_reasons"] = dict(
            sorted(venue_summary["unscorable_reasons"].items())
        )
        by_venue_rows.append(venue_summary)

    return {
        "bankroll": bankroll,
        "total_cost": total_cost,
        "marked_value": marked_cv,
        "unknown_cost": unknown_cost,
        "priced_count": priced_count,
        "unpriced_count": unpriced_count,
        # Surfaces how many priced positions used a STALE entry-time snapshot
        # rather than a live mark (live market transiently absent). Without this
        # an all-stale run looks like a normal fully-priced run; callers/reports
        # should flag a non-zero value as a degraded-equity reading.
        "snapshot_fallback_count": snapshot_fallback_count,
        "gross_bid_value": gross_bid_value,
        "estimated_exit_fees": estimated_exit_fees,
        "report_net_liquidation_value": report_net_value,
        "unrealized_fee_net_pnl": report_net_value - scorable_cost,
        "unscorable_cost": unscorable_cost,
        "unscorable_reasons": dict(sorted(unscorable_reasons.items())),
        "fee_schedule_hashes": fee_schedule_hashes,
        "as_of": as_of.isoformat(),
        "by_venue": sorted(by_venue_rows, key=lambda item: item["venue"]),
        "rows": out_rows,
    }


def main() -> int:
    marks = compute_open_position_marks()
    if marks is None:
        print("No database at %s" % DB_PATH)
        return 1
    bankroll = marks["bankroll"]
    total_cost = marks["total_cost"]
    marked_cv = marks["marked_value"]
    unknown_cost = marks["unknown_cost"]

    lines = ["Open-position mark-to-market (read-only)", ""]
    lines.append("%-38s %-11s %-4s %4s %8s %8s %9s"
                 % ("Ticker", "Venue", "Side", "Ct", "Cost$", "Px(c)", "Value$"))
    lines.append("-" * 92)
    for row in marks["rows"]:
        px_s = "--" if row["cents"] is None else "%.1f" % row["cents"]
        cv_s = "??" if row["value"] is None else "%.2f" % row["value"]
        note = row["note"]
        lines.append("%-38s %-11s %-4s %4d %8.2f %8s %9s%s"
                     % (row["ticker"][:38], row["venue"][:11], row["side"],
                        row["contracts"], row["cost"], px_s, cv_s,
                        ("  " + note) if note else ""))

    lines.append("")
    lines.append("Notional bankroll (open cost already deducted): $%.2f" % bankroll)
    lines.append("Open positions total entry cost              : $%.2f" % total_cost)
    lines.append("Marked current value (priced positions)       : $%.2f" % marked_cv)
    if unknown_cost > 0:
        lo = bankroll + marked_cv
        hi = bankroll + marked_cv + unknown_cost
        lines.append("Unpriced open cost (value unknown)            : $%.2f" % unknown_cost)
        lines.append("")
        lines.append("TRUE PAPER EQUITY (range): $%.2f .. $%.2f" % (lo, hi))
        lines.append("  (low = unpriced positions worthless; high = unpriced worth entry cost)")
    else:
        lines.append("")
        lines.append("TRUE PAPER EQUITY: $%.2f" % (bankroll + marked_cv))
    lines.append("")
    lines.append("vs $50.00 start -> true drawdown: %.1f%% .. %.1f%%"
                 % ((1 - (bankroll + marked_cv + unknown_cost) / 50.0) * 100,
                    (1 - (bankroll + marked_cv) / 50.0) * 100))
    lines.extend(
        [
            "",
            "Report-only fee-net executable liquidation (not a G7 input)",
            "As of                                      : %s" % marks["as_of"],
            "Gross executable bid value                 : $%.2f"
            % marks["gross_bid_value"],
            "Estimated exit fees                        : $%.2f"
            % marks["estimated_exit_fees"],
            "Fee-net liquidation value                  : $%.2f"
            % marks["report_net_liquidation_value"],
            "Unscorable open cost                       : $%.2f"
            % marks["unscorable_cost"],
        ]
    )
    if marks["unscorable_reasons"]:
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in marks["unscorable_reasons"].items()
        )
        lines.append("Unscorable reasons                         : %s" % reasons)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
