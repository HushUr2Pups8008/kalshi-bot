"""Default-off strict settlement reads from official venue clients only."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import math
import re
import time
from typing import Any

from kalshi import KalshiMarket
from kalshi.rest_client import KalshiRestClient
from kalshi.settlement import normalize_kalshi_settlement
from polymarket.public_client import PolymarketPublicClient
from polymarket.settlement import normalize_polymarket_settlement
from polymarket.settlement_reconciler import SettlementNotFound
from trading.settlement import SettlementDriftError, SettlementObservation
from trading.venue import MarketRef, Venue


DEFAULT_AUTHORITATIVE_SETTLEMENT_TIMEOUT_SECONDS = 3.0
KALSHI_SETTLEMENT_RULES_VERSION = "kalshi-settlement-v1"
KALSHI_SETTLEMENT_SOURCE_ID = "kalshi-market-api"
POLYMARKET_SETTLEMENT_RULES_VERSION = "polymarket-us-settlement-v1"
POLYMARKET_SETTLEMENT_SOURCE_ID = "polymarket-us-public-api"

_KALSHI_NONTERMINAL_STATUSES = frozenset(
    {
        "initialized",
        "inactive",
        "active",
        "closed",
        "determined",
        "disputed",
        "amended",
        "unopened",
        "open",
        "paused",
    }
)
_KALSHI_TERMINAL_STATUSES = frozenset({"finalized", "settled"})
_POLYMARKET_ID_PATTERN = re.compile(r"[0-9]+")
_POLYMARKET_SLUG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_POLYMARKET_CANONICAL_ID_KEYS = ("id", "market_id", "marketId")


class AuthoritativeSettlementSource:
    """Strict async adapter over bounded official settlement endpoints.

    The adapter is deliberately isolated: callers choose whether to instantiate it,
    and it does not import startup code, write persistence, or trigger a runtime task.
    """

    def __init__(
        self,
        *,
        kalshi_client: KalshiRestClient,
        polymarket_client: PolymarketPublicClient,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        timeout_seconds: float = DEFAULT_AUTHORITATIVE_SETTLEMENT_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        self._kalshi_client = kalshi_client
        self._polymarket_client = polymarket_client
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._timeout_seconds = float(timeout_seconds)

    async def get_settlement_exact(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation | None:
        """Return a terminal source observation, known nonterminal, or typed error."""
        if not isinstance(market_ref, MarketRef):
            raise TypeError("market_ref must be MarketRef")
        if market_ref.venue is Venue.KALSHI:
            self._validate_prior_observation(
                market_ref,
                prior_observation,
                rules_version=KALSHI_SETTLEMENT_RULES_VERSION,
                source_id=KALSHI_SETTLEMENT_SOURCE_ID,
            )
            return await self._get_kalshi_settlement(
                market_ref, prior_observation=prior_observation
            )
        if market_ref.venue is Venue.POLYMARKET_US:
            self._validate_prior_observation(
                market_ref,
                prior_observation,
                rules_version=POLYMARKET_SETTLEMENT_RULES_VERSION,
                source_id=POLYMARKET_SETTLEMENT_SOURCE_ID,
            )
            return await self._get_polymarket_settlement(
                market_ref, prior_observation=prior_observation
            )
        raise SettlementDriftError("unsupported authoritative settlement venue")

    async def _get_kalshi_settlement(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation | None:
        if market_ref.alias != market_ref.venue_market_id:
            raise SettlementDriftError("Kalshi authoritative settlement alias mismatch")
        try:
            market = await self._kalshi_client.get_market_exact_bounded(
                market_ref.venue_market_id,
                timeout_seconds=self._timeout_seconds,
            )
        except ValueError as exc:
            raise SettlementDriftError(
                "Kalshi authoritative settlement client returned malformed data"
            ) from exc
        if market is None:
            raise SettlementNotFound(
                "Kalshi authoritative settlement market was not found"
            )
        self._validate_kalshi_market_identity(market_ref, market)
        status = market.status.strip().lower()
        if status in _KALSHI_NONTERMINAL_STATUSES:
            return None
        if status not in _KALSHI_TERMINAL_STATUSES:
            raise SettlementDriftError(
                f"Kalshi authoritative settlement has unsupported status: {market.status!r}"
            )
        if not isinstance(market.result, str):
            raise SettlementDriftError(
                "Kalshi authoritative settlement has non-text terminal result"
            )

        return self._stabilize_observation(
            prior_observation,
            lambda **kwargs: normalize_kalshi_settlement(
                market_ref,
                market,
                rules_version=KALSHI_SETTLEMENT_RULES_VERSION,
                source_id=KALSHI_SETTLEMENT_SOURCE_ID,
                **kwargs,
            ),
        )

    async def _get_polymarket_settlement(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation | None:
        self._validate_polymarket_market_ref(market_ref)
        deadline = self._deadline()
        try:
            settlement = await self._polymarket_client.get_market_settlement_exact_bounded(
                market_ref.alias,
                timeout_seconds=self._remaining_timeout(deadline),
            )
        except ValueError as exc:
            raise SettlementDriftError(
                "Polymarket authoritative settlement client returned malformed data"
            ) from exc
        try:
            market = await self._polymarket_client.get_market_by_slug_exact_bounded(
                market_ref.alias,
                timeout_seconds=self._remaining_timeout(deadline),
            )
        except ValueError as exc:
            raise SettlementDriftError(
                "Polymarket authoritative market client returned malformed data"
            ) from exc

        if market is None:
            if settlement is None:
                raise SettlementNotFound(
                    "Polymarket authoritative settlement market was not found"
                )
            raise SettlementDriftError(
                "Polymarket authoritative settlement has no exact market identity"
            )
        self._validate_polymarket_market_identity(market_ref, market)
        if settlement is None:
            return None
        payload = self._bind_polymarket_settlement_identity(market_ref, settlement)
        return self._stabilize_observation(
            prior_observation,
            lambda **kwargs: normalize_polymarket_settlement(
                market_ref,
                payload,
                rules_version=POLYMARKET_SETTLEMENT_RULES_VERSION,
                source_id=POLYMARKET_SETTLEMENT_SOURCE_ID,
                **kwargs,
            ),
        )

    def _deadline(self) -> float:
        started = float(self._monotonic())
        if not math.isfinite(started):
            raise TimeoutError("authoritative settlement deadline is invalid")
        return started + self._timeout_seconds

    def _remaining_timeout(self, deadline: float) -> float:
        now = float(self._monotonic())
        if not math.isfinite(now):
            raise TimeoutError("authoritative settlement deadline is invalid")
        remaining = deadline - now
        if remaining <= 0:
            raise TimeoutError("authoritative settlement deadline expired")
        return remaining

    @staticmethod
    def _validate_prior_observation(
        market_ref: MarketRef,
        prior_observation: SettlementObservation | None,
        *,
        rules_version: str,
        source_id: str,
    ) -> None:
        if prior_observation is None:
            return
        if not isinstance(prior_observation, SettlementObservation):
            raise SettlementDriftError("prior authoritative observation is invalid")
        if prior_observation.market_ref != market_ref:
            raise SettlementDriftError("prior authoritative observation identity mismatch")
        if (
            prior_observation.rules_version != rules_version
            or prior_observation.source_id != source_id
        ):
            raise SettlementDriftError("prior authoritative observation provenance mismatch")

    @staticmethod
    def _validate_kalshi_market_identity(
        market_ref: MarketRef, market: KalshiMarket
    ) -> None:
        if not isinstance(market.ticker, str) or market.ticker != market_ref.venue_market_id:
            raise SettlementDriftError(
                "Kalshi authoritative settlement returned mismatched ticker"
            )
        if not isinstance(market.status, str):
            raise SettlementDriftError(
                "Kalshi authoritative settlement returned non-text status"
            )

    @staticmethod
    def _validate_polymarket_market_ref(market_ref: MarketRef) -> None:
        if (
            not isinstance(market_ref.venue_market_id, str)
            or _POLYMARKET_ID_PATTERN.fullmatch(market_ref.venue_market_id) is None
        ):
            raise SettlementDriftError(
                "Polymarket authoritative settlement requires a canonical numeric id"
            )
        if (
            not isinstance(market_ref.alias, str)
            or _POLYMARKET_SLUG_PATTERN.fullmatch(market_ref.alias) is None
        ):
            raise SettlementDriftError(
                "Polymarket authoritative settlement requires an exact slug alias"
            )

    @staticmethod
    def _validate_polymarket_market_identity(
        market_ref: MarketRef, market: Mapping[str, Any]
    ) -> None:
        if not isinstance(market, Mapping):
            raise SettlementDriftError(
                "Polymarket authoritative market response must be an object"
            )
        if market.get("slug") != market_ref.alias or market.get("id") != market_ref.venue_market_id:
            raise SettlementDriftError(
                "Polymarket authoritative market identity mismatch"
            )

    @staticmethod
    def _bind_polymarket_settlement_identity(
        market_ref: MarketRef, settlement: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(settlement, Mapping):
            raise SettlementDriftError(
                "Polymarket authoritative settlement response must be an object"
            )
        if settlement.get("slug") != market_ref.alias:
            raise SettlementDriftError(
                "Polymarket authoritative settlement alias mismatch"
            )
        payload = dict(settlement)
        for key in _POLYMARKET_CANONICAL_ID_KEYS:
            if (
                key in payload
                and AuthoritativeSettlementSource._canonical_polymarket_settlement_id(
                    payload[key]
                )
                != market_ref.venue_market_id
            ):
                raise SettlementDriftError(
                    "Polymarket authoritative settlement has conflicting canonical id"
                )
            payload.pop(key, None)
        payload["id"] = market_ref.venue_market_id
        return payload

    @staticmethod
    def _canonical_polymarket_settlement_id(value: object) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return None

    def _stabilize_observation(
        self,
        prior_observation: SettlementObservation | None,
        normalize: Callable[..., SettlementObservation],
    ) -> SettlementObservation:
        observed_at = self._clock()
        provisional = normalize(
            observed_at=observed_at,
            effective_at=(
                prior_observation.effective_at
                if prior_observation is not None
                else observed_at
            ),
            previous_observation=None,
            supersedes_observation_sha256=None,
        )
        if (
            prior_observation is None
            or provisional.observation_sha256 == prior_observation.observation_sha256
        ):
            return provisional
        corrected_at = self._clock()
        return normalize(
            observed_at=corrected_at,
            effective_at=corrected_at,
            previous_observation=prior_observation,
            supersedes_observation_sha256=prior_observation.observation_sha256,
        )
