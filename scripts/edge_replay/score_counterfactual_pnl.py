#!/usr/bin/env python3
"""Score counterfactual P&L slices from an edge-replay dataset."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev
from typing import Any


GROUP_KEYS = ("signal_source", "series_ticker", "signal_type", "news_class")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _infer_side(row: dict[str, Any]) -> str:
    side = str(row.get("side") or "").lower()
    if side in {"yes", "no"}:
        return side
    model_prob = _as_float(row.get("model_prob"))
    market_yes_price = _as_float(row.get("market_yes_price"))
    if model_prob is not None and market_yes_price is not None:
        return "yes" if model_prob >= market_yes_price / 100.0 else "no"
    return "yes"


def _pnl(side: str, yes_price_cents: float, contracts: int, resolved_yes: bool) -> float:
    yes_cost = yes_price_cents / 100.0
    if side == "yes":
        per_contract = (1.0 - yes_cost) if resolved_yes else -yes_cost
    else:
        per_contract = -((1.0 - yes_cost)) if resolved_yes else yes_cost
    return per_contract * contracts


def _readiness_admitted(
    row: dict[str, Any],
    *,
    readiness_confidence: float,
    readiness_yes_threshold: float,
    readiness_no_threshold: float,
) -> bool:
    confidence = _as_float(row.get("confidence"))
    model_prob = _as_float(row.get("model_prob"))
    if confidence is None or model_prob is None or confidence < readiness_confidence:
        return False
    return model_prob >= readiness_yes_threshold or model_prob <= readiness_no_threshold


def score_candidate(
    row: dict[str, Any],
    *,
    min_edge: float = 0.02,
    default_contracts: int = 1,
    readiness_confidence: float = 0.85,
    readiness_yes_threshold: float = 0.60,
    readiness_no_threshold: float = 0.40,
) -> dict[str, Any]:
    edge = _as_float(row.get("edge")) or 0.0
    price = _as_float(row.get("market_yes_price"))
    resolved_yes = _as_bool(row.get("resolved_yes"))
    contracts = int(_as_float(row.get("contracts")) or default_contracts)
    side = _infer_side(row)
    would_trade = price is not None and resolved_yes is not None and abs(edge) >= min_edge
    pnl = _pnl(side, price, contracts, resolved_yes) if would_trade and price is not None and resolved_yes is not None else 0.0
    readiness = _readiness_admitted(
        row,
        readiness_confidence=readiness_confidence,
        readiness_yes_threshold=readiness_yes_threshold,
        readiness_no_threshold=readiness_no_threshold,
    )
    model_prob = _as_float(row.get("model_prob"))
    predicted_yes = model_prob is not None and model_prob >= 0.5
    would_have_won_if_taken = predicted_yes == resolved_yes if readiness and resolved_yes is not None else None
    return {
        **row,
        "side": side,
        "contracts": contracts,
        "edge": edge,
        "would_have_traded": would_trade,
        "would_have_won": (side == "yes") == resolved_yes if would_trade and resolved_yes is not None else None,
        "counterfactual_pnl": pnl,
        "readiness_admitted": readiness,
        "left_on_table": readiness and not would_trade,
        "would_have_won_if_taken": would_have_won_if_taken,
    }


def bootstrap_ci(values: list[float], *, iterations: int = 1000, seed: int = 17) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        draw = [values[rng.randrange(len(values))] for _ in values]
        samples.append(mean(draw))
    samples.sort()
    lo_idx = max(0, int(0.025 * len(samples)) - 1)
    hi_idx = min(len(samples) - 1, int(0.975 * len(samples)))
    return (samples[lo_idx], samples[hi_idx])


def _summarize_group(rows: list[dict[str, Any]], group: dict[str, str]) -> dict[str, Any]:
    traded = [row for row in rows if row["would_have_traded"]]
    pnl_values = [float(row["counterfactual_pnl"]) for row in traded]
    wins = [row for row in traded if row["would_have_won"]]
    left_on_table = [row for row in rows if row.get("left_on_table")]
    missed_wins = [row for row in left_on_table if row.get("would_have_won_if_taken")]
    avg = mean(pnl_values) if pnl_values else 0.0
    std = pstdev(pnl_values) if len(pnl_values) > 1 else 0.0
    ci_lo, ci_hi = bootstrap_ci(pnl_values)
    return {
        "group": group,
        "candidates": len(rows),
        "trades": len(traded),
        "wins": len(wins),
        "win_rate": (len(wins) / len(traded)) if traded else None,
        "pnl": sum(pnl_values),
        "avg_pnl_per_trade": avg,
        "sharpe_like": (avg / std) if std else None,
        "ci95_avg_pnl": [ci_lo, ci_hi],
        "ev_ci_95_lo": ci_lo,
        "ev_ci_95_hi": ci_hi,
        "left_on_table": len(left_on_table),
        "left_on_table_would_have_won": len(missed_wins),
    }


def summarize_scores(scored_rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in scored_rows:
        key = tuple(str(row.get(name) or "unknown") for name in GROUP_KEYS)
        groups[key].append(row)

    summaries = [
        _summarize_group(rows, dict(zip(GROUP_KEYS, key, strict=True)))
        for key, rows in groups.items()
    ]
    summaries.sort(key=lambda row: (row["pnl"], row["trades"]), reverse=True)
    overall = _summarize_group(scored_rows, {"all": "all"})
    positive = [row for row in summaries if row["trades"] > 0 and row["avg_pnl_per_trade"] > 0 and row["ev_ci_95_lo"] > 0]
    return {
        "overall": overall,
        "groups": summaries,
        "positive_ev_slices": positive,
        "no_positive_ev_slice": not positive,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--readiness-confidence", type=float, default=0.85)
    parser.add_argument("--readiness-yes-threshold", type=float, default=0.60)
    parser.add_argument("--readiness-no-threshold", type=float, default=0.40)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scored = [
        score_candidate(
            row,
            min_edge=args.min_edge,
            default_contracts=args.contracts,
            readiness_confidence=args.readiness_confidence,
            readiness_yes_threshold=args.readiness_yes_threshold,
            readiness_no_threshold=args.readiness_no_threshold,
        )
        for row in load_jsonl(args.dataset)
    ]
    summary = summarize_scores(scored)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "rows": scored}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(scored), "positive_ev_slices": len(summary["positive_ev_slices"]), "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
