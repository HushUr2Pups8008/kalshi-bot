"""EDGE-004 Wave-2 expected-state ladder from existing archive audits."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.simulations import (  # noqa: E402
    g1_admittance_counterfactual,
    lever_a1_source_classifier_counterfactual,
    post_soak_landing_simulation,
)

_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md"


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    wave1 = post_soak_landing_simulation.analyze(paths)
    a1 = lever_a1_source_classifier_counterfactual.analyze(paths)
    g1 = g1_admittance_counterfactual.analyze(paths)
    wave1_final = wave1["steps"][-1]
    g1_rows = {f"{row['g1_floor']:.2f}": row for row in g1["floor_rows"]}
    ladder = [
        {
            "step": "Wave1_post_EXEC002",
            "opportunity": wave1_final["opportunity"],
            "skipped": wave1_final["skipped"],
            "paper_trade": wave1_final["paper_trade"],
            "trade_rate": wave1_final["trade_rate"],
            "read": "Base-stack expected state after OBS-005, MATCH-001 B', OBS-003, EXEC-002.",
        },
        {
            "step": "Lever_A1_classifier_fix",
            "opportunity": wave1_final["opportunity"],
            "skipped": wave1_final["skipped"],
            "paper_trade": wave1_final["paper_trade"],
            "trade_rate": wave1_final["trade_rate"],
            "read": (
                "Archive-visible source reclassification only; no replayable volume change. "
                f"OPPORTUNITY official surrogate post-A.1 = "
                f"{a1['opportunity_source_label_replay']['post_a1_distribution'].get('official', 0)}/260."
            ),
        },
        {
            "step": "Lever_A1_plus_new_feed",
            "opportunity": None,
            "skipped": None,
            "paper_trade": None,
            "trade_rate": None,
            "read": "Not archive-replayable; acceptance window should target >=5% OPPORTUNITY->PAPER_TRADE conversion over 14d.",
        },
        {
            "step": "Lever_B_G1_0.04",
            "opportunity": f"+{g1_rows['0.04']['g1_kills_admitted']} G1-admitted candidates before downstream gates",
            "skipped": "post-OBS-003 attribution required",
            "paper_trade": f"{g1_rows['0.04']['edge_ge_0_02']} candidates with predicted edge >=0.02",
            "trade_rate": None,
            "read": "Attribution lever: admits 32/197 G1 kills, but only 1 candidate clears estimated paper edge.",
        },
        {
            "step": "Lever_C_cross_series_hash",
            "opportunity": "risk-control only",
            "skipped": "cross_series_headline_in_window expected on repeated headline groups",
            "paper_trade": "expected to reduce correlated bursts, not create trades",
            "trade_rate": None,
            "read": "Codex overlap audit sized 128/260 cross-series OPPORTUNITY records; ship only if A/B fail to close EDGE-004.",
        },
    ]
    return {
        "wave1": wave1,
        "lever_a1": a1,
        "g1": g1,
        "ladder": ladder,
        "verdict": (
            "Wave 2 has no archive-backed direct edge-production lever except Lever A.1/A.1+ changing the intake mix. "
            "Lever B is attribution/calibration; Lever C is risk-control. Acceptance windows should reflect that."
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines = ["EDGE-004 Wave-2 expected-state ladder", "step | opportunity | skipped | paper_trade | read"]
    for row in report["ladder"]:
        lines.append(
            f"{row['step']} | {row['opportunity']} | {row['skipped']} | {row['paper_trade']} | {row['read']}"
        )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# EDGE-004 Wave-2 Expected-State Ladder",
        "",
        report["verdict"],
        "",
        "| step | OPPORTUNITY expectation | SKIPPED expectation | PAPER_TRADE expectation | read |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["ladder"]:
        lines.append(
            f"| {row['step']} | {row['opportunity']} | {row['skipped']} | {row['paper_trade']} | {row['read']} |"
        )
    lines += [
        "",
        "## Acceptance Windows",
        "",
        "- Lever A.1 classifier fix: verify source-class distribution moved in the predicted direction; do not expect immediate archive-replayable trade-rate lift from classification alone.",
        "- Lever A.1+ feeds: use the live 14d conversion target (>=5%) because archive replay cannot synthesize new feed arrivals.",
        "- Lever B: treat new admissions as attribution samples; expected direct trade candidates are 1 at floor 0.04 and 2 at floor 0.03 before downstream gates.",
        "- Lever C: treat as correlated-risk suppression; success is fewer repeated-headline paper bursts, not higher trade count.",
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
