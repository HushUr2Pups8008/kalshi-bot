from datetime import datetime, timezone

from scripts.throughput_operator_metrics import summarize_operator_throughput


def test_summarize_operator_throughput_counts_window_normalized_rates():
    summary = summarize_operator_throughput(
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-20T00:10:00+00:00",
                "ticker": "KXAAA",
                "opportunity_age_seconds": 120,
            },
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-20T12:00:00+00:00",
                "ticker": "KXBBB",
                "age_seconds": 300,
            },
            {
                "type": "SKIPPED",
                "ts": "2026-06-20T12:05:00+00:00",
                "ticker": "KXBBB",
            },
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-20T13:00:00+00:00",
                "ticker": "KXBBB",
            },
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-20T14:00:00+00:00",
                "market_ticker": "KXBBB",
            },
            {
                "event": "PAPER_TRADE",
                "ts": "2026-06-20T15:00:00+00:00",
                "series_ticker": "KXAAA",
            },
        ],
        window_start=datetime(2026, 6, 20, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 21, tzinfo=timezone.utc),
    )

    assert summary.opportunities == 2
    assert summary.skipped == 1
    assert summary.paper_trades == 3
    assert summary.opportunities_per_day == 2.0
    assert summary.skipped_per_opportunity == 0.5
    assert summary.top_ticker_trades_per_day == [("KXBBB", 2.0), ("KXAAA", 1.0)]
    assert summary.opportunity_age_p50_seconds == 210.0
    assert summary.opportunity_age_p90_seconds == 282.0


def test_summarize_operator_throughput_marks_missing_age_unavailable():
    summary = summarize_operator_throughput(
        [
            {"type": "OPPORTUNITY", "ts": "2026-06-20T00:10:00+00:00", "ticker": "KXAAA"},
            {"type": "SKIPPED", "ts": "2026-06-20T00:11:00+00:00", "ticker": "KXAAA"},
        ],
        window_start=datetime(2026, 6, 20, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 22, tzinfo=timezone.utc),
    )

    assert summary.opportunities_per_day == 0.5
    assert summary.skipped_per_opportunity == 1.0
    assert summary.opportunity_age_available is False
    assert summary.opportunity_age_p50_seconds is None
    assert summary.opportunity_age_p90_seconds is None
