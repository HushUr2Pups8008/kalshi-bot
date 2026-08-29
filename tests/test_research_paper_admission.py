from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
import sqlite3

import pytest

from analysis.research_gate import ResearchEvidence
from kalshi import KalshiMarket
from scripts.decision_funnel_summary import summarize
from tasks.blend_task import TradeCandidate
from tasks.research_dossier import ResearchDossierSnapshot
from tasks.blend_task import BlendTask
from tasks.research_paper_admission import (
    ResearchBackedBlendStore,
    ResearchPaperAdmissionBridge,
    ResearchPaperSignal,
    ResearchPaperSignalProvider,
    _has_counter_query,
    _market_venue,
)
from tasks.research_prewarm_task import ResearchPrewarmResult
from trading.orderbook import ExecutableLiquidity
from utils.logger import TradeLogger


class FakeResearchStore:
    def __init__(
        self,
        *,
        snapshot: ResearchDossierSnapshot | None,
        evidence: list[ResearchEvidence],
        has_counter_query: bool = True,
        query_texts: list[str] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.evidence = evidence
        self.has_counter_query = has_counter_query
        self.query_texts = query_texts or []
        self.claims: set[tuple[str, str, str]] = set()
        self.completions: list[dict] = []
        self.snapshot_calls = 0

    async def get_dossier_snapshot(
        self,
        market_ticker: str,
    ) -> ResearchDossierSnapshot | None:
        self.snapshot_calls += 1
        return self.snapshot

    async def get_research_run_evidence(
        self,
        market_ticker: str,
        research_run_id: str,
    ) -> list[ResearchEvidence]:
        return self.evidence

    async def has_research_run_query_intent(
        self,
        research_run_id: str,
        intents: set[str],
    ) -> bool:
        return self.has_counter_query

    async def get_research_run_query_texts(
        self,
        research_run_id: str,
    ) -> list[str]:
        return self.query_texts

    async def claim_research_paper_admission(
        self,
        market_ticker: str,
        research_run_id: str,
        contract_fingerprint: str,
    ) -> bool:
        key = (market_ticker, research_run_id, contract_fingerprint)
        if key in self.claims:
            return False
        self.claims.add(key)
        return True

    async def complete_research_paper_admission(
        self,
        market_ticker: str,
        research_run_id: str,
        contract_fingerprint: str,
        *,
        state: str,
        enqueued: bool | None,
        outcome_reason: str | None,
    ) -> None:
        self.completions.append(
            {
                "key": (market_ticker, research_run_id, contract_fingerprint),
                "state": state,
                "enqueued": enqueued,
                "outcome_reason": outcome_reason,
            }
        )


class SpyLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.skipped_records: list[dict] = []
        self.gate_summary_records: list[dict] = []
        self.lane_skipped_records: list[dict] = []
        self.opportunity_records: list[dict] = []

    def log_blend_decision(self, **kwargs) -> None:
        self.records.append(kwargs)

    def log_opportunity(self, **kwargs) -> None:
        self.opportunity_records.append(kwargs)

    def log_skipped(self, **kwargs) -> None:
        self.skipped_records.append(kwargs)

    def log_gate_summary(self, **kwargs) -> None:
        self.gate_summary_records.append(kwargs)

    def log_lane_skipped(self, **kwargs) -> None:
        self.lane_skipped_records.append(kwargs)


def _market(*, venue: object | None = None) -> KalshiMarket:
    market = KalshiMarket(
        ticker="KXRESEARCH-1",
        title="Will decision-grade research support YES?",
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=100,
        open_interest=50,
        close_time="2026-07-10T00:00:00Z",
        status="active",
        regime_weights={"fast": 0.2, "interpretation": 0.7, "structural": 0.1},
        yes_bid_cents=49,
        yes_ask_cents=51,
        no_bid_cents=49,
        no_ask_cents=51,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
        liquidity_dollars=Decimal("1000"),
    )
    if venue is not None:
        market.venue = venue
    return market


@pytest.mark.parametrize(
    "report_venue",
    ["  POLYMARKET_US  ", SimpleNamespace(value="  POLYMARKET_US  ")],
)
def test_market_venue_uses_report_venue_when_primary_venue_is_missing(report_venue: object) -> None:
    assert _market_venue(SimpleNamespace(venue=None, report_venue=report_venue)) == "polymarket_us"


def _market_without_current_price() -> KalshiMarket:
    return KalshiMarket(
        ticker="KXRESEARCH-1",
        title="Will decision-grade research support YES?",
        yes_bid=0,
        yes_ask=0,
        yes_price=0,
        volume=100,
        open_interest=50,
        close_time="2026-07-10T00:00:00Z",
        status="active",
        price_available=False,
        price_source="unavailable",
        price_method="none",
    )


def _market_with_current_ask(*, yes_ask_cents: int, no_ask_cents: int) -> KalshiMarket:
    yes_bid_cents = max(1, min(yes_ask_cents - 1, 100 - no_ask_cents))
    return KalshiMarket(
        ticker="KXRESEARCH-1",
        title="Will decision-grade research support YES?",
        yes_bid=yes_bid_cents,
        yes_ask=yes_ask_cents,
        yes_price=yes_ask_cents,
        volume=100,
        open_interest=50,
        close_time="2026-07-10T00:00:00Z",
        status="active",
        regime_weights={"fast": 0.2, "interpretation": 0.7, "structural": 0.1},
        yes_bid_cents=yes_bid_cents,
        yes_ask_cents=yes_ask_cents,
        no_bid_cents=max(1, no_ask_cents - 1),
        no_ask_cents=no_ask_cents,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
        liquidity_dollars=Decimal("1000"),
    )


def _snapshot(
    *,
    status: str = "decision_grade_candidate",
    side: str = "yes",
    market_price: float = 0.51,
    estimated_probability: float = 0.64,
    estimated_edge: float | None = 0.12,
    last_researched_ts: str = "2026-07-02T16:00:00+00:00",
) -> ResearchDossierSnapshot:
    return ResearchDossierSnapshot(
        market_ticker="KXRESEARCH-1",
        last_research_run_id="rr-decision",
        last_contract_fingerprint="contract-v1",
        contract_question="Will decision-grade research support YES?",
        last_researched_ts=last_researched_ts,
        last_verdict_status=status,
        last_skip_reason=None,
        last_force_side=side,
        last_estimated_probability=estimated_probability,
        last_confidence=0.82,
        last_market_price=market_price,
        last_estimated_edge=estimated_edge,
        last_decision_grade_status=status,
    )


def _evidence(
    source_class: str,
    *,
    claim_type: str,
    direction: str,
    url: str,
) -> ResearchEvidence:
    return ResearchEvidence(
        source_class=source_class,
        source_name=source_class,
        source_url=url,
        title=f"{source_class} title",
        snippet=f"{claim_type} {direction}",
        claim_type=claim_type,
        supports_direction=direction,
        supports_confidence=0.9,
        retrieved_at="2026-07-02T16:00:00+00:00",
        inserted_at="2026-07-02T16:00:00+00:00",
        contract_fingerprint="contract-v1",
    )


def _valid_evidence() -> list[ResearchEvidence]:
    return [
        _evidence(
            "resolution_source",
            claim_type="settlement_source",
            direction="neutral",
            url="https://agency.gov/resolution",
        ),
        _evidence(
            "reputable_secondary",
            claim_type="supporting",
            direction="yes",
            url="https://reuters.com/story",
        ),
        _evidence(
            "reputable_secondary",
            claim_type="disconfirming",
            direction="no",
            url="https://apnews.com/story",
        ),
    ]


def _structured_official_weather_evidence() -> list[ResearchEvidence]:
    source_url = "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC"
    return [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url=source_url,
            title="NWS Central Park daily maximum for July 1, 2026: 93F",
            snippet=(
                "NWS Central Park climate report lists TODAY MAXIMUM 93F "
                "for July 1, 2026, versus the below 99F market range, supporting YES."
            ),
            claim_type="official_resolution",
            supports_direction="yes",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=93.0,
            metric_unit="fahrenheit",
            extraction_confidence=0.95,
            published_at="2026-07-01",
            retrieved_at="2026-07-02T16:00:00+00:00",
            inserted_at="2026-07-02T16:00:00+00:00",
            contract_fingerprint="contract-v1",
        ),
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url=source_url,
            title="NWS Central Park daily maximum countercheck for July 1, 2026: 93F",
            snippet=(
                "Disconfirming search checked the NWS Central Park daily maximum "
                "of 93F against the below 99F market range; no contrary official "
                "high-temperature fact was found."
            ),
            claim_type="disconfirming",
            supports_direction="neutral",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=93.0,
            metric_unit="fahrenheit",
            extraction_confidence=0.95,
            published_at="2026-07-01",
            retrieved_at="2026-07-02T16:00:00+00:00",
            inserted_at="2026-07-02T16:00:00+00:00",
            contract_fingerprint="contract-v1",
        ),
    ]


def _structured_official_weather_same_side_countercheck() -> list[ResearchEvidence]:
    evidence = _structured_official_weather_evidence()
    return [
        evidence[0],
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url=evidence[0].source_url,
            title="NWS Central Park daily maximum countercheck for July 1, 2026: 93F",
            snippet=(
                "Disconfirming search rechecked the NWS Central Park daily maximum "
                "of 93F against the below 99F market range and found the same "
                "official metric."
            ),
            claim_type="disconfirming",
            supports_direction="yes",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=93.0,
            metric_unit="fahrenheit",
            extraction_confidence=0.95,
            published_at="2026-07-01",
            retrieved_at="2026-07-02T16:00:00+00:00",
            inserted_at="2026-07-02T16:00:00+00:00",
            contract_fingerprint="contract-v1",
        ),
    ]


@pytest.mark.asyncio
async def test_decision_grade_dossier_enters_paper_review_blend_queue() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=logger,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.admitted is True
    assert result.enqueued is True
    assert queue.qsize() == 1
    candidate = queue.get_nowait()
    assert candidate.side == "yes"
    assert candidate.fast_lane_analysis.signal_type == "research_decision_grade"
    assert candidate.signal_meta["research_admission_status"] == "decision_grade_candidate"
    assert candidate.signal_meta["research_run_id"] == "rr-decision"
    assert candidate.signal_meta["lifecycle_id"].startswith("lc-")
    assert candidate.fast_lane_analysis.signal_meta["lifecycle_id"] == candidate.signal_meta["lifecycle_id"]
    assert logger.records
    assert logger.opportunity_records == [
        {
            "ticker": "KXRESEARCH-1",
            "market_title": "Will decision-grade research support YES?",
            "entry_price_cents": 51.0,
            "estimated_probability": 0.64,
            "edge": 0.12,
            "kelly_fraction": 0.0,
            "kelly_dollars": 0.0,
            "capped_dollars": 0.0,
            "side": "yes",
            "venue": "kalshi",
            "reasoning": "decision-grade research admitted for paper-review blend",
            "source": "reputable_secondary",
            "headline": "reputable_secondary title",
            "method": "research_decision_grade",
            "llm_direction": "yes",
            "source_class": "reputable_secondary",
            "evidence_id": "research:rr-decision:2",
            "settlement_source_match": False,
            "research_status": "decision_grade_candidate",
            "research_run_id": "rr-decision",
            "signal_type": "research_decision_grade",
            "lifecycle_id": candidate.signal_meta["lifecycle_id"],
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_venue", "expected_venue"),
    [
        ("  POLYMARKET_US  ", "polymarket_us"),
        (SimpleNamespace(value="  POLYMARKET_US  "), "polymarket_us"),
    ],
)
async def test_research_opportunity_normalizes_market_venue(
    raw_venue: object,
    expected_venue: str,
) -> None:
    logger = SpyLogger()
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=_valid_evidence(),
        ),
        trading_queue=asyncio.Queue(),
        logger=logger,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(venue=raw_venue),
    )

    assert result.admitted is True
    opportunity = logger.opportunity_records[0]
    assert (opportunity["venue"], opportunity["ticker"], opportunity["side"]) == (
        expected_venue,
        "KXRESEARCH-1",
        "yes",
    )


@pytest.mark.asyncio
async def test_research_admission_claim_routes_same_proof_once() -> None:
    store = FakeResearchStore(snapshot=_snapshot(), evidence=_valid_evidence())
    logger = SpyLogger()
    routed = []

    async def route_analysis(analysis, _store):
        routed.append(analysis)
        return SimpleNamespace(ready=True, enqueued=True, trade_blocked_reason=None)

    bridge = ResearchPaperAdmissionBridge(
        research_store=store,
        trading_queue=asyncio.Queue(),
        logger=logger,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
        route_analysis=route_analysis,
    )
    prewarm = ResearchPrewarmResult(
        market_ticker="KXRESEARCH-1",
        status="decision_grade_candidate",
        attempted=True,
        research_run_id="rr-decision",
        research_contract_fingerprint="contract-v1",
    )

    first = await bridge.admit_prewarm_result(prewarm, _market())
    second = await bridge.admit_prewarm_result(prewarm, _market())

    assert first.admitted is True
    assert second.admitted is False
    assert second.reason == "duplicate_research_admission"
    assert len(routed) == 1
    assert len(logger.opportunity_records) == 1
    assert store.completions == [
        {
            "key": ("KXRESEARCH-1", "rr-decision", "contract-v1"),
            "state": "completed",
            "enqueued": True,
            "outcome_reason": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_venue", "terminal_venue"),
    [
        (None, "kalshi"),
        (SimpleNamespace(value="  POLYMARKET_US  "), "polymarket_us"),
    ],
)
async def test_research_admission_opportunity_links_to_g7_terminal_in_same_window(
    tmp_path,
    raw_venue: object | None,
    terminal_venue: str,
) -> None:
    trade_log = TradeLogger(path=tmp_path / "trades.jsonl")

    async def route_analysis(analysis, _store):
        trade_log.log_skipped(
            reason="G7_open_exposure_drawdown",
            ticker=analysis.market.ticker,
            side=analysis.side,
            venue=terminal_venue,
            signal_meta=analysis.signal_meta,
        )
        return SimpleNamespace(
            ready=False,
            enqueued=False,
            trade_blocked_reason="G7_open_exposure_drawdown",
        )

    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(snapshot=_snapshot(), evidence=_valid_evidence()),
        trading_queue=asyncio.Queue(),
        logger=trade_log,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
        route_analysis=route_analysis,
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(venue=raw_venue),
    )

    attribution = summarize(tmp_path / "trades.jsonl", since=None, until=None)["same_window_lifecycle_attribution"]
    assert result.admitted is False
    assert result.reason == "G7_open_exposure_drawdown"
    assert attribution["opportunity_lifecycle_count"] == 1
    assert attribution["g7_skip_lifecycle_count"] == 1
    assert attribution["pending_opportunity_lifecycle_count"] == 0
    assert attribution["identity_incomplete_lifecycle_count"] == 0
    assert attribution["quarantined_lifecycle_count"] == 0


@pytest.mark.asyncio
async def test_research_admission_blocks_expired_market_before_dossier_load() -> None:
    store = FakeResearchStore(snapshot=_snapshot(), evidence=_valid_evidence())
    bridge = ResearchPaperAdmissionBridge(
        research_store=store,
        trading_queue=asyncio.Queue(),
        now=lambda: datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        replace(_market(), close_time="2026-07-10T00:00:00Z"),
    )

    assert result.admitted is False
    assert result.reason == "market_expired"
    assert store.snapshot_calls == 0


@pytest.mark.asyncio
async def test_research_admission_blocks_result_snapshot_identity_mismatch() -> None:
    store = FakeResearchStore(snapshot=_snapshot(), evidence=_valid_evidence())
    bridge = ResearchPaperAdmissionBridge(
        research_store=store,
        trading_queue=asyncio.Queue(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-stale",
            research_contract_fingerprint="contract-stale",
        ),
        _market(),
    )

    assert result.admitted is False
    assert result.reason == "research_result_snapshot_mismatch"
    assert store.claims == set()


@pytest.mark.asyncio
async def test_research_admission_blocks_cross_ticker_result_before_dossier_load() -> None:
    store = FakeResearchStore(snapshot=_snapshot(), evidence=_valid_evidence())
    logger = SpyLogger()
    route_calls = 0

    async def route_analysis(_analysis, _store):
        nonlocal route_calls
        route_calls += 1

    bridge = ResearchPaperAdmissionBridge(
        research_store=store,
        trading_queue=asyncio.Queue(),
        logger=logger,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
        route_analysis=route_analysis,
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-OTHER",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.reason == "research_market_identity_mismatch"
    assert store.snapshot_calls == 0
    assert store.claims == set()
    assert logger.opportunity_records == []
    assert route_calls == 0


@pytest.mark.asyncio
async def test_research_admission_checks_provider_identity_before_price_access() -> None:
    store = FakeResearchStore(snapshot=_snapshot(), evidence=_valid_evidence())
    signal = ResearchPaperSignal(
        market_ticker="KXRESEARCH-OTHER",
        research_run_id="rr-decision",
        contract_fingerprint="contract-v1",
        side="yes",
        estimated_probability=0.64,
        confidence=0.82,
        market_price=0.51,
        estimated_edge=0.12,
        researched_ts=datetime(2026, 7, 2, 16, 0, tzinfo=UTC),
        evidence=tuple(_valid_evidence()),
    )
    signal_provider = SimpleNamespace(
        store=store,
        get_signal=lambda _ticker: None,
    )

    async def get_signal(_ticker):
        return signal, None

    signal_provider.get_signal = get_signal

    def fail_price_access():
        raise AssertionError("identity must be checked before current price")

    market = SimpleNamespace(
        ticker="KXRESEARCH-1",
        status="active",
        close_time="2026-07-10T00:00:00Z",
        is_tradeable=fail_price_access,
    )
    bridge = ResearchPaperAdmissionBridge(
        signal_provider=signal_provider,
        trading_queue=asyncio.Queue(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        market,
    )

    assert result.reason == "research_result_snapshot_mismatch"
    assert store.claims == set()


@pytest.mark.asyncio
async def test_research_admission_route_failure_is_not_retried() -> None:
    store = FakeResearchStore(snapshot=_snapshot(), evidence=_valid_evidence())
    route_calls = 0

    async def route_analysis(_analysis, _store):
        nonlocal route_calls
        route_calls += 1
        raise RuntimeError("route failed")

    bridge = ResearchPaperAdmissionBridge(
        research_store=store,
        trading_queue=asyncio.Queue(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
        route_analysis=route_analysis,
    )
    prewarm = ResearchPrewarmResult(
        market_ticker="KXRESEARCH-1",
        status="decision_grade_candidate",
        attempted=True,
        research_run_id="rr-decision",
        research_contract_fingerprint="contract-v1",
    )

    with pytest.raises(RuntimeError, match="route failed"):
        await bridge.admit_prewarm_result(prewarm, _market())
    second = await bridge.admit_prewarm_result(prewarm, _market())

    assert second.reason == "duplicate_research_admission"
    assert route_calls == 1
    assert store.completions == [
        {
            "key": ("KXRESEARCH-1", "rr-decision", "contract-v1"),
            "state": "failed",
            "enqueued": None,
            "outcome_reason": "RuntimeError: route failed",
        }
    ]


@pytest.mark.asyncio
async def test_research_admission_requires_contract_fingerprint() -> None:
    store = FakeResearchStore(
        snapshot=replace(_snapshot(), last_contract_fingerprint=None),
        evidence=_valid_evidence(),
    )
    bridge = ResearchPaperAdmissionBridge(
        research_store=store,
        trading_queue=asyncio.Queue(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
        ),
        _market(),
    )

    assert result.reason == "missing_contract_fingerprint"
    assert store.claims == set()


@pytest.mark.asyncio
async def test_research_admission_blocks_store_without_durable_claim_support() -> None:
    store = FakeResearchStore(snapshot=_snapshot(), evidence=_valid_evidence())
    store.claim_research_paper_admission = None
    bridge = ResearchPaperAdmissionBridge(
        research_store=store,
        trading_queue=asyncio.Queue(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.reason == "admission_claim_unavailable"


class _TrackingSqliteConnection:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.closed = False

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def close(self) -> None:
        self.closed = True
        self.connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_counter_row", [False, True])
async def test_counter_query_fallback_closes_read_only_connection(
    monkeypatch,
    tmp_path,
    with_counter_row,
) -> None:
    db_path = tmp_path / "research.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE research_run_queries (research_run_id TEXT, query_intent TEXT)")
        if with_counter_row:
            conn.execute(
                "INSERT INTO research_run_queries VALUES (?, ?)",
                ("rr-decision", "disconfirming"),
            )
    real_connect = sqlite3.connect
    connections = []

    def tracking_connect(*args, **kwargs):
        tracked = _TrackingSqliteConnection(real_connect(*args, **kwargs))
        connections.append(tracked)
        return tracked

    monkeypatch.setattr(
        "tasks.research_paper_admission.sqlite3.connect",
        tracking_connect,
    )

    result = await _has_counter_query(SimpleNamespace(db_path=db_path), "rr-decision")

    assert result is with_counter_row
    assert len(connections) == 1
    assert connections[0].closed is True


@pytest.mark.asyncio
async def test_decision_grade_dossier_uses_injected_blend_route() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    routed = []

    async def route_analysis(analysis, store):
        routed.append((analysis, store))
        return SimpleNamespace(
            ready=True,
            enqueued=True,
            trade_blocked_reason=None,
        )

    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=logger,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
        route_analysis=route_analysis,
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.admitted is True
    assert result.enqueued is True
    assert queue.qsize() == 0
    assert len(routed) == 1
    analysis, store = routed[0]
    assert analysis.signal_type == "research_decision_grade"
    assert analysis.signal_meta["research_admission_status"] == "decision_grade_candidate"
    assert isinstance(store, ResearchBackedBlendStore)
    assert store.signal.research_run_id == "rr-decision"


@pytest.mark.asyncio
async def test_structured_official_signal_enters_paper_review_with_single_source_path() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=_structured_official_weather_evidence(),
        ),
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.admitted is True
    assert result.enqueued is True
    assert queue.get_nowait().fast_lane_analysis.signal_type == "research_decision_grade"


@pytest.mark.asyncio
async def test_structured_official_same_side_countercheck_enters_paper_review() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=_structured_official_weather_same_side_countercheck(),
        ),
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.admitted is True
    assert result.enqueued is True
    assert queue.get_nowait().fast_lane_analysis.signal_type == "research_decision_grade"


@pytest.mark.asyncio
async def test_terminal_decision_grade_dossier_enters_paper_review_blend_queue() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=logger,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="skipped_terminal",
            attempted=False,
            skip_reason="decision_grade_candidate",
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.admitted is True
    assert result.enqueued is True
    assert queue.qsize() == 1
    candidate = queue.get_nowait()
    assert candidate.signal_meta["research_admission_status"] == "decision_grade_candidate"
    assert candidate.signal_meta["research_run_id"] == "rr-decision"
    assert logger.records


@pytest.mark.asyncio
async def test_generic_trade_candidate_does_not_enter_paper_review() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(status="trade_candidate"),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="trade_candidate",
            attempted=True,
            research_run_id="rr-decision",
        ),
        _market(),
    )

    assert result.admitted is False
    assert result.reason == "not_decision_grade_candidate"
    assert queue.empty()


@pytest.mark.asyncio
async def test_no_counter_evidence_does_not_enter_paper_review() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=[
                _valid_evidence()[0],
                _valid_evidence()[1],
            ],
            has_counter_query=True,
        ),
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.admitted is False
    assert result.reason == "missing_counter_evidence"
    assert queue.empty()


@pytest.mark.asyncio
async def test_irrelevant_speech_evidence_does_not_enter_paper_review() -> None:
    ticker = "KXTRUMPMENTION-26JUL24-MAGA"
    question = (
        "What will Donald Trump say during White House Correspondents' Dinner originally scheduled for July 24th, 2026?"
    )
    evidence = [
        ResearchEvidence(
            source_class="rules_source",
            source_name="Kalshi",
            source_url="https://kalshi.com/markets/KXTRUMPMENTION",
            title="Contract terms",
            snippet="The rules define the dinner mention condition.",
            claim_type="rules",
            supports_direction="neutral",
            retrieved_at="2026-07-02T16:00:00+00:00",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="USA Today",
            source_url="https://usatoday.com/america-birthday",
            title="Celebrations start in DC for America's birthday",
            snippet="Officials expect tight security at the White House event.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at="2026-07-02T16:00:00+00:00",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="New York Times",
            source_url="https://nytimes.com/greenland",
            title="Trump discusses Greenland",
            snippet="Trump says the public will find out what happens next.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.1,
            retrieved_at="2026-07-02T16:00:00+00:00",
        ),
    ]
    research_store = FakeResearchStore(
        snapshot=replace(
            _snapshot(),
            market_ticker=ticker,
            contract_question=question,
        ),
        evidence=evidence,
        query_texts=[
            f"{question} If Donald Trump says MAGA / Make America Great Again "
            "as part of the dinner, then the market resolves Yes."
        ],
    )
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    bridge = ResearchPaperAdmissionBridge(
        research_store=research_store,
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker=ticker,
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        replace(_market(), ticker=ticker, title=question),
    )

    assert result.admitted is False
    assert result.reason == "missing_directional_support"
    assert queue.empty()


@pytest.mark.asyncio
async def test_unrelated_counter_boilerplate_does_not_enter_paper_review() -> None:
    ticker = "KXUSTRDAGREEMENT-26JUL01"
    question = "Will the US sign a trade agreement before July 1?"
    research_store = FakeResearchStore(
        snapshot=replace(
            _snapshot(),
            market_ticker=ticker,
            contract_question=question,
        ),
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Commerce Department",
                source_url="https://commerce.gov/trade-agreement",
                title="US signs bilateral trade agreement",
                snippet="Officials signed the trade agreement before July 1.",
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at="2026-07-02T16:00:00+00:00",
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Sports Wire",
                source_url="https://sports.example.com/objection",
                title="Opponent denied objection",
                snippet="The objection concerns an unrelated sports dispute.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.8,
                retrieved_at="2026-07-02T16:00:00+00:00",
            ),
        ],
        query_texts=[
            question,
            (f"{question} evidence against YES evidence against NO false not confirmed denied opponent objection"),
        ],
    )
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    bridge = ResearchPaperAdmissionBridge(
        research_store=research_store,
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker=ticker,
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        replace(_market(), ticker=ticker, title=question),
    )

    assert result.admitted is False
    assert result.reason == "missing_counter_evidence"
    assert queue.empty()


@pytest.mark.asyncio
async def test_decision_grade_dossier_requires_current_market_price() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    routed = []

    async def route_analysis(analysis, _store):
        routed.append(analysis)
        return SimpleNamespace(
            ready=True,
            enqueued=True,
            trade_blocked_reason=None,
        )

    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=logger,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
        route_analysis=route_analysis,
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market_without_current_price(),
    )

    assert result.admitted is False
    assert result.reason == "current_market_price_unavailable"
    assert routed == []
    assert logger.opportunity_records == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_decision_grade_dossier_recomputes_edge_against_current_market_price() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()
    routed = []

    async def route_analysis(analysis, _store):
        routed.append(analysis)
        return SimpleNamespace(
            ready=True,
            enqueued=True,
            trade_blocked_reason=None,
        )

    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=logger,
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
        route_analysis=route_analysis,
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market_with_current_ask(yes_ask_cents=70, no_ask_cents=40),
    )

    assert result.admitted is False
    assert result.reason == "current_market_no_positive_edge"
    assert routed == []
    assert logger.opportunity_records == []
    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "evidence", "has_counter_query", "expected_reason"),
    [
        (
            _snapshot(estimated_edge=None),
            _valid_evidence(),
            True,
            "missing_price_edge",
        ),
        (
            _snapshot(estimated_probability=0.52, estimated_edge=0.0),
            _valid_evidence(),
            True,
            "no_positive_edge",
        ),
        (
            _snapshot(last_researched_ts="2026-07-01T00:00:00+00:00"),
            _valid_evidence(),
            True,
            "stale_research",
        ),
        (
            _snapshot(),
            [
                _evidence(
                    "resolution_source",
                    claim_type="settlement_source",
                    direction="neutral",
                    url="https://same.example.com/resolution",
                ),
                _evidence(
                    "reputable_secondary",
                    claim_type="supporting",
                    direction="yes",
                    url="https://same.example.com/support",
                ),
                _evidence(
                    "reputable_secondary",
                    claim_type="disconfirming",
                    direction="no",
                    url="https://same.example.com/counter",
                ),
            ],
            True,
            "no_reliable_source_path",
        ),
        (
            _snapshot(),
            _valid_evidence(),
            False,
            "missing_counter_query",
        ),
    ],
)
async def test_invalid_decision_grade_dossiers_do_not_enter_paper_review(
    snapshot: ResearchDossierSnapshot,
    evidence: list[ResearchEvidence],
    has_counter_query: bool,
    expected_reason: str,
) -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=snapshot,
            evidence=evidence,
            has_counter_query=has_counter_query,
        ),
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
        ),
        _market(),
    )

    assert result.admitted is False
    assert result.reason == expected_reason
    assert queue.empty()


@pytest.mark.asyncio
async def test_future_nws_evidence_does_not_enter_paper_review() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    future_evidence = [
        replace(
            item,
            published_at="2026-07-03",
            retrieved_at="2026-07-02T16:00:00+00:00",
        )
        for item in _structured_official_weather_same_side_countercheck()
    ]
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=_snapshot(),
            evidence=future_evidence,
        ),
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.admitted is False
    assert result.reason == "temporally_invalid_evidence"
    assert queue.empty()


@pytest.mark.asyncio
async def test_invalid_ancillary_evidence_does_not_block_valid_paper_signal() -> None:
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    evidence = [
        *_structured_official_weather_same_side_countercheck(),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Malformed ancillary source",
            source_url="https://example.com/ancillary",
            title="Irrelevant ancillary item",
            snippet="This neutral item is not part of the settlement proof.",
            claim_type="corroboration",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at="not-a-timestamp",
        ),
    ]
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(snapshot=_snapshot(), evidence=evidence),
        trading_queue=queue,
        logger=SpyLogger(),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )

    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-1",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        _market(),
    )

    assert result.admitted is True
    assert result.enqueued is True


@pytest.mark.asyncio
async def test_official_p_admits_cheap_yes_when_labeled_the_other_side(monkeypatch) -> None:
    """B209: p=0.39 labeled NO, trade YES. Paper admission must not park."""
    monkeypatch.setattr(
        "tasks.research_paper_admission.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    ticker = "KXTRUTHSOCIAL-26AUG29-B209"
    question = (
        "Will Donald Trump make between 200 and 219 Truth Social posts "
        "the week of Aug 23, 2026?"
    )
    provider = ResearchPaperSignalProvider(
        store=FakeResearchStore(
            snapshot=replace(
                _snapshot(
                    side="yes",
                    estimated_probability=0.39,
                    market_price=0.23,
                    estimated_edge=0.15,
                ),
                market_ticker=ticker,
                contract_question=question,
            ),
            evidence=[
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="Roll Call Factbase Truth Social records",
                    source_url="https://rollcall.com/wp-json/factbase/v1/twitter",
                    title=question,
                    snippet=(
                        "Factbase records show 175 posts; requested range is "
                        "200-219. Implied YES probability 0.390."
                    ),
                    claim_type="official_resolution",
                    supports_direction="no",
                    supports_confidence=0.85,
                    retrieved_at="2026-07-02T16:00:00+00:00",
                    metric_name="truth_social_range_probability",
                    metric_value=0.39,
                    metric_unit="probability",
                    extraction_confidence=0.96,
                )
            ],
            has_counter_query=False,
            query_texts=[question],
        ),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )
    signal, reason = await provider.get_signal(ticker)
    assert reason is None
    assert signal is not None
    assert signal.side == "yes"
    assert signal.estimated_probability == pytest.approx(0.39)


@pytest.mark.asyncio
async def test_white_house_remaining_time_p_admits_without_counter_query(
    monkeypatch,
) -> None:
    """T6 remaining-time p is both-sides. Missing Google counter must not park."""
    monkeypatch.setattr(
        "tasks.research_paper_admission.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    ticker = "KXTRUMPACT-26AUG23-T6"
    question = "Will there be at least 6 presidential actions in the week of Aug 23, 2026?"
    provider = ResearchPaperSignalProvider(
        store=FakeResearchStore(
            snapshot=replace(
                _snapshot(
                    side="yes",
                    estimated_probability=0.98,
                    market_price=0.55,
                    estimated_edge=0.42,
                ),
                market_ticker=ticker,
                contract_question=question,
            ),
            evidence=[
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="White House Presidential Actions",
                    source_url="https://www.whitehouse.gov/presidential-actions/",
                    title=question,
                    snippet=(
                        "Official count is 6 versus threshold 6 "
                        "(already_above_threshold); implied YES probability 0.980."
                    ),
                    claim_type="official_resolution",
                    supports_direction="yes",
                    supports_confidence=0.95,
                    retrieved_at="2026-07-02T16:00:00+00:00",
                    metric_name="white_house_action_range_probability",
                    metric_value=0.98,
                    metric_unit="probability",
                    extraction_confidence=0.96,
                )
            ],
            has_counter_query=False,
            query_texts=[question],
        ),
        now=lambda: datetime(2026, 7, 2, 16, 5, tzinfo=UTC),
    )
    signal, reason = await provider.get_signal(ticker)
    assert reason is None
    assert signal is not None
    assert signal.side == "yes"
    assert signal.estimated_probability == pytest.approx(0.98)


class _HarshLlmCalibration:
    def get_scaling_factor(self, _lane: str) -> float:
        return 0.05


@pytest.mark.asyncio
async def test_official_research_survives_llm_calibration_g1_kill(monkeypatch) -> None:
    """T240 died at G1 because news-lane calibration scaled official p."""
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()

    async def route(analysis, store):
        task = BlendTask(
            trading_queue=queue,
            store=store,
            logger=logger,
            is_paper_mode=True,
            calibration=_HarshLlmCalibration(),
            now=lambda: datetime(2026, 8, 28, 16, 5, tzinfo=UTC),
        )
        return await task.process_fast_lane_result(analysis)

    market = _market_with_current_ask(yes_ask_cents=88, no_ask_cents=13)
    market.ticker = "KXTRUTHSOCIAL-26AUG29-T240"
    market.title = "Will Donald Trump make above 240 Truth Social posts the week of Aug 23, 2026?"
    market.close_time = "2026-08-30T13:59:00Z"
    market.regime_weights = {}
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=replace(
                _snapshot(
                    side="yes",
                    estimated_probability=0.98,
                    market_price=0.88,
                    estimated_edge=0.09,
                    last_researched_ts="2026-08-28T16:00:00+00:00",
                ),
                market_ticker="KXTRUTHSOCIAL-26AUG29-T240",
            ),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=logger,
        now=lambda: datetime(2026, 8, 28, 16, 5, tzinfo=UTC),
        route_analysis=route,
    )
    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXTRUTHSOCIAL-26AUG29-T240",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        market,
    )
    assert result.reason != "G1_blended_confidence"
    assert result.admitted is True
    assert result.enqueued is True


@pytest.mark.asyncio
async def test_official_research_survives_zero_rest_liquidity_when_book_is_live(
    monkeypatch,
) -> None:
    """Kalshi liquidity_dollars=0 is not a dead T7 book when the orderbook has size."""
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()

    async def provider(analysis):
        return ExecutableLiquidity(
            market_ticker=analysis.market.ticker,
            side=analysis.side,
            limit_price=Decimal(analysis.executed_price_cents) / Decimal("100"),
            best_price=Decimal(analysis.executed_price_cents) / Decimal("100"),
            executable_quantity=Decimal("8"),
            executable_notional=Decimal("1.12"),
            as_of=datetime(2026, 8, 28, 16, 5, tzinfo=UTC),
            raw_payload_hash="c" * 64,
        )

    async def route(analysis, store):
        task = BlendTask(
            trading_queue=queue,
            store=store,
            logger=logger,
            is_paper_mode=True,
            execution_liquidity_provider=provider,
            now=lambda: datetime(2026, 8, 28, 16, 5, tzinfo=UTC),
        )
        return await task.process_fast_lane_result(analysis)

    market = _market_with_current_ask(yes_ask_cents=14, no_ask_cents=87)
    market.ticker = "KXTRUMPACT-26AUG23-T7"
    market.title = "Will there be at least 7 presidential actions in the week of Aug 23, 2026?"
    market.close_time = "2026-08-30T13:59:00Z"
    market.regime_weights = {}
    market.liquidity_dollars = Decimal("0")
    market.yes_ask_size = Decimal("8")
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=replace(
                _snapshot(
                    side="yes",
                    estimated_probability=0.73,
                    market_price=0.14,
                    estimated_edge=0.58,
                    last_researched_ts="2026-08-28T16:00:00+00:00",
                ),
                market_ticker="KXTRUMPACT-26AUG23-T7",
            ),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=logger,
        now=lambda: datetime(2026, 8, 28, 16, 5, tzinfo=UTC),
        route_analysis=route,
    )
    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXTRUMPACT-26AUG23-T7",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        market,
    )
    assert result.reason != "G7_zero_liquidity"
    assert result.admitted is True
    assert result.enqueued is True


@pytest.mark.asyncio
async def test_official_research_uses_rest_top_size_when_orderbook_fetch_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "utils.event_news_research.is_event_news_paper_cohort",
        lambda _config=None: True,
    )
    queue: asyncio.Queue[TradeCandidate] = asyncio.Queue()
    logger = SpyLogger()

    async def provider(_analysis):
        raise RuntimeError("orderbook unavailable")

    async def route(analysis, store):
        task = BlendTask(
            trading_queue=queue,
            store=store,
            logger=logger,
            is_paper_mode=True,
            execution_liquidity_provider=provider,
            now=lambda: datetime(2026, 8, 28, 16, 5, tzinfo=UTC),
        )
        return await task.process_fast_lane_result(analysis)

    market = _market_with_current_ask(yes_ask_cents=14, no_ask_cents=87)
    market.ticker = "KXTRUMPACT-26AUG23-T7"
    market.title = "Will there be at least 7 presidential actions in the week of Aug 23, 2026?"
    market.close_time = "2026-08-30T13:59:00Z"
    market.regime_weights = {}
    market.liquidity_dollars = Decimal("0")
    market.yes_ask_size = Decimal("8")
    bridge = ResearchPaperAdmissionBridge(
        research_store=FakeResearchStore(
            snapshot=replace(
                _snapshot(
                    side="yes",
                    estimated_probability=0.73,
                    market_price=0.14,
                    estimated_edge=0.58,
                    last_researched_ts="2026-08-28T16:00:00+00:00",
                ),
                market_ticker="KXTRUMPACT-26AUG23-T7",
            ),
            evidence=_valid_evidence(),
        ),
        trading_queue=queue,
        logger=logger,
        now=lambda: datetime(2026, 8, 28, 16, 5, tzinfo=UTC),
        route_analysis=route,
    )
    result = await bridge.admit_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXTRUMPACT-26AUG23-T7",
            status="decision_grade_candidate",
            attempted=True,
            research_run_id="rr-decision",
            research_contract_fingerprint="contract-v1",
        ),
        market,
    )
    assert result.reason != "G7_zero_liquidity"
    assert result.admitted is True
    assert result.enqueued is True
