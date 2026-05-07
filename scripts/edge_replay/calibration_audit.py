#!/usr/bin/env python3
"""Cycle-14 fastest-first calibration audit."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.edge_replay.score_counterfactual_pnl import score_candidate


NO_DIRECTION_LOW = 0.49
NO_DIRECTION_HIGH = 0.51
MOVEMENT_FLOOR = 0.01


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return None
    text = str(value).lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def direction_correct(model_prob: float | None, resolved_yes: bool | None) -> bool | None:
    if model_prob is None or resolved_yes is None:
        return None
    if NO_DIRECTION_LOW <= model_prob <= NO_DIRECTION_HIGH:
        return None
    return (model_prob > 0.5) == resolved_yes


def _is_moved(row: dict[str, Any]) -> bool:
    edge = _as_float(row.get("edge"))
    if edge is not None:
        return abs(edge) > MOVEMENT_FLOOR
    model_prob = _as_float(row.get("model_prob"))
    return model_prob is not None and abs(model_prob - 0.5) > MOVEMENT_FLOOR


def brier_score(rows: list[dict[str, Any]]) -> float | None:
    values = []
    for row in rows:
        prob = _as_float(row.get("model_prob"))
        resolved = _as_bool(row.get("resolved_yes"))
        if prob is not None and resolved is not None:
            values.append((prob - (1.0 if resolved else 0.0)) ** 2)
    return sum(values) / len(values) if values else None


def log_loss(rows: list[dict[str, Any]]) -> float | None:
    values = []
    for row in rows:
        prob = _as_float(row.get("model_prob"))
        resolved = _as_bool(row.get("resolved_yes"))
        if prob is None or resolved is None:
            continue
        prob = min(0.999999, max(0.000001, prob))
        values.append(-(math.log(prob) if resolved else math.log(1.0 - prob)))
    return sum(values) / len(values) if values else None


def _direction_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    correct = 0
    incorrect = 0
    excluded = 0
    for row in rows:
        result = direction_correct(_as_float(row.get("model_prob")), _as_bool(row.get("resolved_yes")))
        if result is True:
            correct += 1
        elif result is False:
            incorrect += 1
        else:
            excluded += 1
    denom = correct + incorrect
    return {
        "correct": correct,
        "incorrect": incorrect,
        "excluded_no_direction": excluded,
        "denominator": denom,
        "direction_correctness": (correct / denom) if denom else None,
    }


def _pnl_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [score_candidate(row) for row in rows]
    traded = [row for row in scored if row["would_have_traded"]]
    return {
        "candidates": len(rows),
        "trades": len(traded),
        "pnl": sum(float(row["counterfactual_pnl"]) for row in traded),
        "wins": sum(1 for row in traded if row["would_have_won"]),
    }


def _latest_by_ticker(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        if ticker not in latest or str(row.get("decision_ts") or "") >= str(latest[ticker].get("decision_ts") or ""):
            latest[ticker] = row
    return list(latest.values())


def audit_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dossier_rows = [row for row in rows if row.get("decision_kind") == "dossier_update"]
    moved_rows = [row for row in rows if _is_moved(row)]
    unmoved_rows = [row for row in rows if not _is_moved(row)]
    sized_rows = [row for row in rows if row.get("decision_kind") == "paper_trade"]
    latest_rows = _latest_by_ticker(rows)
    return {
        "movement": {
            "moved": len(moved_rows),
            "total": len(rows),
            "dossier_updates": len(dossier_rows),
            "movement_rate": (len(moved_rows) / len(rows)) if rows else 0.0,
            "movement_floor": MOVEMENT_FLOOR,
        },
        "direction_correctness": _direction_summary(rows),
        "moved": _pnl_summary(moved_rows),
        "unmoved": _pnl_summary(unmoved_rows),
        "sized_bet_subset": {
            "n": len(sized_rows),
            **_direction_summary(sized_rows),
            **_pnl_summary(sized_rows),
        },
        "brier": {
            "score": brier_score(latest_rows),
            "n": len(latest_rows),
            "caveat": "n is small; use Brier/log-loss as supporting evidence only.",
        },
        "log_loss": {
            "score": log_loss(latest_rows),
            "n": len(latest_rows),
            "caveat": "n is small; use Brier/log-loss as supporting evidence only.",
        },
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_rows(load_jsonl(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "movement_rate": result["movement"]["movement_rate"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
