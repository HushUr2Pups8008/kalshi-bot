import json
import shutil
import uuid
from pathlib import Path

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
                    "final_probability": 0.58,
                    "market_price": 0.54,
                    "ts": "2026-04-12T12:05:00+00:00",
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

        assert stats["counts"]["SIGNAL_ANALYSIS_DETAIL"] == 2
        assert stats["counts"]["SIGNAL"] == 2
        assert stats["counts"]["OPPORTUNITY"] == 2
        assert stats["counts"]["SKIPPED"] == 1
        assert stats["counts"]["EXECUTED"] == 1
        assert stats["skip_breakdown"]["zero_edge"] == 1
        assert stats["skip_breakdown"]["duplicate"] == 0

        first_row = stats["audit_rows"][0]
        second_row = stats["audit_rows"][1]
        assert first_row["ticker"] == "KXTWO"
        assert first_row["outcome"] == "executed"
        assert second_row["ticker"] == "KXONE"
        assert second_row["outcome"] == "opportunity skipped: zero edge"

        by_source = {row["source"]: row for row in stats["by_source"]}
        assert by_source["Reuters"]["signals"] == 1
        assert by_source["Reuters"]["opportunities"] == 1
        assert by_source["Reuters"]["avg_edge"] == 0.04
        assert by_source["NYT > World News"]["zero_edge"] == 1
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
        assert "Live orders are counted in the cohort summary" in output
    finally:
        _cleanup_tmp_dir(tmp)
