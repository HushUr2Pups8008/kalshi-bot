from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json
import sqlite3
from pathlib import Path

import pytest

import tasks.capital_guard_shadow_capture as capture_module
from analysis import SignalAnalysis
from analysis.decision_blender import BlendResult
from feeds import NewsItem
from kalshi import KalshiMarket
from kalshi.series_metadata import SettlementSource
from tasks.capital_guard_shadow_capture import (
    CapitalGuardShadowCaptureEnvelope,
    CapitalGuardShadowCaptureSink,
)
from tasks.trade_readiness_gate import evaluate_readiness
from trading.capital_guard_shadow import CapitalGuardShadowStore
from trading.fees import (
    KALSHI_GENERAL_2026_07_07,
    POLYMARKET_US_2026_07_01,
    fee_type_for_schedule,
)


NOW = datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
D = Decimal


def _rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as conn:
        return [tuple(row) for row in conn.execute(sql)]


def _envelope(
    *,
    disagreement: float = 0.10,
    include_fee_precision: bool = True,
    include_explicit_caps: bool = True,
    venue: str = "kalshi",
    side: str = "yes",
):
    schedule = (
        KALSHI_GENERAL_2026_07_07
        if venue == "kalshi"
        else POLYMARKET_US_2026_07_01
    )
    market = KalshiMarket(
        ticker="KXTEST-26JUL15-T50",
        title="Will the test threshold resolve YES?",
        yes_bid=40,
        yes_ask=42,
        yes_price=41,
        volume=100,
        open_interest=50,
        close_time="2026-07-16T12:30:00Z",
        status="active",
        series_ticker="KXTEST",
        event_ticker="KXTEST-26JUL15",
        rules_primary="Resolves YES when the official value is at least 50.",
        settlement_sources=(
            SettlementSource(
                label="Official source",
                url="https://example.test/value",
                domain="example.test",
            ),
        ),
        price_available=True,
        yes_bid_cents=40,
        yes_ask_cents=42,
        no_bid_cents=58,
        no_ask_cents=60,
        yes_bid_levels=((D("0.40"), D("10")),),
        no_bid_levels=((D("0.58"), D("7")), (D("0.57"), D("10"))),
        book_as_of=NOW,
        book_payload_hash="a" * 64,
        price_source="kalshi-orderbook-v2",
        price_method="fixed-point-depth-complement-v1",
        quantity_step=D("1"),
        fee_multiplier=D("1"),
        fee_type=fee_type_for_schedule(schedule),
        fee_effective_at=schedule.effective_from,
        fill_role="taker",
        fee_provenance_hash="c" * 64,
        report_venue=venue,
        report_venue_market_id=(
            "KXTEST-26JUL15-T50" if venue == "kalshi" else "pm-test-threshold-50"
        ),
        outcome_side=side,
    )
    signal_meta = {
        "lifecycle_id": "lifecycle-1",
        "settlement_source_match": True,
        "sizing_bankroll_dollars": "8.76",
        "fee_accumulator_dollars": "0",
    }
    if include_explicit_caps:
        signal_meta.update(
            {
                "shadow_max_position_dollars": "5",
                "shadow_max_ticker_exposure_dollars": "5",
            }
        )
    if include_fee_precision:
        signal_meta["fee_account_precision_dollars"] = "0.0001"
    analysis = SignalAnalysis(
        news_item=NewsItem("Threshold update", "https://example.test/news", "wire"),
        market=market,
        estimated_probability=0.55,
        executed_price_cents=42 if side == "yes" else 60,
        edge=0.13 if side == "yes" else -0.15,
        side=side,
        kelly_fraction=float(D("3") / D("8.76")),
        kelly_dollars=3.0,
        capped_dollars=2.1,
        confidence=0.8,
        signal_meta=signal_meta,
    )
    blend = BlendResult(
        blended_p=0.55,
        blended_confidence=0.8,
        disagreement_score=disagreement,
        blend_mode="weighted_blend",
        readiness_gate_min_edge_override=None,
        trade_blocked_reason=None,
        fast_lane_p=0.55,
        fast_lane_confidence=0.8,
        accumulation_p=None,
        accumulation_confidence=None,
        structural_p=None,
        structural_confidence=None,
    )
    readiness_input = {
        "source_lane": "fast",
        "blended_confidence": 0.8,
        "disagreement_score": disagreement,
        "default_min_edge": 0.05,
        "evidence_source_classes": [],
        "drift_suspect": False,
        "in_recovery": False,
        "recency_score": 1.0,
        "time_to_close_seconds": 86400.0,
        "settlement_source_relevant": True,
        "market_liquidity_dollars": 100.0,
        "market_price_momentum_cents": 0.0,
        "intended_side": side,
        "open_exposure_drawdown_pct": 0.30,
    }
    readiness = evaluate_readiness(readiness_input, 1.0)
    return CapitalGuardShadowCaptureEnvelope(
        analysis=analysis,
        blend_result=blend,
        readiness_decision=readiness,
        readiness_input=readiness_input,
        regime_weights={"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
        regime_confidence=1.0,
        trade_blocked_reason=readiness.trade_blocked_reason,
        venue=venue,
        market_family="KXTEST",
        lifecycle_id="lifecycle-1",
        decision_at=NOW,
        default_min_edge=0.05,
    )


@pytest.mark.asyncio
async def test_complete_sole_g7_attempt_persists_replay_eligible_candidate(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    sink = CapitalGuardShadowCaptureSink(store)

    result = await sink.capture(_envelope())

    assert result.attempt_status == "inserted"
    assert result.candidate_status == "inserted"
    assert _rows(store.db_path, "SELECT scorable FROM capital_guard_shadow_capture_attempts") == [(1,)]
    assert _rows(store.db_path, "SELECT replay_eligible FROM capital_guard_shadow_candidates") == [(1,)]


@pytest.mark.asyncio
async def test_other_gate_failure_is_captured_but_not_replay_eligible(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    await CapitalGuardShadowCaptureSink(store).capture(_envelope(disagreement=0.30))

    failures, replay = _rows(
        store.db_path,
        "SELECT ordered_failures_json, replay_eligible FROM capital_guard_shadow_candidates",
    )[0]
    assert json.loads(str(failures)) == [
        "G3_disagreement_score",
        "G7_open_exposure_drawdown",
    ]
    assert replay == 0


@pytest.mark.asyncio
async def test_missing_fee_provenance_persists_unscorable_attempt_only(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    result = await CapitalGuardShadowCaptureSink(store).capture(
        replace(
            _envelope(include_fee_precision=False),
            trade_blocked_reason="structural_tier2_veto",
        )
    )

    assert result.candidate_status is None
    failures, blocker, reasons = _rows(
        store.db_path,
        "SELECT ordered_failures_json, non_gate_blocker, "
        "ordered_unscorable_reasons_json FROM capital_guard_shadow_capture_attempts",
    )[0]
    assert json.loads(str(failures)) == ["G7_open_exposure_drawdown"]
    assert blocker == "structural_tier2_veto"
    assert "missing_fee_account_precision_dollars" in json.loads(str(reasons))
    assert _rows(store.db_path, "SELECT count(*) FROM capital_guard_shadow_candidates") == [(0,)]


@pytest.mark.asyncio
async def test_malformed_book_timestamp_still_persists_denominator_attempt(
    tmp_path: Path,
) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    envelope = _envelope()
    envelope.analysis.market.book_as_of = datetime(2026, 7, 15, 12, 30)

    result = await CapitalGuardShadowCaptureSink(store).capture(envelope)

    assert result.candidate_status is None
    reasons = json.loads(
        str(
            _rows(
                store.db_path,
                "SELECT ordered_unscorable_reasons_json "
                "FROM capital_guard_shadow_capture_attempts",
            )[0][0]
        )
    )
    assert "invalid_book_as_of" in reasons


@pytest.mark.asyncio
async def test_identical_lifecycle_retry_is_idempotent(tmp_path: Path) -> None:
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)
    sink = CapitalGuardShadowCaptureSink(store)
    envelope = _envelope()

    first = await sink.capture(envelope)
    retry = await sink.capture(envelope)

    assert (first.attempt_status, first.candidate_status) == ("inserted", "inserted")
    assert (retry.attempt_status, retry.candidate_status) == ("identical", "identical")
    assert _rows(store.db_path, "SELECT count(*) FROM capital_guard_shadow_capture_attempts") == [(1,)]
    assert _rows(store.db_path, "SELECT count(*) FROM capital_guard_shadow_candidates") == [(1,)]


@pytest.mark.asyncio
async def test_position_caps_derive_from_exact_bankroll_and_runtime_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture_module.cfg, "max_bet_pct_bankroll", 0.5)
    monkeypatch.setattr(capture_module.cfg, "min_bet_dollars", 1.0)
    monkeypatch.setattr(capture_module.cfg, "max_bet_hard_cap", 10.0)
    monkeypatch.setattr(capture_module.cfg, "max_ticker_exposure_pct", 0.5)
    store = CapitalGuardShadowStore(tmp_path / "shadow.db")
    store.initialize(applied_at=NOW)

    result = await CapitalGuardShadowCaptureSink(store).capture(
        _envelope(include_explicit_caps=False)
    )

    assert result.candidate_status == "inserted"
    sizing = json.loads(
        str(
            _rows(
                store.db_path,
                "SELECT sizing_json FROM capital_guard_shadow_candidates",
            )[0][0]
        )
    )
    assert sizing["max_position_dollars"] == "4.38"
    assert sizing["max_ticker_exposure_dollars"] == "4.38"


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["yes", "no"])
async def test_kalshi_and_polymarket_capture_identical_selected_side_book_bytes(
    tmp_path: Path,
    side: str,
) -> None:
    captured: dict[str, tuple[object, ...]] = {}
    for venue in ("kalshi", "polymarket_us"):
        store = CapitalGuardShadowStore(tmp_path / f"{venue}-{side}.db")
        store.initialize(applied_at=NOW)
        result = await CapitalGuardShadowCaptureSink(store).capture(
            _envelope(venue=venue, side=side)
        )
        assert result.candidate_status == "inserted"
        captured[venue] = _rows(
            store.db_path,
            "SELECT executable_book_json, executable_price_dollars, "
            "executable_quantity, gross_edge FROM capital_guard_shadow_candidates",
        )[0]

    assert captured["kalshi"] == captured["polymarket_us"]
