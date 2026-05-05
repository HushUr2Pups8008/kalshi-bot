"""Retroactive parse-error root-cause audit for governance soak logs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_INPUT = _REPO_ROOT / "logs/governance/decisions.jsonl"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "GOVERNANCE_DECISION_PARSE_ERROR":
            rows.append(row)
    return rows


def _root_cause(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key, ""))
        for key in ("error", "message", "parse_error", "reason")
    ).lower()
    if "json" in text and ("decode" in text or "parse" in text or "expecting" in text):
        return "json_decode"
    if "missing" in text and ("required" in text or "field" in text):
        return "missing_required_fields"
    if "schema" in text or "validation" in text:
        return "schema_validation"
    if "empty" in text or "no content" in text:
        return "empty_response"
    if "timeout" in text:
        return "timeout"
    return "other"


def analyze(path: Path = _DEFAULT_INPUT) -> dict[str, Any]:
    rows = _load_rows(path)
    root_causes = Counter(_root_cause(row) for row in rows)
    by_cycle = Counter(str(row.get("cycle_id") or row.get("batch_id") or "unknown") for row in rows)
    candidate_actions = Counter(str(row.get("candidate_action") or "<missing>") for row in rows)
    candidate_targets = Counter(str(row.get("candidate_target") or "<missing>") for row in rows)
    return {
        "input_path": str(path),
        "total_parse_errors": len(rows),
        "root_causes": dict(root_causes),
        "cycles": dict(by_cycle),
        "candidate_actions": dict(candidate_actions),
        "top_candidate_targets": [
            {"target": target, "count": count}
            for target, count in candidate_targets.most_common(20)
        ],
    }


def _table(rows: list[tuple[str, Any]]) -> str:
    out = ["| item | value |", "|---|---:|"]
    out.extend(f"| {key} | {value} |" for key, value in rows)
    return "\n".join(out)


def render_markdown(report: dict[str, Any]) -> str:
    targets = [
        f"- `{row['target']}`: {row['count']}"
        for row in report["top_candidate_targets"]
    ] or ["- None."]
    return "\n".join([
        "# Governance Parse-Error Retroactive Audit",
        "",
        f"Input: `{report['input_path']}`",
        "",
        "## Summary",
        "",
        _table([("total parse errors", report["total_parse_errors"])]),
        "",
        "## Root Causes",
        "",
        _table(sorted(report["root_causes"].items())),
        "",
        "## Candidate Actions",
        "",
        _table(sorted(report["candidate_actions"].items())),
        "",
        "## Top Candidate Targets",
        "",
        "\n".join(targets),
    ]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=_DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = analyze(args.input)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    else:
        sys.stdout.write(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
