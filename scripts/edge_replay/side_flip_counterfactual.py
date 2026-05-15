#!/usr/bin/env python3
"""Cycle-17C E3 side-inference flip-sign counterfactual replay."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.edge_replay import g1_admission_sweep as g1
from scripts.edge_replay.score_counterfactual_pnl import bootstrap_ci

DEFAULT_DATASET = Path("logs/edge_replay/cycle16d/replay_dataset.jsonl")
DEFAULT_SCORES = Path("logs/edge_replay/cycle16d/counterfactual_scores.json")
DEFAULT_PRICES = Path("logs/edge_replay/cycle16d/historical_prices_cycle16d.json")
DEFAULT_COVERAGE = Path("logs/edge_replay/cycle16d/coverage_audit.json")
DEFAULT_OUTPUT_DIR = Path("logs/edge_replay/cycle17c-e3")
DEFAULT_REPORT = Path("docs/governance/2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md")

GROUP_KEYS = ("signal_source", "market_family", "signal_type", "news_class")
CONSISTENCY_AXES = ("signal_source", "market_family", "signal_type", "news_class")
IC16_MIN_TRADES = 10
TRIVIAL_EXCESS_WIN_THRESHOLD = 0.5
CONSISTENCY_MIN_P = 0.20
DOMINANT_WIN_SHARE_MAX = 0.75

FORBIDDEN_TOUCH_SURFACES = (
    "analysis/signal_analyzer.py",
    "scripts/edge_replay/scorer_forensics_audit.py",
    "logs/edge_replay/cycle16d/*",
    "tasks/blend_task.py",
    "executor.py",
    "tasks/trade_readiness_gate.py",
    "governance/prompts.py",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entry_price_cents(row: dict) -> float | None:
    # F-11 P1-C: prefer canonical post-P1-A key; fall back to the legacy
    # `market_yes_price` key for pre-bounce JSONL records and historical
    # replay corpora. Missing stays missing -- do NOT default to 0 or 50.
    val = row.get("entry_price_cents")
    if val is None:
        val = row.get("market_yes_price")
    return _as_float(val)


def _abs_delta(left: Any, right: Any) -> float | None:
    left_value = _as_float(left)
    right_value = _as_float(right)
    if left_value is None or right_value is None:
        return None
    return abs(left_value - right_value)


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"yes", "true", "1"}:
        return True
    if text in {"no", "false", "0"}:
        return False
    return None


def _market_family(row: dict[str, Any]) -> str:
    return str(row.get("market_family") or row.get("series_ticker") or "unknown")


def _axis_value(row: dict[str, Any], axis: str) -> str:
    if axis == "market_family":
        return _market_family(row)
    return str(row.get(axis) or "unknown")


def _pnl(side: str, price_cents: float, contracts: int, resolved_yes: bool) -> float:
    cost = price_cents / 100.0
    if side == "yes":
        per_contract = (1.0 - cost) if resolved_yes else -cost
    else:
        per_contract = -cost if resolved_yes else (1.0 - cost)
    return per_contract * contracts


def _contract_price_cents(row: dict[str, Any], *, target_side: str, original_side: str) -> float | None:
    canonical = _as_float(row.get("entry_price_cents"))
    if canonical is not None:
        if original_side in {"yes", "no"} and target_side != original_side:
            return 100.0 - canonical
        return canonical
    yes_price = _as_float(row.get("market_yes_price"))
    if yes_price is None:
        return None
    return yes_price if target_side == "yes" else 100.0 - yes_price


def _production_proxy_rows(raw_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    admission_rows = [g1._admission_row(row, g1_threshold=0.05) for row in raw_rows]
    indexed = list(enumerate(admission_rows))
    indexed.sort(key=lambda item: (str(item[1]["decision_ts"]), item[0]))

    accepted_indices: list[int] = []
    skipped: Counter[str] = Counter()
    last_trade_ts: dict[str, Any] = {}
    open_position: dict[str, dict[str, Any]] = {}

    for index, gate_row in indexed:
        ticker = str(gate_row["ticker"])
        if not gate_row["candidate"]:
            skipped["baseline_not_trade"] += 1
            continue
        if not gate_row["readiness"]:
            skipped["readiness_not_admitted"] += 1
            continue
        if _entry_price_cents(gate_row) is None:
            skipped["skipped_no_price"] += 1
            continue
        if not gate_row["paper_price"]:
            skipped["paper_price_sanity"] += 1
            continue
        if not gate_row["signed_edge"]:
            skipped["signed_edge_mismatch"] += 1
            continue

        decision_ts = g1._parse_ts(gate_row["decision_ts"])
        last_ts = last_trade_ts.get(ticker)
        if decision_ts is not None and last_ts is not None:
            elapsed = (decision_ts - last_ts).total_seconds()
            if elapsed < g1.PAPER_TICKER_COOLDOWN_SECONDS:
                skipped["paper_ticker_cooldown"] += 1
                continue

        position = open_position.get(ticker)
        if position is not None:
            if position["side"] != gate_row["side"]:
                skipped["opposing_position"] += 1
                continue
            prob_delta = abs((position["model_prob"] or 0.0) - (gate_row["model_prob"] or 0.0))
            price_delta = _abs_delta(position["entry_price_cents"], _entry_price_cents(gate_row))
            if price_delta is None:
                skipped["skipped_no_price"] += 1
                continue
            if (
                g1.PAPER_BLOCK_SAME_SIDE_DUPLICATE
                and prob_delta < g1.PAPER_DUPLICATE_PROB_DELTA
                and price_delta < g1.PAPER_DUPLICATE_PRICE_DELTA
            ):
                skipped["paper_duplicate_position"] += 1
                continue
            if prob_delta < g1.SAME_SIGNAL_PROB_DELTA and price_delta < g1.SAME_SIGNAL_PRICE_DELTA:
                skipped["same_signal_position"] += 1
                continue

        accepted_indices.append(index)
        if decision_ts is not None:
            last_trade_ts[ticker] = decision_ts
        open_position[ticker] = {
            "side": gate_row["side"],
            "model_prob": gate_row["model_prob"],
            # F-11 P1-C: store under canonical key; already resolved via _entry_price_cents above
            "entry_price_cents": _entry_price_cents(gate_row),
        }

    accepted = []
    for index in accepted_indices:
        row = deepcopy(raw_rows[index])
        row["_source_index"] = index
        row["_original_side"] = admission_rows[index]["side"]
        accepted.append(row)
    return accepted, dict(sorted(skipped.items()))


def _score_flip_row(row: dict[str, Any]) -> dict[str, Any]:
    original_side = str(row.get("_original_side") or g1._infer_side(row))
    flip_side = "no" if original_side == "yes" else "yes"
    price = _contract_price_cents(row, target_side=flip_side, original_side=original_side)
    resolved_yes = _as_bool(row.get("resolved_yes"))
    contracts = int(_as_float(row.get("contracts")) or 1)
    if price is None or resolved_yes is None:
        raise ValueError(f"cannot score row {row.get('_source_index')}: missing price or resolution")
    flip_win = (flip_side == "yes" and resolved_yes is True) or (flip_side == "no" and resolved_yes is False)
    scored = {
        **row,
        "market_family": _market_family(row),
        "original_side": original_side,
        "flip_side": flip_side,
        "flip_win": flip_win,
        "would_have_traded": True,
        "would_have_won": flip_win,
        "counterfactual_pnl": _pnl(flip_side, price, contracts, resolved_yes),
        "market_implied_win_prob_flip": price / 100.0,
    }
    scored.pop("_original_side", None)
    return scored


def _summarize_group(rows: list[dict[str, Any]], group: dict[str, str]) -> dict[str, Any]:
    pnl_values = [float(row["counterfactual_pnl"]) for row in rows]
    wins = [row for row in rows if row["flip_win"]]
    avg = mean(pnl_values) if pnl_values else 0.0
    std = pstdev(pnl_values) if len(pnl_values) > 1 else 0.0
    ci_lo, ci_hi = bootstrap_ci(pnl_values)
    trades = len(rows)
    return {
        "group": group,
        "trades": trades,
        "wins": len(wins),
        "losses": trades - len(wins),
        "win_rate": (len(wins) / trades) if trades else None,
        "pnl": sum(pnl_values),
        "ev_mean": avg,
        "avg_pnl_per_trade": avg,
        "sharpe_like": (avg / std) if std else None,
        "ev_ci_95_lo": ci_lo,
        "ev_ci_95_hi": ci_hi,
        "ic16_eligible": ci_lo > 0 and trades >= IC16_MIN_TRADES,
    }


def _slice_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(_axis_value(row, name) for name in GROUP_KEYS)
        groups[key].append(row)

    metrics = [_summarize_group(group_rows, dict(zip(GROUP_KEYS, key, strict=True))) for key, group_rows in groups.items()]
    metrics.sort(key=lambda row: (row["ic16_eligible"], row["ev_ci_95_lo"], row["trades"]), reverse=True)
    return metrics


def _comb(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


def _hypergeom_prob(a: int, row1: int, col1: int, total: int) -> float:
    return (_comb(col1, a) * _comb(total - col1, row1 - a)) / _comb(total, row1)


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    row1 = a + b
    row2 = c + d
    col1 = a + c
    total = row1 + row2
    if total == 0 or row1 == 0 or row2 == 0:
        return 1.0
    observed = _hypergeom_prob(a, row1, col1, total)
    low = max(0, row1 - (total - col1))
    high = min(row1, col1)
    p_value = 0.0
    for x in range(low, high + 1):
        prob = _hypergeom_prob(x, row1, col1, total)
        if prob <= observed + 1e-12:
            p_value += prob
    return min(1.0, p_value)


def _axis_consistency(rows: list[dict[str, Any]], axis: str) -> dict[str, Any]:
    bins: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bins[_axis_value(row, axis)].append(row)

    bin_metrics = []
    for name, bin_rows in sorted(bins.items()):
        trades = len(bin_rows)
        wins = sum(1 for row in bin_rows if row["flip_win"])
        bin_metrics.append(
            {
                "bin": name,
                "trades": trades,
                "wins": wins,
                "losses": trades - wins,
                "flip_win_rate": (wins / trades) if trades else None,
                "win_share": None,
            }
        )

    total_wins = sum(item["wins"] for item in bin_metrics)
    total_trades = sum(item["trades"] for item in bin_metrics)
    for item in bin_metrics:
        if total_wins > 0:
            item["win_share"] = item["wins"] / total_wins
        elif total_trades > 0:
            item["win_share"] = item["trades"] / total_trades
        else:
            item["win_share"] = None

    pairwise = []
    for left, right in itertools.combinations(bin_metrics, 2):
        if left["trades"] == 0 or right["trades"] == 0:
            continue
        pairwise.append(
            {
                "left": left["bin"],
                "right": right["bin"],
                "p_value": fisher_exact_two_sided(left["wins"], left["losses"], right["wins"], right["losses"]),
            }
        )

    min_p = min((item["p_value"] for item in pairwise), default=1.0)
    dominant_share = max((item["win_share"] or 0.0 for item in bin_metrics), default=0.0)
    failed = min_p < CONSISTENCY_MIN_P and dominant_share > DOMINANT_WIN_SHARE_MAX
    return {
        "axis": axis,
        "bins": bin_metrics,
        "pairwise": pairwise,
        "min_pairwise_p_value": min_p,
        "dominant_bin_win_share": dominant_share,
        "passed": not failed,
    }


def evaluate_counterfactual(
    raw_rows: list[dict[str, Any]],
    *,
    scores_payload: dict[str, Any] | None = None,
    prices_payload: dict[str, Any] | None = None,
    coverage_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    admitted_rows, skipped = _production_proxy_rows(raw_rows)
    scored_rows = [_score_flip_row(row) for row in admitted_rows]
    slices = _slice_metrics(scored_rows)
    ic16_slices = [row for row in slices if row["ic16_eligible"]]

    actual_flip_wins = sum(1 for row in scored_rows if row["flip_win"])
    market_implied_expected_wins_flip = sum(float(row["market_implied_win_prob_flip"]) for row in scored_rows)
    excess_wins_vs_market_flip = actual_flip_wins - market_implied_expected_wins_flip

    consistency = {axis: _axis_consistency(scored_rows, axis) for axis in CONSISTENCY_AXES}
    consistency_passed = all(item["passed"] for item in consistency.values())

    if len(scored_rows) < IC16_MIN_TRADES:
        verdict_label = "diagnostic_only_revert"
    elif not ic16_slices:
        verdict_label = "revert_required_no_ic16_slice"
    elif excess_wins_vs_market_flip <= TRIVIAL_EXCESS_WIN_THRESHOLD:
        verdict_label = "revert_trivial_inversion"
    elif not consistency_passed:
        verdict_label = "revert_uniform_consistency_failed"
    else:
        verdict_label = "keep_candidate"

    summary = {
        "verdict": verdict_label,
        "verdict_label": verdict_label,
        "input_rows": len(raw_rows),
        "admitted_rows": len(scored_rows),
        "production_proxy_skipped": skipped,
        "actual_flip_wins": actual_flip_wins,
        "actual_flip_losses": len(scored_rows) - actual_flip_wins,
        "market_implied_expected_wins_flip": market_implied_expected_wins_flip,
        "excess_wins_vs_market": excess_wins_vs_market_flip,
        "excess_wins_vs_market_flip": excess_wins_vs_market_flip,
        "total_slices": len(slices),
        "ic16_slice_count": len(ic16_slices),
        "raw_positive_ev_slice_count": sum(1 for row in slices if row["ev_ci_95_lo"] > 0),
        "top_slice": slices[0] if slices else None,
        "ic16_slices": ic16_slices,
        "slices": slices,
        "consistency": consistency,
        "clause_pass": {
            "A_ic16_baseline": bool(ic16_slices),
            "B_trivial_inversion": excess_wins_vs_market_flip > TRIVIAL_EXCESS_WIN_THRESHOLD,
            "C_sub_slice_consistency": consistency_passed,
            "D_charter_compliance": True,
        },
        "charter": {
            "script_mutates_production_or_frozen_artifacts": False,
            "forbidden_touch_surfaces": list(FORBIDDEN_TOUCH_SURFACES),
            "permitted_output_artifacts": [
                "scripts/edge_replay/side_flip_counterfactual.py",
                "tests/test_side_flip_counterfactual.py",
                "docs/governance/2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md",
                "logs/edge_replay/cycle17c-e3/side_flip_summary.json",
                "logs/edge_replay/cycle17c-e3/side_flip_per_row.jsonl",
            ],
        },
        "artifact_context": {
            "scores_rows": len((scores_payload or {}).get("rows", [])) if isinstance(scores_payload, dict) else None,
            "price_ticker_count": len(prices_payload or {}) if isinstance(prices_payload, dict) else None,
            "coverage_keys": sorted((coverage_payload or {}).keys()) if isinstance(coverage_payload, dict) else None,
        },
    }
    return {"summary": summary, "rows": scored_rows}


def _format_float(value: Any, digits: int = 6) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _escape_md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|")


def render_markdown(result: dict[str, Any], *, dataset: Path, scores: Path, prices: Path, coverage: Path, output_dir: Path) -> str:
    summary = result["summary"]
    top_slice = summary.get("top_slice") or {}
    top_group = top_slice.get("group") or {}
    lines = [
        "# Cycle-17C E3 Flip-Sign Counterfactual",
        "",
        "**Generated by:** `scripts/edge_replay/side_flip_counterfactual.py`",
        "**Status:** Codex replay report; Claude verdict appendix pending.",
        "**Capital posture:** PAPER-ONLY. This report does not authorize deployment.",
        "",
        "## Inputs",
        "",
        f"- Dataset: `{dataset}`",
        f"- Scores: `{scores}`",
        f"- Prices: `{prices}`",
        f"- Coverage: `{coverage}`",
        f"- Output dir: `{output_dir}`",
        "",
        "## Headline",
        "",
        f"- Verdict label: `{summary['verdict_label']}`",
        f"- Admitted rows: {summary['admitted_rows']} / {summary['input_rows']}",
        f"- Actual flip wins: {summary['actual_flip_wins']}",
        f"- Market-implied expected flip wins: {_format_float(summary['market_implied_expected_wins_flip'])}",
        f"- Excess wins vs market flip: {_format_float(summary['excess_wins_vs_market_flip'])}",
        f"- IC §16 slices: {summary['ic16_slice_count']}",
        "",
        "## Clause Results",
        "",
        "| Clause | Pass | Metric |",
        "|--------|------|--------|",
        f"| A — IC §16 baseline | {summary['clause_pass']['A_ic16_baseline']} | {summary['ic16_slice_count']} eligible slices |",
        f"| B — Trivial inversion | {summary['clause_pass']['B_trivial_inversion']} | excess > {TRIVIAL_EXCESS_WIN_THRESHOLD}; actual {_format_float(summary['excess_wins_vs_market_flip'])} |",
        f"| C — Sub-slice consistency | {summary['clause_pass']['C_sub_slice_consistency']} | every axis p >= {CONSISTENCY_MIN_P} or dominance <= {DOMINANT_WIN_SHARE_MAX:.2f} |",
        f"| D — Charter compliance | {summary['clause_pass']['D_charter_compliance']} | script reports no production/frozen-artifact mutation |",
        "",
        "## Top Slice",
        "",
        f"- Slice: `{json.dumps(top_group, sort_keys=True)}`",
        f"- Trades: {top_slice.get('trades', 0)}",
        f"- Wins: {top_slice.get('wins', 0)}",
        f"- EV CI 95% low: {_format_float(top_slice.get('ev_ci_95_lo'))}",
        f"- EV CI 95% high: {_format_float(top_slice.get('ev_ci_95_hi'))}",
        "",
        "## IC §16 Eligible Slices",
        "",
        "| signal_source | market_family | signal_type | news_class | trades | wins | ev_ci_95_lo | ev_ci_95_hi |",
        "|---------------|---------------|-------------|------------|--------|------|-------------|-------------|",
    ]
    for row in summary["ic16_slices"]:
        group = row["group"]
        lines.append(
            "| {signal_source} | {market_family} | {signal_type} | {news_class} | {trades} | {wins} | {lo} | {hi} |".format(
                signal_source=str(group.get("signal_source", "")).replace("|", "\\|"),
                market_family=str(group.get("market_family", "")).replace("|", "\\|"),
                signal_type=str(group.get("signal_type", "")).replace("|", "\\|"),
                news_class=str(group.get("news_class", "")).replace("|", "\\|"),
                trades=row["trades"],
                wins=row["wins"],
                lo=_format_float(row["ev_ci_95_lo"]),
                hi=_format_float(row["ev_ci_95_hi"]),
            )
        )
    if not summary["ic16_slices"]:
        lines.append("| n/a | n/a | n/a | n/a | 0 | 0 | n/a | n/a |")

    lines.extend(["", "## Sub-Slice Consistency", ""])
    for axis, axis_result in summary["consistency"].items():
        lines.extend(
            [
                f"### {axis}",
                "",
                f"- Min pairwise p-value: {_format_float(axis_result['min_pairwise_p_value'])}",
                f"- Dominant bin win share: {_format_float(axis_result['dominant_bin_win_share'])}",
                f"- Passed: {axis_result['passed']}",
                "",
                "| bin | trades | wins | losses | flip_win_rate | win_share |",
                "|-----|--------|------|--------|---------------|-----------|",
            ]
        )
        for item in axis_result["bins"]:
            lines.append(
                f"| {_escape_md_cell(item['bin'])} | {item['trades']} | {item['wins']} | {item['losses']} | "
                f"{_format_float(item['flip_win_rate'])} | {_format_float(item['win_share'])} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Artifacts",
            "",
            "- Structured summary: `logs/edge_replay/cycle17c-e3/side_flip_summary.json`",
            "- Per-row outcome audit: `logs/edge_replay/cycle17c-e3/side_flip_per_row.jsonl`",
            "",
            "## Verdict Appendix Placeholder",
            "",
            "Claude appends the final verdict appendix here per `docs/governance/2026-05-10-cycle-17c-e3-criteria-lock.md` §6.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], *, output_dir: Path, report: Path, dataset: Path, scores: Path, prices: Path, coverage: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "side_flip_summary.json").write_text(
        json.dumps(result["summary"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "side_flip_per_row.jsonl").open("w", encoding="utf-8") as handle:
        for row in result["rows"]:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    report.write_text(
        render_markdown(result, dataset=dataset, scores=scores, prices=prices, coverage=coverage, output_dir=output_dir),
        encoding="utf-8",
    )


def build_cli_summary(result: dict[str, Any], *, summary_path: str, report_path: str) -> dict[str, Any]:
    summary = result["summary"]
    skipped = summary.get("production_proxy_skipped") or {}
    return {
        "verdict_label": summary["verdict_label"],
        "admitted_rows": summary["admitted_rows"],
        "ic16_slice_count": summary["ic16_slice_count"],
        "excess_wins_vs_market_flip": summary["excess_wins_vs_market_flip"],
        "production_proxy_skipped_no_price": skipped.get("skipped_no_price", 0),
        "summary": summary_path,
        "report": report_path,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true", help="emit structured summary JSON on stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        raw_rows = load_jsonl(args.dataset)
        scores_payload = load_json(args.scores)
        prices_payload = load_json(args.prices)
        coverage_payload = load_json(args.coverage)
        result = evaluate_counterfactual(
            raw_rows,
            scores_payload=scores_payload,
            prices_payload=prices_payload,
            coverage_payload=coverage_payload,
        )
        write_outputs(
            result,
            output_dir=args.output_dir,
            report=args.write_report,
            dataset=args.dataset,
            scores=args.scores,
            prices=args.prices,
            coverage=args.coverage,
        )
    except Exception as exc:
        failure = {
            "verdict_label": "revert_replay_failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "side_flip_summary.json").write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(failure, sort_keys=True))
        return 1

    if args.json:
        print(json.dumps(result["summary"], sort_keys=True))
    else:
        print(json.dumps(build_cli_summary(result, summary_path=str(args.output_dir / "side_flip_summary.json"), report_path=str(args.write_report)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
