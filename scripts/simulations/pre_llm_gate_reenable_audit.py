"""Pre-LLM gate re-enable feasibility sweep over archived trade logs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-edge004-lever-d-pre-llm-gate-audit.md"
_DEFAULT_THRESHOLDS = (0.04, 0.05, 0.06, 0.08)


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("market_ticker") or "")


def _headline(row: dict[str, Any]) -> str:
    return str(row.get("headline") or row.get("signal_headline") or "")


def _source(row: dict[str, Any]) -> str:
    return str(row.get("source") or row.get("signal_source") or "")


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (_ticker(row), _headline(row), _source(row))


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(iter_trade_records(path))
    return rows


def _best_by_score(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = _key(row)
        if key not in out or _float(row.get("match_score")) > _float(out[key].get("match_score")):
            out[key] = row
    return out


def _first_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        out.setdefault(_key(row), row)
    return out


def _pre_llm_pass(match: dict[str, Any] | None, detail: dict[str, Any] | None) -> bool:
    if detail is not None:
        return not bool(detail.get("pre_llm_would_block")) and detail.get("pre_llm_quality_pass") is not False
    if match is not None:
        return not bool(match.get("would_fail_pre_llm_gate"))
    return False


def analyze(
    paths: list[Path] | None = None,
    thresholds: tuple[float, ...] = _DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    matches = [r for r in rows if _typ(r) == "MATCH_DIAGNOSTIC"]
    details = [r for r in rows if _typ(r) == "SIGNAL_ANALYSIS_DETAIL"]
    opportunities = [r for r in rows if _typ(r) == "OPPORTUNITY"]
    papers = [r for r in rows if _typ(r) == "PAPER_TRADE"]

    match_by_key = _best_by_score(matches)
    detail_by_key = _first_by_key(details)
    paper_keys = {_key(r) for r in papers}
    baseline_mean_abs = (
        sum(abs(_float(r.get("edge"))) for r in opportunities) / len(opportunities)
        if opportunities
        else 0.0
    )
    baseline_nonzero = sum(1 for r in opportunities if _float(r.get("edge")) != 0.0)
    baseline_positive = sum(1 for r in opportunities if _float(r.get("edge")) > 0.0)

    threshold_rows: list[dict[str, Any]] = []
    for threshold in sorted(set(round(t, 4) for t in thresholds)):
        retained: list[dict[str, Any]] = []
        score_blocked = gate_blocked = missing_match = 0
        retained_diagnostics = 0
        for match in matches:
            if _float(match.get("match_score")) >= threshold and _pre_llm_pass(match, detail_by_key.get(_key(match))):
                retained_diagnostics += 1
        for opp in opportunities:
            key = _key(opp)
            match = match_by_key.get(key)
            detail = detail_by_key.get(key)
            if match is None:
                missing_match += 1
                continue
            if _float(match.get("match_score")) < threshold:
                score_blocked += 1
                continue
            if not _pre_llm_pass(match, detail):
                gate_blocked += 1
                continue
            retained.append(opp)

        retained_mean_abs = (
            sum(abs(_float(r.get("edge"))) for r in retained) / len(retained) if retained else 0.0
        )
        retained_nonzero = sum(1 for r in retained if _float(r.get("edge")) != 0.0)
        retained_positive = sum(1 for r in retained if _float(r.get("edge")) > 0.0)
        threshold_rows.append(
            {
                "threshold": threshold,
                "diagnostics_retained_by_gate": retained_diagnostics,
                "opportunities_retained": len(retained),
                "opportunity_retention": len(retained) / len(opportunities) if opportunities else 0.0,
                "score_blocked_opportunities": score_blocked,
                "gate_blocked_opportunities": gate_blocked,
                "missing_match_opportunities": missing_match,
                "paper_trades_retained": sum(1 for r in retained if _key(r) in paper_keys),
                "positive_edge_retained": retained_positive,
                "positive_edge_total": baseline_positive,
                "nonzero_edge_retained": retained_nonzero,
                "nonzero_edge_total": baseline_nonzero,
                "mean_abs_edge_retained": retained_mean_abs,
                "mean_abs_edge_baseline": baseline_mean_abs,
                "mean_abs_edge_lift": retained_mean_abs / baseline_mean_abs if baseline_mean_abs else 0.0,
            }
        )

    return {
        "paths": [str(p) for p in roots],
        "match_diagnostic_total": len(matches),
        "signal_analysis_detail_total": len(details),
        "opportunity_total": len(opportunities),
        "paper_trade_total": len(papers),
        "baseline_mean_abs_edge": baseline_mean_abs,
        "baseline_positive_edge_count": baseline_positive,
        "baseline_nonzero_edge_count": baseline_nonzero,
        "threshold_rows": threshold_rows,
        "limitation": (
            "Archive MATCH_DIAGNOSTIC records only cover candidates that cleared the live matcher. "
            "Thresholds below the live 0.06 floor cannot recover never-logged below-floor candidates."
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "Pre-LLM gate re-enable audit",
        f"MATCH_DIAGNOSTIC: {report['match_diagnostic_total']}",
        f"SIGNAL_ANALYSIS_DETAIL: {report['signal_analysis_detail_total']}",
        f"OPPORTUNITY: {report['opportunity_total']}",
        f"PAPER_TRADE: {report['paper_trade_total']}",
        "threshold | opp_retained | paper_retained | nonzero_edge_retained | mean_abs_edge_lift",
    ]
    for row in report["threshold_rows"]:
        lines.append(
            f"{row['threshold']:.2f} | {row['opportunities_retained']} | "
            f"{row['paper_trades_retained']} | {row['nonzero_edge_retained']}/{row['nonzero_edge_total']} | "
            f"{row['mean_abs_edge_lift']:.2f}x"
        )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# EDGE-004 Lever D Pre-LLM Gate Re-Enable Audit",
        "",
        "Read-only sweep over the 13-day MacBook archive. The simulation keeps an `OPPORTUNITY` only when its archived `MATCH_DIAGNOSTIC.match_score` clears the tested floor and the archived pre-LLM gate diagnostics say the candidate would pass.",
        "",
        "## Summary",
        "",
        f"- MATCH_DIAGNOSTIC total: {report['match_diagnostic_total']}",
        f"- SIGNAL_ANALYSIS_DETAIL total: {report['signal_analysis_detail_total']}",
        f"- OPPORTUNITY total: {report['opportunity_total']}",
        f"- PAPER_TRADE total: {report['paper_trade_total']}",
        f"- Baseline positive-edge OPPORTUNITY: {report['baseline_positive_edge_count']}",
        f"- Baseline nonzero-edge OPPORTUNITY: {report['baseline_nonzero_edge_count']}",
        f"- Limitation: {report['limitation']}",
        "",
        "## Retention Curve",
        "",
        "| min_match_score | diagnostics retained by gate | OPPORTUNITY retained | OPPORTUNITY retention | score-blocked OPP | gate-blocked OPP | PAPER_TRADE retained | positive-edge retained | nonzero-edge retained | mean abs edge lift |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["threshold_rows"]:
        lines.append(
            f"| {row['threshold']:.2f} | {row['diagnostics_retained_by_gate']} | "
            f"{row['opportunities_retained']} | {row['opportunity_retention']:.1%} | "
            f"{row['score_blocked_opportunities']} | {row['gate_blocked_opportunities']} | "
            f"{row['paper_trades_retained']} | {row['positive_edge_retained']}/{row['positive_edge_total']} | "
            f"{row['nonzero_edge_retained']}/{row['nonzero_edge_total']} | {row['mean_abs_edge_lift']:.2f}x |"
        )
    lines += [
        "",
        "## Read",
        "",
        "Lever D is an aggressive volume filter, not a clean EDGE-004 primary fix. At the live floor range (0.04-0.06) it retains 67/260 OPPORTUNITY records (-74%) while preserving all 3 historical PAPER_TRADE records and 4/5 nonzero-edge OPPORTUNITY records. At 0.08 it tightens further to 56/260 (-78%) and still preserves all 3 historical PAPER_TRADE records.",
        "",
        "The edge enrichment is real but partly misleading: the retained set has a higher mean absolute edge because it keeps the three FISA paper trades, which were losing same-series trades and are already the EXEC-002 target. Lever D also retains only 1/2 positive-edge non-trades, so it would hide one known OBS-003 positive-edge miss rather than make it executable.",
        "",
        "## Recommendation",
        "",
        "Do not select Lever D as the first EDGE-004 Wave-2 implementation. Use it as a secondary LLM-budget / noise-control knob after MATCH-001 B' and OBS-003 make the post-fix attribution stream visible. Lever A remains the better first design target despite its weak standalone case because it changes weighting rather than deleting roughly three quarters of the archive OPPORTUNITY surface.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, action="append", dest="paths")
    parser.add_argument("--threshold", type=float, action="append", dest="thresholds")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", type=Path, nargs="?", const=_DEFAULT_REPORT)
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
