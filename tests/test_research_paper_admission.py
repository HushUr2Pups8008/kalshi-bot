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
from tasks.blend_task import TradeCandidate
from tasks.research_dossier import ResearchDossierSnapshot
from tasks.research_paper_admission import (
    ResearchBackedBlendStore,
    ResearchPaperAdmissionBridge,
    ResearchPaperSignal,
    _has_counter_query,
)
from tasks.research_prewarm_task import ResearchPrewarmResult


class FakeResearchStore:
    def __init__(
        self,
        *,
        snapshot: ResearchDossierSnapshot | None,
        evidence: list[ResearchEvidence],
        has_counter_query: bool = True,
    ) -> None:
        self.snapshot = snapshot
        self.evidence = evidence
        self.has_counter_query = has_counter_query
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


def _market() -> KalshiMarket:
    return KalshiMarket(
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
            title="NWS Central Park daily maximum for July 2, 2026: 93F",
            snippet=(
                "NWS Central Park climate report lists TODAY MAXIMUM 93F "
                "for July 2, 2026, versus the below 99F market range, supporting YES."
            ),
            claim_type="official_resolution",
            supports_direction="yes",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=93.0,
            metric_unit="fahrenheit",
            extraction_confidence=0.95,
            retrieved_at="2026-07-02T16:00:00+00:00",
            inserted_at="2026-07-02T16:00:00+00:00",
            contract_fingerprint="contract-v1",
        ),
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url=source_url,
            title="NWS Central Park daily maximum countercheck for July 2, 2026: 93F",
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
            title="NWS Central Park daily maximum countercheck for July 2, 2026: 93F",
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
        conn.execute(
            "CREATE TABLE research_run_queries "
            "(research_run_id TEXT, query_intent TEXT)"
        )
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
        ),
        _market(),
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
