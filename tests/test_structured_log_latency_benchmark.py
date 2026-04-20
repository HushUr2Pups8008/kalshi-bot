from pathlib import Path

from scripts.structured_log_latency_benchmark import run_benchmark, summarize_latencies


def test_summarize_latencies_reports_tail_values():
    summary = summarize_latencies([1.0, 2.0, 3.0, 4.0, 100.0])

    assert summary["count"] == 5
    assert summary["min_ms"] == 1.0
    assert summary["max_ms"] == 100.0
    assert summary["p95_ms"] > summary["mean_ms"]
    assert summary["p99_ms"] <= summary["max_ms"]


def test_run_benchmark_writes_structured_records(tmp_path: Path):
    summary = run_benchmark(records=3, root=tmp_path)

    assert summary["count"] == 3
    assert summary["max_ms"] >= 0
    live_log = tmp_path / "live" / "trades.jsonl"
    assert live_log.exists()
    assert live_log.read_text(encoding="utf-8").count(
        "STRUCTURED_LOG_LATENCY_BENCHMARK"
    ) == 3
