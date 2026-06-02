from kalshi.series_metadata import normalize_series_payload
from kalshi.normalizer import normalize_market_list_entry
from tasks.market_metadata_snapshot import build_snapshot


def test_normalize_series_payload_preserves_real_fields_and_sources():
    meta = normalize_series_payload({
        "series": {
            "ticker": "KXTRUMPIRAN",
            "title": "Will Trump visit Iran?",
            "tags": ["Trump", "Iran", "Trump"],
            "settlement_sources": [
                {"name": "The Associated Press", "url": "https://apnews.com/politics"},
                {"label": "Kalshi", "url": "https://kalshi.com/markets/example"},
            ],
            "contract_terms_url": "https://example.com/terms",
            "rules_primary": "Primary rule",
            "rules_secondary": "Secondary rule",
            "fee_multiplier": "0.07",
            "fee_type": "linear",
            "can_close_early": True,
        }
    })

    assert meta.series_ticker == "KXTRUMPIRAN"
    assert meta.tags == ("Trump", "Iran")
    assert meta.settlement_sources[0].label == "The Associated Press"
    assert meta.settlement_sources[0].domain == "apnews.com"
    assert meta.contract_terms_url == "https://example.com/terms"
    assert meta.rules_primary == "Primary rule"
    assert meta.fee_multiplier == "0.07"
    assert meta.can_close_early is True


def test_market_normalizer_preserves_explicit_contract_fields():
    market = normalize_market_list_entry({
        "ticker": "KXTRUMPIRAN-26",
        "title": "Will Trump visit Iran?",
        "yes_bid_dollars": "0.49",
        "yes_ask_dollars": "0.51",
        "no_bid_dollars": "0.49",
        "no_ask_dollars": "0.51",
        "volume_fp": "10",
        "open_interest_fp": "25",
        "close_time": "2026-12-31T23:59:59Z",
        "status": "active",
        "series_ticker": "KXTRUMPIRAN",
        "rules_primary": "Primary rule",
        "rules_secondary": "Secondary rule",
        "settlement_timer_seconds": "3600",
        "early_close_condition": "May close early",
        "expected_expiration_time": "2026-12-31T22:00:00Z",
        "expiration_time": "2026-12-31T23:00:00Z",
    })

    assert market.rules_primary == "Primary rule"
    assert market.rules_secondary == "Secondary rule"
    assert market.settlement_timer_seconds == 3600
    assert market.early_close_condition == "May close early"
    assert market.expected_expiration_time == "2026-12-31T22:00:00Z"
    assert market.expiration_time == "2026-12-31T23:00:00Z"


class _SnapshotClient:
    def get_all_series(self):
        return [{
            "ticker": "KXTRUMPIRAN",
            "title": "Will Trump visit Iran?",
            "tags": ["Trump", "Iran"],
            "settlement_sources": [{"name": "Reuters", "url": "https://reuters.com/world"}],
        }]


def test_market_metadata_snapshot_is_shadow_only_and_hashes_series_payload():
    snapshot = build_snapshot(_SnapshotClient())

    assert snapshot["shadow_only"] is True
    assert snapshot["schema_version"] == 1
    assert snapshot["series"]["KXTRUMPIRAN"]["settlement_sources"][0]["domain"] == "reuters.com"
    assert len(snapshot["payload_hash"]) == 64
