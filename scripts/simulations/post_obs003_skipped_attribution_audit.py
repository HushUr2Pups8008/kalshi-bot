"""Post-OBS-003 SKIPPED-stream attribution audit.

Pre-staged during the PROFIT-PHASE2-001 soak. Once OBS-003 lands and
14 d of full-attribution SKIPPED data accumulates, Lever B (G1
calibration) sizing requires aggregating live SKIPPED records by
`reason` to identify the dominant kill gates and counterfactually
compute the bin distributions per the Lever B spec §4.

This audit is the **live-data complement** to the pre-OBS-003
synthesizer (`scripts/simulations/obs003_skipped_stream_synthesis.py`):

  - Synthesizer: produces a representative fixture for bothealth.sh
    and aggregator-shape contract testing. Pre-fix data.
  - This audit: reads the actual production SKIPPED stream once
    OBS-003 is live and reports per-reason aggregates + per-gate
    counterfactual edge bins for Lever B sizing. Post-fix data.

Read-only. No DB writes. Safe to run at any time once OBS-003 lands.

Operator workflow (post-OBS-003 deploy + 14 d window):

    python scripts/simulations/post_obs003_skipped_attribution_audit.py
    # Reads logs/trades/live/trades.jsonl + archive/2026/05+ JSONL

    python scripts/simulations/post_obs003_skipped_attribution_audit.py --since 2026-05-15
    # Filter to post-OBS-003-deploy window

    python scripts/simulations/post_obs003_skipped_attribution_audit.py --json
    # Machine-readable for downstream tooling

The audit reports:

  1. Total SKIPPED records partitioned by `reason`.
  2. G1 / G2 / G3 / G4 / G5 / G6 + blender-side reason counts.
  3. Per-gate counterfactual edge distribution: for each G1-killed
     candidate, what is its `model_probability - market_price` (i.e.,
     post-blend edge)? Bin into [< -0.02], [-0.02, 0], [0, 0.02],
     [0.02, 0.05], [≥ 0.05]. Lever B sizing reads this distribution
     to project the EV of admitting candidates at lower G1 floors.
  4. Source-class distribution per kill gate: do G1 kills concentrate
     in certain source-classes? (Joins SKIPPED.signal_meta if present.)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_DEFAULT_PATHS: tuple[Path, ...] = (
    _REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl",
    *sorted((_REPO_ROOT / "logs" / "trades" / "archive" / "2026" / "05").glob("*.jsonl")),
    *sorted((_REPO_ROOT / "logs" / "trades" / "archive" / "2026" / "06").glob("*.jsonl")),
)


def _load_skipped(paths: Iterable[Path], since: str | None = None) -> list[dict[str, Any]]:
    """Load SKIPPED records from the given JSONL paths."""
    out: list[dict[str, Any]] = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "SKIPPED":
                continue
            if since:
                ts = rec.get("ts", "")
                if ts and ts[:10] < since:
                    continue
            out.append(rec)
    return out


def aggregate_by_reason(records: list[dict[str, Any]]) -> Counter:
    """Count SKIPPED records by reason — same shape as the synthesizer's
    aggregator function. Mirrors what `bothealth.sh` will need to surface."""
    return Counter(r.get("reason", "<no_reason>") for r in records)


def counterfactual_edge_bins(records: list[dict[str, Any]], reason: str) -> Counter:
    """For SKIPPED records with the given reason, bin their post-blend
    edge into the Lever B sizing buckets:

      < -0.02          : strongly negative (would lose)
      [-0.02, 0)       : marginally negative
      [0, 0.02)        : zero / sub-threshold
      [0.02, 0.05)     : tradeable (passes paper_min_edge)
      ≥ 0.05           : strongly tradeable

    Lever B sizing (admitting candidates at lower G1 floors) is only
    EV-positive if the [0.02, 0.05) and ≥ 0.05 bins together account
    for a meaningful share of the kills. Codex's 2026-05-03 G1
    admittance counterfactual found these bins are sparse.
    """
    bins = Counter()
    for r in records:
        if r.get("reason") != reason:
            continue
        edge = r.get("edge")
        if edge is None:
            bins["<no_edge>"] += 1
            continue
        if edge < -0.02:
            bins["lt_-0.02"] += 1
        elif edge < 0:
            bins["[-0.02,0)"] += 1
        elif edge < 0.02:
            bins["[0,0.02)"] += 1
        elif edge < 0.05:
            bins["[0.02,0.05)"] += 1
        else:
            bins["ge_0.05"] += 1
    return bins


def source_class_per_gate(records: list[dict[str, Any]]) -> dict[str, Counter]:
    """For each kill reason, count the source_class of contributing evidence.
    Reads SKIPPED.signal_meta.source_class if present (BlendTask SKIPPED
    records carry signal_meta per OBS-003 spec §5)."""
    out: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        reason = r.get("reason", "<no_reason>")
        sc = (r.get("signal_meta") or {}).get("source_class", "<unknown>")
        out[reason][sc] += 1
    return out


def _is_blendtask_emitted(record: dict[str, Any]) -> bool:
    """Heuristic: BlendTask SKIPPED records carry one of the G1-G6
    or blender-side reasons. Executor SKIPPED records carry the
    pre-existing reason set (cooldown / opposing-position / etc).
    Used to partition the stream when OBS-003 is live."""
    reason = record.get("reason", "")
    if reason.startswith("G") and len(reason) >= 2 and reason[1].isdigit():
        return True
    if "structural_tier" in reason or reason == "structural_tier2_veto":
        return True
    return False


def analyze(paths: Iterable[Path] | None = None, since: str | None = None) -> dict[str, Any]:
    paths_list = list(paths or _DEFAULT_PATHS)
    records = _load_skipped(paths_list, since=since)

    by_reason = aggregate_by_reason(records)
    blendtask = [r for r in records if _is_blendtask_emitted(r)]
    executor = [r for r in records if not _is_blendtask_emitted(r)]

    return {
        "paths_scanned": [str(p) for p in paths_list],
        "since": since,
        "total_skipped": len(records),
        "blendtask_emitted": len(blendtask),
        "executor_emitted": len(executor),
        "by_reason": dict(by_reason.most_common()),
        "g1_edge_bins": dict(counterfactual_edge_bins(records, "G1_blended_confidence")),
        "g6_edge_bins": dict(counterfactual_edge_bins(records, "G6_recency_score")),
        "g2_edge_bins": dict(counterfactual_edge_bins(records, "G2_evidence_source_class_diversity")),
        "source_class_per_gate": {k: dict(v) for k, v in source_class_per_gate(records).items()},
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "# Post-OBS-003 SKIPPED-stream attribution audit",
        "",
        f"- Total SKIPPED: {report['total_skipped']} ({report['blendtask_emitted']} BlendTask + {report['executor_emitted']} executor)",
        f"- Since filter: {report['since'] or '<none>'}",
        "",
        "## By reason",
        "",
        "| reason | count |",
        "| --- | ---: |",
    ]
    for reason, count in report["by_reason"].items():
        lines.append(f"| `{reason}` | {count} |")
    if report["g1_edge_bins"]:
        lines += ["", "## G1_blended_confidence — counterfactual edge bins", "", "| bin | count |", "| --- | ---: |"]
        for bin_, count in report["g1_edge_bins"].items():
            lines.append(f"| `{bin_}` | {count} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--since", help="YYYY-MM-DD; filter SKIPPED records to this date or later", default=None)
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args(argv)
    report = analyze(since=args.since)
    if args.json:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
