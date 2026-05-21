from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi.source_hints import (
    SourceClass,
    SourceRegistry,
    SourceTargetCounters,
    build_market_source_hint_diagnostics,
    build_market_source_hints,
    build_market_source_target_plan,
    canonicalize_source_label,
)
from kalshi import KalshiMarket
from kalshi.normalizer import normalize_market_detail
from feeds.search_news_monitor import _markets_to_queries

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "market_source_hints"


def _generate_valid_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _market_with_source_metadata() -> KalshiMarket:
    return KalshiMarket(
        ticker="KXTRUMPIRAN-27JAN01",
        title="Will Trump announce military action against Iran?",
        yes_bid=49.0,
        yes_ask=51.0,
        yes_price=50.0,
        volume=100,
        open_interest=200,
        close_time="2026-12-31T23:59:59Z",
        status="active",
        series_ticker="KXTRUMPIRAN",
        price_available=True,
        market_metadata={"rules_text": "Resolution source: Reuters and AP."},
    )


def test_market_source_hints_extracts_canonical_structured_plan_from_rules_fixture():
    rules_text = (FIXTURE_DIR / "trump_iran_rules.txt").read_text(encoding="utf-8")

    plan = build_market_source_hints(
        ticker="KXTRUMPIRAN-27JAN01",
        title="Will Trump announce military action against Iran?",
        rules_text=rules_text,
    )

    assert plan.ticker == "KXTRUMPIRAN-27JAN01"
    assert plan.shadow_only is True
    assert [hint.canonical_name for hint in plan.hints[:4]] == [
        "Associated Press",
        "CNBC",
        "CNN",
        "Department of Defense",
    ]
    assert [hint.canonical_name for hint in plan.hints] == sorted(
        hint.canonical_name for hint in plan.hints
    )
    assert {hint.canonical_name: hint.domain for hint in plan.hints}["Reuters"] == "reuters.com"
    assert {hint.canonical_name: hint.source_class for hint in plan.hints}[
        "White House"
    ] == SourceClass.OFFICIAL_GOVERNMENT
    assert plan.search_queries["Reuters"] == [
        'site:reuters.com "Will Trump announce military action against Iran?"'
    ]
    assert plan.rejected_labels["Reuters/Getty Images"] == "photo_credit_or_wire_credit"
    assert plan.rejected_labels["news outlets"] == "generic_or_unverifiable_label"
    assert plan.rejected_labels["social media"] == "generic_or_unverifiable_label"


def test_source_registry_lookup_is_alias_aware_and_deterministic():
    registry = SourceRegistry.default()

    assert registry.lookup("AP").canonical_name == "Associated Press"
    assert registry.lookup("The Associated Press").canonical_name == "Associated Press"
    assert [hint.canonical_name for hint in registry.all_sources()][:3] == [
        "Associated Press",
        "CNBC",
        "CNN",
    ]


def test_canonicalize_source_label_maps_aliases_and_rejects_pollution():
    assert canonicalize_source_label("AP").canonical_name == "Associated Press"
    assert canonicalize_source_label("The Wall Street Journal").domain == "wsj.com"
    assert canonicalize_source_label("official statements from Department of State").canonical_name == "Department of State"
    assert canonicalize_source_label("Reuters/Getty Images") is None
    assert canonicalize_source_label("news outlets") is None


def test_local_mastheads_require_explicit_allowlist_and_are_flagged_local():
    assert canonicalize_source_label("Tehran Times") is None

    plan = build_market_source_hints(
        ticker="LOCAL-1",
        title="Will a city council approve the budget?",
        rules_text="Settlement source: The Daily Pilot or official city records.",
        local_mastheads={"The Daily Pilot": "dailypilot.com"},
    )

    assert [hint.canonical_name for hint in plan.hints] == ["The Daily Pilot"]
    assert plan.hints[0].source_class == SourceClass.LOCAL_MASTHEAD
    assert plan.hints[0].domain == "dailypilot.com"
    assert plan.search_queries["The Daily Pilot"] == [
        'site:dailypilot.com "Will a city council approve the budget?"'
    ]


def test_source_target_plan_uses_market_metadata_for_shadow_query_and_feed_targets():
    market = KalshiMarket(
        ticker="KXTRUMPIRAN-27JAN01",
        title="Will Trump announce military action against Iran?",
        yes_bid=49.0,
        yes_ask=51.0,
        yes_price=50.0,
        volume=100,
        open_interest=200,
        close_time="2026-12-31T23:59:59Z",
        status="active",
        series_ticker="KXTRUMPIRAN",
        price_available=True,
        market_metadata={
            "rules_text": "Resolution source: Reuters and official statements from Department of State.",
            "settlement_text": "AP and The Guardian may also be used.",
        },
    )

    plan = build_market_source_target_plan(
        market,
        feed_url_builders={"Google News": lambda query: f"gnews:{query}"},
    )

    assert plan.ticker == market.ticker
    assert plan.shadow_only is True
    assert [target.source.canonical_name for target in plan.targets] == [
        "Associated Press",
        "Department of State",
        "Reuters",
        "The Guardian",
    ]
    reuters = {target.source.canonical_name: target for target in plan.targets}["Reuters"]
    assert reuters.search_queries == (
        'site:reuters.com "Will Trump announce military action against Iran?"',
    )
    assert reuters.feed_urls == (
        'gnews:site:reuters.com "Will Trump announce military action against Iran?"',
    )


def test_source_target_plan_uses_normalized_kalshi_rules_metadata_keys():
    market = normalize_market_detail(
        {
            "market": {
                "ticker": "KXSOURCEHINT-NORMALIZED",
                "title": "Will a wire service confirm the event?",
                "yes_bid_dollars": "0.4100",
                "yes_ask_dollars": "0.4300",
                "no_bid_dollars": "0.5700",
                "no_ask_dollars": "0.5900",
                "status": "active",
                "close_time": "2027-01-01T00:00:00Z",
                "rules_primary": "Resolution source: Reuters and AP.",
                "rules_secondary": "Only public reporting in the rules will be considered.",
            }
        }
    )

    plan = build_market_source_target_plan(market)

    assert market.market_metadata == {
        "rules_primary": "Resolution source: Reuters and AP.",
        "rules_secondary": "Only public reporting in the rules will be considered.",
    }
    assert [target.source.canonical_name for target in plan.targets] == [
        "Associated Press",
        "Reuters",
    ]
    assert {
        target.source.canonical_name: target.search_queries
        for target in plan.targets
    } == {
        "Associated Press": (
            'site:apnews.com "Will a wire service confirm the event?"',
        ),
        "Reuters": (
            'site:reuters.com "Will a wire service confirm the event?"',
        ),
    }


def test_source_target_counters_emit_shadow_hit_miss_and_freshness_log_records():
    market = KalshiMarket(
        ticker="KXTRUMPIRAN-27JAN01",
        title="Will Trump announce military action against Iran?",
        yes_bid=49.0,
        yes_ask=51.0,
        yes_price=50.0,
        volume=100,
        open_interest=200,
        close_time="2026-12-31T23:59:59Z",
        status="active",
        price_available=True,
        market_metadata={"rules_text": "Resolution source: Reuters and AP."},
    )
    plan = build_market_source_target_plan(market)
    counters = SourceTargetCounters(plan)

    counters.record_query_result("Reuters", hit=True, age_seconds=90)
    counters.record_query_result("Associated Press", hit=False)

    assert counters.snapshot()["Reuters"] == {
        "hits": 1,
        "misses": 0,
        "freshest_age_seconds": 90,
    }
    assert counters.snapshot()["Associated Press"]["misses"] == 1
    assert counters.log_records() == [
        {
            "type": "MARKET_SOURCE_HINT_SHADOW",
            "ticker": "KXTRUMPIRAN-27JAN01",
            "source": "Reuters",
            "domain": "reuters.com",
            "hit": True,
            "freshness_age_seconds": 90,
            "shadow_only": True,
        },
        {
            "type": "MARKET_SOURCE_HINT_SHADOW",
            "ticker": "KXTRUMPIRAN-27JAN01",
            "source": "Associated Press",
            "domain": "apnews.com",
            "hit": False,
            "freshness_age_seconds": None,
            "shadow_only": True,
        },
    ]


def test_market_source_target_plan_does_not_change_default_search_query_path():
    market = _market_with_source_metadata()

    _ = build_market_source_target_plan(market)

    assert _markets_to_queries([market]) == ["trump announce military action"]


def test_market_source_hint_diagnostics_default_off_no_builder_calls_or_records():
    called_queries: list[str] = []

    def build_feed_url(query: str) -> str:
        called_queries.append(query)
        return f"unexpected:{query}"

    market = _market_with_source_metadata()

    diagnostics = build_market_source_hint_diagnostics(
        market,
        mode="off",
        emit_records=True,
        feed_url_builders={"future_fetcher": build_feed_url},
    )

    assert diagnostics.mode == "off"
    assert diagnostics.shadow_only is True
    assert diagnostics.plan.shadow_only is True
    assert diagnostics.plan.targets == ()
    assert diagnostics.counters == {}
    assert diagnostics.log_records == []
    assert called_queries == []
    assert _markets_to_queries([market]) == ["trump announce military action"]


def test_market_source_hint_diagnostics_shadow_and_advisory_are_shadow_only_without_fake_hits():
    market = _market_with_source_metadata()

    shadow = build_market_source_hint_diagnostics(market, mode="shadow", emit_records=True)
    advisory = build_market_source_hint_diagnostics(market, mode="advisory", emit_records=True)

    for diagnostics in (shadow, advisory):
        assert diagnostics.mode in {"shadow", "advisory"}
        assert diagnostics.shadow_only is True
        assert diagnostics.plan.shadow_only is True
        assert [target.source.canonical_name for target in diagnostics.plan.targets] == [
            "Associated Press",
            "Reuters",
        ]
        assert diagnostics.counters == {
            "Associated Press": {
                "hits": 0,
                "misses": 0,
                "freshest_age_seconds": None,
            },
            "Reuters": {
                "hits": 0,
                "misses": 0,
                "freshest_age_seconds": None,
            },
        }
        assert diagnostics.log_records == []


def test_market_source_hints_config_defaults_and_rejects_invalid_mode(monkeypatch):
    from config import BotConfig

    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_API_KEY_SECRET", _generate_valid_pem())
    monkeypatch.delenv("MARKET_SOURCE_HINTS_MODE", raising=False)
    monkeypatch.delenv("MARKET_SOURCE_HINTS_EMIT_RECORDS", raising=False)
    default_config = BotConfig()

    assert default_config.market_source_hints_mode == "off"
    assert default_config.market_source_hints_emit_records is False

    monkeypatch.setenv("MARKET_SOURCE_HINTS_MODE", "advisory")
    monkeypatch.setenv("MARKET_SOURCE_HINTS_EMIT_RECORDS", "true")
    advisory_config = BotConfig()

    assert advisory_config.market_source_hints_mode == "advisory"
    assert advisory_config.market_source_hints_emit_records is True

    monkeypatch.setenv("MARKET_SOURCE_HINTS_MODE", "readiness_candidate")
    with pytest.raises(SystemExit):
        BotConfig()


def test_unvalidated_or_missing_source_hints_fail_closed_without_fetch_targets():
    called_queries: list[str] = []

    def build_feed_url(query: str) -> str:
        called_queries.append(query)
        return f"unexpected:{query}"

    market = KalshiMarket(
        ticker="KXGENERIC-27JAN01",
        title="Will generic news sources report an event?",
        yes_bid=49.0,
        yes_ask=51.0,
        yes_price=50.0,
        volume=100,
        open_interest=200,
        close_time="2026-12-31T23:59:59Z",
        status="active",
        series_ticker="KXGENERIC",
        price_available=True,
        market_metadata={"rules_text": "Resolution source: news outlets and unknown local mastheads."},
    )

    plan = build_market_source_target_plan(
        market,
        feed_url_builders={"future_fetcher": build_feed_url},
    )
    counters = SourceTargetCounters(plan)

    assert plan.targets == ()
    assert plan.shadow_only is True
    assert plan.rejected_labels == {"news outlets": "generic_or_unverifiable_label"}
    assert called_queries == []
    assert counters.snapshot() == {}
    assert counters.log_records() == []
    assert _markets_to_queries([market]) == ["generic news sources report"]


def test_operator_shadow_rollout_doc_covers_diagnostics_and_safety_contract():
    doc = Path("docs/governance/market_source_hints_shadow_rollout.md")

    text = doc.read_text(encoding="utf-8")

    required_phrases = [
        "pytest tests/test_market_source_hints.py -q",
        "MARKET_SOURCE_HINT_SHADOW",
        "shadow_only",
        "hits",
        "misses",
        "freshest_age_seconds",
        "does not affect admission, readiness, execution, or trading behavior",
        "source-target plans do not poll",
        "bounded, rate-limited, cached fetch mechanisms",
        "fetch, cache, or rate-limit failure leaves zero source-hint hits",
        "fail closed to empty targets or rejected labels",
        "never broaden search or create executable signals",
        "_markets_to_queries",
    ]
    for phrase in required_phrases:
        assert phrase in text
