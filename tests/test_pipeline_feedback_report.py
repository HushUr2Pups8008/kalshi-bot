from __future__ import annotations

import json
from pathlib import Path

from scripts.pipeline_feedback_report import summarize_events


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


class TestPipelineFunnelReport:
    def test_summarizes_stage_counts_reasons_and_tickers(self, tmp_path: Path):
        log_path = _write_jsonl(
            tmp_path / "trades.jsonl",
            [
                {"type": "EARLY_FRESH_PASS", "source": "Reuters"},
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "ticker": "KXIRAN-26JUN01",
                    "source": "Reuters",
                },
                {
                    "type": "MATCH_LLM_REVIEW",
                    "ticker": "KXIRAN-26JUN01",
                    "market_prefix": "KXIRAN",
                    "source": "Reuters",
                    "verdict": "false_positive_neutral",
                },
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "KXIRAN-26JUN01",
                    "source": "Reuters",
                },
                {
                    "type": "SKIPPED",
                    "ticker": "KXIRAN-26JUN01",
                    "reason": "G1_blended_confidence",
                },
                {
                    "type": "PAPER_TRADE",
                    "ticker": "KXUSAIRANAGREEMENT-27-26JUN",
                    "source": "AP",
                },
            ],
        )

        summary = summarize_events([log_path])

        assert summary["funnel"]["stage_counts"] == {
            "EARLY_FRESH_PASS": 1,
            "MATCH_DIAGNOSTIC": 1,
            "MATCH_WEIGHT_APPLIED": 0,
            "MATCH_LLM_REVIEW": 1,
            "SIGNAL_ANALYSIS_DETAIL": 1,
            "SIGNAL": 0,
            "OPPORTUNITY": 0,
            "BLEND_DECISION": 0,
            "SKIPPED": 1,
            "PAPER_TRADE": 1,
            "PAPER_RESOLUTION": 0,
            "CALIBRATION_CHECK": 0,
        }
        assert {
            "key": "SKIPPED:G1_blended_confidence",
            "count": 1,
        } in summary["funnel"]["top_reasons"]
        assert summary["funnel"]["top_tickers"][0] == {
            "key": "KXIRAN-26JUN01",
            "count": 4,
        }


class TestSourceFreshnessReport:
    def test_groups_fresh_and_stale_intake_by_source_class_and_reason(self, tmp_path: Path):
        log_path = _write_jsonl(
            tmp_path / "freshness.jsonl",
            [
                {
                    "type": "EARLY_FRESH_PASS",
                    "source": "Reuters",
                    "source_class": "newswire",
                },
                {
                    "type": "EARLY_STALE_DROP",
                    "source": "Reuters",
                    "source_class": "newswire",
                    "reason": "stale_by_source_policy",
                },
                {
                    "type": "EARLY_STALE_DROP",
                    "source": "Blog",
                    "source_class": "blog",
                    "reason": "disabled_source",
                },
            ],
        )

        summary = summarize_events([log_path])

        assert summary["freshness"]["totals"] == {
            "EARLY_FRESH_PASS": 1,
            "EARLY_STALE_DROP": 2,
        }
        assert summary["freshness"]["by_source"]["Reuters"] == {
            "EARLY_FRESH_PASS": 1,
            "EARLY_STALE_DROP": 1,
        }
        assert summary["freshness"]["by_source_class"]["newswire"] == {
            "EARLY_FRESH_PASS": 1,
            "EARLY_STALE_DROP": 1,
        }
        assert summary["freshness"]["top_reasons"][0] == {
            "key": "EARLY_STALE_DROP:stale_by_source_policy",
            "count": 1,
        }


class TestMarketMixReport:
    def test_groups_llm_neutral_and_signal_yield_by_prefix_and_source_class(self, tmp_path: Path):
        log_path = _write_jsonl(
            tmp_path / "market_mix.jsonl",
            [
                {
                    "type": "MATCH_DIAGNOSTIC",
                    "ticker": "KXIRAN-26JUN01",
                    "source_class": "newswire",
                },
                {
                    "type": "MATCH_LLM_REVIEW",
                    "ticker": "KXIRAN-26JUN01",
                    "market_prefix": "KXIRAN",
                    "source_class": "newswire",
                    "verdict": "false_positive_neutral",
                },
                {
                    "type": "MATCH_LLM_REVIEW",
                    "ticker": "KXIRAN-26JUN01",
                    "market_prefix": "KXIRAN",
                    "source_class": "newswire",
                    "verdict": "true_positive",
                },
                {
                    "type": "SIGNAL",
                    "ticker": "KXIRAN-26JUN01",
                    "source_class": "newswire",
                },
                {
                    "type": "OPPORTUNITY",
                    "ticker": "KXIRAN-26JUN01",
                    "source_class": "newswire",
                },
            ],
        )

        summary = summarize_events([log_path])

        assert summary["market_mix"]["by_prefix"]["KXIRAN"] == {
            "MATCH_DIAGNOSTIC": 1,
            "MATCH_LLM_REVIEW": 2,
            "llm_neutral": 1,
            "llm_true_positive": 1,
            "SIGNAL_ANALYSIS_DETAIL": 0,
            "SIGNAL": 1,
            "OPPORTUNITY": 1,
            "BLEND_DECISION": 0,
            "SKIPPED": 0,
            "PAPER_TRADE": 0,
            "PAPER_RESOLUTION": 0,
            "CALIBRATION_CHECK": 0,
        }
        assert summary["market_mix"]["by_source_class"]["newswire"]["llm_neutral"] == 1

    def test_groups_non_kalshi_market_mix_by_explicit_series_or_venue(self, tmp_path: Path):
        log_path = _write_jsonl(
            tmp_path / "polymarket_mix.jsonl",
            [
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "ewc-usgub-ks-2026-11-03-dem",
                    "series_ticker": "polymarket_us",
                    "source_class": "newswire",
                },
                {
                    "type": "SIGNAL",
                    "ticker": "some-polymarket-slug",
                    "venue": "polymarket_us",
                    "source_class": "newswire",
                },
            ],
        )

        summary = summarize_events([log_path])

        assert summary["market_mix"]["by_prefix"]["polymarket_us"]["SIGNAL_ANALYSIS_DETAIL"] == 1
        assert summary["market_mix"]["by_prefix"]["polymarket_us"]["SIGNAL"] == 1

    def test_groups_full_polymarket_path_by_venue(self, tmp_path: Path):
        log_path = _write_jsonl(
            tmp_path / "polymarket_full_path.jsonl",
            [
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "ewc-usgub-ks-2026-11-03-dem",
                    "venue": "polymarket_us",
                    "source_class": "newswire",
                },
                {
                    "type": "OPPORTUNITY",
                    "ticker": "ewc-usgub-ks-2026-11-03-dem",
                    "venue": "polymarket_us",
                    "source_class": "newswire",
                },
                {
                    "type": "BLEND_DECISION",
                    "market_ticker": "ewc-usgub-ks-2026-11-03-dem",
                    "venue": "polymarket_us",
                    "source_class": "newswire",
                },
                {
                    "type": "SKIPPED",
                    "ticker": "ewc-usgub-ks-2026-11-03-dem",
                    "venue": "polymarket_us",
                    "source_class": "newswire",
                    "reason": "G1_blended_confidence",
                },
                {
                    "type": "PAPER_TRADE",
                    "ticker": "ewc-usgub-ks-2026-11-03-dem",
                    "venue": "polymarket_us",
                    "source_class": "newswire",
                },
                {
                    "type": "PAPER_RESOLUTION",
                    "ticker": "ewc-usgub-ks-2026-11-03-dem",
                    "venue": "polymarket_us",
                    "source_class": "newswire",
                },
                {
                    "type": "CALIBRATION_CHECK",
                    "market_ticker": "ewc-usgub-ks-2026-11-03-dem",
                    "venue": "polymarket_us",
                    "source_class": "newswire",
                },
            ],
        )

        summary = summarize_events([log_path])

        polymarket_mix = summary["market_mix"]["by_prefix"]["polymarket_us"]
        assert polymarket_mix["SIGNAL_ANALYSIS_DETAIL"] == 1
        assert polymarket_mix["OPPORTUNITY"] == 1
        assert polymarket_mix["BLEND_DECISION"] == 1
        assert polymarket_mix["SKIPPED"] == 1
        assert polymarket_mix["PAPER_TRADE"] == 1
        assert polymarket_mix["PAPER_RESOLUTION"] == 1
        assert polymarket_mix["CALIBRATION_CHECK"] == 1
