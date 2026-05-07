#!/usr/bin/env python3
"""Cycle-14 LLM-vs-non-LLM movement split."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.edge_replay.calibration_audit import _is_moved, _direction_summary


def _branch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    moved = [row for row in rows if _is_moved(row)]
    return {
        "n": len(rows),
        "moved": len(moved),
        "movement_rate": len(moved) / len(rows) if rows else 0.0,
        **_direction_summary(rows),
    }


def audit_llm_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dossier_rows = [row for row in rows if row.get("decision_kind") == "dossier_update"]
    llm_rows = [row for row in dossier_rows if bool(row.get("llm_called"))]
    non_llm_rows = [row for row in dossier_rows if not bool(row.get("llm_called"))]
    llm_rate = _branch(llm_rows)["movement_rate"]
    non_rate = _branch(non_llm_rows)["movement_rate"]
    ratio: float | str
    if non_rate == 0 and llm_rate > 0:
        ratio = "inf"
    elif non_rate == 0:
        ratio = 0.0
    else:
        ratio = llm_rate / non_rate

    source_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in dossier_rows:
        source_groups[str(row.get("signal_source") or "unknown")].append(row)
    per_source = []
    for source, group in source_groups.items():
        per_source.append(
            {
                "source": source,
                "n": len(group),
                "llm_called_fraction": sum(1 for row in group if bool(row.get("llm_called"))) / len(group),
            }
        )
    per_source.sort(key=lambda row: row["n"], reverse=True)
    return {
        "branches": {
            "llm_called": _branch(llm_rows),
            "non_llm_called": _branch(non_llm_rows),
        },
        "llm_movement_rate_ratio": ratio,
        "per_source": per_source,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_llm_split(load_jsonl(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "ratio": result["llm_movement_rate_ratio"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
