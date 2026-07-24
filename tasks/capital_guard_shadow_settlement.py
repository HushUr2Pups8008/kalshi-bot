"""Bounded authoritative outcome collection for the isolated shadow store.

Runtime scheduling stays intentionally absent until an exact, total-deadline
authoritative source is available. The existing venue router is synchronous and
lossy, so it does not satisfy this module's protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
from typing import Protocol

from polymarket.settlement_reconciler import SettlementNotFound
from trading.capital_guard_shadow import (
    MAX_SETTLEMENT_CANDIDATES_PER_MARKET,
    MAX_SETTLEMENT_MARKETS_PER_RUN,
    CapitalGuardShadowIdentityError,
    CapitalGuardShadowStore,
)
from trading.settlement import (
    SettlementDriftError,
    SettlementObservation,
    SettlementValidationError,
    UnsupportedVoidError,
)
from trading.venue import MarketRef


class StrictAsyncAuthoritativeSettlementSource(Protocol):
    """Exact async source: None means nonterminal; 404 raises SettlementNotFound."""

    async def get_settlement_exact(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation | None: ...


@dataclass(frozen=True)
class SettlementCollectionResult:
    checked: int = 0
    nonterminal: int = 0
    not_found: int = 0
    transient_errors: int = 0
    internal_errors: int = 0
    quarantined: int = 0
    terminal: int = 0
    inserted_observations: int = 0
    identical_observations: int = 0
    conflicts: int = 0


class CapitalGuardShadowSettlementCollector:
    """Poll exact market groups without payout, evaluation, or runtime mutation."""

    def __init__(
        self,
        *,
        store: CapitalGuardShadowStore,
        source: StrictAsyncAuthoritativeSettlementSource,
        clock: Callable[[], datetime] | None = None,
        max_candidates_per_market: int = MAX_SETTLEMENT_CANDIDATES_PER_MARKET,
    ) -> None:
        if not isinstance(store, CapitalGuardShadowStore):
            raise TypeError("store must be CapitalGuardShadowStore")
        method = getattr(source, "get_settlement_exact", None)
        if method is None or not inspect.iscoroutinefunction(method):
            raise TypeError("source must implement the strict async exact contract")
        if (
            not isinstance(max_candidates_per_market, int)
            or isinstance(max_candidates_per_market, bool)
            or max_candidates_per_market <= 0
            or max_candidates_per_market > MAX_SETTLEMENT_CANDIDATES_PER_MARKET
        ):
            raise ValueError("max_candidates_per_market exceeds hard bounded maximum")
        self._store = store
        self._source = source
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_candidates_per_market = max_candidates_per_market

    async def run_once(
        self,
        *,
        limit: int = MAX_SETTLEMENT_MARKETS_PER_RUN,
    ) -> SettlementCollectionResult:
        keys = self._store.settlement_market_backlog(limit=limit)
        counts = {
            "checked": 0,
            "nonterminal": 0,
            "not_found": 0,
            "transient_errors": 0,
            "internal_errors": 0,
            "quarantined": 0,
            "terminal": 0,
            "inserted_observations": 0,
            "identical_observations": 0,
            "conflicts": 0,
        }
        for key in keys:
            attempted_at = self._clock()
            try:
                backlog = self._store.candidate_settlement_backlog(
                    key, limit=self._max_candidates_per_market
                )
            except CapitalGuardShadowIdentityError as exc:
                result = self._store.record_settlement_identity_quarantine(
                    exc, attempted_at=attempted_at
                )
                counts["checked"] += 1
                self._count_write_result(counts, result)
                continue

            counts["checked"] += 1
            if backlog.authoritative_head_error is not None:
                result = self._store.record_settlement_attempt(
                    backlog,
                    attempted_at=attempted_at,
                    status="quarantined",
                    error_taxonomy="source_drift",
                    error_sha256=_error_sha256("source_drift"),
                    quarantine_reason="source_drift",
                )
                self._count_write_result(counts, result)
                continue
            try:
                observation = await self._source.get_settlement_exact(
                    backlog.market_ref,
                    prior_observation=backlog.prior_authoritative_observation,
                )
            except SettlementNotFound as exc:
                result = self._store.record_settlement_attempt(
                    backlog,
                    attempted_at=attempted_at,
                    status="not_found",
                    error_taxonomy="authoritative_not_found",
                    error_sha256=_error_sha256("authoritative_not_found", exc),
                )
            except UnsupportedVoidError as exc:
                result = self._store.record_settlement_attempt(
                    backlog,
                    attempted_at=attempted_at,
                    status="quarantined",
                    error_taxonomy="missing_void_refund_contract",
                    error_sha256=_error_sha256(
                        "missing_void_refund_contract", exc
                    ),
                    quarantine_reason="missing_void_refund_contract",
                )
            except (SettlementDriftError, SettlementValidationError) as exc:
                result = self._store.record_settlement_attempt(
                    backlog,
                    attempted_at=attempted_at,
                    status="quarantined",
                    error_taxonomy="source_drift",
                    error_sha256=_error_sha256("source_drift", exc),
                    quarantine_reason="source_drift",
                )
            except TimeoutError as exc:
                result = self._store.record_settlement_attempt(
                    backlog,
                    attempted_at=attempted_at,
                    status="transient_error",
                    error_taxonomy="timeout",
                    error_sha256=_error_sha256("timeout", exc),
                )
            except ConnectionError as exc:
                result = self._store.record_settlement_attempt(
                    backlog,
                    attempted_at=attempted_at,
                    status="transient_error",
                    error_taxonomy="connection",
                    error_sha256=_error_sha256("connection", exc),
                )
            except OSError as exc:
                result = self._store.record_settlement_attempt(
                    backlog,
                    attempted_at=attempted_at,
                    status="transient_error",
                    error_taxonomy="transport_os_error",
                    error_sha256=_error_sha256("transport_os_error", exc),
                )
            except Exception as exc:
                result = self._store.record_settlement_attempt(
                    backlog,
                    attempted_at=attempted_at,
                    status="internal_error",
                    error_taxonomy="internal_source_error",
                    error_sha256=_error_sha256("internal_source_error", exc),
                )
            else:
                if observation is None:
                    result = self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="nonterminal",
                        error_taxonomy="authoritative_nonterminal",
                        error_sha256=_error_sha256("authoritative_nonterminal"),
                    )
                else:
                    result = self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="terminal",
                        observation=observation,
                    )
            self._count_write_result(counts, result)
        return SettlementCollectionResult(**counts)

    @staticmethod
    def _count_write_result(counts: dict[str, int], result: object) -> None:
        if result.status == "conflict":
            counts["conflicts"] += 1
            return
        status_field = {
            "transient_error": "transient_errors",
            "internal_error": "internal_errors",
        }.get(result.attempt_status, result.attempt_status)
        counts[status_field] += 1
        if result.observation_status == "inserted":
            counts["inserted_observations"] += 1
        elif result.observation_status == "identical":
            counts["identical_observations"] += 1


def _error_sha256(taxonomy: str, exc: BaseException | None = None) -> str:
    # Persist only the bounded category fingerprint, never exception text.
    return hashlib.sha256(
        (
            f"capital-guard-settlement-error-v1\0{taxonomy}\0"
            f"{type(exc).__module__ if exc is not None else 'none'}\0"
            f"{type(exc).__qualname__ if exc is not None else 'none'}"
        ).encode("utf-8")
    ).hexdigest()
