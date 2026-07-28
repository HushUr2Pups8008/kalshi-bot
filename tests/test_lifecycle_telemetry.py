import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config as _cfg_module
from analysis import SignalAnalysis
from analysis import market_matcher as market_matcher_module
from analysis.market_matcher import MarketMatcher
from feeds import NewsItem
from kalshi import KalshiMarket
from kalshi.series_metadata import SettlementSource
from tasks.blend_task import BlendTask
from tasks.blend_task import _settlement_source_relevant
from tasks.evidence_store import DossierState, EvidenceRecord, StructuralPriorRecord
from utils.lifecycle import (
    build_lifecycle_id,
    build_research_lifecycle_id,
    settlement_source_match,
)
from utils.logger import TradeLogger


def test_lifecycle_id_is_deterministic_and_scoped_by_venue_and_ticker() -> None:
    kwargs = {
        "venue": "kalshi",
        "ticker": "KXTEST-26",
        "source": "Reuters",
        "url": "https://reuters.com/story",
        "headline": "Test event happened",
        "published": datetime(2026, 7, 11, 18, tzinfo=UTC),
    }

    lifecycle_id = build_lifecycle_id(**kwargs)

    assert lifecycle_id == build_lifecycle_id(**kwargs)
    assert lifecycle_id.startswith("lc-")
    assert lifecycle_id != build_lifecycle_id(**{**kwargs, "ticker": "KXOTHER-26"})
    assert lifecycle_id != build_lifecycle_id(**{**kwargs, "venue": "polymarket_us"})


def test_research_lifecycle_id_is_stable_for_claim_identity() -> None:
    lifecycle_id = build_research_lifecycle_id(
        ticker="KXTEST-26",
        research_run_id="rr-1",
        contract_fingerprint="contract-1",
    )

    assert lifecycle_id == build_research_lifecycle_id(
        ticker="KXTEST-26",
        research_run_id="rr-1",
        contract_fingerprint="contract-1",
    )
    assert lifecycle_id != build_research_lifecycle_id(
        ticker="KXTEST-26",
        research_run_id="rr-2",
        contract_fingerprint="contract-1",
    )


def test_settlement_source_match_is_strict_tristate() -> None:
    settlement_sources = (SimpleNamespace(label="Reuters", domain="reuters.com", url="https://reuters.com"),)

    assert (
        settlement_source_match(
            source="Reuters",
            url="https://reuters.com/story",
            source_hint_domain=None,
            settlement_sources=settlement_sources,
        )
        is True
    )
    assert (
        settlement_source_match(
            source="Associated Press",
            url="https://apnews.com/story",
            source_hint_domain=None,
            settlement_sources=settlement_sources,
        )
        is False
    )
    assert (
        settlement_source_match(
            source="Reuters",
            url=None,
            source_hint_domain=None,
            settlement_sources=(),
        )
        is None
    )
    assert (
        settlement_source_match(
            source="Reuters",
            url=None,
            source_hint_domain=None,
            settlement_sources=("This market resolves according to reporting selected after close.",),
        )
        is None
    )
    assert (
        settlement_source_match(
            source=None,
            url=None,
            source_hint_domain=None,
            settlement_sources=settlement_sources,
        )
        is None
    )


def test_all_eight_lifecycle_records_share_top_level_context(tmp_path: Path) -> None:
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    lifecycle_id = "lc-0123456789abcdef0123456789abcdef"
    signal_meta = {
        "lifecycle_id": lifecycle_id,
        "settlement_source_match": None,
        "unrelated": "preserved-nested-only",
    }

    with patch.object(logger, "_write") as write_mock:
        logger.log_match_diagnostic(
            source="Reuters",
            headline="Headline",
            ticker="KXTEST-26",
            market_title="Market",
            match_score=0.8,
            matched_tokens=["test"],
            token_overlap_count=1,
            geo_overlap_count=0,
            generic_overlap_count=0,
            headline_token_count=1,
            market_title_token_count=1,
            overlap_ratio=1.0,
            low_match_quality=False,
            lifecycle_id=lifecycle_id,
            settlement_source_match=None,
        )
        logger.log_opportunity(
            ticker="KXTEST-26",
            market_title="Market",
            entry_price_cents=50.0,
            estimated_probability=0.6,
            edge=0.1,
            kelly_fraction=0.1,
            kelly_dollars=5.0,
            capped_dollars=5.0,
            side="yes",
            reasoning="reason",
            lifecycle_id=lifecycle_id,
            settlement_source_match=None,
        )
        logger.log_blend_decision(
            market_ticker="KXTEST-26",
            fast_lane_p=0.6,
            fast_lane_confidence=0.8,
            accumulation_p=None,
            accumulation_confidence=None,
            structural_p=None,
            structural_confidence=None,
            regime_weights={"fast": 1.0},
            regime_confidence=1.0,
            blended_p=0.6,
            blended_confidence=0.8,
            disagreement_score=0.0,
            blend_mode="fast_lane_only",
            trade_considered=True,
            trade_blocked_reason=None,
            evidence_ids_contributing=[],
            lifecycle_id=lifecycle_id,
            settlement_source_match=None,
        )
        logger.log_skipped(
            reason="blocked",
            ticker="KXTEST-26",
            side="yes",
            venue="kalshi",
            signal_meta=signal_meta,
        )
        logger.log_paper_trade(
            trade_id="paper-1",
            ticker="KXTEST-26",
            market_title="Market",
            side="yes",
            contracts=1,
            price_cents=50,
            cost_dollars=0.5,
            estimated_probability=0.6,
            entry_price_cents=50.0,
            edge=0.1,
            kelly_dollars=0.0,
            reasoning="reason",
            signal_headline="Headline",
            signal_source="Reuters",
            signal_meta=signal_meta,
        )
        logger.log_live_submission_intent(
            submission_id="submission-1",
            ticker="KXTEST-26",
            side="yes",
            contracts=1,
            price_cents=50,
            cost_dollars=0.5,
            venue="kalshi",
            signal_meta=signal_meta,
        )
        logger.log_live_submission_unknown(
            submission_id="submission-1",
            ticker="KXTEST-26",
            side="yes",
            contracts=1,
            price_cents=50,
            cost_dollars=0.5,
            outcome="error_result",
            venue="kalshi",
            signal_meta=signal_meta,
        )
        logger.log_live_order(
            order_id="live-1",
            ticker="KXTEST-26",
            side="yes",
            contracts=1,
            price_cents=50,
            cost_dollars=0.5,
            status="resting",
            venue="kalshi",
            signal_meta=signal_meta,
        )

    records = [call.args[0] for call in write_mock.call_args_list]
    assert [record["type"] for record in records] == [
        "MATCH_DIAGNOSTIC",
        "OPPORTUNITY",
        "BLEND_DECISION",
        "SKIPPED",
        "PAPER_TRADE",
        "LIVE_SUBMISSION_INTENT",
        "LIVE_SUBMISSION_UNKNOWN",
        "LIVE_ORDER",
    ]
    assert {record["lifecycle_id"] for record in records} == {lifecycle_id}
    assert all("settlement_source_match" in record for record in records)
    assert all(record["settlement_source_match"] is None for record in records)
    assert all("unrelated" not in record for record in records)
    assert records[-1]["signal_meta"] == signal_meta
    terminal_records = [
        record
        for record in records
        if record["type"]
        in {"SKIPPED", "PAPER_TRADE", "LIVE_SUBMISSION_INTENT", "LIVE_SUBMISSION_UNKNOWN", "LIVE_ORDER"}
    ]
    assert all({"lifecycle_id", "venue", "ticker", "side"} <= record.keys() for record in terminal_records)


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolated_match_feedback_weights")
async def test_real_origin_blend_and_terminal_surfaces_share_lifecycle_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    records: list[dict[str, object]] = []
    now = datetime.now(UTC)
    news = NewsItem(
        headline="Iran threatens Strait of Hormuz closure",
        url="https://reuters.com/world/iran-hormuz",
        source="Reuters",
        published=now,
        body="Iran discussed closing the Strait of Hormuz.",
        item_id="lifecycle-integration",
    )
    market = KalshiMarket(
        ticker="KXIRAN-26",
        title="Will Iran close the Strait of Hormuz in 2026?",
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=100,
        open_interest=50,
        close_time=(now + timedelta(days=7)).isoformat(),
        status="active",
        series_ticker="KXIRAN",
        yes_bid_cents=49,
        yes_ask_cents=51,
        no_bid_cents=49,
        no_ask_cents=51,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
        regime_weights={"fast": 1.0, "interpretation": 0.0, "structural": 0.0},
    )
    market.settlement_sources = (SettlementSource(label="Reuters", url="https://reuters.com"),)
    matcher = MarketMatcher(MagicMock())
    matcher._cache.get_markets = AsyncMock(return_value=[market])
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(market_matcher_module, "trade_log", logger)

    class _Store:
        async def get_dossier(self, _ticker: str) -> DossierState:
            return DossierState(
                market_ticker=market.ticker,
                dossier_version=2,
                current_estimate=0.68,
                confidence=0.8,
                prior_estimate=0.55,
                drift_suspect=False,
                in_recovery=False,
                created_ts=now.isoformat(),
                updated_ts=now.isoformat(),
            )

        async def get_structural_prior(self, _ticker: str) -> StructuralPriorRecord:
            return StructuralPriorRecord(
                market_ticker=market.ticker,
                prior_estimate=0.64,
                confidence=0.5,
                computed_ts=now.isoformat(),
                recompute_trigger="dossier_update",
                input_source_count=2,
                llm_called=False,
            )

        async def get_recent_evidence(
            self,
            _ticker: str,
            *,
            limit: int = 100,
        ) -> list[EvidenceRecord]:
            assert limit == 100
            return [
                EvidenceRecord(
                    evidence_id="ev-news",
                    market_ticker=market.ticker,
                    source="Reuters",
                    source_class="news",
                    headline=news.headline,
                    ingested_ts=now.isoformat(),
                    content_hash="hash-news",
                    update_type="state",
                    dossier_version_before=0,
                    dossier_version_after=1,
                    original_weight=0.8,
                ),
                EvidenceRecord(
                    evidence_id="ev-official",
                    market_ticker=market.ticker,
                    source="Official",
                    source_class="official",
                    headline="Official confirmation",
                    ingested_ts=now.isoformat(),
                    content_hash="hash-official",
                    update_type="state",
                    dossier_version_before=1,
                    dossier_version_after=2,
                    original_weight=0.8,
                ),
            ]

    with patch.object(logger, "_write", side_effect=records.append):
        matches = await matcher.find_candidates(news)

        assert len(matches) == 1
        matched_market, match_score, match_meta = matches[0]
        lifecycle_id = match_meta["lifecycle_id"]
        assert match_meta["settlement_source_match"] is True

        logger.log_opportunity(
            ticker=matched_market.ticker,
            market_title=matched_market.title,
            entry_price_cents=51.0,
            estimated_probability=0.72,
            edge=0.21,
            kelly_fraction=0.1,
            kelly_dollars=5.0,
            capped_dollars=5.0,
            side="yes",
            reasoning="reason",
            lifecycle_id=match_meta["lifecycle_id"],
            settlement_source_match=match_meta["settlement_source_match"],
            venue="kalshi",
        )
        match_meta["not_whitelisted"] = "drop-before-terminal"
        analysis = SignalAnalysis(
            news_item=news,
            market=matched_market,
            estimated_probability=0.72,
            executed_price_cents=51,
            edge=0.21,
            side="yes",
            kelly_fraction=0.1,
            kelly_dollars=5.0,
            capped_dollars=5.0,
            keywords_matched=["iran", "hormuz"],
            reasoning="reason",
            confidence=0.9,
            match_score=match_score,
            signal_meta=match_meta,
        )
        queue: asyncio.Queue = asyncio.Queue()
        blend_result = await BlendTask(
            trading_queue=queue,
            store=_Store(),
            logger=logger,
            is_paper_mode=True,
            now=lambda: now,
        ).process_fast_lane_result(analysis)

        assert blend_result.ready
        candidate = await queue.get()
        signal_meta = candidate.signal_meta
        assert signal_meta["lifecycle_id"] == lifecycle_id
        assert signal_meta["settlement_source_match"] is True
        assert "not_whitelisted" not in signal_meta

        logger.log_skipped(
            reason="blocked",
            ticker=matched_market.ticker,
            signal_meta=signal_meta,
        )
        logger.log_paper_trade(
            trade_id="paper-1",
            ticker=matched_market.ticker,
            market_title=matched_market.title,
            side="yes",
            contracts=1,
            price_cents=51,
            cost_dollars=0.51,
            estimated_probability=0.72,
            entry_price_cents=51.0,
            edge=0.21,
            kelly_dollars=0.0,
            reasoning="reason",
            signal_headline=news.headline,
            signal_source=news.source,
            signal_meta=signal_meta,
        )
        logger.log_live_order(
            order_id="live-1",
            ticker=matched_market.ticker,
            side="yes",
            contracts=1,
            price_cents=51,
            cost_dollars=0.51,
            status="resting",
            signal_meta=signal_meta,
        )

    lifecycle_records = [
        record
        for record in records
        if record.get("type")
        in {
            "MATCH_DIAGNOSTIC",
            "OPPORTUNITY",
            "BLEND_DECISION",
            "SKIPPED",
            "PAPER_TRADE",
            "LIVE_ORDER",
        }
    ]
    assert [record["type"] for record in lifecycle_records] == [
        "MATCH_DIAGNOSTIC",
        "OPPORTUNITY",
        "BLEND_DECISION",
        "SKIPPED",
        "PAPER_TRADE",
        "LIVE_ORDER",
    ]
    assert {record["lifecycle_id"] for record in lifecycle_records} == {lifecycle_id}
    assert all(record["settlement_source_match"] is True for record in lifecycle_records)
    assert lifecycle_records[-1]["signal_meta"] == signal_meta


@pytest.mark.parametrize("value", [True, False, None])
def test_terminal_promotion_preserves_strict_settlement_tristate(
    tmp_path: Path,
    value: bool | None,
) -> None:
    logger = TradeLogger(path=tmp_path / "trades.jsonl")

    with patch.object(logger, "_write") as write_mock:
        logger.log_skipped(
            reason="blocked",
            ticker="KXTEST-26",
            signal_meta={
                "lifecycle_id": "lc-tristate",
                "settlement_source_match": value,
            },
        )

    assert write_mock.call_args.args[0]["settlement_source_match"] is value


def test_terminal_promotion_rejects_truthy_non_boolean_attribution(tmp_path: Path) -> None:
    logger = TradeLogger(path=tmp_path / "trades.jsonl")

    with patch.object(logger, "_write") as write_mock:
        logger.log_skipped(
            reason="blocked",
            ticker="KXTEST-26",
            signal_meta={
                "lifecycle_id": "lc-tristate",
                "settlement_source_match": "true",
            },
        )

    assert write_mock.call_args.args[0]["settlement_source_match"] is None


def test_blend_rejects_truthy_non_boolean_attribution() -> None:
    analysis = SimpleNamespace(signal_meta={"settlement_source_match": "true"})

    assert _settlement_source_relevant(analysis) is None


def test_opportunity_rejects_truthy_non_boolean_without_lifecycle(tmp_path: Path) -> None:
    logger = TradeLogger(path=tmp_path / "trades.jsonl")

    with patch.object(logger, "_write") as write_mock:
        logger.log_opportunity(
            ticker="KXTEST-26",
            market_title="Market",
            entry_price_cents=50.0,
            estimated_probability=0.6,
            edge=0.1,
            kelly_fraction=0.1,
            kelly_dollars=5.0,
            capped_dollars=5.0,
            side="yes",
            reasoning="reason",
            settlement_source_match="false",
        )

    assert write_mock.call_args.args[0]["settlement_source_match"] is None
