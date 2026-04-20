"""Measure durable structured-log append latency.

This is an offline diagnostic for PROFIT-PERF-001. It writes synthetic JSONL
records through TradeLogStore, preserving the same flush+fsync path used by
runtime structured telemetry, and reports latency percentiles.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from utils.logger import TradeLogStore


def summarize_latencies(latencies_ms: Sequence[float]) -> dict[str, float | int]:
    if not latencies_ms:
        return {
            "count": 0,
            "min_ms": 0.0,
            "mean_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
        }
    ordered = sorted(latencies_ms)
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 4),
        "mean_ms": round(statistics.fmean(ordered), 4),
        "p95_ms": round(_percentile(ordered, 0.95), 4),
        "p99_ms": round(_percentile(ordered, 0.99), 4),
        "max_ms": round(ordered[-1], 4),
    }


def run_benchmark(*, records: int, root: Path) -> dict[str, float | int | str]:
    store = TradeLogStore(
        root=root,
        live_path=root / "live" / "trades.jsonl",
        legacy_path=root / "trades.jsonl",
    )
    latencies_ms: list[float] = []
    for idx in range(records):
        record = {
            "type": "STRUCTURED_LOG_LATENCY_BENCHMARK",
            "ts": datetime.now(timezone.utc).isoformat(),
            "idx": idx,
            "ticker": "KXBENCH",
            "payload": "x" * 256,
        }
        started = time.perf_counter()
        store.append(record)
        latencies_ms.append((time.perf_counter() - started) * 1000)
    summary = summarize_latencies(latencies_ms)
    summary["root"] = str(root)
    return summary


def _percentile(ordered: Sequence[float], pct: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    idx = pct * (len(ordered) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(ordered) - 1)
    weight = idx - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=250)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    if args.records < 1:
        parser.error("--records must be >= 1")

    if args.root is None:
        with tempfile.TemporaryDirectory(prefix="trade-log-bench-") as tmp:
            summary = run_benchmark(records=args.records, root=Path(tmp))
    else:
        args.root.mkdir(parents=True, exist_ok=True)
        summary = run_benchmark(records=args.records, root=args.root)

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("Structured log latency benchmark")
        for key, value in summary.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
