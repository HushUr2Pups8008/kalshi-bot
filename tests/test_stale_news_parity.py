"""Tests for analyzer-stage stale-news parity with intake.

PROFIT-STALE-001 (2026-05-24): the analyzer used a flat
MAX_NEWS_AGE_SECONDS=300 stale check, while intake admits items under
the per-source EARLY_MAX_NEWS_AGE_BY_SOURCE override (1800s for ~25
geopolitical sources). 19% of analyzer-stage candidates were stale-
rejected purely because of the threshold mismatch.

The fix routes the analyzer's stale check through the same helper the
intake uses (`_early_max_news_age_seconds_for_source`). These tests pin
the parity invariant:

  for any source s,
      analyzer_threshold(s) == intake_threshold(s)

If a future refactor drifts the analyzer's threshold off the intake
helper, these tests catch it.
"""
from __future__ import annotations

import config
from main import _early_max_news_age_seconds_for_source


class TestAnalyzerIntakeParityInvariant:
    """The analyzer's stale check must use the same per-source threshold
    that intake uses. This invariant is the load-bearing fix for the
    2026-05-24 19% analyzer-stage stale-loss."""

    def test_default_source_returns_default_threshold(self):
        """Sources not in the per-source map fall through to the default."""
        assert _early_max_news_age_seconds_for_source("Unknown Source") == \
            config.EARLY_MAX_NEWS_AGE_SECONDS

    def test_geopolitical_sources_get_per_source_override(self):
        """Major geo sources have an 1800s override per
        config.EARLY_MAX_NEWS_AGE_BY_SOURCE. Spot-check three of them."""
        for source in (
            "NYT > World News",
            "World news | The Guardian",
            "Middle East and north Africa | The Guardian",
        ):
            override = _early_max_news_age_seconds_for_source(source)
            assert override == config.EARLY_MAX_NEWS_AGE_BY_SOURCE[source]
            assert override >= config.EARLY_MAX_NEWS_AGE_SECONDS, (
                f"{source}: per-source override must be at least the global "
                f"default. Post-PROFIT-STALE-002 (2026-05-24) the default rose "
                f"from 300s to 1800s to remove per-source-list maintenance; "
                f"overrides may now equal the default and remain as "
                f"documentation of intent. They must never DROP below it — "
                f"that would silently shorten the window for a curated source."
            )

    def test_per_source_map_contains_expected_geopolitical_sources(self):
        """Pin the set of sources that have per-source overrides so
        accidental removal surfaces. If a source is dropped here, its
        EARLY_FRESH_PASS rate falls back to the 300s default — which
        the 2026-05-24 audit showed kills most candidates from these
        publishers."""
        required = (
            "NYT > World News",
            "World news | The Guardian",
            "Middle East and north Africa | The Guardian",
            "Ukraine | The Guardian",
            "Al Jazeera – Breaking News, World News and Video from Al Jazeera",
            "The Times of Israel",
            "Iran International",
        )
        for source in required:
            assert source in config.EARLY_MAX_NEWS_AGE_BY_SOURCE, (
                f"{source!r}: per-source override removed. Either restore "
                "it or relocate this assertion if intentional."
            )

    def test_analyzer_call_site_uses_per_source_helper(self):
        """Source-level pin: the analyzer's stale check (main.py) must
        call `_early_max_news_age_seconds_for_source(news.source)` —
        NOT compare against a flat `MAX_NEWS_AGE_SECONDS` constant.

        This is a textual check so any future refactor that breaks the
        parity invariant surfaces here.
        """
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "main.py"
        text = src.read_text(encoding="utf-8")

        # Locate the analyzer-stage stale check block (between the
        # docstring-tagged PARITY INVARIANT comment and its body).
        marker = "PARITY INVARIANT"
        assert marker in text, (
            "load-bearing: the analyzer-stage stale check must carry the "
            "'PARITY INVARIANT' marker so the linkage to the intake helper "
            "is greppable. If you refactor the comment, update this test."
        )
        # The block must call the per-source helper.
        idx = text.index(marker)
        block = text[idx: idx + 2000]
        assert "_early_max_news_age_seconds_for_source(news.source)" in block, (
            "load-bearing: analyzer-stage stale threshold must come from "
            "the per-source helper, not a flat constant. If this assertion "
            "fails, the 13-day-style false-rejection regression is back."
        )

    def test_per_source_threshold_strictly_greater_than_or_equal_to_default(self):
        """Sanity: every per-source override must be >= the default.
        A per-source value below the default would be a strictly-
        stricter-than-default policy, which is fine semantically but
        unusual; pin so any such entry is intentional and reviewed."""
        for source, threshold in config.EARLY_MAX_NEWS_AGE_BY_SOURCE.items():
            assert threshold >= config.EARLY_MAX_NEWS_AGE_SECONDS, (
                f"{source!r}: per-source threshold {threshold} is below "
                f"default {config.EARLY_MAX_NEWS_AGE_SECONDS}. If "
                "intentional, update this assertion. Otherwise the entry "
                "is silently stricter than the intake default."
            )


class TestAnalysisRejectedRecordSchema:
    """ANALYSIS_REJECTED records emitted by the analyzer-stage stale
    check now include `threshold_seconds` so the operator can see the
    per-source policy at rejection time. This pins the schema."""

    def test_log_analysis_rejected_accepts_threshold_seconds_kwarg(self):
        """The trade_log API must accept `threshold_seconds=` so the
        analyzer can pass through the per-source threshold."""
        from utils.logger import TradeLogger
        import inspect

        sig = inspect.signature(TradeLogger.log_analysis_rejected)
        params = sig.parameters
        assert "threshold_seconds" in params, (
            "post-fix invariant: log_analysis_rejected must accept "
            "`threshold_seconds=` for operator triage. Removing this "
            "field hides which threshold a stale rejection hit."
        )
        # Must be optional (default None) for back-compat with any caller
        # that hasn't been updated.
        assert params["threshold_seconds"].default is None, (
            "post-fix invariant: threshold_seconds must default to None "
            "for back-compat with non-updated callers."
        )
