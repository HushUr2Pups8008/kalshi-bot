"""Wave-1 acceptance-ladder forecast from the post-soak stack simulation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.simulations import post_soak_landing_simulation  # noqa: E402

_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-wave1-acceptance-ladder-forecast.md"


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    sim = post_soak_landing_simulation.analyze(paths)
    steps = {row["step"]: row for row in sim["steps"]}
    ladder = [
        {
            "item": "OBS-005",
            "validation_window": "24h",
            "expected_post_state": "No archive-visible count change; cooldown sentinel no longer blocks never-traded tickers after restart.",
            "archive_anchor": steps["OBS-005"],
            "rollback_trigger": "Fresh-process never-traded ticker still gets cooldown-blocked, or cooldown bypasses a genuinely recent paper trade.",
        },
        {
            "item": "MATCH-001_B_prime",
            "validation_window": "24h",
            "expected_post_state": "OPPORTUNITY retained forecast 87/260; PAPER_TRADE retained 3/3 before EXEC-002; MATCH_SUPPRESSED rate rises materially.",
            "archive_anchor": steps["MATCH-001_B_prime"],
            "rollback_trigger": "Any canonical-event headline is suppressed for its canonical ticker, or suppression count moves opposite the forecast.",
        },
        {
            "item": "OBS-003",
            "validation_window": "24h",
            "expected_post_state": "Visible SKIPPED forecast 87 retained records after MATCH-001; dominant new reasons G1=59, G6=14, G2=5.",
            "archive_anchor": steps["OBS-003"],
            "rollback_trigger": "OPPORTUNITY without PAPER_TRADE/SKIPPED persists, or SKIPPED payload misses required keys.",
        },
        {
            "item": "EXEC-002",
            "validation_window": "72h",
            "expected_post_state": "PAPER_TRADE forecast drops from 3 to 1 on archive due same-series burst suppression; SKIPPED +2.",
            "archive_anchor": steps["EXEC-002"],
            "rollback_trigger": "Same-series duplicate paper trades still occur inside 1h, or unrelated series are suppressed.",
        },
    ]
    return {
        "simulation": sim,
        "ladder": ladder,
        "summary": "Wave-1 expected end state after all four items: 87 OPPORTUNITY, 89 SKIPPED, 1 PAPER_TRADE on the 13-day archive counterfactual.",
    }


def render(report: dict[str, Any]) -> str:
    lines = ["Wave-1 acceptance ladder forecast", "item | window | opp | skipped | paper"]
    for row in report["ladder"]:
        anchor = row["archive_anchor"]
        lines.append(
            f"{row['item']} | {row['validation_window']} | {anchor['opportunity']} | "
            f"{anchor['skipped']} | {anchor['paper_trade']}"
        )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Wave-1 Acceptance-Ladder Forecast",
        "",
        report["summary"],
        "",
        "| item | validation window | OPPORTUNITY | SKIPPED | PAPER_TRADE | expected post-state | rollback trigger |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in report["ladder"]:
        anchor = row["archive_anchor"]
        lines.append(
            f"| {row['item']} | {row['validation_window']} | {anchor['opportunity']} | "
            f"{anchor['skipped']} | {anchor['paper_trade']} | {row['expected_post_state']} | "
            f"{row['rollback_trigger']} |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Forecast uses the existing 13-day MacBook archive replay, not live post-soak records.",
        "- Counts are acceptance windows, not guarantees; live feed mix can shift.",
        "- The ladder pairs with the pre-soak-close rehearsal checklist and post-soak rollback runbook.",
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
