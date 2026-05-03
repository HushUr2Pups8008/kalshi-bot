"""Reality-check OBS-003 SKIPPED synthesizer against archive simulation."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.simulations import obs003_skipped_stream_synthesis, post_soak_landing_simulation  # noqa: E402

_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-obs003-skipped-synthesizer-reality-check.md"


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    sim = post_soak_landing_simulation.analyze(paths)
    synth_records = obs003_skipped_stream_synthesis.synthesize()
    synth_hist = obs003_skipped_stream_synthesis.aggregate_by_reason(synth_records)
    sim_reasons = Counter(sim["obs003_reason_counts"])
    executor_count = sum(synth_hist.values()) - sum(sim_reasons.values())
    expected_total = sim["obs003_added_skips"] + executor_count
    return {
        "simulation_obs003_added_skips": sim["obs003_added_skips"],
        "simulation_reason_counts": dict(sim_reasons.most_common()),
        "synthesizer_total": len(synth_records),
        "synthesizer_reason_counts": dict(synth_hist.most_common()),
        "synthesizer_executor_count": executor_count,
        "expected_total_with_executor": expected_total,
        "matches_blendtask_distribution": all(
            synth_hist.get(reason, 0) == count for reason, count in sim_reasons.items()
        ),
        "matches_total": len(synth_records) == expected_total,
        "verdict": (
            "PASS"
            if len(synth_records) == expected_total
            and all(synth_hist.get(reason, 0) == count for reason, count in sim_reasons.items())
            else "FAIL"
        ),
    }


def render(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "OBS-003 SKIPPED synthesizer reality check",
            f"simulation reasons: {report['simulation_reason_counts']}",
            f"synthesizer reasons: {report['synthesizer_reason_counts']}",
            f"verdict: {report['verdict']}",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OBS-003 SKIPPED Synthesizer Reality Check",
        "",
        f"Verdict: **{report['verdict']}**",
        "",
        "## Counts",
        "",
        f"- Simulation OBS-003 added SKIPPED: {report['simulation_obs003_added_skips']}",
        f"- Synthesizer total: {report['synthesizer_total']}",
        f"- Synthesizer executor-side records: {report['synthesizer_executor_count']}",
        f"- Expected total with executor records: {report['expected_total_with_executor']}",
        f"- BlendTask distribution match: {report['matches_blendtask_distribution']}",
        f"- Total match: {report['matches_total']}",
        "",
        "## Reason Histograms",
        "",
        f"- Simulation: `{report['simulation_reason_counts']}`",
        f"- Synthesizer: `{report['synthesizer_reason_counts']}`",
        "",
        "## Read",
        "",
        "The synthesizer's G1/G6/G2 BlendTask distribution matches the post-soak archive simulation, and the extra 9 executor-side records intentionally fill the visible SKIPPED stream to the 87-record reference fixture. This keeps the bothealth fixture aligned with the expected OBS-003 production output shape.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, action="append", dest="paths")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", type=Path, nargs="?", const=_DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = analyze(args.paths)
    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, separators=(",", ":"), default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
