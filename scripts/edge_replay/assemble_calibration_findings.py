#!/usr/bin/env python3
"""Assemble Cycle-14 diagnostic outputs into one structured findings file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def classify_verdict(
    calibration: dict[str, Any],
    per_source: dict[str, Any],
    llm_split: dict[str, Any],
    synthetic: dict[str, Any],
) -> dict[str, str]:
    movement_rate = float(calibration["movement"]["movement_rate"])
    direction_rate = calibration["direction_correctness"]["direction_correctness"]
    sized_rate = calibration["sized_bet_subset"]["direction_correctness"]
    lane_a_pass = bool(synthetic["lane_a"]["pass"])
    lane_b_pass = bool(synthetic["lane_b"]["pass"])

    if not lane_a_pass:
        return {"verdict": "calibration_inert", "recommended_cycle15_scope": "fix calibration/update model"}
    if lane_a_pass and not lane_b_pass:
        return {"verdict": "extraction_broken", "recommended_cycle15_scope": "rebuild extraction"}
    if movement_rate < 0.10:
        return {"verdict": "calibration_inert", "recommended_cycle15_scope": "fix extraction or update sensitivity"}
    if direction_rate is not None and direction_rate < 0.50:
        return {"verdict": "sign_error", "recommended_cycle15_scope": "fix calibration math with replay-gated validation"}
    if movement_rate >= 0.10 and direction_rate is not None and direction_rate >= 0.60 and sized_rate is not None and sized_rate < 0.50:
        return {"verdict": "model_fine", "recommended_cycle15_scope": "fix sizing/admission subset selection"}
    wrong_clusters = [row for row in per_source.get("groups", []) if row.get("wrong_direction_cluster")]
    if lane_a_pass and lane_b_pass and wrong_clusters:
        return {"verdict": "information_frontier", "recommended_cycle15_scope": "source-onboarding with replay evidence"}
    if lane_a_pass and lane_b_pass:
        return {"verdict": "sample_noise", "recommended_cycle15_scope": "extend evidence_store window 30+ days and rerun"}
    return {"verdict": "redesign", "recommended_cycle15_scope": "strategic pivot"}


def assemble(out_dir: Path) -> dict[str, Any]:
    calibration = _load(out_dir / "calibration_audit.json")
    per_source = _load(out_dir / "per_source_movement.json")
    llm_split = _load(out_dir / "llm_split.json")
    synthetic = _load(out_dir / "synthetic_lanes.json")
    verdict = classify_verdict(calibration, per_source, llm_split, synthetic)
    return {
        "movement_rate": calibration["movement"]["movement_rate"],
        "direction_correctness": calibration["direction_correctness"],
        "sized_bet_subset": calibration["sized_bet_subset"],
        "moved": calibration["moved"],
        "unmoved": calibration["unmoved"],
        "brier": calibration["brier"],
        "log_loss": calibration["log_loss"],
        "llm_split": llm_split["branches"],
        "llm_movement_rate_ratio": llm_split["llm_movement_rate_ratio"],
        "top_per_source_groups": per_source.get("groups", [])[:10],
        "synthetic": synthetic,
        **verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = assemble(args.out_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "verdict": result["verdict"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
