"""Scheduled research dossier prewarm task.

This task owns orchestration only: select market snapshots, run the existing
research gate in non-live mode, persist dossier evidence through the store, and
return cycle results. It does not blend, size, gate, or execute trades.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Iterable

from analysis.research_gate import (
    DirectFetcher,
    ResearchAdjudicator,
    SearchProvider,
    default_direct_fetcher,
    default_search_provider,
    market_has_research_source_path,
    run_research_gate,
)
from tasks.research_dossier import ResearchDossierStore, default_store
from utils.logger import get_logger, trade_log, write_trade_log_async

_log = get_logger("research_prewarm_task")

ResearchGateRunner = Callable[..., Awaitable[Any]]
ResearchPrewarmResultSink = Callable[["ResearchPrewarmResult"], Awaitable[None]]


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
    error: str | None = None
    research_run_id: str | None = None
    research_contract_fingerprint: str | None = None
    research_persisted: bool | None = None
    research_persistence_error: str | None = None
    research_direct_fetch_failures: tuple[str, ...] = ()


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
        max_concurrency: int = 3,
        result_sink: ResearchPrewarmResultSink | None = None,
    ) -> None:
        self.store = store or default_store()
        self.research_gate = research_gate
        self.search_provider = search_provider
        self.direct_fetcher = direct_fetcher
        self.adjudicator = adjudicator
        self.max_queries = int(max_queries)
        self.research_timeout_seconds = float(research_timeout_seconds)
        self.max_concurrency = max(1, int(max_concurrency))
        self.result_sink = result_sink or _write_research_prewarm_result

    async def process_market(self, market: Any) -> ResearchPrewarmResult:
        ticker = str(getattr(market, "ticker", "") or "")
        if not ticker:
            raise ResearchPrewarmError("market ticker is required")
        market_status = str(getattr(market, "status", "open") or "open").lower()
        if market_status not in {"open", "active"}:
            return ResearchPrewarmResult(
                market_ticker=ticker,
                status="skipped_closed",
                attempted=False,
            )
        if not market_has_research_source_path(market):
            return ResearchPrewarmResult(
                market_ticker=ticker,
                status="skipped_unresearchable",
                attempted=False,
                skip_reason="missing_source_path",
            )
        try:
            await self.store.initialize()
            verdict = await self.research_gate(
                _prewarm_news(market),
                market,
                model_direction="neutral",
                model_confidence=0.0,
                model_reason="scheduled research prewarm",
                yes_ask=_ask_probability(market, "yes_ask_cents", "yes_ask"),
                no_ask=_ask_probability(market, "no_ask_cents", "no_ask"),
                live_mode=False,
                search_provider=self.search_provider,
                direct_fetcher=self.direct_fetcher,
                adjudicator=self.adjudicator,
                dossier_store=self.store,
                max_queries=self.max_queries,
                research_timeout_seconds=self.research_timeout_seconds,
            )
            return ResearchPrewarmResult(
                market_ticker=ticker,
                status=_status_value(getattr(verdict, "status", "")),
                attempted=bool(getattr(verdict, "attempted", False)),
                query_count=len(getattr(verdict, "queries", ()) or ()),
                evidence_count=len(getattr(verdict, "evidence", ()) or ()),
                skip_reason=getattr(verdict, "skip_reason", None),
                research_run_id=getattr(verdict, "research_run_id", None),
                research_contract_fingerprint=getattr(
                    verdict,
                    "log_fields",
                    lambda: {},
                )().get("research_contract_fingerprint"),
                research_persisted=getattr(verdict, "research_persisted", None),
                research_persistence_error=getattr(
                    verdict,
                    "research_persistence_error",
                    None,
                ),
                research_direct_fetch_failures=tuple(
                    getattr(verdict, "research_direct_fetch_failures", ()) or ()
                ),
            )
        except Exception as exc:
            raise ResearchPrewarmError(f"failed research prewarm for {ticker}") from exc

    async def emit_result(self, result: ResearchPrewarmResult) -> None:
        await self.result_sink(result)

    async def run_once(self, markets: Iterable[Any]) -> list[ResearchPrewarmResult]:
        market_list = list(markets)
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
                await self.emit_result(_failure_result_for_market(market, result))
            else:
                ok.append(result)
                await self.emit_result(result)
        if failed:
            _log.warning(
                "[RESEARCH_PREWARM] %d/%d markets failed this cycle",
                failed,
                len(raw),
            )
        return ok

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


def _prewarm_news(market: Any) -> SimpleNamespace:
    return SimpleNamespace(
        headline="",
        source="research_prewarm",
        url="",
    )


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


def _status_value(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value or "")


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
    await write_trade_log_async(
        trade_log.log_research_prewarm_result,
        ticker=result.market_ticker,
        status=result.status,
        attempted=result.attempted,
        query_count=result.query_count,
        evidence_count=result.evidence_count,
        skip_reason=result.skip_reason,
        error=result.error,
        research_run_id=result.research_run_id,
        research_contract_fingerprint=result.research_contract_fingerprint,
        research_persisted=result.research_persisted,
        research_persistence_error=result.research_persistence_error,
        research_direct_fetch_failures=list(result.research_direct_fetch_failures),
        research_direct_fetch_failure_count=len(result.research_direct_fetch_failures),
    )


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
