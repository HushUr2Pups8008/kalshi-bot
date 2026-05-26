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
