"""
Tests for LLM latency observability (v0.27.4).

Covers:
  - _llm_meta() latency_ms field
  - log_signal_analysis_detail() writes llm_latency_ms when provided
  - signal_edge_diagnostics.summarize() collects latency samples
  - fmt_latency_stats() / _p95() arithmetic
  - Non-LLM rows and missing-latency rows are excluded from samples
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


from analysis.signal_analyzer import _llm_meta
from utils.log_records import SignalAnalysisDetail
from scripts.signal_edge_diagnostics import (
    _p95,
    fmt_latency_stats,
    summarize,
)
from tests._helpers import make_tmp_dir, write_jsonl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cleanup(path: Path) -> None:
    """Module-specific cleanup: supports path-or-file arg (differs from
    the generic cleanup_tmp_dir in tests/_helpers.py)."""
    shutil.rmtree(path.parent if path.is_file() else path, ignore_errors=True)


def _base_detail(
    ticker: str = "KXTEST",
    method: str = "llm",
    llm_attempted: bool = True,
    llm_result_used: bool = True,
    llm_result_status: str = "ollama_success",
    llm_latency_ms: int | None = None,
    llm_total_stage_ms: int | None = None,
    llm_queue_wait_ms: int | None = None,
    llm_http_round_trip_ms: int | None = None,
    llm_parse_ms: int | None = None,
    llm_contention_observed: bool | None = None,
    llm_in_flight_at_entry: int | None = None,
    ts: str = "2026-04-15T10:00:00+00:00",
) -> dict:
    record: dict = {
        "type": "SIGNAL_ANALYSIS_DETAIL",
        "ticker": ticker,
        "source": "Reuters",
        "headline": "Test headline",
        "method": method,
        "llm_attempted": llm_attempted,
        "llm_result_used": llm_result_used,
        "llm_result_status": llm_result_status,
        "final_probability": 0.6,
        "market_price": 0.5,
        "ts": ts,
    }
    if llm_latency_ms is not None:
        record["llm_latency_ms"] = llm_latency_ms
    if llm_total_stage_ms is not None:
        record["llm_total_stage_ms"] = llm_total_stage_ms
    if llm_queue_wait_ms is not None:
        record["llm_queue_wait_ms"] = llm_queue_wait_ms
    if llm_http_round_trip_ms is not None:
        record["llm_http_round_trip_ms"] = llm_http_round_trip_ms
    if llm_parse_ms is not None:
        record["llm_parse_ms"] = llm_parse_ms
    if llm_contention_observed is not None:
        record["llm_contention_observed"] = llm_contention_observed
    if llm_in_flight_at_entry is not None:
        record["llm_in_flight_at_entry"] = llm_in_flight_at_entry
    return record


# ---------------------------------------------------------------------------
# 1. _llm_meta latency_ms field
# ---------------------------------------------------------------------------

class TestLlmMeta:
    def test_latency_ms_included_when_provided(self):
        meta = _llm_meta(
            attempted=True,
            status="ollama_success",
            provider="ollama",
            result_used=True,
            latency_ms=1234,
            total_stage_ms=1500,
            queue_wait_ms=200,
            http_round_trip_ms=1200,
            parse_ms=50,
            contention_observed=True,
            in_flight_at_entry=1,
        )
        assert meta["latency_ms"] == 1234
        assert meta["total_stage_ms"] == 1500
        assert meta["queue_wait_ms"] == 200
        assert meta["http_round_trip_ms"] == 1200
        assert meta["parse_ms"] == 50
        assert meta["contention_observed"] is True
        assert meta["in_flight_at_entry"] == 1

    def test_latency_ms_none_by_default(self):
        meta = _llm_meta(attempted=True, status="ollama_success", provider="ollama")
        assert meta["latency_ms"] is None

    def test_latency_ms_zero_is_valid(self):
        meta = _llm_meta(attempted=True, status="ollama_http_error", provider="ollama", latency_ms=0)
        assert meta["latency_ms"] == 0

    def test_non_latency_fields_unchanged(self):
        meta = _llm_meta(
            attempted=True,
            status="ollama_timeout",
            provider="ollama",
            result_used=False,
            latency_ms=59000,
        )
        assert meta["attempted"] is True
        assert meta["status"] == "ollama_timeout"
        assert meta["result_used"] is False
        assert meta["provider"] == "ollama"


# ---------------------------------------------------------------------------
# 2. log_signal_analysis_detail writes llm_latency_ms
# ---------------------------------------------------------------------------

class TestLogSignalAnalysisDetail:
    def test_latency_ms_written_to_record(self):
        from utils.logger import TradeLogger
        tmp = make_tmp_dir("llm_latency_observability")
        log_file = tmp / "trades.jsonl"
        logger = TradeLogger(log_file)
        try:
            logger.log_signal_analysis_detail(SignalAnalysisDetail(
                ticker="KXONE",
                source="AP",
                headline="Test",
                method="llm",
                keywords=["war"],
                keyword_contributions=None,
                base_probability=0.5,
                final_probability=0.65,
                market_price=0.5,
                llm_attempted=True,
                llm_result_used=True,
                llm_result_status="ollama_success",
                llm_provider="ollama",
                llm_latency_ms=2500,
                llm_total_stage_ms=3200,
                llm_queue_wait_ms=600,
                llm_http_round_trip_ms=2400,
                llm_parse_ms=80,
                llm_contention_observed=True,
                llm_in_flight_at_entry=1,
            ))
            record = json.loads(log_file.read_text(encoding="utf-8").strip())
            assert record["llm_latency_ms"] == 2500
            assert record["llm_total_stage_ms"] == 3200
            assert record["llm_queue_wait_ms"] == 600
            assert record["llm_http_round_trip_ms"] == 2400
            assert record["llm_parse_ms"] == 80
            assert record["llm_contention_observed"] is True
            assert record["llm_in_flight_at_entry"] == 1
        finally:
            _cleanup(tmp)

    def test_latency_ms_absent_when_none(self):
        from utils.logger import TradeLogger
        tmp = make_tmp_dir("llm_latency_observability")
        log_file = tmp / "trades.jsonl"
        logger = TradeLogger(log_file)
        try:
            logger.log_signal_analysis_detail(SignalAnalysisDetail(
                ticker="KXONE",
                source="AP",
                headline="Test",
                method="keyword",
                keywords=["war"],
                keyword_contributions=None,
                base_probability=0.5,
                final_probability=0.55,
                market_price=0.5,
            ))
            record = json.loads(log_file.read_text(encoding="utf-8").strip())
            assert "llm_latency_ms" not in record
        finally:
            _cleanup(tmp)

    def test_pre_llm_fields_written_when_provided(self):
        from utils.logger import TradeLogger
        tmp = make_tmp_dir("llm_latency_observability")
        log_file = tmp / "trades.jsonl"
        logger = TradeLogger(log_file)
        try:
            logger.log_signal_analysis_detail(SignalAnalysisDetail(
                ticker="KXONE",
                source="AP",
                headline="Test",
                method="llm",
                keywords=[],
                keyword_contributions=None,
                base_probability=0.5,
                final_probability=0.65,
                market_price=0.5,
                pre_llm_quality_pass=False,
                pre_llm_semantic_overlap_count=1,
                pre_llm_semantic_overlap_ratio=0.2,
                pre_llm_would_block=True,
                pre_llm_keyword_override=False,
                pre_llm_gate_reason="weak_semantic_overlap",
            ))
            record = json.loads(log_file.read_text(encoding="utf-8").strip())
            assert record["pre_llm_quality_pass"] is False
            assert record["pre_llm_semantic_overlap_count"] == 1
            assert record["pre_llm_semantic_overlap_ratio"] == 0.2
            assert record["pre_llm_would_block"] is True
            assert record["pre_llm_keyword_override"] is False
            assert record["pre_llm_gate_reason"] == "weak_semantic_overlap"
        finally:
            _cleanup(tmp)


# ---------------------------------------------------------------------------
# 3. summarize() collects latency samples
# ---------------------------------------------------------------------------

class TestSummarizeLatency:
    def test_latency_samples_collected(self):
        tmp = make_tmp_dir("llm_latency_observability")
        path = tmp / "trades.jsonl"
        write_jsonl(path, [
            _base_detail(llm_latency_ms=1000, llm_total_stage_ms=1200),
            _base_detail(llm_latency_ms=2000, llm_total_stage_ms=2200, ts="2026-04-15T10:01:00+00:00"),
            _base_detail(llm_latency_ms=3000, llm_total_stage_ms=3200, ts="2026-04-15T10:02:00+00:00"),
        ])
        try:
            stats = summarize(path, None, None)
            samples = stats["llm_observability"]["latency_ms_samples"]
            assert sorted(samples) == [1000.0, 2000.0, 3000.0]
            assert sorted(stats["llm_observability"]["total_stage_ms_samples"]) == [1200.0, 2200.0, 3200.0]
        finally:
            _cleanup(tmp)

    def test_rows_without_latency_excluded_from_samples(self):
        tmp = make_tmp_dir("llm_latency_observability")
        path = tmp / "trades.jsonl"
        write_jsonl(path, [
            _base_detail(llm_latency_ms=500),
            _base_detail(ts="2026-04-15T10:01:00+00:00"),  # no latency field
        ])
        try:
            stats = summarize(path, None, None)
            samples = stats["llm_observability"]["latency_ms_samples"]
            assert samples == [500.0]
        finally:
            _cleanup(tmp)

    def test_keyword_only_rows_can_have_latency(self):
        # keyword-only fallback still attempted Ollama (and got a latency)
        tmp = make_tmp_dir("llm_latency_observability")
        path = tmp / "trades.jsonl"
        write_jsonl(path, [
            _base_detail(
                method="keyword",
                llm_attempted=True,
                llm_result_used=False,
                llm_result_status="ollama_timeout",
                llm_latency_ms=60000,
            ),
        ])
        try:
            stats = summarize(path, None, None)
            samples = stats["llm_observability"]["latency_ms_samples"]
            assert samples == [60000.0]
        finally:
            _cleanup(tmp)

    def test_timing_breakdown_samples_collected(self):
        tmp = make_tmp_dir("llm_latency_observability")
        path = tmp / "trades.jsonl"
        write_jsonl(path, [
            _base_detail(
                llm_latency_ms=2000,
                llm_total_stage_ms=2400,
                llm_queue_wait_ms=300,
                llm_http_round_trip_ms=1900,
                llm_parse_ms=40,
                llm_contention_observed=True,
                llm_in_flight_at_entry=1,
            ),
        ])
        try:
            stats = summarize(path, None, None)
            assert stats["llm_observability"]["queue_wait_ms_samples"] == [300.0]
            assert stats["llm_observability"]["http_round_trip_ms_samples"] == [1900.0]
            assert stats["llm_observability"]["parse_ms_samples"] == [40.0]
            assert stats["llm_observability"]["contention_observed"] == 1
            assert stats["llm_observability"]["max_in_flight_at_entry"] == 1
        finally:
            _cleanup(tmp)

    def test_empty_log_gives_empty_samples(self):
        tmp = make_tmp_dir("llm_latency_observability")
        path = tmp / "trades.jsonl"
        path.write_text("", encoding="utf-8")
        try:
            stats = summarize(path, None, None)
            assert stats["llm_observability"]["latency_ms_samples"] == []
        finally:
            _cleanup(tmp)


# ---------------------------------------------------------------------------
# 4. fmt_latency_stats and _p95 arithmetic
# ---------------------------------------------------------------------------

class TestFmtLatencyStats:
    def test_empty_returns_na(self):
        assert fmt_latency_stats([]) == "n/a"

    def test_single_sample(self):
        result = fmt_latency_stats([1000.0])
        assert "1000ms" in result
        assert "n=1" in result

    def test_avg_correct(self):
        result = fmt_latency_stats([1000.0, 3000.0])
        assert "avg=2000ms" in result

    def test_max_correct(self):
        result = fmt_latency_stats([500.0, 1000.0, 2000.0])
        assert "max=2000ms" in result

    def test_contains_n_count(self):
        result = fmt_latency_stats([100.0, 200.0, 300.0])
        assert "n=3" in result


class TestP95:
    def test_none_for_single_value(self):
        assert _p95([500.0]) is None

    def test_two_values_returns_float(self):
        result = _p95([100.0, 200.0])
        assert isinstance(result, float)

    def test_p95_above_median(self):
        # For a uniform distribution, p95 should be > median
        samples = [float(i) for i in range(1, 101)]
        p95_val = _p95(samples)
        med = 50.5
        assert p95_val is not None
        assert p95_val > med

    def test_p95_at_most_max(self):
        samples = [float(i) for i in range(1, 101)]
        p95_val = _p95(samples)
        assert p95_val is not None
        assert p95_val <= max(samples)
