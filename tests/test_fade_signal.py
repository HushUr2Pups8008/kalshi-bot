"""
Tests for analysis/fade_signal.py

Covers: tweet hype detection and price-threshold crossing behaviour.
"""

from datetime import datetime, timezone

import pytest

from analysis.fade_signal import detect_fade_pattern, detect_price_fade
from feeds import NewsItem


def _tweet(headline: str, body: str = ""):
    return NewsItem(
        headline=headline,
        url="https://example.com/tweet",
        source="@Kalshi",
        published=datetime.now(timezone.utc),
        body=body,
        item_id="tweet-1",
    )


class TestDetectFadePattern:
    @pytest.mark.parametrize(
        ("headline", "body"),
        [
            ("BREAKING: odds surging", ""),
            ("Market hits all-time high", ""),
            ("ATH for this contract", ""),
            ("Odds update", "record high on the platform"),
            ("Price update", "just hit 81"),
        ],
    )
    def test_bullish_patterns_detected(self, headline, body):
        assert detect_fade_pattern(_tweet(headline, body)) == "bullish"

    @pytest.mark.parametrize(
        ("headline", "body"),
        [
            ("Weekly market recap", ""),
            ("Odds update", "markets remain range-bound"),
            ("New listing announced", ""),
        ],
    )
    def test_non_hype_text_returns_none(self, headline, body):
        assert detect_fade_pattern(_tweet(headline, body)) is None


class TestDetectPriceFade:
    @pytest.mark.parametrize(
        ("prev_mid", "now_mid", "expected"),
        [
            (83.0, 85.0, "high_cross"),
            (10.0, 15.0, None),
            (17.0, 15.0, "low_cross"),
            (85.0, 86.0, None),
            (14.0, 13.0, None),
            (84.5, 84.9, None),
        ],
    )
    def test_threshold_crossing_behavior(self, prev_mid, now_mid, expected):
        assert detect_price_fade(
            "KXTEST",
            prev_mid=prev_mid,
            now_mid=now_mid,
            high_threshold=85.0,
            low_threshold=15.0,
        ) == expected
