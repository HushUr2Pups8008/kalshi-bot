"""Time-driven structural prior recompute task (S3.2).

This task owns orchestration only: select markets, decide whether a structural
prior recompute is needed, call the pure analysis function, persist the result,
and emit telemetry. It does not blend, gate trades, or execute anything.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol

from analysis.evidence_types import PriorEstimate
from analysis.structural_prior import compute_structural_prior
from kalshi import KalshiMarket
from tasks.evidence_store import (
    DossierState,
    EvidenceRecord,
    EvidenceStore,
    StructuralPriorRecord,
    default_store,
)
from utils.logger import get_logger, trade_log, write_trade_log_async

# Use the project's logger factory so messages reach bot.log + errors.log.
# Raw `logging.getLogger(...)` returns a logger with no handlers, and the
# root logger has no handlers either, so its records are silently dropped.
_log = get_logger("structural_task")


class StructuralTaskError(Exception):
    """Base class for structural task orchestration failures."""


class StructuralComputationError(StructuralTaskError):
    """Raised when one market's structural recompute path fails."""


class StructuralPriorComputer(Protocol):
    def __call__(self, market: KalshiMarket, context: dict[str, Any]) -> PriorEstimate:
        ...


@dataclass(frozen=True)
class StructuralTaskResult:
    market_ticker: str
    status: str
    recompute_trigger: str | None = None
    prior: PriorEstimate | None = None
    error: str | None = None


class StructuralTask:
    """Scheduled structural-prior recompute orchestrator."""

    def __init__(
        self,
        *,
        store: EvidenceStore | None = None,
        computer: StructuralPriorComputer = compute_structural_prior,
        logger=trade_log,
        recent_evidence_limit: int = 100,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store or default_store()
        self.computer = computer
        self.logger = logger
        self.recent_evidence_limit = recent_evidence_limit
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def process_market(self, market: KalshiMarket) -> StructuralTaskResult:
        """Evaluate one market and recompute/persist if the contract requires it."""
        ticker = market.ticker
        try:
            dossier = await self.store.get_dossier(ticker)
            if dossier is None:
                return StructuralTaskResult(market_ticker=ticker, status="missing_dossier")

            existing_prior = await self.store.get_structural_prior(ticker)
            recompute_trigger = _recompute_trigger(dossier, existing_prior)
            if recompute_trigger is None:
                return StructuralTaskResult(
                    market_ticker=ticker,
                    status="skipped",
                )

            evidence_records = await self.store.get_recent_evidence(
                ticker,
                limit=self.recent_evidence_limit,
            )
            context = _build_context(
                dossier=dossier,
                structural_prior=existing_prior,
                evidence_records=evidence_records,
                now_ts=self._now().isoformat(),
            )
            new_prior = self.computer(market, context)
            await self.store.update_structural_prior(
                new_prior,
                recompute_trigger=recompute_trigger,
            )
            await write_trade_log_async(
                self.logger.log_structural_prior_recompute,
                market_ticker=ticker,
                prior_estimate=(
                    existing_prior.prior_estimate
                    if existing_prior is not None and existing_prior.prior_estimate is not None
                    else 0.0
                ),
                new_estimate=new_prior.estimate,
                input_sources=_input_sources(evidence_records),
                llm_called=new_prior.llm_called,
                token_count=int(context["token_count"]),
            )
            return StructuralTaskResult(
                market_ticker=ticker,
                status="recomputed",
                recompute_trigger=recompute_trigger,
                prior=new_prior,
            )
        except Exception as exc:
            raise StructuralComputationError(
                f"failed structural recompute for {ticker}"
            ) from exc

    async def run_once(
        self,
        markets: Iterable[KalshiMarket],
    ) -> list[StructuralTaskResult]:
        """Process a provided market snapshot once.

        Per-market failures are logged and excluded from the return value so
        that one bad market cannot abort the entire recompute cycle.
        """
        raw = await asyncio.gather(
            *(self.process_market(market) for market in markets),
            return_exceptions=True,
        )
        ok: list[StructuralTaskResult] = []
        failed = 0
        for r in raw:
            if isinstance(r, BaseException):
                failed += 1
                _log.warning("[STRUCTURAL] per-market recompute failed: %s", r)
            else:
                ok.append(r)
        if failed:
            _log.warning(
                "[STRUCTURAL] %d/%d markets failed recompute this cycle",
                failed,
                len(raw),
            )
        return ok

    async def run_periodic(
        self,
        market_provider: Callable[[], Iterable[KalshiMarket]],
        *,
        interval_seconds: float,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run until cancelled or until ``stop_event`` is set."""
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while stop_event is None or not stop_event.is_set():
            markets = list(market_provider())
            markets_to_process = await self._dossier_backed_markets(markets)
            results = await self.run_once(markets_to_process)
            status_counts: dict[str, int] = {}
            for result in results:
                status_counts[result.status] = status_counts.get(result.status, 0) + 1
            _log.info(
                "[STRUCTURAL] recompute cycle active_markets=%d dossier_markets=%d results=%s",
                len(markets),
                len(markets_to_process),
                status_counts or {},
            )
            if stop_event is None:
                await asyncio.sleep(interval_seconds)
            else:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                except asyncio.TimeoutError:
                    continue

    async def _dossier_backed_markets(
        self,
        markets: list[KalshiMarket],
    ) -> list[KalshiMarket]:
        """Return active markets that have dossiers.

        ``process_market`` already skips missing dossiers without side effects.
        Filtering here keeps the scheduled loop from spending an entire cycle
        opening SQLite connections for hundreds of no-op active markets.
        """
        dossier_tickers = set(await self.store.list_dossier_market_tickers())
        if not dossier_tickers:
            return []
        return [market for market in markets if market.ticker in dossier_tickers]


def _recompute_trigger(
    dossier: DossierState,
    structural_prior: StructuralPriorRecord | None,
) -> str | None:
    if structural_prior is None:
        return "scheduled"
    if dossier.updated_ts > structural_prior.computed_ts:
        return "dossier_update"
    return None


def _build_context(
    *,
    dossier: DossierState,
    structural_prior: StructuralPriorRecord | None,
    evidence_records: list[EvidenceRecord],
    now_ts: str,
) -> dict[str, Any]:
    return {
        "dossier": dossier,
        "structural_prior": structural_prior,
        "evidence_records": evidence_records,
        "llm_called": False,
        "llm_estimate": None,
        "token_count": 0,
        "now_ts": now_ts,
    }


def _input_sources(evidence_records: list[EvidenceRecord]) -> list[str]:
    seen: set[str] = set()
    sources: list[str] = []
    for record in evidence_records:
        source = f"{record.source_class}:{record.source}"
        if source not in seen:
            sources.append(source)
            seen.add(source)
    return sources


async def run_once(
    markets: Iterable[KalshiMarket],
    *,
    store: EvidenceStore | None = None,
    computer: StructuralPriorComputer = compute_structural_prior,
    logger=trade_log,
) -> list[StructuralTaskResult]:
    return await StructuralTask(
        store=store,
        computer=computer,
        logger=logger,
    ).run_once(markets)
