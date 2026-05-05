"""Close-day decision-distribution analysis for PROFIT-PHASE2-001.

Reads GOVERNANCE_DECISION rows, groups mechanically uniform dead-source
disables, surfaces anomalies for manual review, and renders a close-day
Markdown report template.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_INPUT = _REPO_ROOT / "logs/governance/decisions.jsonl"
_DEFAULT_OUTPUT = _REPO_ROOT / "docs/governance/PROFIT-PHASE2-001-close-day-distribution-analysis-v2.md"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_decisions(path: Path, since: str | None = None) -> list[dict[str, Any]]:
    since_ts = _parse_ts(since)
    decisions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") != "GOVERNANCE_DECISION":
            continue
        decided_at = _parse_ts(row.get("decided_at"))
        if since_ts and decided_at and decided_at < since_ts:
            continue
        decisions.append(row)
    return decisions


def _confidence_bucket(confidence: Any) -> str:
    if not isinstance(confidence, int | float):
        return "unknown"
    if confidence >= 0.90:
        return ">=0.90"
    if confidence >= 0.85:
        return "0.85-0.89"
    if confidence >= 0.75:
        return "0.75-0.84"
    return "<0.75"


def _is_uniform_dead_source_disable(row: dict[str, Any]) -> bool:
    evidence = row.get("evidence_summary") or {}
    safety = row.get("safety_checks_passed") or {}
    return (
        row.get("action") == "disable_source"
        and all(safety.values())
        and (evidence.get("fresh_pass_count") or 0) == 0
        and (evidence.get("match_count") or 0) == 0
        and (evidence.get("active_market_count") or 0) == 0
    )


def _anomaly_reasons(row: dict[str, Any]) -> list[str]:
    evidence = row.get("evidence_summary") or {}
    safety = row.get("safety_checks_passed") or {}
    reasons: list[str] = []
    if row.get("action") != "disable_source":
        reasons.append("non_disable_source_action")
    failed_safety = sorted(key for key, value in safety.items() if value is not True)
    if failed_safety:
        reasons.append("failed_safety:" + ",".join(failed_safety))
    if (evidence.get("fresh_pass_count") or 0) > 0:
        reasons.append("fresh_pass_count_gt_zero")
    if (evidence.get("match_count") or 0) > 0:
        reasons.append("match_count_gt_zero")
    if (evidence.get("active_market_count") or 0) > 0:
        reasons.append("active_market_count_gt_zero")
    confidence = row.get("confidence")
    if isinstance(confidence, int | float) and confidence < 0.75:
        reasons.append("confidence_lt_0.75")
    return reasons


def analyze(path: Path = _DEFAULT_INPUT, since: str | None = None) -> dict[str, Any]:
    decisions = _load_decisions(path, since=since)
    action_counts = Counter(row.get("action", "<missing>") for row in decisions)
    confidence_counts = Counter(_confidence_bucket(row.get("confidence")) for row in decisions)

    uniform_dead = [row for row in decisions if _is_uniform_dead_source_disable(row)]
    anomalies = []
    for row in decisions:
        reasons = _anomaly_reasons(row)
        if reasons:
            anomalies.append({
                "decision_id": row.get("decision_id"),
                "decided_at": row.get("decided_at"),
                "action": row.get("action"),
                "target": row.get("target"),
                "confidence": row.get("confidence"),
                "reasons": reasons,
            })

    targets = Counter(str(row.get("target", "<missing>")) for row in decisions)
    duplicate_targets = [
        {"target": target, "count": count}
        for target, count in targets.most_common()
        if count > 1
    ]

    return {
        "input_path": str(path),
        "since": since,
        "total_decisions": len(decisions),
        "action_counts": dict(action_counts),
        "confidence_buckets": dict(confidence_counts),
        "bulk_review": {
            "uniform_disable_source_dead_count": len(uniform_dead),
            "uniform_disable_source_dead_pct": (
                round(100.0 * len(uniform_dead) / len(decisions), 1)
                if decisions else 0.0
            ),
        },
        "anomalies": anomalies,
        "duplicate_targets": duplicate_targets,
        "operator_template": {
            "bulk_verdict": "PENDING",
            "anomaly_verdicts": "PENDING",
            "gate_6_ready": False,
        },
    }


def _table(rows: list[tuple[str, Any]]) -> str:
    out = ["| item | value |", "|---|---:|"]
    out.extend(f"| {key} | {value} |" for key, value in rows)
    return "\n".join(out)


def render_markdown(report: dict[str, Any]) -> str:
    anomaly_lines = [
        f"- `{row['decision_id']}` `{row['action']}` `{row['target']}`: {', '.join(row['reasons'])}"
        for row in report["anomalies"]
    ] or ["- None."]
    duplicate_lines = [
        f"- `{row['target']}`: {row['count']} decisions"
        for row in report["duplicate_targets"][:20]
    ] or ["- None."]

    action_rows = sorted(report["action_counts"].items())
    confidence_rows = sorted(report["confidence_buckets"].items())
    return "\n".join([
        "# Close-Day Decision Distribution v2",
        "",
        f"Input: `{report['input_path']}`",
        f"Since: `{report['since'] or 'beginning'}`",
        "",
        "## Summary",
        "",
        _table([
            ("total decisions", report["total_decisions"]),
            ("uniform dead-source disables", report["bulk_review"]["uniform_disable_source_dead_count"]),
            ("uniform dead-source disables %", f"{report['bulk_review']['uniform_disable_source_dead_pct']}%"),
            ("manual anomaly count", len(report["anomalies"])),
        ]),
        "",
        "## Action Distribution",
        "",
        _table(action_rows),
        "",
        "## Confidence Distribution",
        "",
        _table(confidence_rows),
        "",
        "## Bulk Review Template",
        "",
        "- Bulk class: `disable_source` decisions with zero fresh-pass, match, and active-market signal, all safety checks true.",
        "- Operator verdict: `PENDING`.",
        "- Rationale: `PENDING`.",
        "",
        "## Manual Anomalies",
        "",
        "\n".join(anomaly_lines),
        "",
        "## Duplicate Targets",
        "",
        "\n".join(duplicate_lines),
        "",
        "## Close-Day Fill-In",
        "",
        "- Gate-6 aggregate file(s): `PENDING`",
        "- Bulk-review verdict source: `PENDING`",
        "- Anomaly-review verdict source: `PENDING`",
        "- Final reasonable rate: `PENDING`",
    ]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=_DEFAULT_INPUT)
    parser.add_argument("--since")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, nargs="?", const=_DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    report = analyze(args.input, since=args.since)
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
