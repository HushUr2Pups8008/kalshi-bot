"""Polymarket feedback round-trip simulation.

Drives the Polymarket paper path through the same shared infrastructure used
by Kalshi-style simulations:

    1. Polymarket market match + analysis routing
    2. BlendTask queue handoff
    3. TradeExecutor paper execution
    4. PaperTrader row insert
    5. SettlementReconciler resolution
    6. keyword/source/calibration/report feedback

Read-only contract: writes only to a temp SQLite DB and a temp JSONL report
fixture owned by the harness; nothing under ``data/`` is touched.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config as _cfg_module  # noqa: E402
from analysis.decision_blender import BlendResult  # noqa: E402
from feeds import NewsItem  # noqa: E402
from polymarket.models import PolymarketMarket  # noqa: E402
from polymarket.paper_runtime import PolymarketPaperRuntime  # noqa: E402
from polymarket.settlement_reconciler import SettlementReconciler  # noqa: E402
from scripts.pipeline_feedback_report import summarize_events  # noqa: E402
from tasks.blend_task import BlendDecisionLogger, BlendTask, TradeCandidate  # noqa: E402
from tasks.calibration_task import CalibrationTask  # noqa: E402
from tasks.stats.source_stats import SourceStats  # noqa: E402
from tasks.trade_readiness_gate import ReadinessDecision  # noqa: E402
from trading.executor import TradeExecutor  # noqa: E402
from trading.paper_trader import PaperTrader  # noqa: E402
from trading.venue import Venue  # noqa: E402


_DEFAULT_BANKROLL = 500.0
_SOURCE = "Polymarket Simulation Wire"
_TICKER = "ewc-usgub-ks-2026-11-03-dem"


@dataclass(frozen=True)
class PolymarketFeedbackReport:
    ticker: str
    routed_count: int
    blend_enqueued: bool
    trade_id: str | None
    paper_row: dict[str, Any]
    resolved_row: dict[str, Any]
    settlement_checked: int
    settlement_resolved: int
    settlement_not_found: int
    keyword_outcomes: dict[str, dict[str, Any]]
    source_stats: dict[str, int]
    source_credibility: dict[str, Any]
    calibration_samples: dict[str, int]
    feedback_report: dict[str, Any]


class _FakeClient:
    def __init__(self, markets: list[PolymarketMarket]):
        self._markets = markets

    def get_markets(self, *, limit: int) -> tuple[list[PolymarketMarket], None]:
        return self._markets[:limit], None


class _FakeSettlementSource:
    def get_settlement(self, market_id: str) -> dict[str, Any]:
        if market_id != _TICKER:
            raise KeyError(market_id)
        return {"settled": True, "resolvedOutcome": "YES"}


class _EmptyStore:
    async def get_dossier(self, market_ticker: str) -> None:
        return None

    async def get_structural_prior(self, market_ticker: str) -> None:
        return None

    async def get_recent_evidence(
        self,
        market_ticker: str,
        *,
        limit: int = 100,
    ) -> list[Any]:
        return []


class _RecordingBlendLogger(BlendDecisionLogger):
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def log_blend_decision(self, **kwargs: Any) -> None:
        self.records.append({"type": "BLEND_DECISION", **kwargs})

    def log_skipped(self, **kwargs: Any) -> None:
        self.records.append({"type": "SKIPPED", **kwargs})

    def log_gate_summary(self, **kwargs: Any) -> None:
        self.records.append({"type": "GATE_SUMMARY", **kwargs})

    def log_lane_skipped(self, **kwargs: Any) -> None:
        self.records.append({"type": "LANE_SKIPPED", **kwargs})


def _news() -> NewsItem:
    return NewsItem(
        headline="Kansas governor election tightens after new polling",
        url="https://example.invalid/polymarket-feedback-roundtrip",
        source=_SOURCE,
        published=datetime(2026, 6, 10, tzinfo=UTC),
        body="Kansas governor election polling moved the race.",
    )


def _market() -> PolymarketMarket:
    return PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id=_TICKER,
        title="Democratic Party",
        question="Kansas Governor Election Winner",
        subtitle="2026 race",
        category="politics",
        status="open",
        yes_ask_cents=42,
        no_ask_cents=59,
        volume_dollars=1000.0,
        open_interest_dollars=100.0,
        close_time="2026-12-31T23:59:59Z",
        is_binary=True,
    )


async def _estimate_probability(
    news: NewsItem,
    market: PolymarketMarket,
    *,
    keyword_stats: Any = None,
    match_meta: dict[str, Any] | None = None,
) -> tuple[float, float, list[str], str, str, str, float]:
    if match_meta is None or not match_meta.get("pre_llm_quality_pass"):
        raise ValueError("expected shared match metadata")
    return 0.65, 0.8, [], "polymarket simulation reason", "yes", "moderate", 0.8


def _pass_readiness(_input: Any, regime_confidence: float) -> ReadinessDecision:
    return ReadinessDecision(
        passed=True,
        failure_reasons=(),
        trade_blocked_reason=None,
        readiness_gate_min_edge_override=0.02,
        scaled_confidence=0.8,
        regime_confidence=regime_confidence,
        fail_safe_active=False,
        applied_conditions=("simulation",),
    )


def _blend_override(**kwargs: Any) -> BlendResult:
    fast = kwargs["fast"]
    return BlendResult(
        blended_p=fast.p,
        blended_confidence=fast.confidence,
        disagreement_score=0.0,
        blend_mode="fast_only",
        readiness_gate_min_edge_override=0.02,
        trade_blocked_reason=None,
        fast_lane_p=fast.p,
        fast_lane_confidence=fast.confidence,
        accumulation_p=None,
        accumulation_confidence=None,
        structural_p=None,
        structural_confidence=None,
    )


def _read_row(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def _keyword_outcomes(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT keyword, series_ticker, correct FROM keyword_outcomes "
        "ORDER BY keyword"
    ).fetchall()
    return {str(row["keyword"]): dict(row) for row in rows}


def _source_stats(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT posts_seen, signals FROM source_stats WHERE source = ?",
        (_SOURCE,),
    ).fetchone()
    return dict(row) if row else {}


def _source_credibility(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT wins, losses, total, accuracy FROM source_credibility WHERE source = ?",
        (_SOURCE,),
    ).fetchone()
    return dict(row) if row else {}


def _write_report_fixture(
    *,
    path: Path,
    blend_records: list[dict[str, Any]],
    trade_id: str,
    paper_row: dict[str, Any],
    resolved_row: dict[str, Any],
    calibration_samples: dict[str, int],
) -> None:
    events: list[dict[str, Any]] = [
        {
            "type": "SIGNAL_ANALYSIS_DETAIL",
            "ts": "2026-06-10T00:00:00+00:00",
            "ticker": _TICKER,
            "venue": Venue.POLYMARKET_US.value,
            "series_ticker": Venue.POLYMARKET_US.value,
            "source_class": "newswire",
        },
        {
            "type": "MATCH_LLM_REVIEW",
            "ts": "2026-06-10T00:00:01+00:00",
            "ticker": _TICKER,
            "venue": Venue.POLYMARKET_US.value,
            "matched_tokens": json.loads(paper_row["keywords_matched"]),
        },
    ]
    for record in blend_records:
        event = dict(record)
        event.setdefault("ts", "2026-06-10T00:00:02+00:00")
        event.setdefault("venue", Venue.POLYMARKET_US.value)
        event.setdefault("ticker", _TICKER)
        events.append(event)
    events.extend(
        [
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-10T00:00:03+00:00",
                "trade_id": trade_id,
                "ticker": _TICKER,
                "venue": Venue.POLYMARKET_US.value,
                "series_ticker": Venue.POLYMARKET_US.value,
                "keywords_matched": json.loads(paper_row["keywords_matched"]),
            },
            {
                "type": "PAPER_RESOLUTION",
                "ts": "2026-06-10T00:00:04+00:00",
                "trade_id": trade_id,
                "ticker": _TICKER,
                "venue": Venue.POLYMARKET_US.value,
                "resolved_yes": bool(resolved_row["resolved_yes"]),
            },
        ]
    )
    for lane, count in calibration_samples.items():
        for _idx in range(count):
            events.append(
                {
                    "type": "CALIBRATION_CHECK",
                    "ts": "2026-06-10T00:00:05+00:00",
                    "ticker": _TICKER,
                    "venue": Venue.POLYMARKET_US.value,
                    "lane": lane,
                }
            )

    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")


async def _run_async(root: Path) -> PolymarketFeedbackReport:
    cfg = _cfg_module.cfg
    old_values = {
        "is_paper_trading": cfg.is_paper_trading,
        "bankroll": cfg.bankroll,
        "max_ticker_exposure_pct": cfg.max_ticker_exposure_pct,
        "kelly_fraction": cfg.kelly_fraction,
        "paper_ticker_cooldown": cfg.paper_ticker_cooldown,
        "series_correlation_window_seconds": cfg.series_correlation_window_seconds,
    }
    cfg.is_paper_trading = True
    cfg.bankroll = _DEFAULT_BANKROLL
    cfg.max_ticker_exposure_pct = 0.25
    cfg.kelly_fraction = 0.5
    cfg.paper_ticker_cooldown = 0
    cfg.series_correlation_window_seconds = 0
    try:
        db_path = root / "polymarket_feedback_roundtrip.db"
        calibration_task = CalibrationTask()
        paper = PaperTrader(
            db_path=db_path,
            startup_context="test",
            calibration_task=calibration_task,
        )
        paper._set_state("notional_bankroll", str(_DEFAULT_BANKROLL))
        source_stats = SourceStats(db_path=db_path)
        source_stats.increment_posts(_SOURCE)

        queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
        blend_logger = _RecordingBlendLogger()
        blend_task = BlendTask(
            trading_queue=queue,
            store=_EmptyStore(),
            logger=blend_logger,
            blender=_blend_override,
            readiness_evaluator=_pass_readiness,
            is_paper_mode=True,
            now=lambda: datetime(2026, 6, 10, tzinfo=UTC),
        )
        executor = TradeExecutor(
            rest_client=MagicMock(name="rest_client_unused_for_polymarket"),
            paper_trader=paper,
        )

        async def route_analysis(analysis: Any, **kwargs: Any) -> Any:
            if kwargs != {"accumulate": True, "watch": False}:
                raise AssertionError(f"unexpected route kwargs: {kwargs!r}")
            return await blend_task.process_fast_lane_result(analysis)

        runtime = PolymarketPaperRuntime(
            client=_FakeClient([_market()]),
            route_analysis=route_analysis,
            keyword_stats=None,
            source_stats=source_stats,
            estimate_probability_fn=_estimate_probability,
            market_limit=10,
        )

        routed_count = await runtime.process_news(_news())
        source_stats.flush()
        if queue.qsize() != 1:
            raise AssertionError(f"expected one blended candidate, got {queue.qsize()}")
        candidate = await queue.get()
        trade_id = await executor.execute(candidate)
        if not trade_id:
            raise AssertionError("expected paper trade id from Polymarket candidate")

        settlement = SettlementReconciler(
            source=_FakeSettlementSource(),
            resolver=paper,
        ).reconcile()
        await paper.record_resolution_calibration_events(settlement.lane_events)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            paper_row = _read_row(
                conn,
                "SELECT * FROM paper_trades WHERE trade_id = ?",
                (trade_id,),
            )
            resolved_row = dict(paper_row)
            keyword_outcomes = _keyword_outcomes(conn)
            source_stats_row = _source_stats(conn)
            source_credibility = _source_credibility(conn)
        finally:
            conn.close()

        calibration_samples = {
            lane: state.sample_count
            for lane, state in calibration_task._state.lanes.items()
            if state.sample_count
        }
        report_log_path = root / "polymarket_feedback_roundtrip.jsonl"
        _write_report_fixture(
            path=report_log_path,
            blend_records=blend_logger.records,
            trade_id=trade_id,
            paper_row=paper_row,
            resolved_row=resolved_row,
            calibration_samples=calibration_samples,
        )
        feedback_report = summarize_events([report_log_path])

        return PolymarketFeedbackReport(
            ticker=_TICKER,
            routed_count=routed_count,
            blend_enqueued=bool(blend_logger.records)
            and any(record["type"] == "BLEND_DECISION" for record in blend_logger.records),
            trade_id=trade_id,
            paper_row=paper_row,
            resolved_row=resolved_row,
            settlement_checked=settlement.checked,
            settlement_resolved=settlement.resolved,
            settlement_not_found=settlement.not_found,
            keyword_outcomes=keyword_outcomes,
            source_stats=source_stats_row,
            source_credibility=source_credibility,
            calibration_samples=calibration_samples,
            feedback_report=feedback_report,
        )
    finally:
        for key, value in old_values.items():
            setattr(cfg, key, value)


def run(*, db_root: Optional[Path] = None) -> PolymarketFeedbackReport:
    if db_root is not None:
        db_root.mkdir(parents=True, exist_ok=True)
        return asyncio.run(_run_async(db_root))

    with tempfile.TemporaryDirectory(prefix="polymarket-feedback-roundtrip-") as tmp:
        return asyncio.run(_run_async(Path(tmp)))


def _print_report(report: PolymarketFeedbackReport) -> None:
    print("=" * 90)
    print("Polymarket feedback round-trip simulation")
    print("=" * 90)
    print(f"ticker          : {report.ticker}")
    print(f"routed          : {report.routed_count}")
    print(f"blend enqueued  : {report.blend_enqueued}")
    print(f"paper trade     : {report.trade_id}")
    print(
        "paper row       : "
        f"venue={report.paper_row.get('venue')} "
        f"series={report.paper_row.get('series_ticker')} "
        f"keywords={report.paper_row.get('keywords_matched')}"
    )
    print(
        "settlement      : "
        f"checked={report.settlement_checked} "
        f"resolved={report.settlement_resolved} "
        f"not_found={report.settlement_not_found}"
    )
    print(f"keyword outcomes: {sorted(report.keyword_outcomes)}")
    print(f"source stats    : {report.source_stats}")
    print(f"credibility     : {report.source_credibility}")
    print(f"calibration     : {report.calibration_samples}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Polymarket feedback round-trip sim against a temp DB.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    parser.add_argument(
        "--db-root",
        type=Path,
        default=None,
        help="optional persistent DB directory (default: tempdir, deleted on exit)",
    )
    args = parser.parse_args(argv)

    report = run(db_root=args.db_root)
    if args.json:
        json.dump(asdict(report), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
