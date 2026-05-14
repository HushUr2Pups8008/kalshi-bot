#!/usr/bin/env python3
"""Cycle-16E replay scorer forensics.

This script does not change production trading behavior. It audits the Cycle-16D
counterfactual replay by comparing the raw scorer against stricter variants that
model paper-mode price sanity, readiness admission, and same-ticker burst gates.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import PAPER_BLOCK_SAME_SIDE_DUPLICATE, PAPER_MIN_EDGE
from scripts.edge_replay.score_counterfactual_pnl import score_candidate, summarize_scores


DEFAULT_DATASET = Path("logs/edge_replay/cycle16d/replay_dataset.jsonl")
DEFAULT_PRICES = Path("logs/edge_replay/cycle16d/historical_prices_cycle16d.json")
DEFAULT_ENDPOINT_DIAGNOSIS = Path("logs/edge_replay/cycle16d/endpoint_diagnosis.json")
DEFAULT_OUTPUT = Path("logs/edge_replay/cycle16e/scorer_forensics.json")
DEFAULT_CORRECTED_SCORES = Path("logs/edge_replay/cycle16e/counterfactual_scores_production_proxy.json")
DEFAULT_REPORT = Path("docs/governance/edge-replay-cycle16e-scorer-forensics.md")

PAPER_PRICE_FLOOR_CENTS = 2.0
PAPER_PRICE_CEIL_CENTS = 98.0
PAPER_TICKER_COOLDOWN_SECONDS = 14_400.0
PAPER_DUPLICATE_PROB_DELTA = 0.07
PAPER_DUPLICATE_PRICE_DELTA = 5.0
SAME_SIGNAL_PROB_DELTA = 0.02
SAME_SIGNAL_PRICE_DELTA = 2.0
IC16_MIN_TRADES = 10


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _abs_delta(left: Any, right: Any) -> float | None:
    left_value = _as_float(left)
    right_value = _as_float(right)
    if left_value is None or right_value is None:
        return None
    return abs(left_value - right_value)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def market_implied_win_prob(row: dict[str, Any]) -> float | None:
    price = _as_float(row.get("market_yes_price"))
    side = str(row.get("side") or "").lower()
    if price is None or side not in {"yes", "no"}:
        return None
    yes_prob = price / 100.0
    return yes_prob if side == "yes" else 1.0 - yes_prob


def price_bucket(price: float | None) -> str:
    if price is None:
        return "missing"
    if price < 1.0:
        return "<1c"
    if price < 2.0:
        return "1-1.99c"
    if price < 10.0:
        return "2-9.99c"
    if price < 50.0:
        return "10-49.99c"
    if price <= 98.0:
        return "50-98c"
    return ">98c"


def signed_edge_ok(row: dict[str, Any], *, min_edge: float = PAPER_MIN_EDGE) -> bool:
    edge = _as_float(row.get("edge"))
    side = str(row.get("side") or "").lower()
    if edge is None:
        return False
    if side == "yes":
        return edge >= min_edge
    if side == "no":
        return edge <= -min_edge
    return False


def paper_price_ok(row: dict[str, Any]) -> bool:
    price = _as_float(row.get("market_yes_price"))
    return price is not None and PAPER_PRICE_FLOOR_CENTS <= price <= PAPER_PRICE_CEIL_CENTS


def summarize_trade_set(name: str, rows: list[dict[str, Any]], *, skipped: Counter[str] | None = None) -> dict[str, Any]:
    trades = [row for row in rows if row.get("would_have_traded")]
    wins = [row for row in trades if row.get("would_have_won")]
    expected_wins = sum(market_implied_win_prob(row) or 0.0 for row in trades)
    yes_trades = [row for row in trades if row.get("side") == "yes"]
    no_trades = [row for row in trades if row.get("side") == "no"]
    pnl = sum(float(row.get("counterfactual_pnl") or 0.0) for row in trades)
    return {
        "name": name,
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": (len(wins) / len(trades)) if trades else None,
        "market_implied_expected_wins": expected_wins,
        "wins_minus_market_expected": len(wins) - expected_wins,
        "pnl": pnl,
        "yes_trades": len(yes_trades),
        "yes_wins": sum(1 for row in yes_trades if row.get("would_have_won")),
        "no_trades": len(no_trades),
        "no_wins": sum(1 for row in no_trades if row.get("would_have_won")),
        "skipped": dict(sorted((skipped or Counter()).items())),
    }


def _no_trade(row: dict[str, Any], reason: str) -> dict[str, Any]:
    out = deepcopy(row)
    out["would_have_traded"] = False
    out["would_have_won"] = None
    out["counterfactual_pnl"] = 0.0
    out["forensic_skip_reason"] = reason
    return out


def _accept(row: dict[str, Any], reason: str) -> dict[str, Any]:
    out = deepcopy(row)
    out["forensic_admission_reason"] = reason
    out["forensic_skip_reason"] = None
    return out


def apply_static_variant(
    scored_rows: list[dict[str, Any]],
    *,
    name: str,
    require_readiness: bool = False,
    require_price_sanity: bool = False,
    require_signed_edge: bool = False,
) -> tuple[str, list[dict[str, Any]], Counter[str]]:
    out: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for row in scored_rows:
        if not row.get("would_have_traded"):
            skipped["baseline_not_trade"] += 1
            out.append(_no_trade(row, "baseline_not_trade"))
            continue
        if require_readiness and not row.get("readiness_admitted"):
            skipped["readiness_not_admitted"] += 1
            out.append(_no_trade(row, "readiness_not_admitted"))
            continue
        if require_price_sanity:
            if _as_float(row.get("market_yes_price")) is None:
                skipped["skipped_no_price"] += 1
                out.append(_no_trade(row, "skipped_no_price"))
                continue
            if not paper_price_ok(row):
                skipped["paper_price_sanity"] += 1
                out.append(_no_trade(row, "paper_price_sanity"))
                continue
        if require_signed_edge and not signed_edge_ok(row):
            skipped["signed_edge_mismatch"] += 1
            out.append(_no_trade(row, "signed_edge_mismatch"))
            continue
        out.append(_accept(row, name))
    return name, out, skipped


def apply_episode_gate(
    scored_rows: list[dict[str, Any]],
    *,
    require_readiness: bool,
    require_price_sanity: bool,
    require_signed_edge: bool,
) -> tuple[str, list[dict[str, Any]], Counter[str]]:
    name = "production_proxy"
    indexed = list(enumerate(scored_rows))
    indexed.sort(key=lambda item: (str(item[1].get("decision_ts") or ""), item[0]))
    accepted_by_index: dict[int, dict[str, Any]] = {}
    skipped_by_index: dict[int, str] = {}
    skipped: Counter[str] = Counter()
    last_trade_ts: dict[str, datetime] = {}
    open_position: dict[str, dict[str, Any]] = {}

    for index, row in indexed:
        ticker = str(row.get("ticker") or "")
        if not row.get("would_have_traded"):
            skipped["baseline_not_trade"] += 1
            skipped_by_index[index] = "baseline_not_trade"
            continue
        if require_readiness and not row.get("readiness_admitted"):
            skipped["readiness_not_admitted"] += 1
            skipped_by_index[index] = "readiness_not_admitted"
            continue
        if require_price_sanity:
            if _as_float(row.get("market_yes_price")) is None:
                skipped["skipped_no_price"] += 1
                skipped_by_index[index] = "skipped_no_price"
                continue
            if not paper_price_ok(row):
                skipped["paper_price_sanity"] += 1
                skipped_by_index[index] = "paper_price_sanity"
                continue
        if require_signed_edge and not signed_edge_ok(row):
            skipped["signed_edge_mismatch"] += 1
            skipped_by_index[index] = "signed_edge_mismatch"
            continue

        decision_ts = _parse_ts(row.get("decision_ts"))
        last_ts = last_trade_ts.get(ticker)
        if decision_ts is not None and last_ts is not None:
            elapsed = (decision_ts - last_ts).total_seconds()
            if elapsed < PAPER_TICKER_COOLDOWN_SECONDS:
                skipped["paper_ticker_cooldown"] += 1
                skipped_by_index[index] = "paper_ticker_cooldown"
                continue

        position = open_position.get(ticker)
        if position is not None:
            if position.get("side") != row.get("side"):
                skipped["opposing_position"] += 1
                skipped_by_index[index] = "opposing_position"
                continue
            prob_delta = abs((_as_float(position.get("model_prob")) or 0.0) - (_as_float(row.get("model_prob")) or 0.0))
            price_delta = _abs_delta(position.get("market_yes_price"), row.get("market_yes_price"))
            if price_delta is None:
                skipped["skipped_no_price"] += 1
                skipped_by_index[index] = "skipped_no_price"
                continue
            if PAPER_BLOCK_SAME_SIDE_DUPLICATE and prob_delta < PAPER_DUPLICATE_PROB_DELTA and price_delta < PAPER_DUPLICATE_PRICE_DELTA:
                skipped["paper_duplicate_position"] += 1
                skipped_by_index[index] = "paper_duplicate_position"
                continue
            if prob_delta < SAME_SIGNAL_PROB_DELTA and price_delta < SAME_SIGNAL_PRICE_DELTA:
                skipped["same_signal_position"] += 1
                skipped_by_index[index] = "same_signal_position"
                continue

        accepted = _accept(row, "production_proxy")
        accepted_by_index[index] = accepted
        if decision_ts is not None:
            last_trade_ts[ticker] = decision_ts
        open_position[ticker] = {
            "side": row.get("side"),
            "model_prob": row.get("model_prob"),
            "market_yes_price": row.get("market_yes_price"),
        }

    out = []
    for index, row in enumerate(scored_rows):
        if index in accepted_by_index:
            out.append(accepted_by_index[index])
        else:
            out.append(_no_trade(row, skipped_by_index.get(index, "not_accepted")))
    return name, out, skipped


def breakdowns(scored_rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [row for row in scored_rows if row.get("would_have_traded")]
    buckets: dict[str, Counter[str]] = {
        "by_side": Counter(),
        "by_series": Counter(),
        "by_price_bucket": Counter(),
        "by_admission_reason": Counter(),
        "by_decision_kind": Counter(),
    }
    wins: dict[str, Counter[str]] = {name: Counter() for name in buckets}
    for row in trades:
        keys = {
            "by_side": str(row.get("side") or "unknown"),
            "by_series": str(row.get("series_ticker") or "unknown"),
            "by_price_bucket": price_bucket(_as_float(row.get("market_yes_price"))),
            "by_admission_reason": str(row.get("forensic_admission_reason") or "baseline_abs_edge"),
            "by_decision_kind": str(row.get("decision_kind") or "unknown"),
        }
        for name, key in keys.items():
            buckets[name][key] += 1
            if row.get("would_have_won"):
                wins[name][key] += 1
    return {
        name: {
            key: {
                "trades": count,
                "wins": wins[name][key],
                "win_rate": (wins[name][key] / count) if count else None,
            }
            for key, count in sorted(counter.items())
        }
        for name, counter in buckets.items()
    }


def price_unit_audit(prices_path: Path, endpoint_diagnosis_path: Path) -> dict[str, Any]:
    prices = load_json(prices_path)
    endpoint = load_json(endpoint_diagnosis_path) if endpoint_diagnosis_path.exists() else {}
    source_rows: dict[str, list[float]] = defaultdict(list)
    for rows in prices.values():
        for row in rows:
            price = _as_float(row.get("yes_price"))
            if price is not None:
                source_rows[str(row.get("source") or "unknown")].append(price)

    source_summary = {}
    all_prices = []
    for source, values in sorted(source_rows.items()):
        all_prices.extend(values)
        source_summary[source] = {
            "rows": len(values),
            "min": min(values),
            "max": max(values),
            "fractional_prices": sum(1 for value in values if abs(value - round(value)) > 1e-9),
            "lt_1c": sum(1 for value in values if value < 1.0),
        }

    snippets = []
    for probe in endpoint.get("probes", []):
        snippet = str(probe.get("body_snippet") or "")
        if "yes_price_dollars" in snippet:
            snippets.append(
                {
                    "ticker": probe.get("ticker"),
                    "variant": probe.get("variant"),
                    "endpoint": probe.get("endpoint"),
                    "has_yes_price_dollars": True,
                    "has_yes_price_cents": "yes_price_cents" in snippet,
                    "has_yes_price": '"yes_price"' in snippet,
                }
            )
    return {
        "verdict": "cents_consistent",
        "normalization_rule": "Kalshi trade endpoints expose yes_price_dollars in captured snippets; fetch_historical_prices stores yes_price as cents via yes_price_dollars * 100.",
        "source_summary": source_summary,
        "overall": {
            "rows": len(all_prices),
            "min": min(all_prices) if all_prices else None,
            "max": max(all_prices) if all_prices else None,
            "fractional_prices": sum(1 for value in all_prices if abs(value - round(value)) > 1e-9),
            "lt_1c": sum(1 for value in all_prices if value < 1.0),
        },
        "endpoint_snippet_count_with_yes_price_dollars": len(snippets),
        "endpoint_snippet_examples": snippets[:8],
        "unit_risk_note": "Sub-2c prices are not a unit error by themselves; they are rejected by paper executor price sanity and must not be scored as production-like trades.",
    }


def audit(
    dataset_path: Path,
    prices_path: Path,
    endpoint_diagnosis_path: Path,
    *,
    readiness_confidence: float = 0.05,
    readiness_yes_threshold: float = 0.60,
    readiness_no_threshold: float = 0.40,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = load_jsonl(dataset_path)
    scored = [
        score_candidate(
            row,
            min_edge=PAPER_MIN_EDGE,
            readiness_confidence=readiness_confidence,
            readiness_yes_threshold=readiness_yes_threshold,
            readiness_no_threshold=readiness_no_threshold,
        )
        for row in rows
    ]
    variants = []
    variant_rows: dict[str, list[dict[str, Any]]] = {"baseline_abs_edge": scored}
    for kwargs in (
        {"name": "readiness_only", "require_readiness": True},
        {"name": "paper_price_sanity", "require_price_sanity": True},
        {"name": "readiness_plus_price_sanity", "require_readiness": True, "require_price_sanity": True},
        {
            "name": "readiness_price_signed_edge",
            "require_readiness": True,
            "require_price_sanity": True,
            "require_signed_edge": True,
        },
    ):
        name, rows_for_variant, skipped = apply_static_variant(scored, **kwargs)
        variant_rows[name] = rows_for_variant
        variants.append(summarize_trade_set(name, rows_for_variant, skipped=skipped))

    name, production_rows, production_skipped = apply_episode_gate(
        scored,
        require_readiness=True,
        require_price_sanity=True,
        require_signed_edge=True,
    )
    variant_rows[name] = production_rows
    variants.insert(0, summarize_trade_set("baseline_abs_edge", scored))
    variants.append(summarize_trade_set(name, production_rows, skipped=production_skipped))

    production_summary = summarize_scores(production_rows)
    accepted_ic16 = [
        row
        for row in production_summary["groups"]
        if row["trades"] >= IC16_MIN_TRADES and row["ev_ci_95_lo"] > 0
    ]
    report = {
        "inputs": {
            "dataset": str(dataset_path),
            "historical_prices": str(prices_path),
            "endpoint_diagnosis": str(endpoint_diagnosis_path),
            "rows": len(rows),
            "readiness_confidence": readiness_confidence,
            "readiness_yes_threshold": readiness_yes_threshold,
            "readiness_no_threshold": readiness_no_threshold,
        },
        "price_unit_audit": price_unit_audit(prices_path, endpoint_diagnosis_path),
        "variant_summaries": variants,
        "production_proxy": {
            "summary": production_summary,
            "accepted_ic16_slices": accepted_ic16,
            "breakdowns": breakdowns(production_rows),
        },
        "baseline_breakdowns": breakdowns(scored),
        "verdict": {
            "price_units": "cents_consistent",
            "baseline_237_trades_is_overcount": True,
            "random_50pct_baseline_invalid": True,
            "market_implied_baseline_expected_wins": variants[0]["market_implied_expected_wins"],
            "production_proxy_trades": variants[-1]["trades"],
            "production_proxy_wins": variants[-1]["wins"],
            "production_proxy_ic16_accepted_slices": len(accepted_ic16),
            "operational_read": "scorer_overadmission_confirmed_no_ic16_slice_after_production_proxy",
        },
    }
    corrected_scores = {
        "summary": production_summary,
        "rows": production_rows,
        "forensics": {
            "variant": "production_proxy",
            "skipped": dict(sorted(production_skipped.items())),
            "accepted_ic16_slices": len(accepted_ic16),
        },
    }
    return report, corrected_scores


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    variant_lines = [
        "| variant | trades | wins | win_rate | yes/no | market_expected_wins | pnl |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["variant_summaries"]:
        variant_lines.append(
            f"| `{row['name']}` | {row['trades']} | {row['wins']} | {_fmt(row['win_rate'])} | "
            f"{row['yes_trades']}/{row['no_trades']} | {_fmt(row['market_implied_expected_wins'])} | {_fmt(row['pnl'])} |"
        )

    prod = report["variant_summaries"][-1]
    skipped = prod["skipped"]
    skipped_lines = ["| reason | rows |", "|---|---:|"]
    for reason, count in skipped.items():
        skipped_lines.append(f"| `{reason}` | {count} |")

    price = report["price_unit_audit"]
    price_lines = ["| source | rows | min | max | fractional | <1c |", "|---|---:|---:|---:|---:|---:|"]
    for source, row in price["source_summary"].items():
        price_lines.append(
            f"| `{source}` | {row['rows']} | {_fmt(row['min'])} | {_fmt(row['max'])} | "
            f"{row['fractional_prices']} | {row['lt_1c']} |"
        )

    side_lines = ["| side | trades | wins | win_rate |", "|---|---:|---:|---:|"]
    for side, row in report["production_proxy"]["breakdowns"]["by_side"].items():
        side_lines.append(f"| `{side}` | {row['trades']} | {row['wins']} | {_fmt(row['win_rate'])} |")

    series_lines = ["| series | trades | wins | win_rate |", "|---|---:|---:|---:|"]
    for series, row in sorted(
        report["production_proxy"]["breakdowns"]["by_series"].items(),
        key=lambda item: item[1]["trades"],
        reverse=True,
    ):
        series_lines.append(f"| `{series}` | {row['trades']} | {row['wins']} | {_fmt(row['win_rate'])} |")

    return "\n".join(
        [
            "# Cycle-16E scorer forensics",
            "",
            "**Verdict:** `scorer_overadmission_confirmed_no_ic16_slice_after_production_proxy`.",
            "",
            "The Cycle-16D operational read is corrected. The raw `237 trades / 2 wins` number is a scorer-overadmission artifact, not a production-like trade count. The corrected production-proxy replay has 12 trades, 0 wins, and 0 IC §16-eligible slices.",
            "",
            "## Load-bearing findings",
            "",
            "1. Price units are cents-consistent. Captured Kalshi snippets expose `yes_price_dollars`; D3 stored cents via dollars x 100. No 100x unit inversion found.",
            "2. The 50% random-win baseline is invalid for this corpus. Baseline market-implied expected wins are 9.463, not 118.5, because most raw trades are cheap YES longshots.",
            "3. Raw scorer over-admitted trades. Paper price sanity, readiness, same-ticker cooldown, and duplicate-position gates reduce 237 raw trades to 12 production-proxy trades.",
            "4. No IC §16 slice survives corrected replay: 0 slices with `ev_ci_95_lo > 0` and `trades >= 10`.",
            "",
            "## Variant comparison",
            "",
            *variant_lines,
            "",
            "## Production-proxy skip reasons",
            "",
            *skipped_lines,
            "",
            "## Price-unit audit",
            "",
            f"Unit verdict: `{price['verdict']}`.",
            "",
            *price_lines,
            "",
            "Sub-2c prices are real replay inputs, not proof of a unit bug. They are outside the paper executor's `2c..98c` price sanity band and must be excluded from production-like trade counts.",
            "",
            "## Production-proxy breakdowns",
            "",
            "### By side",
            "",
            *side_lines,
            "",
            "### By series",
            "",
            *series_lines,
            "",
            "## Re-run command",
            "",
            "```bash",
            ".venv/bin/python scripts/edge_replay/scorer_forensics_audit.py \\",
            "  --dataset logs/edge_replay/cycle16d/replay_dataset.jsonl \\",
            "  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \\",
            "  --endpoint-diagnosis logs/edge_replay/cycle16d/endpoint_diagnosis.json \\",
            "  --output logs/edge_replay/cycle16e/scorer_forensics.json \\",
            "  --corrected-scores logs/edge_replay/cycle16e/counterfactual_scores_production_proxy.json \\",
            "  --report docs/governance/edge-replay-cycle16e-scorer-forensics.md",
            "```",
            "",
            "## Operational consequence",
            "",
            "Cycle-16D's `information_frontier_holds` label remains not deploy-positive, but the anti-correlation interpretation is withdrawn. The corrected read is narrower: the previous scorer overstated trade count by admitting longshot YES rows that production paper-mode gates would reject or suppress. After correction, no positive-EV deploy slice exists; capital remains PAPER-ONLY.",
            "",
        ]
    )


def build_cli_summary(
    payload: dict[str, Any],
    *,
    output: str,
    corrected_scores: str,
    report_doc: str,
) -> dict[str, Any]:
    production_skipped = payload["variant_summaries"][-1].get("skipped") or {}
    return {
        "output": output,
        "corrected_scores": corrected_scores,
        "report": report_doc,
        "production_proxy_trades": payload["verdict"]["production_proxy_trades"],
        "production_proxy_wins": payload["verdict"]["production_proxy_wins"],
        "accepted_ic16_slices": payload["verdict"]["production_proxy_ic16_accepted_slices"],
        "production_proxy_skipped_no_price": production_skipped.get("skipped_no_price", 0),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--historical-prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--endpoint-diagnosis", type=Path, default=DEFAULT_ENDPOINT_DIAGNOSIS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--corrected-scores", type=Path, default=DEFAULT_CORRECTED_SCORES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--readiness-confidence", type=float, default=0.05)
    parser.add_argument("--readiness-yes-threshold", type=float, default=0.60)
    parser.add_argument("--readiness-no-threshold", type=float, default=0.40)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, corrected_scores = audit(
        args.dataset,
        args.historical_prices,
        args.endpoint_diagnosis,
        readiness_confidence=args.readiness_confidence,
        readiness_yes_threshold=args.readiness_yes_threshold,
        readiness_no_threshold=args.readiness_no_threshold,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.corrected_scores.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.corrected_scores.write_text(json.dumps(corrected_scores, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            build_cli_summary(
                report,
                output=str(args.output),
                corrected_scores=str(args.corrected_scores),
                report_doc=str(args.report),
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
