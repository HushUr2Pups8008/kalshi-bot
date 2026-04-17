import json
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.signal_edge_diagnostics import (
    classify_skip_reason,
    parse_date_end,
    parse_date_start,
    print_summary,
    summarize,
)


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_signal_edge_diagnostics"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _cleanup_tmp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for record in records:
        if isinstance(record, str):
            lines.append(record)
        else:
            lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_classify_skip_reason_distinguishes_zero_edge_duplicate_and_other():
    assert classify_skip_reason({"reason": "edge +0.0000 below min_edge 0.02", "edge": 0.0}) == "zero_edge"
    assert classify_skip_reason({"reason": "paper duplicate skip: existing position"}) == "duplicate"
    assert classify_skip_reason({"reason": "edge +0.0100 below min_edge 0.02", "edge": 0.01}) == "below_threshold"
    assert classify_skip_reason({"reason": "market status=closed"}) == "other"


def test_summarize_builds_edge_audit_and_group_metrics():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "KXONE",
                    "source": "NYT > World News",
                    "headline": "Talks resume",
                    "method": "llm",
                    "llm_direction": "yes",
                    "llm_magnitude": "moderate",
                    "llm_attempted": True,
                    "llm_result_used": True,
                    "llm_result_status": "ollama_success",
                    "llm_total_stage_ms": 1400,
                    "llm_queue_wait_ms": 200,
                    "llm_http_round_trip_ms": 1150,
                    "llm_parse_ms": 40,
                    "llm_contention_observed": True,
                    "llm_in_flight_at_entry": 1,
                    "final_probability": 0.62,
                    "market_price": 0.62,
                    "ts": "2026-04-12T12:00:00+00:00",
                },
                {
                    "type": "SIGNAL",
                    "source": "NYT > World News",
                    "headline": "Talks resume",
                    "ts": "2026-04-12T12:00:01+00:00",
                },
                {
                    "type": "OPPORTUNITY",
                    "ticker": "KXONE",
                    "source": "NYT > World News",
                    "headline": "Talks resume",
                    "estimated_probability": 0.62,
                    "market_yes_price": 0.62,
                    "edge": 0.0,
                    "method": "llm",
                    "llm_direction": "yes",
                    "llm_magnitude": "moderate",
                    "ts": "2026-04-12T12:00:02+00:00",
                },
                {
                    "type": "SKIPPED",
                    "ticker": "KXONE",
                    "source": "NYT > World News",
                    "headline": "Talks resume",
                    "reason": "edge +0.0000 below min_edge 0.02",
                    "edge": 0.0,
                    "ts": "2026-04-12T12:00:03+00:00",
                },
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "KXTWO",
                    "source": "Reuters",
                    "headline": "Ceasefire tested",
                    "method": "keyword",
                    "llm_attempted": True,
                    "llm_result_used": False,
                    "llm_result_status": "ollama_http_5xx",
                    "llm_total_stage_ms": 2100,
                    "llm_queue_wait_ms": 350,
                    "llm_http_round_trip_ms": 1700,
                    "final_probability": 0.58,
                    "market_price": 0.54,
                    "ts": "2026-04-12T12:05:00+00:00",
                },
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "KXTHREE",
                    "source": "AP",
                    "headline": "Balanced market skip",
                    "method": "keyword",
                    "llm_attempted": False,
                    "llm_result_used": False,
                    "llm_result_status": "llm_skipped_routing_price_band_excluded",
                    "llm_routing_passed": False,
                    "llm_routing_reason": "price_band_excluded",
                    "final_probability": 0.52,
                    "market_price": 0.50,
                    "ts": "2026-04-12T12:06:00+00:00",
                },
                {
                    "type": "SIGNAL",
                    "source": "Reuters",
                    "headline": "Ceasefire tested",
                    "ts": "2026-04-12T12:05:01+00:00",
                },
                {
                    "type": "OPPORTUNITY",
                    "ticker": "KXTWO",
                    "source": "Reuters",
                    "headline": "Ceasefire tested",
                    "estimated_probability": 0.58,
                    "market_yes_price": 0.54,
                    "edge": 0.04,
                    "method": "keyword",
                    "ts": "2026-04-12T12:05:02+00:00",
                },
                {
                    "type": "PAPER_TRADE",
                    "ticker": "KXTWO",
                    "signal_source": "Reuters",
                    "signal_headline": "Ceasefire tested",
                    "ts": "2026-04-12T12:05:03+00:00",
                },
            ],
        )

        stats = summarize(path, since=None, until=None)

        assert stats["counts"]["SIGNAL_ANALYSIS_DETAIL"] == 3
        assert stats["counts"]["SIGNAL"] == 2
        assert stats["counts"]["OPPORTUNITY"] == 2
        assert stats["counts"]["SKIPPED"] == 1
        assert stats["counts"]["EXECUTED"] == 1
        assert stats["skip_breakdown"]["zero_edge"] == 1
        assert stats["skip_breakdown"]["duplicate"] == 0
        assert stats["llm_observability"]["attempted"] == 2
        assert stats["llm_observability"]["result_used"] == 1
        assert stats["llm_observability"]["fallback"] == 1
        assert stats["llm_observability"]["skipped_routing"] == 1
        assert stats["llm_observability"]["skipped_routing_reasons"]["price_band_excluded"] == 1
        assert stats["llm_observability"]["status_counts"]["ollama_success"] == 1
        assert stats["llm_observability"]["status_counts"]["ollama_http_5xx"] == 1
        assert stats["llm_observability"]["queue_wait_ms_samples"] == [200.0, 350.0]
        assert stats["llm_observability"]["http_round_trip_ms_samples"] == [1150.0, 1700.0]
        assert stats["llm_observability"]["parse_ms_samples"] == [40.0]
        assert stats["llm_observability"]["contention_observed"] == 1
        assert stats["llm_observability"]["max_in_flight_at_entry"] == 1
        assert stats["llm_value_add"]["llm_rows"] == 1
        assert stats["llm_value_add"]["probability_movement_buckets"]["moderate"] == 1
        assert stats["llm_value_add"]["edge_magnitude_buckets"]["zero_neutral"] == 1
        assert stats["llm_value_add"]["impact_classifications"]["weak_signal"] == 1
        assert stats["llm_value_add"]["near_neutral_outputs"] == 0
        assert stats["llm_value_add"]["non_zero_edge_outputs"] == 0
        by_source_seg = {
            row["source"]: row for row in stats["llm_value_add"]["segmentation"]["by_source"]
        }
        assert by_source_seg["NYT > World News"]["llm_rows"] == 1
        assert by_source_seg["NYT > World News"]["meaningful_signal_rate"] == pytest.approx(0.0)
        by_price_band_seg = {
            row["price_band"]: row for row in stats["llm_value_add"]["segmentation"]["by_price_band"]
        }
        assert by_price_band_seg["0.60-0.80"]["llm_rows"] == 1
        assert stats["llm_value_add"]["segmentation"]["timing"]["available"] is False

        first_row = stats["audit_rows"][0]
        second_row = stats["audit_rows"][1]
        third_row = stats["audit_rows"][2]
        assert first_row["ticker"] == "KXTHREE"
        assert first_row["outcome"] == "signal only"
        assert second_row["ticker"] == "KXTWO"
        assert second_row["outcome"] == "executed"
        assert third_row["ticker"] == "KXONE"
        assert third_row["outcome"] == "opportunity skipped: zero edge"

        by_source = {row["source"]: row for row in stats["by_source"]}
        assert by_source["Reuters"]["signals"] == 1
        assert by_source["Reuters"]["opportunities"] == 1
        assert by_source["Reuters"]["avg_edge"] == pytest.approx(0.04)
        assert by_source["NYT > World News"]["zero_edge"] == 1
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_reads_partitioned_trade_root():
    tmp = _make_tmp_dir()
    try:
        root = tmp / "trades"
        _write_jsonl(
            root / "archive" / "2026" / "04" / "2026-04-11.jsonl",
            [
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "KXONE",
                    "source": "Reuters",
                    "headline": "Talks resume",
                    "final_probability": 0.60,
                    "market_price": 0.55,
                    "ts": "2026-04-11T12:00:00+00:00",
                },
            ],
        )
        _write_jsonl(
            root / "live" / "trades.jsonl",
            [
                {
                    "type": "OPPORTUNITY",
                    "ticker": "KXONE",
                    "source": "Reuters",
                    "headline": "Talks resume",
                    "estimated_probability": 0.60,
                    "market_yes_price": 0.55,
                    "edge": 0.05,
                    "ts": "2026-04-12T12:00:00+00:00",
                },
            ],
        )

        stats = summarize(root, since=None, until=None)

        assert stats["lines_total"] == 2
        assert stats["counts"]["SIGNAL_ANALYSIS_DETAIL"] == 1
        assert stats["counts"]["OPPORTUNITY"] == 1
    finally:
        _cleanup_tmp_dir(tmp)


def test_summarize_applies_date_window_and_reports_live_attribution_limit():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "KXOLD",
                    "source": "Reuters",
                    "headline": "Old event",
                    "final_probability": 0.55,
                    "market_price": 0.50,
                    "ts": "2026-04-10T23:59:59+00:00",
                },
                {
                    "type": "LIVE_ORDER",
                    "ticker": "KXNEW",
                    "status": "resting",
                    "ts": "2026-04-11T12:00:00+00:00",
                },
            ],
        )

        stats = summarize(
            path,
            since=parse_date_start("2026-04-11"),
            until=parse_date_end("2026-04-11"),
        )

        assert stats["counts"]["SIGNAL_ANALYSIS_DETAIL"] == 0
        assert stats["counts"]["EXECUTED"] == 1
        assert stats["live_execution_attribution_limited"] is True
    finally:
        _cleanup_tmp_dir(tmp)


def test_print_summary_includes_edge_sections(capsys):
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "SIGNAL_ANALYSIS_DETAIL",
                    "ticker": "KXONE",
                    "source": "Reuters",
                    "headline": "Edge audit",
                    "method": "keyword",
                    "final_probability": 0.55,
                    "market_price": 0.50,
                    "ts": "2026-04-12T12:00:00+00:00",
                },
                {
                    "type": "OPPORTUNITY",
                    "ticker": "KXONE",
                    "source": "Reuters",
                    "headline": "Edge audit",
                    "estimated_probability": 0.55,
                    "market_yes_price": 0.50,
                    "edge": 0.05,
                    "method": "keyword",
                    "ts": "2026-04-12T12:00:01+00:00",
                },
            ],
        )

        stats = summarize(path, since=None, until=None)
        print_summary(stats, top=5, recent=5, since=None, until=None)
        output = capsys.readouterr().out

        assert "SIGNAL-TO-EDGE DIAGNOSTICS" in output
        assert "Recent Signal Cohort" in output
        assert "Zero-Edge Breakdown" in output
        assert "Per-Event Edge Audit" in output
        assert "Aggregate by Source" in output
        assert "Aggregate by Ticker" in output
        assert "LLM Path Observability" in output
        assert "LLM Value-Add Analysis" in output
        assert "LLM Value-Add Segmentation" in output
        assert "LLM skipped (routing)" in output
        assert "Top sources by meaningful signal rate" in output
        assert "Market price bands by meaningful signal rate" in output
        assert "Rare non-neutral examples" in output
        assert "Near-neutral outputs" in output
        assert "Strong LLM Signal Examples" in output
        assert "Neutral Confirmation Examples" in output
        assert "LLM total stage" in output
        assert "LLM queue wait" in output
        assert "Live orders are counted in the cohort summary" in output
    finally:
        _cleanup_tmp_dir(tmp)
