#!/usr/bin/env python3
"""Cycle-14 per-source belief movement audit."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.edge_replay.calibration_audit import MOVEMENT_FLOOR, direction_correct


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _abs_move(row: dict[str, Any]) -> float:
    edge = _as_float(row.get("edge"))
    if edge is not None:
        return abs(edge)
    prob = _as_float(row.get("model_prob"))
    return abs(prob - 0.5) if prob is not None else 0.0


def _summarize(rows: list[dict[str, Any]], key: tuple[str, str, str, str]) -> dict[str, Any]:
    moves = [_abs_move(row) for row in rows]
    direction_results = [
        direction_correct(_as_float(row.get("model_prob")), row.get("resolved_yes"))
        for row in rows
    ]
    correct = sum(1 for item in direction_results if item is True)
    incorrect = sum(1 for item in direction_results if item is False)
    denom = correct + incorrect
    llm_called = sum(1 for row in rows if bool(row.get("llm_called")))
    mean_abs_move = mean(moves) if moves else 0.0
    direction_rate = (correct / denom) if denom else None
    return {
        "signal_source": key[0],
        "news_class": key[1],
        "series_ticker": key[2],
        "signal_type": key[3],
        "count": len(rows),
        "mean_abs_move": mean_abs_move,
        "max_abs_move": max(moves) if moves else 0.0,
        "movement_rate": sum(1 for move in moves if move > MOVEMENT_FLOOR) / len(rows) if rows else 0.0,
        "direction_correctness": direction_rate,
        "direction_correct": correct,
        "direction_incorrect": incorrect,
        "direction_denominator": denom,
        "llm_called_fraction": llm_called / len(rows) if rows else 0.0,
        "inert_source": mean_abs_move < 0.005,
        "wrong_direction_cluster": denom > 0 and direction_rate is not None and direction_rate < 0.5,
    }


def audit_per_source(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("decision_kind") != "dossier_update":
            continue
        key = (
            str(row.get("signal_source") or "unknown"),
            str(row.get("news_class") or "unknown"),
            str(row.get("series_ticker") or "unknown"),
            str(row.get("signal_type") or "unknown"),
        )
        groups[key].append(row)
    summaries = [_summarize(group_rows, key) for key, group_rows in groups.items()]
    summaries.sort(key=lambda row: (row["wrong_direction_cluster"], row["mean_abs_move"], row["count"]), reverse=True)
    return {"groups": summaries}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_per_source(load_jsonl(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "groups": len(result["groups"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
