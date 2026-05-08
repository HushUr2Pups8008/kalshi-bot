#!/usr/bin/env python3
"""Outcome-blind G1 admission-count sweep for Cycle-17C E2."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import PAPER_BLOCK_SAME_SIDE_DUPLICATE, PAPER_MIN_EDGE

DEFAULT_DATASET = Path("logs/edge_replay/cycle16d/replay_dataset.jsonl")
DEFAULT_REPORT = Path("docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md")
DEFAULT_THRESHOLDS = (0.05, 0.04, 0.03, 0.02, 0.01, 0.00)

PAPER_PRICE_FLOOR_CENTS = 2.0
PAPER_PRICE_CEIL_CENTS = 98.0
PAPER_TICKER_COOLDOWN_SECONDS = 14_400.0
PAPER_DUPLICATE_PROB_DELTA = 0.07
PAPER_DUPLICATE_PRICE_DELTA = 5.0
SAME_SIGNAL_PROB_DELTA = 0.02
SAME_SIGNAL_PRICE_DELTA = 2.0
READINESS_YES_THRESHOLD = 0.60
READINESS_NO_THRESHOLD = 0.40

FORBIDDEN_OUTPUT_PATTERN = re.compile(r"win|pnl|profit|resolution|settlement|ev|ic16", re.IGNORECASE)

VARIANT_NAMES = (
    "baseline_abs_edge",
    "readiness_only",
    "paper_price_sanity",
    "readiness_plus_price_sanity",
    "readiness_price_signed_edge",
    "production_proxy",
)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def guard_output_labels(labels: list[str] | tuple[str, ...]) -> None:
    for label in labels:
        if FORBIDDEN_OUTPUT_PATTERN.search(str(label)):
            raise ValueError(f"forbidden outcome-sensitive field in sweep output: {label}")


def _guard_result_shape(value: Any) -> None:
    if isinstance(value, dict):
        guard_output_labels(tuple(str(key) for key in value))
        for item in value.values():
            _guard_result_shape(item)
    elif isinstance(value, list):
        for item in value:
            _guard_result_shape(item)


def _infer_side(row: dict[str, Any]) -> str:
    side = str(row.get("side") or "").lower()
    if side in {"yes", "no"}:
        return side
    model_prob = _as_float(row.get("model_prob"))
    market_yes_price = _as_float(row.get("market_yes_price"))
    if model_prob is not None and market_yes_price is not None:
        return "yes" if model_prob >= market_yes_price / 100.0 else "no"
    return "yes"


def _admission_row(row: dict[str, Any], *, g1_threshold: float) -> dict[str, Any]:
    edge = _as_float(row.get("edge")) or 0.0
    price = _as_float(row.get("market_yes_price"))
    confidence = _as_float(row.get("confidence"))
    model_prob = _as_float(row.get("model_prob"))
    side = _infer_side(row)
    return {
        "ticker": str(row.get("ticker") or ""),
        "decision_ts": str(row.get("decision_ts") or ""),
        "model_prob": model_prob,
        "market_yes_price": price,
        "side": side,
        "candidate": price is not None and abs(edge) >= PAPER_MIN_EDGE,
        "readiness": (
            confidence is not None
            and model_prob is not None
            and confidence >= g1_threshold
            and (model_prob >= READINESS_YES_THRESHOLD or model_prob <= READINESS_NO_THRESHOLD)
        ),
        "paper_price": price is not None and PAPER_PRICE_FLOOR_CENTS <= price <= PAPER_PRICE_CEIL_CENTS,
        "signed_edge": (edge >= PAPER_MIN_EDGE if side == "yes" else edge <= -PAPER_MIN_EDGE),
    }


def _static_count(
    rows: list[dict[str, Any]],
    *,
    require_readiness: bool = False,
    require_price_sanity: bool = False,
    require_signed_edge: bool = False,
) -> tuple[int, Counter[str]]:
    accepted = 0
    skipped: Counter[str] = Counter()
    for row in rows:
        if not row["candidate"]:
            skipped["baseline_not_trade"] += 1
            continue
        if require_readiness and not row["readiness"]:
            skipped["readiness_not_admitted"] += 1
            continue
        if require_price_sanity and not row["paper_price"]:
            skipped["paper_price_sanity"] += 1
            continue
        if require_signed_edge and not row["signed_edge"]:
            skipped["signed_edge_mismatch"] += 1
            continue
        accepted += 1
    return accepted, skipped


def _production_proxy_count(rows: list[dict[str, Any]]) -> tuple[int, Counter[str]]:
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda item: (str(item[1]["decision_ts"]), item[0]))
    accepted = 0
    skipped: Counter[str] = Counter()
    last_trade_ts: dict[str, datetime] = {}
    open_position: dict[str, dict[str, Any]] = {}

    for _, row in indexed:
        ticker = str(row["ticker"])
        if not row["candidate"]:
            skipped["baseline_not_trade"] += 1
            continue
        if not row["readiness"]:
            skipped["readiness_not_admitted"] += 1
            continue
        if not row["paper_price"]:
            skipped["paper_price_sanity"] += 1
            continue
        if not row["signed_edge"]:
            skipped["signed_edge_mismatch"] += 1
            continue

        decision_ts = _parse_ts(row["decision_ts"])
        last_ts = last_trade_ts.get(ticker)
        if decision_ts is not None and last_ts is not None:
            elapsed = (decision_ts - last_ts).total_seconds()
            if elapsed < PAPER_TICKER_COOLDOWN_SECONDS:
                skipped["paper_ticker_cooldown"] += 1
                continue

        position = open_position.get(ticker)
        if position is not None:
            if position["side"] != row["side"]:
                skipped["opposing_position"] += 1
                continue
            prob_delta = abs((position["model_prob"] or 0.0) - (row["model_prob"] or 0.0))
            price_delta = abs((position["market_yes_price"] or 0.0) - (row["market_yes_price"] or 0.0))
            if PAPER_BLOCK_SAME_SIDE_DUPLICATE and prob_delta < PAPER_DUPLICATE_PROB_DELTA and price_delta < PAPER_DUPLICATE_PRICE_DELTA:
                skipped["paper_duplicate_position"] += 1
                continue
            if prob_delta < SAME_SIGNAL_PROB_DELTA and price_delta < SAME_SIGNAL_PRICE_DELTA:
                skipped["same_signal_position"] += 1
                continue

        accepted += 1
        if decision_ts is not None:
            last_trade_ts[ticker] = decision_ts
        open_position[ticker] = {
            "side": row["side"],
            "model_prob": row["model_prob"],
            "market_yes_price": row["market_yes_price"],
        }

    return accepted, skipped


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _summarize_threshold(raw_rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    rows = [_admission_row(row, g1_threshold=threshold) for row in raw_rows]
    baseline_count, baseline_skipped = _static_count(rows)
    readiness_count, readiness_skipped = _static_count(rows, require_readiness=True)
    price_count, price_skipped = _static_count(rows, require_price_sanity=True)
    readiness_price_count, readiness_price_skipped = _static_count(
        rows, require_readiness=True, require_price_sanity=True
    )
    readiness_price_signed_count, readiness_price_signed_skipped = _static_count(
        rows,
        require_readiness=True,
        require_price_sanity=True,
        require_signed_edge=True,
    )
    production_count, production_skipped = _production_proxy_count(rows)
    return {
        "g1_threshold": threshold,
        "baseline_abs_edge": baseline_count,
        "readiness_only": readiness_count,
        "paper_price_sanity": price_count,
        "readiness_plus_price_sanity": readiness_price_count,
        "readiness_price_signed_edge": readiness_price_signed_count,
        "production_proxy": production_count,
        "skipped": {
            "baseline_abs_edge": _sorted_counter(baseline_skipped),
            "readiness_only": _sorted_counter(readiness_skipped),
            "paper_price_sanity": _sorted_counter(price_skipped),
            "readiness_plus_price_sanity": _sorted_counter(readiness_price_skipped),
            "readiness_price_signed_edge": _sorted_counter(readiness_price_signed_skipped),
            "production_proxy": _sorted_counter(production_skipped),
        },
    }


def run_sweep(raw_rows: list[dict[str, Any]], *, thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    guard_output_labels(("rows", "thresholds", "g1_threshold", "skipped") + VARIANT_NAMES)
    result = {
        "rows": len(raw_rows),
        "thresholds": [_summarize_threshold(raw_rows, threshold) for threshold in thresholds],
    }
    _guard_result_shape(result)
    return result


def render_markdown(result: dict[str, Any], *, dataset: Path) -> str:
    production_counts = [row["production_proxy"] for row in result["thresholds"]]
    readiness_counts = [row["readiness_only"] for row in result["thresholds"]]
    all_counts_identical = len(set(production_counts)) == 1 and len(set(readiness_counts)) == 1
    lines = [
        "# Cycle-17C E2 G1 Admission Sweep",
        "",
        "**Type:** Outcome-blind admission-count projection.",
        f"**Dataset:** `{dataset}`",
        f"**Rows:** {result['rows']}",
        "",
        "**Guardrail:** This is not an IC §16 replay. Admission counts are projection-only and cannot justify keep/deploy.",
        "",
        "No wins, P&L, market-implied expected wins, EV, accepted slices, settlement, or resolution fields are computed.",
        "",
        "## Counts",
        "",
        "| G1 threshold | baseline_abs_edge | readiness_only | paper_price_sanity | readiness_plus_price_sanity | readiness_price_signed_edge | production_proxy |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["thresholds"]:
        lines.append(
            "| {g1_threshold:.2f} | {baseline_abs_edge} | {readiness_only} | {paper_price_sanity} | "
            "{readiness_plus_price_sanity} | {readiness_price_signed_edge} | {production_proxy} |".format(**row)
        )
    lines.extend(["", "## Projection Read", ""])
    if all_counts_identical:
        lines.extend(
            [
                "All tested G1 thresholds produce identical `readiness_only` and `production_proxy` counts.",
                "",
                "Lowering G1 does not admit additional candidates on the frozen corpus. Current G1 already projects `production_proxy >= 10`; loosening it is a no-op for admission count. Do not criteria-lock an E2 G1 implementation unless a fresh operator decision overrides this projection.",
            ]
        )
    else:
        lines.append(
            "At least one tested G1 threshold changes admission count. Operator should pick the smallest loosening that crosses the target count, then criteria-lock that exact value before replay."
        )
    lines.extend(["", "## Skip Breakdown", ""])
    for row in result["thresholds"]:
        lines.append(f"### G1 = {row['g1_threshold']:.2f}")
        lines.append("")
        lines.append("| variant | skipped |")
        lines.append("|---|---|")
        for variant in VARIANT_NAMES:
            skipped = row["skipped"][variant]
            text = ", ".join(f"{key}: {value}" for key, value in skipped.items()) if skipped else "none"
            lines.append(f"| `{variant}` | {text} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_thresholds(value: str) -> tuple[float, ...]:
    return tuple(float(part.strip()) for part in value.split(",") if part.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--thresholds", default=",".join(f"{value:.2f}" for value in DEFAULT_THRESHOLDS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rows = load_jsonl(args.dataset)
    result = run_sweep(rows, thresholds=parse_thresholds(args.thresholds))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown(result, dataset=args.dataset), encoding="utf-8")
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
