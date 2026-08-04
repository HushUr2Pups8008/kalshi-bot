"""Scheduled research dossier prewarm task.

This task owns orchestration only: select market snapshots, run the existing
research gate in non-live mode, persist dossier evidence through the store, and
return cycle results. It does not blend, size, gate, or execute trades.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Iterable

from analysis.research_gate import (
    DirectFetcher,
    PrewarmPhaseTimeouts,
    ResearchAdjudicator,
    SearchProvider,
    _PREWARM_PHASE_TIMEOUTS_CAPABILITY,
    default_direct_fetcher,
    default_search_provider,
    market_has_research_source_path,
    run_research_gate,
)
from tasks.research_dossier import (
    RESEARCH_TASK_TERMINAL_AFTER_SAME_REASON,
    ResearchDossierStore,
    default_store,
)
from utils.logger import get_logger, trade_log, write_trade_log_async
from utils.research_evidence_quality import has_reliable_research_source_path
from utils.research_market_eligibility import research_market_eligibility
from utils.research_priority import research_market_priority_key

_log = get_logger("research_prewarm_task")
_TERMINAL_RESEARCH_TASK_STATES = {"decision_grade_candidate", "untradeable"}
_COUNTER_QUERY_INTENTS = frozenset({"disconfirming", "contradiction_check"})
_RETRYABLE_TERMINAL_RESEARCH_REASONS = frozenset(
    {"official_data_pending", "research_timeout_exhausted"}
)
ResearchGateRunner = Callable[..., Awaitable[Any]]
ResearchPrewarmResultSink = Callable[["ResearchPrewarmResult"], Awaitable[None]]
ResearchPrewarmMarketResultSink = Callable[
    ["ResearchPrewarmResult", Any],
    Awaitable[None],
]
# Serialize provider starts across prewarm task instances while keeping the
# eight-query decision-grade fanout inside the 12-second research budget.
_RESEARCH_PREWARM_PROVIDER_MIN_START_INTERVAL_SECONDS = 0.25


class _ResearchPrewarmProviderAdmission:
    """Serialize and pace prewarm provider starts across task instances."""

    def __init__(
        self,
        *,
        min_start_interval_seconds: float,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        interval = float(min_start_interval_seconds)
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("provider start interval must be finite and non-negative")
        self._min_start_interval_seconds = interval
        self._clock = clock
        self._sleeper = sleeper
        self._lock = asyncio.Lock()
        self._next_start = 0.0

    async def run(self, provider: SearchProvider, query: Any) -> Any:
        async with self._lock:
            clock = self._clock or asyncio.get_running_loop().time
            delay = self._next_start - clock()
            if delay > 0:
                await self._sleeper(delay)
            started_at = clock()
            self._next_start = (
                max(started_at, self._next_start)
                + self._min_start_interval_seconds
            )
        return await provider(query)


_RESEARCH_PREWARM_PROVIDER_ADMISSION_ATTRIBUTE = (
    "_kalshi_research_prewarm_provider_admission"
)


def _get_research_prewarm_provider_admission() -> _ResearchPrewarmProviderAdmission:
    loop = asyncio.get_running_loop()
    admission = getattr(
        loop,
        _RESEARCH_PREWARM_PROVIDER_ADMISSION_ATTRIBUTE,
        None,
    )
    if admission is None:
        admission = _ResearchPrewarmProviderAdmission(
            min_start_interval_seconds=(
                _RESEARCH_PREWARM_PROVIDER_MIN_START_INTERVAL_SECONDS
            ),
            clock=loop.time,
        )
        setattr(
            loop,
            _RESEARCH_PREWARM_PROVIDER_ADMISSION_ATTRIBUTE,
            admission,
        )
    return admission


class ResearchPrewarmError(Exception):
    """Raised when one market's research prewarm path fails."""


@dataclass(frozen=True)
class ResearchPrewarmResult:
    market_ticker: str
    status: str
    attempted: bool
    query_count: int = 0
    evidence_count: int = 0
    skip_reason: str | None = None
    research_pending_origin: str | None = None
    error: str | None = None
    research_run_id: str | None = None
    research_contract_fingerprint: str | None = None
    research_persisted: bool | None = None
    research_persistence_error: str | None = None
    research_direct_fetch_failures: tuple[str, ...] = ()
    research_timeout_stage: str | None = None
    research_provider_error_count: int = 0
    research_provider_error_attributions: tuple[str, ...] = ()
    research_generic_search_circuit_state: str | None = None
    research_generic_search_failure_classes: tuple[str, ...] = ()
    research_generic_search_attempt_delta: int = 0
    research_generic_search_blocked_call_delta: int = 0


class ResearchPrewarmTask:
    """Async worker that builds research dossiers before signal-time rejection."""

    def __init__(
        self,
        *,
        store: ResearchDossierStore | None = None,
        research_gate: ResearchGateRunner = run_research_gate,
        search_provider: SearchProvider = default_search_provider,
        direct_fetcher: DirectFetcher = default_direct_fetcher,
        adjudicator: ResearchAdjudicator | None = None,
        max_queries: int = 6,
        research_timeout_seconds: float = 12.0,
        initial_adjudication_timeout_seconds: float = 20.0,
        counter_query_timeout_seconds: float = 5.0,
        counter_adjudication_timeout_seconds: float = 20.0,
        max_concurrency: int = 1,
        target_cooldown_seconds: float = 0.0,
        result_sink: ResearchPrewarmResultSink | None = None,
        market_result_sink: ResearchPrewarmMarketResultSink | None = None,
    ) -> None:
        self.store = store or default_store()
        self.research_gate = research_gate
        self.search_provider = search_provider
        self.direct_fetcher = direct_fetcher
        self.adjudicator = adjudicator
        self.max_queries = int(max_queries)
        self.research_timeout_seconds = float(research_timeout_seconds)
        self.prewarm_phase_timeouts = PrewarmPhaseTimeouts(
            initial_adjudication_seconds=initial_adjudication_timeout_seconds,
            counter_query_seconds=counter_query_timeout_seconds,
            counter_adjudication_seconds=counter_adjudication_timeout_seconds,
        )
        self.max_concurrency = max(1, int(max_concurrency))
        self.target_cooldown_seconds = max(0.0, float(target_cooldown_seconds))
        self._last_attempted_by_ticker: dict[str, float] = {}
        self.result_sink = result_sink or _write_research_prewarm_result
        self.market_result_sink = market_result_sink

    async def _search_provider_with_admission(self, query: Any) -> Any:
        return await _get_research_prewarm_provider_admission().run(
            self.search_provider,
            query,
        )

    async def process_market(
        self,
        market: Any,
        *,
        bypass_persisted_cooldown: bool = False,
    ) -> ResearchPrewarmResult:
        ticker = str(getattr(market, "ticker", "") or "")
        if not ticker:
            raise ResearchPrewarmError("market ticker is required")
        contract_question = _market_contract_question(market)
        await self.store.initialize()
        eligibility = research_market_eligibility(market)
        if not eligibility.eligible and (
            eligibility.reason == "market_expired"
            or eligibility.status in {"closed", "finalized", "settled", "resolved"}
        ):
            terminal_reason = (
                "market_expired"
                if eligibility.reason == "market_expired"
                else "market_closed"
            )
            return await self._terminal_skip_result(
                ticker,
                status="skipped_closed",
                result_skip_reason=terminal_reason,
                terminal_reason=terminal_reason,
                summary=(
                    "Market is past its close time; research task is terminal."
                    if terminal_reason == "market_expired"
                    else "Market is closed or finalized; research task is terminal."
                ),
                contract_question=contract_question,
                market=market,
            )
        if not eligibility.eligible:
            return await self._queued_skip_result(
                ticker,
                status="needs_research",
                result_skip_reason=eligibility.reason or "market_not_active",
                summary="Market eligibility metadata is not tradeable; keep research queued.",
                contract_question=contract_question,
                market=market,
            )
        if not market_has_research_source_path(market):
            return await self._queued_skip_result(
                ticker,
                status="needs_research",
                result_skip_reason="no_reliable_source_path",
                summary=(
                    "Market has no reliable source path yet; keep queued for "
                    "future source discovery."
                ),
                contract_question=contract_question,
                market=market,
            )
        try:
            persisted_skip = await self._persisted_task_skip_result(
                ticker,
                market,
                bypass_persisted_cooldown=bypass_persisted_cooldown,
            )
            if persisted_skip is not None:
                return persisted_skip
            open_questions = await self._task_open_questions(ticker)
            await self.store.mark_research_task_researching(ticker)
            verdict = await self.research_gate(
                _prewarm_news(market, open_questions),
                market,
                model_direction="neutral",
                model_confidence=0.0,
                model_reason="scheduled research prewarm",
                yes_ask=_ask_probability(market, "yes_ask_cents", "yes_ask"),
                no_ask=_ask_probability(market, "no_ask_cents", "no_ask"),
                live_mode=False,
                search_provider=self._search_provider_with_admission,
                direct_fetcher=self.direct_fetcher,
                adjudicator=self.adjudicator,
                dossier_store=self.store,
                max_queries=self.max_queries,
                research_timeout_seconds=self.research_timeout_seconds,
                prewarm_phase_timeouts=self.prewarm_phase_timeouts,
                _prewarm_phase_timeouts_capability=(
                    _PREWARM_PHASE_TIMEOUTS_CAPABILITY
                ),
                require_decision_grade=True,
            )
            if getattr(verdict, "research_persisted", False) is not True:
                fallback_run_id = getattr(verdict, "research_run_id", None) or (
                    f"rr-prewarm-{uuid.uuid4().hex}"
                )
                await self.store.record_research_run(
                    ticker,
                    fallback_run_id,
                    trigger_headline="",
                    trigger_source="research_prewarm",
                    contract_question=contract_question,
                    attempted=bool(getattr(verdict, "attempted", False)),
                    summary=str(getattr(verdict, "summary", "") or ""),
                    verdict_status=_status_value(getattr(verdict, "status", "")),
                    skip_reason=getattr(verdict, "skip_reason", None),
                    research_pending_origin=getattr(
                        verdict,
                        "research_pending_origin",
                        None,
                    ),
                    force_side=getattr(verdict, "force_side", None),
                    estimated_probability=getattr(
                        verdict,
                        "estimated_probability",
                        None,
                    ),
                    confidence=getattr(verdict, "confidence", None),
                    contract_fingerprint=_research_contract_fingerprint(verdict),
                    market_price=getattr(verdict, "market_price", None),
                    estimated_edge=getattr(verdict, "estimated_edge", None),
                    decision_grade_status=_status_value(
                        getattr(verdict, "status", ""),
                    ),
                    decision_grade_reasons=list(
                        getattr(verdict, "decision_grade_reasons", ()) or ()
                    ),
                    open_questions=list(getattr(verdict, "open_questions", ()) or ()),
                    counterclaims=list(getattr(verdict, "counterclaims", ()) or ()),
                    queries=list(getattr(verdict, "queries", ()) or ()),
                    evidence=list(getattr(verdict, "evidence", ()) or ()),
                    market_status=getattr(market, "status", None),
                    market_close_time=getattr(market, "close_time", None),
                )
                verdict = SimpleNamespace(
                    **{
                        **getattr(verdict, "__dict__", {}),
                        "research_run_id": fallback_run_id,
                        "research_persisted": True,
                    }
                )
            verdict = await _reconcile_prewarm_persisted_status(
                verdict,
                store=self.store,
                ticker=ticker,
            )
            return ResearchPrewarmResult(
                market_ticker=ticker,
                status=_status_value(getattr(verdict, "status", "")),
                attempted=bool(getattr(verdict, "attempted", False)),
                query_count=len(getattr(verdict, "queries", ()) or ()),
                evidence_count=len(getattr(verdict, "evidence", ()) or ()),
                skip_reason=getattr(verdict, "skip_reason", None),
                research_pending_origin=getattr(
                    verdict,
                    "research_pending_origin",
                    None,
                ),
                research_run_id=getattr(verdict, "research_run_id", None),
                research_contract_fingerprint=_research_contract_fingerprint(verdict),
                research_persisted=getattr(verdict, "research_persisted", None),
                research_persistence_error=getattr(
                    verdict,
                    "research_persistence_error",
                    None,
                ),
                research_direct_fetch_failures=tuple(
                    getattr(verdict, "research_direct_fetch_failures", ()) or ()
                ),
                research_timeout_stage=getattr(verdict, "research_timeout_stage", None),
                research_provider_error_count=int(
                    getattr(verdict, "research_provider_error_count", 0) or 0
                ),
                research_provider_error_attributions=tuple(
                    getattr(verdict, "research_provider_error_attributions", ()) or ()
                ),
                research_generic_search_circuit_state=getattr(
                    verdict,
                    "research_generic_search_circuit_state",
                    None,
                ),
                research_generic_search_failure_classes=tuple(
                    getattr(
                        verdict,
                        "research_generic_search_failure_classes",
                        (),
                    )
                    or ()
                ),
                research_generic_search_attempt_delta=int(
                    getattr(verdict, "research_generic_search_attempt_delta", 0) or 0
                ),
                research_generic_search_blocked_call_delta=int(
                    getattr(
                        verdict,
                        "research_generic_search_blocked_call_delta",
                        0,
                    )
                    or 0
                ),
            )
        except Exception as exc:
            raise ResearchPrewarmError(f"failed research prewarm for {ticker}") from exc

    async def _terminal_skip_result(
        self,
        ticker: str,
        *,
        status: str,
        result_skip_reason: str,
        terminal_reason: str,
        summary: str,
        contract_question: str | None = None,
        market: Any | None = None,
    ) -> ResearchPrewarmResult:
        run_id = f"rr-skip-{uuid.uuid4().hex}"
        try:
            await self.store.record_research_run(
                ticker,
                run_id,
                trigger_headline="",
                trigger_source="research_prewarm",
                contract_question=contract_question,
                attempted=False,
                summary=summary,
                verdict_status="untradeable",
                skip_reason=terminal_reason,
                decision_grade_status="untradeable",
                market_status=getattr(market, "status", None),
                market_close_time=getattr(market, "close_time", None),
                update_dossier_snapshot=True,
                update_dossier_run_id=True,
            )
        except Exception as exc:
            return ResearchPrewarmResult(
                market_ticker=ticker,
                status=status,
                attempted=False,
                skip_reason=result_skip_reason,
                research_persisted=False,
                research_persistence_error=str(exc),
            )
        return ResearchPrewarmResult(
            market_ticker=ticker,
            status=status,
            attempted=False,
            skip_reason=result_skip_reason,
            research_run_id=run_id,
            research_persisted=True,
        )

    async def _queued_skip_result(
        self,
        ticker: str,
        *,
        status: str,
        result_skip_reason: str,
        summary: str,
        contract_question: str | None = None,
        market: Any | None = None,
    ) -> ResearchPrewarmResult:
        if result_skip_reason == "no_reliable_source_path":
            snapshot = await self.store.get_research_task_snapshot(ticker)
            projected_same_reason_count = (
                int(snapshot.same_reason_count) + 1
                if snapshot is not None
                and snapshot.last_skip_reason == result_skip_reason
                else 1
            )
            if (
                projected_same_reason_count
                >= RESEARCH_TASK_TERMINAL_AFTER_SAME_REASON
            ):
                return await self._terminal_skip_result(
                    ticker,
                    status="untradeable",
                    result_skip_reason="no_reliable_source_path",
                    terminal_reason="no_reliable_source_path",
                    summary=(
                        "Market repeatedly lacked a reliable source path; "
                        "research task is terminal no-trade."
                    ),
                    contract_question=contract_question,
                    market=market,
                )
        run_id = f"rr-skip-{uuid.uuid4().hex}"
        try:
            await self.store.record_research_run(
                ticker,
                run_id,
                trigger_headline="",
                trigger_source="research_prewarm",
                contract_question=contract_question,
                attempted=False,
                summary=summary,
                verdict_status=status,
                skip_reason=result_skip_reason,
                decision_grade_status=status,
                market_status=getattr(market, "status", None),
                market_close_time=getattr(market, "close_time", None),
                update_dossier_snapshot=True,
                update_dossier_run_id=True,
            )
        except Exception as exc:
            return ResearchPrewarmResult(
                market_ticker=ticker,
                status=status,
                attempted=False,
                skip_reason=result_skip_reason,
                research_persisted=False,
                research_persistence_error=str(exc),
            )
        return ResearchPrewarmResult(
            market_ticker=ticker,
            status=status,
            attempted=False,
            skip_reason=result_skip_reason,
            research_run_id=run_id,
            research_persisted=True,
        )

    async def emit_result(self, result: ResearchPrewarmResult) -> None:
        await self.result_sink(result)

    async def emit_market_result(
        self,
        result: ResearchPrewarmResult,
        market: Any,
    ) -> None:
        if self.market_result_sink is None:
            return
        try:
            await self.market_result_sink(result, market)
        except Exception as exc:
            _log.warning(
                "[RESEARCH_PREWARM] market result sink failed ticker=%s: %s",
                result.market_ticker,
                exc,
                exc_info=exc,
            )

    async def run_once(self, markets: Iterable[Any]) -> list[ResearchPrewarmResult]:
        now_monotonic = time.monotonic()
        ordered_markets = await self._prioritize_due_markets(list(markets))
        market_list = []
        for market in ordered_markets:
            if await self._market_available(market, now_monotonic):
                market_list.append(market)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def process_with_limit(market: Any) -> ResearchPrewarmResult:
            async with semaphore:
                return await self.process_market(market)

        raw = await asyncio.gather(
            *(process_with_limit(market) for market in market_list),
            return_exceptions=True,
        )
        ok: list[ResearchPrewarmResult] = []
        failed = 0
        for market, result in zip(market_list, raw, strict=True):
            if isinstance(result, BaseException):
                failed += 1
                cause = getattr(result, "__cause__", None)
                _log.warning(
                    "[RESEARCH_PREWARM] per-market research failed: %s (cause: %s)",
                    result,
                    repr(cause) if cause is not None else "<none>",
                    exc_info=cause if cause is not None else result,
                )
                failure_result = _failure_result_for_market(market, result)
                await self.emit_result(failure_result)
                await self.emit_market_result(failure_result, market)
            else:
                ok.append(result)
                await self.emit_result(result)
                await self.emit_market_result(result, market)
                if result.attempted:
                    self._last_attempted_by_ticker[result.market_ticker] = now_monotonic
        if failed:
            _log.warning(
                "[RESEARCH_PREWARM] %d/%d markets failed this cycle",
                failed,
                len(raw),
            )
        return ok

    async def _prioritize_due_markets(self, markets: list[Any]) -> list[Any]:
        if len(markets) < 2:
            return markets
        by_ticker = {
            str(getattr(market, "ticker", "") or ""): market
            for market in markets
            if str(getattr(market, "ticker", "") or "")
        }
        if not by_ticker:
            return markets
        try:
            due_tickers = await asyncio.to_thread(
                self.store.get_due_research_task_tickers,
                limit=len(by_ticker),
                target_cooldown_seconds=self.target_cooldown_seconds,
            )
        except Exception as exc:
            _log.warning(
                "[RESEARCH_PREWARM] due research task lookup failed: %s",
                exc,
            )
            return markets
        due_set = {ticker for ticker in due_tickers if ticker in by_ticker}
        if not due_set:
            return markets
        due_order = {ticker: index for index, ticker in enumerate(due_tickers)}
        due_markets = sorted(
            [by_ticker[ticker] for ticker in due_tickers if ticker in by_ticker],
            key=lambda market: (
                research_market_priority_key(market),
                due_order[str(getattr(market, "ticker", "") or "")],
            ),
        )
        rest = [
            market
            for market in markets
            if str(getattr(market, "ticker", "") or "") not in due_set
        ]
        return due_markets + rest

    async def _market_available(self, market: Any, now_monotonic: float) -> bool:
        ticker = str(getattr(market, "ticker", "") or "")
        if not ticker:
            return False
        eligibility = research_market_eligibility(market)
        if not eligibility.eligible:
            return True
        if self.target_cooldown_seconds > 0:
            last_attempted = self._last_attempted_by_ticker.get(ticker)
            if (
                last_attempted is not None
                and now_monotonic - last_attempted < self.target_cooldown_seconds
            ):
                return False
        persisted_skip = await self._persisted_task_skip_result(ticker, market)
        if persisted_skip is None:
            return True
        return (
            persisted_skip.status == "skipped_terminal"
            and persisted_skip.skip_reason == "decision_grade_candidate"
        )

    async def _persisted_task_skip_result(
        self,
        ticker: str,
        market: Any | None = None,
        *,
        bypass_persisted_cooldown: bool = False,
    ) -> ResearchPrewarmResult | None:
        try:
            snapshot = await self.store.get_research_task_snapshot(ticker)
        except Exception as exc:
            _log.warning(
                "[RESEARCH_PREWARM] research task snapshot lookup failed ticker=%s: %s",
                ticker,
                exc,
            )
            return None
        if snapshot is None:
            return None
        if snapshot.state == "decision_grade_candidate":
            if not await self._decision_grade_task_has_countercase(ticker):
                return None
        if snapshot.state in _TERMINAL_RESEARCH_TASK_STATES:
            if snapshot.terminal_reason in _RETRYABLE_TERMINAL_RESEARCH_REASONS:
                return None
            if (
                snapshot.state == "untradeable"
                and snapshot.terminal_reason == "no_reliable_source_path"
                and market is not None
                and market_has_research_source_path(market)
            ):
                return None
            return ResearchPrewarmResult(
                market_ticker=ticker,
                status="skipped_terminal",
                attempted=False,
                skip_reason=snapshot.terminal_reason or snapshot.state,
            )
        if bypass_persisted_cooldown or (
            market is not None
            and bool(getattr(market, "_research_prewarm_bypass_cooldown", False))
        ):
            return None
        cooldown_until = _parse_utc_ts(snapshot.cooldown_until_ts)
        updated_at = _parse_utc_ts(snapshot.updated_ts)
        repaired_immediate_retry = (
            cooldown_until is None
            and float(snapshot.backoff_seconds or 0.0) <= 0.0
        )
        if (
            updated_at is not None
            and self.target_cooldown_seconds > 0
            and not repaired_immediate_retry
        ):
            configured_until = updated_at.timestamp() + self.target_cooldown_seconds
            configured_cooldown_until = datetime.fromtimestamp(
                configured_until,
                tz=timezone.utc,
            )
            if (
                cooldown_until is None
                or configured_cooldown_until > cooldown_until
            ):
                cooldown_until = configured_cooldown_until
        if cooldown_until is not None and cooldown_until > datetime.now(timezone.utc):
            return ResearchPrewarmResult(
                market_ticker=ticker,
                status="skipped_cooldown",
                attempted=False,
                skip_reason="research_task_cooldown",
            )
        return None

    async def _task_open_questions(self, ticker: str) -> tuple[str, ...]:
        try:
            snapshot = await self.store.get_research_task_snapshot(ticker)
        except Exception as exc:
            _log.warning(
                "[RESEARCH_PREWARM] research task gap lookup failed ticker=%s: %s",
                ticker,
                exc,
            )
            return ()
        if snapshot is None:
            return ()
        return tuple(snapshot.open_questions or ())

    async def _decision_grade_task_has_countercase(self, ticker: str) -> bool:
        try:
            snapshot = await self.store.get_dossier_snapshot(ticker)
            if snapshot is None or not snapshot.last_research_run_id:
                return False
            query_checker = getattr(self.store, "has_research_run_query_intent", None)
            if not callable(query_checker) or not await query_checker(
                snapshot.last_research_run_id,
                _COUNTER_QUERY_INTENTS,
            ):
                return False
            side = (snapshot.last_force_side or "").strip().lower()
            if side not in {"yes", "no"}:
                return False
            evidence = await self.store.get_research_run_evidence(
                ticker,
                snapshot.last_research_run_id,
            )
        except Exception as exc:
            _log.warning(
                "[RESEARCH_PREWARM] decision-grade countercase lookup failed "
                "ticker=%s: %s",
                ticker,
                exc,
            )
            return False
        if not _has_independent_source_path(evidence):
            return False
        opposite = "no" if side == "yes" else "yes"
        return any(
            item.claim_type in {"disconfirming", "contradiction_check"}
            and item.supports_direction in {opposite, "neutral"}
            for item in evidence
        )

    async def run_periodic(
        self,
        market_provider: Callable[[], Iterable[Any]],
        *,
        interval_seconds: float,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while stop_event is None or not stop_event.is_set():
            markets = list(market_provider())
            results = await self.run_once(markets)
            status_counts: dict[str, int] = {}
            for result in results:
                status_counts[result.status] = status_counts.get(result.status, 0) + 1
            _log.info(
                "[RESEARCH_PREWARM] cycle markets=%d results=%s",
                len(markets),
                status_counts or {},
            )
            if stop_event is None:
                await asyncio.sleep(interval_seconds)
            else:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                except asyncio.TimeoutError:
                    continue


def _prewarm_news(
    market: Any,
    open_questions: Iterable[str] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        headline="",
        source="research_prewarm",
        url="",
        research_open_questions=tuple(open_questions),
    )


def _market_contract_question(market: Any) -> str | None:
    for attr in ("question", "title", "subtitle", "event_title"):
        value = getattr(market, attr, None)
        if value:
            return str(value)
    ticker = str(getattr(market, "ticker", "") or "")
    return ticker or None


def _has_independent_source_path(evidence: Iterable[Any]) -> bool:
    return has_reliable_research_source_path(tuple(evidence))


def _ask_probability(market: Any, cents_attr: str, legacy_attr: str) -> float | None:
    cents = getattr(market, cents_attr, None)
    if cents is not None:
        try:
            return float(cents) / 100.0
        except (TypeError, ValueError):
            return None
    try:
        value = getattr(market, legacy_attr)
    except Exception:
        return None
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric > 1.0:
        return numeric / 100.0
    return numeric


def market_has_actionable_price(market: Any) -> bool:
    return _actionable_probability(_ask_probability(market, "yes_ask_cents", "yes_ask")) or (
        _actionable_probability(_ask_probability(market, "no_ask_cents", "no_ask"))
    )


def _actionable_probability(value: float | None) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return 0.0 < numeric < 1.0


def _status_value(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value or "")


def _research_contract_fingerprint(verdict: Any) -> str | None:
    fingerprint = getattr(verdict, "research_contract_fingerprint", None)
    if fingerprint:
        return str(fingerprint)
    log_fields = getattr(verdict, "log_fields", None)
    if not callable(log_fields):
        return None
    value = log_fields().get("research_contract_fingerprint")
    return str(value) if value else None


async def _reconcile_prewarm_persisted_status(
    verdict: Any,
    *,
    store: Any,
    ticker: str,
) -> Any:
    if _status_value(getattr(verdict, "status", "")) != "decision_grade_candidate":
        return verdict

    def persistence_unverified(detail: str) -> Any:
        return SimpleNamespace(
            **{
                **getattr(verdict, "__dict__", {}),
                "status": "needs_research",
                "skip_reason": "persistence_status_unverified",
                "force_side": None,
                "research_persistence_error": (
                    getattr(verdict, "research_persistence_error", None) or detail
                ),
            }
        )

    run_id = str(getattr(verdict, "research_run_id", None) or "").strip()
    expected_ticker = str(ticker or "").strip()
    expected_fingerprint = str(_research_contract_fingerprint(verdict) or "").strip()
    snapshot_getter = getattr(store, "get_dossier_snapshot", None)
    evidence_getter = getattr(store, "get_research_run_evidence", None)
    if (
        not run_id
        or not expected_ticker
        or not expected_fingerprint
        or not callable(snapshot_getter)
        or not callable(evidence_getter)
    ):
        return persistence_unverified("persisted run identity is unavailable")
    try:
        snapshot = await snapshot_getter(expected_ticker)
        run_evidence = await evidence_getter(expected_ticker, run_id)
    except Exception as exc:
        return persistence_unverified(
            f"persisted status verification failed: {exc}"
        )
    snapshot_identity_matches = (
        snapshot is not None
        and str(getattr(snapshot, "market_ticker", "") or "").strip()
        == expected_ticker
        and str(getattr(snapshot, "last_research_run_id", "") or "").strip() == run_id
        and str(getattr(snapshot, "last_contract_fingerprint", "") or "").strip()
        == expected_fingerprint
    )
    evidence_identity_matches = bool(run_evidence) and all(
        str(getattr(item, "contract_fingerprint", "") or "").strip()
        == expected_fingerprint
        for item in run_evidence
    )
    if not snapshot_identity_matches or not evidence_identity_matches:
        return persistence_unverified("persisted run identity does not match verdict")
    stored_status = str(getattr(snapshot, "last_verdict_status", "") or "")
    if not stored_status:
        return persistence_unverified("persisted verdict status is unavailable")
    if stored_status == _status_value(getattr(verdict, "status", "")):
        return verdict
    return SimpleNamespace(
        **{
            **getattr(verdict, "__dict__", {}),
            "status": stored_status,
            "skip_reason": getattr(snapshot, "last_skip_reason", None),
            "research_pending_origin": getattr(
                snapshot,
                "last_research_pending_origin",
                None,
            ),
            "force_side": getattr(snapshot, "last_force_side", None),
            "estimated_probability": getattr(
                snapshot,
                "last_estimated_probability",
                None,
            ),
            "confidence": getattr(snapshot, "last_confidence", None),
            "market_price": getattr(snapshot, "last_market_price", None),
            "estimated_edge": getattr(snapshot, "last_estimated_edge", None),
        }
    )


def _failure_result_for_market(
    market: Any,
    result: BaseException,
) -> ResearchPrewarmResult:
    return ResearchPrewarmResult(
        market_ticker=str(getattr(market, "ticker", "") or ""),
        status="error",
        attempted=True,
        error=str(result),
    )


async def _write_research_prewarm_result(result: ResearchPrewarmResult) -> None:
    if result.status in {"skipped_cooldown", "skipped_terminal"}:
        return
    await write_trade_log_async(
        trade_log.log_research_prewarm_result,
        ticker=result.market_ticker,
        status=result.status,
        attempted=result.attempted,
        query_count=result.query_count,
        evidence_count=result.evidence_count,
        skip_reason=result.skip_reason,
        research_pending_origin=result.research_pending_origin,
        error=result.error,
        research_run_id=result.research_run_id,
        research_contract_fingerprint=result.research_contract_fingerprint,
        research_persisted=result.research_persisted,
        research_persistence_error=result.research_persistence_error,
        research_direct_fetch_failures=list(result.research_direct_fetch_failures),
        research_direct_fetch_failure_count=len(result.research_direct_fetch_failures),
        research_timeout_stage=result.research_timeout_stage,
        research_provider_error_count=result.research_provider_error_count,
        research_provider_error_attributions=list(
            result.research_provider_error_attributions
        ),
        research_generic_search_circuit_state=(
            result.research_generic_search_circuit_state
        ),
        research_generic_search_failure_classes=list(
            result.research_generic_search_failure_classes
        ),
        research_generic_search_attempt_delta=(
            result.research_generic_search_attempt_delta
        ),
        research_generic_search_blocked_call_delta=(
            result.research_generic_search_blocked_call_delta
        ),
    )


def _parse_utc_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def run_once(
    markets: Iterable[Any],
    *,
    store: ResearchDossierStore | None = None,
    search_provider: SearchProvider = default_search_provider,
    direct_fetcher: DirectFetcher = default_direct_fetcher,
    adjudicator: ResearchAdjudicator | None = None,
) -> list[ResearchPrewarmResult]:
    return await ResearchPrewarmTask(
        store=store,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
    ).run_once(markets)
