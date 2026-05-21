from scripts.market_source_hints_diagnostics import (
    parse_date_end,
    parse_date_start,
    print_summary,
    summarize,
)
from tests._helpers import cleanup_tmp_dir, make_tmp_dir, write_jsonl


def test_summarize_collects_shadow_only_source_hint_diagnostics():
    tmp = make_tmp_dir("market_source_hints_diagnostics")
    try:
        path = tmp / "trades.jsonl"
        write_jsonl(
            path,
            [
                {
                    "type": "MARKET_SOURCE_HINT_DIAGNOSTIC",
                    "ticker": "KXIRAN-1",
                    "mode": "shadow",
                    "shadow_only": True,
                    "targets": [
                        {"source": "Reuters", "domain": "reuters.com", "query_count": 1, "feed_url_count": 0},
                        {"source": "Associated Press", "domain": "apnews.com", "query_count": 2, "feed_url_count": 1},
                    ],
                    "rejected_labels": {"news outlets": "generic_or_unverifiable_label"},
                    "log_records": [{"type": "MARKET_SOURCE_HINT_SHADOW", "shadow_only": True}],
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "MARKET_SOURCE_HINT_DIAGNOSTIC",
                    "ticker": "KXIRAN-1",
                    "mode": "advisory",
                    "shadow_only": True,
                    "targets": [
                        {"source": "Reuters", "domain": "reuters.com", "query_count": 1, "feed_url_count": 0},
                    ],
                    "rejected_labels": {},
                    "log_records": [],
                    "ts": "2026-04-12T10:01:00+00:00",
                },
                {"type": "OTHER", "ticker": "ignored", "ts": "2026-04-12T10:02:00+00:00"},
                "not-json",
            ],
        )

        stats = summarize(path, since=None, until=None)

        assert stats["lines_total"] == 4
        assert stats["lines_malformed"] == 1
        assert stats["records_kept"] == 3
        assert stats["diagnostic_records"] == 2
        assert stats["shadow_only_records"] == 2
        assert stats["non_shadow_records"] == 0
        assert stats["records_with_targets"] == 2
        assert stats["records_with_rejected_labels"] == 1
        assert stats["child_shadow_records"] == 1
        assert stats["target_query_count"] == 4
        assert stats["target_feed_url_count"] == 1
        assert stats["by_mode"]["shadow"] == 1
        assert stats["by_mode"]["advisory"] == 1
        assert stats["by_source"]["Reuters"] == 2
        assert stats["by_source_domain"]["reuters.com"] == 2
        assert stats["by_rejected_reason"]["generic_or_unverifiable_label"] == 1
        assert stats["examples"][0]["ticker"] == "KXIRAN-1"
    finally:
        cleanup_tmp_dir(tmp)


def test_summarize_respects_date_filter_and_exclude_test():
    tmp = make_tmp_dir("market_source_hints_diagnostics")
    try:
        path = tmp / "trades.jsonl"
        write_jsonl(
            path,
            [
                {
                    "type": "MARKET_SOURCE_HINT_DIAGNOSTIC",
                    "ticker": "KXOLD",
                    "mode": "shadow",
                    "shadow_only": True,
                    "targets": [],
                    "ts": "2026-04-10T23:59:59+00:00",
                },
                {
                    "type": "MARKET_SOURCE_HINT_DIAGNOSTIC",
                    "ticker": "KXTEST-SYNTH",
                    "mode": "shadow",
                    "shadow_only": True,
                    "targets": [],
                    "ts": "2026-04-11T12:00:00+00:00",
                },
                {
                    "type": "MARKET_SOURCE_HINT_DIAGNOSTIC",
                    "ticker": "KXNEW",
                    "mode": "shadow",
                    "shadow_only": True,
                    "targets": [],
                    "ts": "2026-04-11T13:00:00+00:00",
                },
            ],
        )

        stats = summarize(
            path,
            since=parse_date_start("2026-04-11"),
            until=parse_date_end("2026-04-11"),
            exclude_test=True,
        )

        assert stats["diagnostic_records"] == 1
        assert stats["by_ticker"]["KXNEW"] == 1
        assert "KXOLD" not in stats["by_ticker"]
        assert "KXTEST-SYNTH" not in stats["by_ticker"]
    finally:
        cleanup_tmp_dir(tmp)


def test_print_summary_includes_safety_sections(capsys):
    tmp = make_tmp_dir("market_source_hints_diagnostics")
    try:
        path = tmp / "trades.jsonl"
        write_jsonl(
            path,
            [
                {
                    "type": "MARKET_SOURCE_HINT_DIAGNOSTIC",
                    "ticker": "KXIRAN-1",
                    "mode": "shadow",
                    "shadow_only": True,
                    "targets": [{"source": "Reuters", "domain": "reuters.com", "query_count": 1, "feed_url_count": 0}],
                    "rejected_labels": {"blogs": "generic_or_unverifiable_label"},
                    "log_records": [{"type": "MARKET_SOURCE_HINT_SHADOW", "shadow_only": True}],
                    "ts": "2026-04-12T10:00:00+00:00",
                },
                {
                    "type": "MARKET_SOURCE_HINT_DIAGNOSTIC",
                    "ticker": "KXBAD",
                    "mode": "shadow",
                    "shadow_only": False,
                    "targets": [],
                    "ts": "2026-04-12T10:01:00+00:00",
                },
            ],
        )
        stats = summarize(path, since=None, until=None)

        print_summary(stats, top=5, recent=5)
        output = capsys.readouterr().out

        assert "MarketSourceHints Diagnostics" in output
        assert "Diagnostic only -- not consumed by readiness/admission/trading" in output
        assert "Diagnostic records:       2" in output
        assert "Shadow-only records:      1 (50.0%)" in output
        assert "Non-shadow records:       1" in output
        assert "SAFETY WARNING" in output
        assert "Top hinted sources" in output
        assert "Rejected label reasons" in output
        assert "Recent examples" in output
    finally:
        cleanup_tmp_dir(tmp)
