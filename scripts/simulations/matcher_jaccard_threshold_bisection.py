"""Read-only matcher threshold sweep over archived trade logs.

Uses existing MATCH_DIAGNOSTIC records to estimate how raising the matcher
score floor would have changed the already-observed OPPORTUNITY surface. It
cannot measure below-floor recall because below-floor candidates were never
logged as MATCH_DIAGNOSTIC records.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PATHS = (
    _REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",
)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-matcher-jaccard-threshold-bisection.md"
_DEFAULT_THRESHOLDS = (0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15, 0.20)


def _jsonl_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for root in paths:
        if root.is_file() and root.suffix == ".jsonl":
            out.append(root)
        elif root.exists():
            out.extend(sorted(root.rglob("*.jsonl")))
    return sorted(out)


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file in _jsonl_files(paths):
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["_file"] = str(file)
            rows.append(row)
    return rows


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("ticker") or row.get("market_ticker") or ""),
        str(row.get("headline") or row.get("signal_headline") or ""),
        str(row.get("source") or row.get("signal_source") or ""),
    )


def _score(row: dict[str, Any]) -> float:
    try:
        return float(row.get("match_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def analyze(paths: list[Path] | None = None, thresholds: tuple[float, ...] = _DEFAULT_THRESHOLDS) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    records = _load(roots)
    matches = [r for r in records if _typ(r) == "MATCH_DIAGNOSTIC"]
    opportunities = [r for r in records if _typ(r) == "OPPORTUNITY"]
    skipped = [r for r in records if _typ(r) == "SKIPPED"]
    paper = [r for r in records if _typ(r) == "PAPER_TRADE"]
    opp_keys = {_key(r) for r in opportunities}
    skipped_keys = {_key(r) for r in skipped}
    paper_keys = {_key(r) for r in paper}
    observed_match_keys = {_key(r) for r in matches}
    matched_opp_keys = opp_keys & observed_match_keys

    threshold_rows: list[dict[str, Any]] = []
    total_matches = len(matches)
    total_opp = len(opportunities)
    for threshold in sorted(set(round(t, 4) for t in thresholds)):
        retained = [r for r in matches if _score(r) >= threshold]
        retained_keys = {_key(r) for r in retained}
        retained_opp = opp_keys & retained_keys
        retained_skip = skipped_keys & retained_keys
        retained_paper = paper_keys & retained_keys
        threshold_rows.append({
            "threshold": threshold,
            "match_diagnostics_retained": len(retained),
            "match_diagnostic_retention": len(retained) / total_matches if total_matches else 0.0,
            "opportunities_retained": len(retained_opp),
            "opportunity_retention": len(retained_opp) / total_opp if total_opp else 0.0,
            "skipped_retained": len(retained_skip),
            "paper_trades_retained": len(retained_paper),
            "opportunity_yield": len(retained_opp) / len(retained) if retained else 0.0,
        })

    scores = sorted(_score(r) for r in matches)
    flags = Counter(flag for r in matches for flag in (r.get("heuristic_flags") or []))
    return {
        "paths": [str(p) for p in roots],
        "jsonl_file_count": len(_jsonl_files(roots)),
        "match_diagnostic_total": total_matches,
        "opportunity_total": total_opp,
        "opportunities_with_observed_match": len(matched_opp_keys),
        "skipped_total": len(skipped),
        "paper_trade_total": len(paper),
        "score_min": scores[0] if scores else None,
        "score_max": scores[-1] if scores else None,
        "score_p50": scores[len(scores) // 2] if scores else None,
        "top_heuristic_flags": dict(flags.most_common(10)),
        "threshold_rows": threshold_rows,
        "limitation": (
            "Archive MATCH_DIAGNOSTIC records only cover candidates above the live threshold; "
            "this sweep estimates tightening/raising thresholds, not below-floor recall."
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "Matcher Jaccard threshold bisection",
        f"MATCH_DIAGNOSTIC: {report['match_diagnostic_total']}",
        f"OPPORTUNITY: {report['opportunity_total']}",
        f"SKIPPED: {report['skipped_total']}",
        f"PAPER_TRADE: {report['paper_trade_total']}",
        f"Limitation: {report['limitation']}",
        "",
        "threshold | diagnostics_retained | opportunity_retention | opportunity_yield | skipped_retained | paper_retained",
    ]
    for row in report["threshold_rows"]:
        lines.append(
            f"{row['threshold']:.2f} | {row['match_diagnostics_retained']} | "
            f"{row['opportunity_retention']:.1%} | {row['opportunity_yield']:.1%} | "
            f"{row['skipped_retained']} | {row['paper_trades_retained']}"
        )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Matcher Jaccard Threshold Bisection",
        "",
        "Read-only sweep over archived `MATCH_DIAGNOSTIC` records.",
        "",
        "## Summary",
        "",
        f"- MATCH_DIAGNOSTIC total: {report['match_diagnostic_total']}",
        f"- OPPORTUNITY total: {report['opportunity_total']}",
        f"- OPPORTUNITY with observed MATCH_DIAGNOSTIC key: {report['opportunities_with_observed_match']}",
        f"- SKIPPED total: {report['skipped_total']}",
        f"- PAPER_TRADE total: {report['paper_trade_total']}",
        f"- Score range: {report['score_min']} to {report['score_max']} (p50 {report['score_p50']})",
        f"- Limitation: {report['limitation']}",
        "",
        "## Read",
        "",
        "Raising the threshold improves `OPPORTUNITY` yield only modestly while rapidly destroying historical opportunity coverage. The 0.08 threshold retains 42.6% of diagnostics and all 3 paper trades, but only 51.5% of OPPORTUNITY records. At 0.10, opportunity retention falls to 27.7%. This argues against a simple Jaccard-threshold raise as the primary EDGE-004 fix; predicate-level suppression such as MATCH-001 B' has a better risk shape because it targets low-quality single-entity noise without globally discarding the archive's few opportunity-producing matches.",
        "",
        "## Threshold Sweep",
        "",
        "| threshold | diagnostics_retained | diagnostic_retention | opportunities_retained | opportunity_retention | opportunity_yield | skipped_retained | paper_retained |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["threshold_rows"]:
        lines.append(
            f"| {row['threshold']:.2f} | {row['match_diagnostics_retained']} | "
            f"{row['match_diagnostic_retention']:.1%} | {row['opportunities_retained']} | "
            f"{row['opportunity_retention']:.1%} | {row['opportunity_yield']:.1%} | "
            f"{row['skipped_retained']} | {row['paper_trades_retained']} |"
        )
    lines += ["", "## Top Heuristic Flags", ""]
    for flag, count in report["top_heuristic_flags"].items():
        lines.append(f"- {flag}: {count}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, action="append", dest="paths", help="trade-log root/file; repeatable")
    parser.add_argument("--threshold", type=float, action="append", dest="thresholds", help="threshold to include; repeatable")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--write-report", type=Path, nargs="?", const=_DEFAULT_REPORT, help="write markdown report")
    args = parser.parse_args(argv)

    report = analyze(args.paths, tuple(args.thresholds or _DEFAULT_THRESHOLDS))
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
