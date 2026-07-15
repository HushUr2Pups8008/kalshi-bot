from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from kalshi.settlement import normalize_kalshi_settlement
from polymarket.settlement import normalize_polymarket_settlement
from trading.settlement import SettlementDriftError, SettlementObservation
from trading.venue import MarketRef, Venue
from utils.logger import get_logger

log = get_logger("polymarket_settlement")


class SettlementNotFound(Exception):
    """Raised by a settlement source when the market has not settled yet."""


class SettlementSource(Protocol):
    def get_settlement(self, market_id: str) -> Mapping[str, Any] | None:
        """Return a settlement payload or raise SettlementNotFound."""


class SettlementResolver(Protocol):
    _conn: sqlite3.Connection

    def _resolve_market_sync(
        self, ticker: str, resolved_yes: bool
    ) -> list[tuple[str, str, float]]:
        """Resolve open paper trades using PaperTrader atomicity semantics."""


class PersistedSettlementSource(Protocol):
    def get_settlement(
        self,
        market_ref: MarketRef,
    ) -> SettlementObservation | None:
        """Return an exact canonical observation or report no settlement."""


class PersistedPositionResolver(Protocol):
    def mapped_open_market_refs(self) -> tuple[MarketRef, ...]:
        """Return exact mapped identities for every unresolved persisted position."""

    def resolve_observation(self, observation: SettlementObservation) -> bool:
        """Apply one canonical observation atomically."""


class KalshiSettlementSource(Protocol):
    def get_market(self, market_id: str) -> Any | None:
        """Return one market by exact canonical ticker."""


class PolymarketPublicSettlementSource:
    def __init__(self, client: Any | None = None):
        if client is None:
            from polymarket.public_client import PolymarketPublicClient

            client = PolymarketPublicClient()
        self._client = client

    def get_settlement(self, market_id: str) -> Mapping[str, Any] | None:
        try:
            payload = self._client.get_market_settlement(market_id)
        except ValueError as exc:
            # The dedicated endpoint client raises a narrow ValueError containing
            # "not found" only for an HTTP 404.
            # Honor this source's documented contract: translate ONLY the
            # not-found case into SettlementNotFound so it flows through
            # reconcile()'s per-ticker not_found path instead of aborting the
            # whole remaining Polymarket batch for the cycle. Re-raise any other
            # ValueError (a real defect, e.g. malformed payload shape) untouched
            # so it is not masked as "not settled yet".
            if "not found" in str(exc).lower():
                raise SettlementNotFound(
                    f"Polymarket market {market_id} not found"
                ) from exc
            raise
        return payload


class VenueRoutingAuthoritativeSettlementSource:
    """Fetch and normalize settlement by exact venue-qualified identity."""

    def __init__(
        self,
        *,
        kalshi_source: KalshiSettlementSource,
        polymarket_source: SettlementSource,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._kalshi_source = kalshi_source
        self._polymarket_source = polymarket_source
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get_settlement(
        self,
        market_ref: MarketRef,
    ) -> SettlementObservation | None:
        observed_at = self._clock()
        if market_ref.venue is Venue.KALSHI:
            market = self._kalshi_source.get_market(market_ref.venue_market_id)
            if market is None:
                return None
            if str(getattr(market, "status", "")).strip().lower() not in {
                "finalized",
                "settled",
            }:
                return None
            effective_at = getattr(market, "updated_time", None) or observed_at
            return normalize_kalshi_settlement(
                market_ref,
                market,
                observed_at=observed_at,
                effective_at=effective_at,
                rules_version="kalshi-settlement-v1",
                source_id="kalshi-market-api",
            )

        if market_ref.venue is Venue.POLYMARKET_US:
            payload = self._polymarket_source.get_settlement(
                market_ref.venue_market_id
            )
            if payload is None:
                return None
            return normalize_polymarket_settlement(
                market_ref,
                payload,
                observed_at=observed_at,
                effective_at=observed_at,
                rules_version="polymarket-us-settlement-v1",
                source_id="polymarket-us-public-api",
            )

        raise SettlementDriftError(
            f"unsupported settlement venue: {market_ref.venue!r}"
        )


@dataclass(frozen=True)
class SettlementReconcileResult:
    checked: int = 0
    resolved: int = 0
    not_found: int = 0
    errors: int = 0
    lane_events: tuple[tuple[str, bool, str, str, float], ...] = ()


class SettlementReconciler:
    def __init__(self, *, source: SettlementSource, resolver: SettlementResolver):
        self._source = source
        self._resolver = resolver

    def reconcile(self, *, limit: int | None = None) -> SettlementReconcileResult:
        tickers = self._open_polymarket_tickers(limit=limit)
        checked = 0
        resolved = 0
        not_found = 0
        errors = 0
        lane_events: list[tuple[str, bool, str, str, float]] = []

        for ticker in tickers:
            checked += 1
            try:
                payload = self._source.get_settlement(ticker)
            except SettlementNotFound:
                not_found += 1
                continue
            except SettlementDriftError:
                # Hard halt: a settled payload whose shape we cannot interpret is
                # a data-integrity violation, not a transient per-ticker miss.
                # Propagate so the operator sees a hard failure rather than a
                # silently-isolated, miscounted "not settled yet".
                raise
            except Exception as exc:
                # Per-ticker safety net (settlement state + observability path):
                # an UNEXPECTED error on one ticker must NOT abort the whole
                # remaining Polymarket batch for the cycle. Log LOUDLY with the
                # ticker so operators can see the isolated failure -- never
                # silently swallow -- then continue to the next ticker.
                errors += 1
                log.error(
                    "Polymarket settlement reconcile: isolating failed ticker "
                    "%s at FETCH stage: %s",
                    ticker,
                    exc,
                    exc_info=True,
                )
                continue

            try:
                resolved_yes = _resolved_yes_from_payload(ticker, payload)
                resolved_lane_events = self._resolver._resolve_market_sync(
                    ticker, resolved_yes
                )
            except SettlementDriftError:
                # See above: drift is a hard halt, not an isolated miss.
                raise
            except Exception as exc:
                errors += 1
                log.error(
                    "Polymarket settlement reconcile: isolating failed ticker "
                    "%s at RESOLUTION stage: %s",
                    ticker,
                    exc,
                    exc_info=True,
                )
                continue

            for trade_id, lane_name, lane_estimate in resolved_lane_events:
                lane_events.append(
                    (ticker, resolved_yes, trade_id, lane_name, lane_estimate)
                )
            resolved += 1

        return SettlementReconcileResult(
            checked=checked,
            resolved=resolved,
            not_found=not_found,
            errors=errors,
            lane_events=tuple(lane_events),
        )

    def _open_polymarket_tickers(self, *, limit: int | None) -> list[str]:
        sql = (
            "SELECT DISTINCT ticker FROM paper_trades "
            "WHERE venue = ? AND resolved = 0 "
            "ORDER BY ts ASC"
        )
        params: list[Any] = [Venue.POLYMARKET_US.value]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._resolver._conn.execute(sql, params).fetchall()
        return [str(row["ticker"]) for row in rows]


class PersistedPositionReconciler:
    """Reconcile mapped persisted positions without alias-based authority."""

    def __init__(
        self,
        *,
        source: PersistedSettlementSource,
        resolver: PersistedPositionResolver,
    ) -> None:
        self._source = source
        self._resolver = resolver

    def reconcile(self, *, limit: int | None = None) -> SettlementReconcileResult:
        if limit is not None and (isinstance(limit, bool) or limit < 0):
            raise ValueError("limit must be a nonnegative integer or None")

        market_refs = tuple(dict.fromkeys(self._resolver.mapped_open_market_refs()))
        if limit is not None:
            market_refs = market_refs[:limit]

        checked = 0
        resolved = 0
        not_found = 0
        errors = 0
        for market_ref in market_refs:
            if not isinstance(market_ref, MarketRef):
                raise SettlementDriftError(
                    "persisted settlement identity must be a MarketRef"
                )
            checked += 1
            try:
                observation = self._source.get_settlement(market_ref)
            except SettlementNotFound:
                not_found += 1
                continue
            except SettlementDriftError:
                raise
            except Exception as exc:
                errors += 1
                log.error(
                    "Persisted settlement reconcile: isolating failed market %s:%s "
                    "at FETCH stage: %s",
                    market_ref.venue.value,
                    market_ref.venue_market_id,
                    exc,
                    exc_info=True,
                )
                continue

            if observation is None:
                not_found += 1
                continue
            if not isinstance(observation, SettlementObservation):
                raise SettlementDriftError(
                    "authoritative settlement must return a SettlementObservation"
                )
            if observation.market_ref != market_ref:
                raise SettlementDriftError(
                    "authoritative settlement identity does not match persisted identity"
                )

            try:
                applied = self._resolver.resolve_observation(observation)
            except SettlementDriftError:
                raise
            except Exception as exc:
                errors += 1
                log.error(
                    "Persisted settlement reconcile: isolating failed market %s:%s "
                    "at RESOLUTION stage: %s",
                    market_ref.venue.value,
                    market_ref.venue_market_id,
                    exc,
                    exc_info=True,
                )
                continue
            if applied:
                resolved += 1

        return SettlementReconcileResult(
            checked=checked,
            resolved=resolved,
            not_found=not_found,
            errors=errors,
        )


def _resolved_yes_from_settlement_value(market_id: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        raise SettlementDriftError(
            f"settlement payload for {market_id} has boolean settlement"
        )
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SettlementDriftError(
            f"settlement payload for {market_id} has nonnumeric settlement"
        ) from exc
    if not math.isfinite(value) or value not in {0.0, 1.0}:
        raise SettlementDriftError(
            f"settlement payload for {market_id} has nonbinary settlement"
        )
    return value == 1.0


def _resolved_yes_from_payload(
    market_id: str, payload: Mapping[str, Any] | None
) -> bool:
    if payload is None:
        raise SettlementDriftError(
            f"settlement payload missing for polymarket market {market_id}"
        )

    if "settlement" in payload:
        return _resolved_yes_from_settlement_value(
            market_id, payload.get("settlement")
        )

    if payload.get("settled", True) is False:
        raise SettlementDriftError(
            f"settlement payload for {market_id} was returned before settlement"
        )

    raw_outcome = (
        payload.get("resolvedOutcome")
        or payload.get("resolved_outcome")
        or payload.get("outcome")
        or payload.get("result")
    )
    if not isinstance(raw_outcome, str):
        raise SettlementDriftError(
            f"settlement payload for {market_id} missing authoritative result"
        )

    outcome = raw_outcome.strip().lower()
    if outcome == "yes":
        return True
    if outcome == "no":
        return False

    raise SettlementDriftError(
        f"settlement payload for {market_id} has unsupported resolvedOutcome: "
        f"{raw_outcome!r}"
    )
