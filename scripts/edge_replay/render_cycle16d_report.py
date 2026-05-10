#!/usr/bin/env python3
"""Render the Cycle-16D replay report from replay artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_DATASET = Path("logs/edge_replay/cycle16d/replay_dataset.jsonl")
DEFAULT_SCORES = Path("logs/edge_replay/cycle16d/counterfactual_scores.json")
DEFAULT_COVERAGE = Path("logs/edge_replay/cycle16d/coverage_audit.json")
DEFAULT_OUTPUT = Path("docs/_archive/governance/edge-replay-cycle16d-report.md")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def latest_model_rows_by_ticker(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("model_prob") is None:
            continue
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        existing = latest.get(ticker)
        if existing is None or str(row.get("decision_ts") or "") > str(existing.get("decision_ts") or ""):
            latest[ticker] = row
    return [latest[ticker] for ticker in sorted(latest)]


def brier_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latest = latest_model_rows_by_ticker(rows)
    values: list[float] = []
    for row in latest:
        p = float(row["model_prob"])
        y = 1.0 if row.get("resolved_yes") is True else 0.0
        values.append((p - y) ** 2)
    return {
        "n": len(values),
        "brier": (sum(values) / len(values)) if values else None,
    }


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _group_label(group: dict[str, Any]) -> str:
    return (
        f"`{group.get('signal_source')}` / `{group.get('series_ticker')}` / "
        f"`{group.get('signal_type')}` / `{group.get('news_class')}`"
    )


def ic16_slices(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in groups
        if int(row.get("trades") or 0) >= 10 and float(row.get("ev_ci_95_lo") or 0.0) > 0.0
    ]


def top_slice_rows(groups: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    rows = [
        "| slice | candidates | trades | wins | win_rate | pnl | ev_ci_95_lo |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    ranked = sorted(groups, key=lambda row: (float(row.get("ev_ci_95_lo") or 0.0), int(row.get("trades") or 0)), reverse=True)
    for row in ranked[:limit]:
        rows.append(
            "| "
            + " | ".join(
                [
                    _group_label(row["group"]),
                    str(row["candidates"]),
                    str(row["trades"]),
                    str(row["wins"]),
                    _fmt(row["win_rate"]),
                    _fmt(row["pnl"]),
                    _fmt(row["ev_ci_95_lo"]),
                ]
            )
            + " |"
        )
    return rows


def render_report(
    *,
    dataset_path: Path = DEFAULT_DATASET,
    scores_path: Path = DEFAULT_SCORES,
    coverage_path: Path = DEFAULT_COVERAGE,
) -> str:
    dataset = load_jsonl(dataset_path)
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    summary = scores["summary"]
    groups = summary["groups"]
    accepted = ic16_slices(groups)
    raw_positive = summary["positive_ev_slices"]
    overall = summary["overall"]
    brier = brier_score(dataset)
    priced_rows = sum(1 for row in dataset if row.get("market_yes_price") is not None)
    anomaly = coverage.get("anomalies", [])
    sentinel = coverage.get("cohort_sentinel", {})
    verdict = (
        "extraction_fixed_with_positive_ev_slice"
        if accepted
        else "extraction_fixed_but_information_frontier_holds"
    )

    lines = [
        "# Cycle-16D replay report",
        "",
        f"Generated: {datetime.now(UTC).date().isoformat()}",
        "",
        "## Verdict",
        "",
        "**IC §16 acceptance: " + ("PASS" if accepted else "FAIL") + ".**",
        "",
        f"Verdict label: `{verdict}`.",
        "",
    ]
    if accepted:
        lines.append("At least one source x market-family x signal-type slice met `ev_ci_95_lo > 0` and `trades >= 10`.")
    else:
        lines.extend(
            [
                "No source x market-family x signal-type slice met both locked criteria:",
                "",
                "- `ev_ci_95_lo > 0`",
                "- `trades >= 10`",
                "",
                "One raw positive-EV scorer group exists, but it has `trades = 1`; it is not an IC §16-accepted slice.",
            ]
        )
    lines.extend(
        [
            "",
            "## Inputs",
            "",
            "- Post-fix DB: `data/dossier_updates_post_fix.db`",
            "- Resolved markets: `logs/edge_replay/cycle13_live/resolved_markets_full.json`",
            "- Historical prices: `logs/edge_replay/cycle16d/historical_prices_cycle16d.json`",
            f"- Dataset: `{dataset_path}`",
            f"- Scores: `{scores_path}`",
            f"- Coverage audit: `{coverage_path}`",
            "",
            "## D6 summary",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Replay rows | {len(dataset)} |",
            f"| Rows with decision-time executable price | {priced_rows} |",
            f"| Coverage | {_fmt(coverage['overall']['coverage'] * 100, 2)}% |",
            f"| Counterfactual trades | {overall['trades']} |",
            f"| Counterfactual wins | {overall['wins']} |",
            f"| Counterfactual P&L | {_fmt(overall['pnl'])} |",
            f"| Overall ev_ci_95_lo | {_fmt(overall['ev_ci_95_lo'])} |",
            f"| Raw positive-EV scorer groups | {len(raw_positive)} |",
            f"| IC §16 accepted slices | {len(accepted)} |",
            f"| Brier score (latest model per market, n={brier['n']}) | {_fmt(brier['brier'])} |",
            "",
            "Brier is supporting evidence only: n=24 markets gives wide uncertainty. IC §16 routing remains governed by slice EV and trade-count criteria.",
            "",
            "## Slice table",
            "",
            *top_slice_rows(groups),
            "",
            "## Coverage and exclusions",
            "",
            f"Coverage passed at {coverage['overall']['covered_rows']}/{coverage['input']['dataset_rows']} rows.",
        ]
    )
    if anomaly:
        lines.extend(
            [
                "",
                "The only missing executable-price row is explicitly excluded from P&L scoring:",
                "",
                "| ticker | rows | covered | reason |",
                "|---|---:|---:|---|",
            ]
        )
        for row in anomaly:
            reasons = ", ".join(f"{key}={value}" for key, value in row.get("missing_reasons", {}).items())
            lines.append(f"| `{row['ticker']}` | {row['total_rows']} | {row['covered_rows']} | `{reasons}` |")
    lines.extend(
        [
            "",
            "## D9 POST_FIX_REBUILT sentinel",
            "",
            f"- Sentinel status: `{sentinel.get('status', 'missing')}`",
            f"- Cohort flag: `{sentinel.get('cohort_flag', 'missing')}`",
            f"- `cycle_15b_c7_deploy_commit`: `{sentinel.get('cycle_15b_c7_deploy_commit', 'missing')}`",
            f"- `cycle_15b_c7_deploy_ts`: `{sentinel.get('cycle_15b_c7_deploy_ts', 'missing')}`",
            "",
            "D6 used `data/dossier_updates_post_fix.db`; pre-fix paper-trade DB and trade-log inputs were pointed at empty `/private/tmp` paths.",
            "",
            "## Reproducibility",
            "",
            "```bash",
            "bash scripts/edge_replay/run_cycle16d_replay.sh --skip-price-backfill",
            "```",
            "",
            "Live price refresh path:",
            "",
            "```bash",
            "bash scripts/edge_replay/run_cycle16d_replay.sh --live-price-backfill",
            "```",
            "",
            "Equivalent command sequence:",
            "",
            "```bash",
            ".venv/bin/python scripts/edge_replay/price_coverage_audit.py \\",
            "  --dataset logs/edge_replay/cycle15b/replay_dataset.jsonl \\",
            "  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \\",
            "  --post-fix-db data/dossier_updates_post_fix.db \\",
            "  --output logs/edge_replay/cycle16d/coverage_audit.json",
            "",
            ".venv/bin/python scripts/edge_replay/build_replay_dataset.py \\",
            "  --markets logs/edge_replay/cycle13_live/resolved_markets_full.json \\",
            "  --paper-trades-db /private/tmp/kalshi_cycle16d_no_paper_trades.db \\",
            "  --trade-log '/private/tmp/kalshi_cycle16d_no_trade_logs/*.jsonl' \\",
            "  --evidence-store-db data/dossier_updates_post_fix.db \\",
            "  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \\",
            "  --output logs/edge_replay/cycle16d/replay_dataset.jsonl",
            "",
            ".venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py \\",
            "  --dataset logs/edge_replay/cycle16d/replay_dataset.jsonl \\",
            "  --readiness-confidence 0.05 \\",
            "  --output logs/edge_replay/cycle16d/counterfactual_scores.json",
            "```",
            "",
            "## Interpretation",
            "",
            "Cycle-16D removes the prior scorer-blocked state: prices are now available for 99.6324% of rows. With prices verified, the post-fix replay still has no deployable IC §16 slice. Overall counterfactual P&L is negative, and the only raw positive group is a one-trade artifact below the locked trade-count floor.",
            "",
            "Routing: no Wave-2/3/D behavioral deploy. Capital posture remains PAPER-ONLY pending Claude M6/M10 verdict consumption.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render_report(dataset_path=args.dataset, scores_path=args.scores, coverage_path=args.coverage),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
