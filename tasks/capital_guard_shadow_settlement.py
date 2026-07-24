"""Bounded authoritative outcome collection for the isolated shadow store.

Runtime scheduling remains default-off and requires an exact, total-deadline
authoritative source plus an existing isolated store. The existing venue router
is synchronous and lossy, so it does not satisfy this module's protocol.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from typing import Protocol, TypeVar

from polymarket.settlement_reconciler import SettlementNotFound
from trading.capital_guard_shadow import (
    MAX_SETTLEMENT_CANDIDATES_PER_MARKET,
    MAX_SETTLEMENT_MARKETS_PER_RUN,
    CapitalGuardShadowIdentityError,
    CapitalGuardShadowStore,
    SettlementMarketKey,
    SettlementPollState,
)
from trading.settlement import (
    SettlementDriftError,
    SettlementObservation,
    SettlementValidationError,
    UnsupportedVoidError,
)
from trading.venue import MarketRef


_T = TypeVar("_T")


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


class SettlementPollingPolicy:
    """Pure bounded policy over durable, read-only store projection facts."""

    _RETRY_BACKOFFS: dict[str, tuple[timedelta, timedelta]] = {
        "transient_error": (timedelta(minutes=5), timedelta(hours=1)),
        "nonterminal": (timedelta(minutes=15), timedelta(hours=24)),
        "internal_error": (timedelta(minutes=15), timedelta(hours=6)),
        "not_found": (timedelta(hours=1), timedelta(hours=24)),
    }

    def next_due_at(
        self,
        state: SettlementPollState,
        *,
        now: datetime,
    ) -> datetime | None:
        """Return the next due time, or ``None`` for a terminal current snapshot."""
        _require_utc_datetime("now", now)
        if state.latest_attempted_at is None or not self._snapshot_matches(state):
            return now
        if state.latest_status in ("terminal", "quarantined"):
            return None
        retry = self._RETRY_BACKOFFS.get(state.latest_status)
        if retry is None:
            raise ValueError("poll state has an invalid latest status")
        base, maximum = retry
        retries = max(1, state.matching_snapshot_retry_count)
        delay_seconds = min(
            base.total_seconds() * (2 ** min(retries - 1, 63)),
            maximum.total_seconds(),
        )
        return state.latest_attempted_at + timedelta(seconds=delay_seconds)

    def select_due(
        self,
        states: Iterable[SettlementPollState],
        *,
        now: datetime,
        limit: int = MAX_SETTLEMENT_MARKETS_PER_RUN,
    ) -> tuple[SettlementMarketKey, ...]:
        """Select at most the existing hard cap in deterministic due-time order."""
        _require_utc_datetime("now", now)
        _require_market_limit(limit)
        due = [
            (next_due_at, state)
            for state in states
            if (next_due_at := self.next_due_at(state, now=now)) is not None and next_due_at <= now
        ]
        due.sort(
            key=lambda item: (
                item[0],
                item[1].first_decision_at,
                item[1].market_key.venue.value,
                item[1].market_key.venue_market_id,
            )
        )
        return tuple(state.market_key for _due_at, state in due[:limit])

    def select_terminal_correction_audit(
        self,
        states: Iterable[SettlementPollState],
        *,
        limit: int = MAX_SETTLEMENT_MARKETS_PER_RUN,
    ) -> tuple[SettlementMarketKey, ...]:
        """Select an explicit, bounded audit of unchanged terminal snapshots."""
        _require_market_limit(limit)
        terminal = [
            state
            for state in states
            if self._snapshot_matches(state)
            and state.latest_status == "terminal"
            and state.latest_attempted_at is not None
        ]
        terminal.sort(
            key=lambda state: (
                state.latest_attempted_at,
                state.first_decision_at,
                state.market_key.venue.value,
                state.market_key.venue_market_id,
            )
        )
        return tuple(state.market_key for state in terminal[:limit])

    @staticmethod
    def _snapshot_matches(state: SettlementPollState) -> bool:
        if state.latest_snapshot_count != state.current_candidate_count:
            return False
        if state.latest_snapshot_complete != state.current_candidate_set_complete:
            return False
        if not state.current_candidate_set_complete:
            return state.latest_snapshot_sha256 is None and state.current_candidate_set_sha256 is None
        return state.latest_snapshot_sha256 == state.current_candidate_set_sha256


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
        self._polling_policy = SettlementPollingPolicy()

    async def run_once(
        self,
        *,
        limit: int = MAX_SETTLEMENT_MARKETS_PER_RUN,
    ) -> SettlementCollectionResult:
        """Run the default bounded policy; unchanged terminal state stays suppressed."""
        return await self._run_once(limit=limit, terminal_correction_audit=False)

    async def audit_terminal_corrections_once(
        self,
        *,
        limit: int = MAX_SETTLEMENT_MARKETS_PER_RUN,
    ) -> SettlementCollectionResult:
        """Explicit bounded audit of unchanged terminal snapshots.

        This path is intentionally not runtime-wired. It exists for a future
        operator/manual correction audit and never bypasses the market cap.
        """
        return await self._run_once(limit=limit, terminal_correction_audit=True)

    async def _run_once(
        self,
        *,
        limit: int,
        terminal_correction_audit: bool,
    ) -> SettlementCollectionResult:
        now = self._clock()
        _require_utc_datetime("clock", now)
        states = await self._store_call(
            lambda: self._store.settlement_poll_states(
                limit=limit,
                terminal_correction_audit=terminal_correction_audit,
                now=now,
            )
        )
        if terminal_correction_audit:
            keys = self._polling_policy.select_terminal_correction_audit(states, limit=limit)
        else:
            keys = self._polling_policy.select_due(states, now=now, limit=limit)
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
            attempted_at = now
            try:
                backlog = await self._store_call(
                    lambda: self._store.candidate_settlement_backlog(key, limit=self._max_candidates_per_market)
                )
            except CapitalGuardShadowIdentityError as exc:
                result = await self._store_call(
                    lambda exc=exc: self._store.record_settlement_identity_quarantine(exc, attempted_at=attempted_at)
                )
                counts["checked"] += 1
                self._count_write_result(counts, result)
                continue

            counts["checked"] += 1
            if backlog.authoritative_head_error is not None:
                result = await self._store_call(
                    lambda: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="quarantined",
                        error_taxonomy="source_drift",
                        error_sha256=_error_sha256("source_drift"),
                        quarantine_reason="source_drift",
                    )
                )
                self._count_write_result(counts, result)
                continue
            if (
                backlog.current_head_sha256 is not None
                and backlog.prior_authoritative_observation is not None
                and backlog.missing_link_candidate_ids
            ):
                # A new candidate can attach to a validated current head without
                # re-querying an already terminal authoritative market.
                result = await self._store_call(
                    lambda: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="terminal",
                        observation=backlog.prior_authoritative_observation,
                    )
                )
                self._count_write_result(counts, result)
                continue
            try:
                observation = await self._source.get_settlement_exact(
                    backlog.market_ref,
                    prior_observation=backlog.prior_authoritative_observation,
                )
            except SettlementNotFound as exc:
                result = await self._store_call(
                    lambda exc=exc: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="not_found",
                        error_taxonomy="authoritative_not_found",
                        error_sha256=_error_sha256("authoritative_not_found", exc),
                    )
                )
            except UnsupportedVoidError as exc:
                result = await self._store_call(
                    lambda exc=exc: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="quarantined",
                        error_taxonomy="missing_void_refund_contract",
                        error_sha256=_error_sha256("missing_void_refund_contract", exc),
                        quarantine_reason="missing_void_refund_contract",
                    )
                )
            except (SettlementDriftError, SettlementValidationError) as exc:
                result = await self._store_call(
                    lambda exc=exc: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="quarantined",
                        error_taxonomy="source_drift",
                        error_sha256=_error_sha256("source_drift", exc),
                        quarantine_reason="source_drift",
                    )
                )
            except TimeoutError as exc:
                result = await self._store_call(
                    lambda exc=exc: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="transient_error",
                        error_taxonomy="timeout",
                        error_sha256=_error_sha256("timeout", exc),
                    )
                )
            except ConnectionError as exc:
                result = await self._store_call(
                    lambda exc=exc: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="transient_error",
                        error_taxonomy="connection",
                        error_sha256=_error_sha256("connection", exc),
                    )
                )
            except OSError as exc:
                result = await self._store_call(
                    lambda exc=exc: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="transient_error",
                        error_taxonomy="transport_os_error",
                        error_sha256=_error_sha256("transport_os_error", exc),
                    )
                )
            except Exception as exc:
                result = await self._store_call(
                    lambda exc=exc: self._store.record_settlement_attempt(
                        backlog,
                        attempted_at=attempted_at,
                        status="internal_error",
                        error_taxonomy="internal_source_error",
                        error_sha256=_error_sha256("internal_source_error", exc),
                    )
                )
            else:
                if observation is None:
                    result = await self._store_call(
                        lambda: self._store.record_settlement_attempt(
                            backlog,
                            attempted_at=attempted_at,
                            status="nonterminal",
                            error_taxonomy="authoritative_nonterminal",
                            error_sha256=_error_sha256("authoritative_nonterminal"),
                        )
                    )
                else:
                    result = await self._store_call(
                        lambda: self._store.record_settlement_attempt(
                            backlog,
                            attempted_at=attempted_at,
                            status="terminal",
                            observation=observation,
                        )
                    )
            self._count_write_result(counts, result)
        return SettlementCollectionResult(**counts)

    @staticmethod
    async def _store_call(operation: Callable[[], _T]) -> _T:
        """Await one synchronous store operation without stranding it on cancellation."""
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A SQLite transaction cannot be cancelled safely from another thread.
            # Let it finish before reporting collector cancellation to the scheduler.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            try:
                task.result()
            except BaseException:
                pass
            raise

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


def _require_utc_datetime(name: str, value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise ValueError(f"{name} must be a UTC timezone-aware datetime")


def _require_market_limit(limit: object) -> None:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit <= 0
        or limit > MAX_SETTLEMENT_MARKETS_PER_RUN
    ):
        raise ValueError("limit exceeds hard bounded maximum")


def _error_sha256(taxonomy: str, exc: BaseException | None = None) -> str:
    # Persist only the bounded category fingerprint, never exception text.
    return hashlib.sha256(
        (
            f"capital-guard-settlement-error-v1\0{taxonomy}\0"
            f"{type(exc).__module__ if exc is not None else 'none'}\0"
            f"{type(exc).__qualname__ if exc is not None else 'none'}"
        ).encode("utf-8")
    ).hexdigest()
