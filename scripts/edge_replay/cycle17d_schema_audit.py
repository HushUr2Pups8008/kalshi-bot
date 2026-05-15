#!/usr/bin/env python3
"""Cycle-17D schema compatibility audit for scorer production-proxy replay."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUTS = {
    "cycle13_live": REPO_ROOT / "logs/edge_replay/cycle13_live/replay_dataset.jsonl",
    "cycle13_local": REPO_ROOT / "logs/edge_replay/cycle13_local/replay_dataset.jsonl",
    "cycle15b": REPO_ROOT / "logs/edge_replay/cycle15b/replay_dataset.jsonl",
    "cycle16d": REPO_ROOT / "logs/edge_replay/cycle16d/replay_dataset.jsonl",
}
DEFAULT_MERGED = REPO_ROOT / "logs/edge_replay/cycle17d/replay_dataset_merged.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "logs/edge_replay/cycle17d/schema_compatibility_audit.json"

SOURCE_ORDER = ("cycle13_live", "cycle13_local", "cycle15b", "cycle16d")
SCORER_REQUIRED_FIELDS = (
    "ticker",
    "decision_ts",
    "signal_source",
    "series_ticker",
    "signal_type",
    "news_class",
    "resolved_yes",
)
SCORER_GROUP_FIELDS = ("signal_source", "series_ticker", "signal_type", "news_class")
CHARTER_SWEEP_FIELDS = ("signal_source", "market_family", "signal_type", "news_class", "cohort")
# F-11 P1-C: "market_yes_price" kept here because pre-bounce corpora (cycle13,
# cycle15b) carry that key. Post-bounce rows carry "entry_price_cents". The
# _missing_counts helper treats a row as present when EITHER key is non-blank;
# see _has_price_field() and _missing_production_proxy_counts() below.
PRODUCTION_PROXY_REQUIRED_FIELDS = (
    "market_yes_price",  # pre-bounce alias -- presence check done via _has_price_field
    "edge",
    "model_prob",
    "confidence",
    "resolved_yes",
)
DEDUP_KEY_FIELDS = ("ticker", "decision_ts", "signal_source", "headline")
DEDUP_REQUIRED_FIELDS = ("ticker", "decision_ts", "signal_source")
OPTIONAL_REPORTED_FIELDS = (
    "headline",
    "side",
    "contracts",
    "decision_kind",
    "market_family",
    "readiness_admitted",
)
# F-11 P1-C: price field appears under either key; numeric validation covers both.
NUMERIC_FIELDS = ("market_yes_price", "entry_price_cents", "edge", "model_prob", "confidence", "contracts")
PROBABILITY_FIELDS = ("model_prob", "confidence")
BOOLEAN_FIELDS = ("resolved_yes", "readiness_admitted")
ALLOWED_COHORTS = ("PRE_FIX", "POST_FIX_REBUILT", "POST_FIX_NEW")


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"line": line_number, "error": str(exc)})
            continue
        if not isinstance(parsed, dict):
            errors.append({"line": line_number, "error": "row is not a JSON object"})
            continue
        rows.append(parsed)
    return rows, errors


def _is_blank(value: Any) -> bool:
    return value is None or value == ""


def _as_float(value: Any) -> float | None:
    if _is_blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_bool_like(value: Any) -> bool:
    if _is_blank(value):
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    return str(value).strip().lower() in {"yes", "true", "1", "no", "false", "0"}


def _missing_counts(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, int]:
    counts = {
        field: sum(1 for row in rows if field not in row or _is_blank(row.get(field)))
        for field in fields
    }
    return {field: count for field, count in counts.items() if count}


def _present_blank_counts(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, int]:
    counts = {
        field: sum(1 for row in rows if field in row and _is_blank(row.get(field)))
        for field in fields
    }
    return {field: count for field, count in counts.items() if count}


def _has_price_field(row: dict[str, Any]) -> bool:
    """F-11 P1-C: accept either canonical or legacy price key as satisfying presence check."""
    return not _is_blank(row.get("entry_price_cents")) or not _is_blank(row.get("market_yes_price"))


def _missing_production_proxy_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Override for PRODUCTION_PROXY_REQUIRED_FIELDS: price field accepts either key."""
    missing: dict[str, int] = {}
    for field in PRODUCTION_PROXY_REQUIRED_FIELDS:
        if field == "market_yes_price":
            count = sum(1 for row in rows if not _has_price_field(row))
        else:
            count = sum(1 for row in rows if field not in row or _is_blank(row.get(field)))
        if count:
            missing[field] = count
    return missing


def _invalid_numeric_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    invalid: dict[str, int] = {}
    for field in NUMERIC_FIELDS:
        count = 0
        for row in rows:
            value = row.get(field)
            if _is_blank(value):
                continue
            number = _as_float(value)
            if number is None:
                count += 1
                continue
            if field in PROBABILITY_FIELDS and not 0.0 <= number <= 1.0:
                count += 1
            # F-11 P1-C: price range check applies to both canonical and legacy key
            if field in {"market_yes_price", "entry_price_cents"} and not 0.0 <= number <= 100.0:
                count += 1
            if field == "contracts" and number <= 0:
                count += 1
        if count:
            invalid[field] = count
    return invalid


def _invalid_boolean_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    invalid: dict[str, int] = {}
    for field in BOOLEAN_FIELDS:
        count = sum(1 for row in rows if field in row and not _is_blank(row.get(field)) and not _is_bool_like(row.get(field)))
        if count:
            invalid[field] = count
    return invalid


def _audit_rows(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    extra_required_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    required_fields = tuple(dict.fromkeys((*SCORER_REQUIRED_FIELDS, *extra_required_fields)))
    missing_required = _missing_counts(rows, required_fields)
    missing_production_proxy = _missing_production_proxy_counts(rows)
    invalid_value_types = {
        **_invalid_numeric_counts(rows),
        **_invalid_boolean_counts(rows),
    }
    structurally_compatible = not errors and not missing_required and not invalid_value_types
    return {
        "path": _display_path(path),
        "row_count": len(rows),
        "json_errors": errors,
        "missing_required_fields": missing_required,
        "missing_production_proxy_fields": missing_production_proxy,
        "missing_optional_fields": _missing_counts(rows, OPTIONAL_REPORTED_FIELDS),
        "blank_optional_fields": _present_blank_counts(rows, OPTIONAL_REPORTED_FIELDS),
        "missing_dedup_key_fields": _missing_counts(rows, DEDUP_KEY_FIELDS),
        "missing_blocking_dedup_fields": _missing_counts(rows, DEDUP_REQUIRED_FIELDS),
        "invalid_value_types": invalid_value_types,
        "structurally_compatible": structurally_compatible,
        "production_proxy_ready": structurally_compatible and not missing_production_proxy,
        "compatible": structurally_compatible and not missing_production_proxy,
    }


def _cohort_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = Counter(str(row.get("cohort") or "") for row in rows)
    missing = values.pop("", 0)
    invalid = {value: count for value, count in sorted(values.items()) if value not in ALLOWED_COHORTS}
    valid = {value: count for value, count in sorted(values.items()) if value in ALLOWED_COHORTS}
    return {
        "allowed": list(ALLOWED_COHORTS),
        "valid": valid,
        "missing": missing,
        "invalid": invalid,
    }


def _blocking_issue_count(sections: list[dict[str, Any]], merged_cohorts: dict[str, Any]) -> int:
    count = 0
    for section in sections:
        count += len(section["json_errors"])
        count += len(section["missing_required_fields"])
        count += len(section["missing_production_proxy_fields"])
        count += len(section["invalid_value_types"])
    count += 1 if merged_cohorts["missing"] else 0
    count += len(merged_cohorts["invalid"])
    return count


def audit_schema_compatibility(
    *,
    inputs: dict[str, Path],
    merged: Path,
    output: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    source_sections: list[dict[str, Any]] = []
    for source in SOURCE_ORDER:
        path = inputs[source]
        rows, errors = load_jsonl(path)
        section = _audit_rows(path, rows=rows, errors=errors)
        sources[source] = section
        source_sections.append(section)

    merged_rows, merged_errors = load_jsonl(merged)
    merged_section = _audit_rows(merged, rows=merged_rows, errors=merged_errors, extra_required_fields=("market_family",))
    cohort_flags = _cohort_report(merged_rows)
    merged_section["cohort_flags"] = cohort_flags

    blocking_issue_count = _blocking_issue_count([*source_sections, merged_section], cohort_flags)
    optional_issue_count = sum(
        len(section["missing_optional_fields"]) + len(section["blank_optional_fields"])
        for section in [*source_sections, merged_section]
    )
    report = {
        "schema_version": 1,
        "compatible": blocking_issue_count == 0,
        "output_path": _display_path(output),
        "scorer": {
            "production_proxy_tool": "scripts/edge_replay/scorer_forensics_audit.py",
            "required_fields": list(SCORER_REQUIRED_FIELDS),
            "production_proxy_required_fields": list(PRODUCTION_PROXY_REQUIRED_FIELDS),
            "group_fields": list(SCORER_GROUP_FIELDS),
            "charter_sweep_fields": list(CHARTER_SWEEP_FIELDS),
            "dedup_key_fields": list(DEDUP_KEY_FIELDS),
            "dedup_blocking_fields": list(DEDUP_REQUIRED_FIELDS),
            "optional_reported_fields": list(OPTIONAL_REPORTED_FIELDS),
        },
        "summary": {
            "source_count": len(SOURCE_ORDER),
            "source_row_counts": {source: sources[source]["row_count"] for source in SOURCE_ORDER},
            "merged_row_count": merged_section["row_count"],
            "blocking_issue_count": blocking_issue_count,
            "optional_issue_count": optional_issue_count,
        },
        "sources": sources,
        "merged": merged_section,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle13-live", type=Path, default=DEFAULT_INPUTS["cycle13_live"])
    parser.add_argument("--cycle13-local", type=Path, default=DEFAULT_INPUTS["cycle13_local"])
    parser.add_argument("--cycle15b", type=Path, default=DEFAULT_INPUTS["cycle15b"])
    parser.add_argument("--cycle16d", type=Path, default=DEFAULT_INPUTS["cycle16d"])
    parser.add_argument("--merged", type=Path, default=DEFAULT_MERGED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true", help="Print the full schema audit report to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_schema_compatibility(
        inputs={
            "cycle13_live": args.cycle13_live,
            "cycle13_local": args.cycle13_local,
            "cycle15b": args.cycle15b,
            "cycle16d": args.cycle16d,
        },
        merged=args.merged,
        output=args.output,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "compatible": report["compatible"],
                    "blocking_issue_count": report["summary"]["blocking_issue_count"],
                    "optional_issue_count": report["summary"]["optional_issue_count"],
                    "output_path": report["output_path"],
                },
                sort_keys=True,
            )
        )
    return 0 if report["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
