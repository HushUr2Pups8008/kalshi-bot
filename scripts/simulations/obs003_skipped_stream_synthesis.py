"""Synthesize a representative post-OBS-003 SKIPPED-stream fixture and provide
the aggregation logic `bothealth.sh` will need to add when OBS-003 lands.

Pre-staged during the PROFIT-PHASE2-001 soak per the OBS-003 spec §7 step 4
("bothealth aggregator validation"). `bothealth.sh` currently does not aggregate
SKIPPED records; once OBS-003 lands and `BlendTask._emit_skipped` is in
production, the operator updates `bothealth.sh` to surface a Skipped trades
section whose histogram matches what `aggregate_by_reason()` here produces for
the same input file.

Read-only. No DB writes. No trade-log writes. Safe to run at any time.

Usage (post-OBS-003-deploy operator workflow):

    # 1. produce the synthetic fixture
    python scripts/simulations/obs003_skipped_stream_synthesis.py \
        --emit logs/synthetic/obs003_fixture.jsonl

    # 2. run bothealth against it (after bothealth.sh is updated)
    SKIPPED_LOG_OVERRIDE=logs/synthetic/obs003_fixture.jsonl bash scripts/bothealth.sh

    # 3. compare bothealth's "Skipped trades" section to the canonical histogram
    python scripts/simulations/obs003_skipped_stream_synthesis.py --histogram

Pre-deploy (today): the test suite asserts that `aggregate_by_reason()` on the
synthesized fixture produces the histogram counts predicted by Codex's 2026-05-03
post-soak landing simulation. That makes this module the canonical reference
shape for the bothealth aggregation update.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


# Per Codex's 2026-05-03 post-soak landing simulation (commit f671468) on the
# 87-OPP post-MATCH-001 archive: BlendTask emits 78 SKIPPED records under the
# OBS-003 fix; reason distribution {G1: 59, G6: 14, G2: 5}. Plus the executor's
# pre-existing SKIPPED reasons (cooldown, opposing-position, capped_dollars,
# etc.) — sized lightly here at 9 records so the aggregate matches the
# simulation's 87-SKIP total.
#
# The synthesizer is shape-faithful to OBS-003 spec §5 (the BlendTask-emitted
# SKIPPED payload's required keys) and to trading/executor.py:137-152 (the
# executor's pre-existing SKIPPED payload). Every record carries the union of
# both shapes' keys so the aggregator's downstream consumers see one schema.

_BLENDTASK_REASON_DISTRIBUTION: dict[str, int] = {
    "G1_blended_confidence": 59,
    "G6_recency_score": 14,
    "G2_evidence_source_class_diversity": 5,
}

_EXECUTOR_REASON_DISTRIBUTION: dict[str, int] = {
    "paper cooldown: last trade 0.5h ago (cooldown=4h)": 4,
    "opposing position exists: open NO at est=0.450 -- no hedging": 3,
    "edge -0.030 below min_edge 0.02": 2,
}


def _blendtask_record(idx: int, reason: str) -> dict[str, Any]:
    """Synthetic BlendTask-emitted SKIPPED record matching OBS-003 spec §5."""
    return {
        "type": "SKIPPED",
        "reason": reason,
        "ticker": f"KXSYN-26MAY{idx:02d}",
        "headline": f"Synthetic OBS-003 fixture headline #{idx} for reason {reason}",
        "source": "Reuters",
        "method": "llm",
        "llm_direction": "yes" if idx % 2 == 0 else "no",
        "llm_magnitude": "moderate",
        "model_probability": 0.04 + (idx % 5) * 0.005,  # blended_p, post-blend
        "market_price": 0.50,
        "edge": 0.04 + (idx % 5) * 0.005 - 0.50,  # post-blend edge
        "min_edge_threshold": 0.02,
        "signal_meta": {"emitter": "BlendTask"},
    }


def _executor_record(idx: int, reason: str) -> dict[str, Any]:
    """Synthetic executor-emitted SKIPPED record matching trading/executor.py:137-152."""
    return {
        "type": "SKIPPED",
        "reason": reason,
        "ticker": f"KXSYN-26MAY{idx + 100:02d}",
        "headline": f"Synthetic executor fixture headline #{idx} for reason {reason}",
        "source": "AP",
        "method": "llm",
        "llm_direction": "yes",
        "llm_magnitude": "small",
        "model_probability": 0.55,  # fast-lane raw, not blended
        "market_price": 0.50,
        "edge": 0.05,
        "min_edge_threshold": 0.02,
    }


def synthesize() -> list[dict[str, Any]]:
    """Produce the canonical OBS-003 SKIPPED-stream fixture (87 records)."""
    out: list[dict[str, Any]] = []
    idx = 0
    for reason, count in _BLENDTASK_REASON_DISTRIBUTION.items():
        for _ in range(count):
            out.append(_blendtask_record(idx, reason))
            idx += 1
    idx = 0
    for reason, count in _EXECUTOR_REASON_DISTRIBUTION.items():
        for _ in range(count):
            out.append(_executor_record(idx, reason))
            idx += 1
    return out


def aggregate_by_reason(records: list[dict[str, Any]]) -> Counter:
    """The canonical aggregation shape `bothealth.sh` must reproduce after OBS-003.

    `bothealth.sh` will need to read the SKIPPED stream and emit one row per
    reason with a count and (optionally) a Median-edge / Median-confidence
    pair. Counts are the load-bearing piece; this is the contract.
    """
    return Counter(r.get("reason", "<no_reason>") for r in records if r.get("type") == "SKIPPED")


def render_histogram(histogram: Counter) -> str:
    """Render the histogram as the markdown table `bothealth.sh` should produce."""
    lines = ["| reason | count |", "| --- | ---: |"]
    for reason, count in histogram.most_common():
        lines.append(f"| `{reason}` | {count} |")
    return "\n".join(lines)


def emit_jsonl(path: Path) -> int:
    """Write the synthesized stream to `path` as one JSON record per line."""
    records = synthesize()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return len(records)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--emit",
        type=Path,
        help="Write the synthetic JSONL fixture to this path.",
    )
    p.add_argument(
        "--histogram",
        action="store_true",
        help="Print the canonical reason histogram for the synthetic stream.",
    )
    args = p.parse_args(argv)

    records = synthesize()

    if args.emit is not None:
        n = emit_jsonl(args.emit)
        print(f"wrote {n} synthetic SKIPPED records to {args.emit}")

    if args.histogram or args.emit is None:
        print(render_histogram(aggregate_by_reason(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
