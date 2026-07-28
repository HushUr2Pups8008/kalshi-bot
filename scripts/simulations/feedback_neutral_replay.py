"""Read-only audit of captured feedback-sizing counterfactual receipts.

This tool never re-runs pricing, Kelly, gates, fills, or settlement logic. It
aggregates only the actual and neutral values emitted at decision time, so its
report is not profit evidence and cannot establish trade readiness.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Sequence


SUPPORTED_VERSION = 1
CHANNELS = ("source", "keyword", "all")
CLAIM_SCOPE = "captured sizing counterfactual only; not profit evidence"


def _is_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class _NonFiniteJsonConstant(ValueError):
    pass


def _reject_nonfinite_json_constant(value: str) -> None:
    raise _NonFiniteJsonConstant(value)


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(timezone.utc)


def _same_input_or_output_path(first: Path, second: Path) -> bool:
    if first.resolve(strict=False) == second.resolve(strict=False):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _mapping(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _required(mapping: dict[str, Any] | None, key: str, prefix: str) -> str | None:
    if mapping is None or key not in mapping:
        return f"missing_{prefix}_{key}"
    return None


def _required_numbers(
    mapping: dict[str, Any] | None,
    prefix: str,
    keys: Sequence[str],
) -> str | None:
    for key in keys:
        reason = _required(mapping, key, prefix)
        if reason is not None:
            return reason
        if mapping is None or not _is_number(mapping[key]):
            return f"invalid_{prefix}_{key}"
    return None


def _required_bools(
    mapping: dict[str, Any] | None,
    prefix: str,
    keys: Sequence[str],
) -> str | None:
    for key in keys:
        reason = _required(mapping, key, prefix)
        if reason is not None:
            return reason
        if mapping is None or not isinstance(mapping[key], bool):
            return f"invalid_{prefix}_{key}"
    return None


def _required_nonempty_strings(
    mapping: dict[str, Any] | None,
    prefix: str,
    keys: Sequence[str],
) -> str | None:
    for key in keys:
        reason = _required(mapping, key, prefix)
        if reason is not None:
            return reason
        if mapping is None or not isinstance(mapping[key], str) or not mapping[key]:
            return f"invalid_{prefix}_{key}"
    return None


def _source_reason(record: dict[str, Any]) -> str | None:
    if record.get("feedback_decision_version") != SUPPORTED_VERSION:
        return "unsupported_feedback_decision_version"

    for key in ("lifecycle_id", "venue", "ticker", "source", "decision_at"):
        if not isinstance(record.get(key), str) or not record[key]:
            return f"missing_{key}"
    try:
        _parse_timestamp(record["decision_at"])
    except ValueError:
        return "invalid_decision_at"
    if "series_ticker" not in record:
        return "missing_series_ticker"
    if record["series_ticker"] is not None and not isinstance(record["series_ticker"], str):
        return "invalid_series_ticker"
    if "probability_actual" not in record:
        return "missing_probability_actual"
    if not _is_number(record["probability_actual"]):
        return "invalid_probability_actual"
    if not 0.0 <= float(record["probability_actual"]) <= 1.0:
        return "invalid_probability_actual"
    if "probability_keyword_neutral" not in record:
        return "missing_probability_keyword_neutral"
    keyword_probability = record["probability_keyword_neutral"]
    if keyword_probability is not None and (
        not _is_number(keyword_probability)
        or not 0.0 <= float(keyword_probability) <= 1.0
    ):
        return "invalid_probability_keyword_neutral"
    if not isinstance(record.get("keyword_counterfactual_status"), str):
        return "invalid_keyword_counterfactual_status"
    if "keyword_neutral" not in record:
        return "missing_keyword_neutral"
    if "all_neutral" not in record:
        return "missing_all_neutral"
    keyword_receipts = record.get("keyword_receipts")
    if not isinstance(keyword_receipts, list) or not all(
        isinstance(receipt, dict) for receipt in keyword_receipts
    ):
        return "invalid_keyword_receipts"

    receipt = _mapping(record.get("source_receipt"))
    for key in (
        "channel",
        "key",
        "applied_multiplier",
        "status",
        "canonical_basis_sha256",
        "delivered_event_count",
        "effective_sample_count",
        "algorithm_version",
        "as_of",
    ):
        reason = _required(receipt, key, "source_receipt")
        if reason is not None:
            return reason
    if receipt is None:
        return "missing_source_receipt"
    if receipt["channel"] != "source":
        return "invalid_source_receipt_channel"
    if receipt["status"] != "canonical":
        return "source_receipt_not_canonical"
    basis = receipt["canonical_basis_sha256"]
    if not isinstance(basis, str) or len(basis) != 64:
        return "invalid_source_receipt_basis"
    if not all(char in "0123456789abcdef" for char in basis.lower()):
        return "invalid_source_receipt_basis"
    reason = _required_numbers(receipt, "source_receipt", ("applied_multiplier",))
    if reason is not None:
        return reason
    if (
        not isinstance(receipt["delivered_event_count"], int)
        or isinstance(receipt["delivered_event_count"], bool)
        or receipt["delivered_event_count"] < 0
    ):
        return "invalid_source_receipt_delivered_event_count"
    if (
        not isinstance(receipt["effective_sample_count"], int)
        or isinstance(receipt["effective_sample_count"], bool)
        or receipt["effective_sample_count"] < 0
    ):
        return "invalid_source_receipt_effective_sample_count"
    if not isinstance(receipt["algorithm_version"], str) or not receipt["algorithm_version"]:
        return "invalid_source_receipt_algorithm_version"
    if not isinstance(receipt["as_of"], str):
        return "invalid_source_receipt_as_of"
    try:
        _parse_timestamp(receipt["as_of"])
    except ValueError:
        return "invalid_source_receipt_as_of"

    sizing_inputs = _mapping(record.get("sizing_inputs"))
    reason = _required_numbers(
        sizing_inputs,
        "sizing_inputs",
        (
            "executed_price_cents",
            "bankroll",
            "kelly_fraction",
            "max_bet_dollars",
            "min_bet_dollars",
            "min_edge",
            "confidence",
            "days_to_close",
            "time_discount_half_life",
            "time_discount_floor",
            "source_multiplier",
            "paper_flat_contracts",
            "floor_clamp_kelly_multiplier",
        ),
    )
    if reason is not None:
        return reason
    reason = _required_bools(
        sizing_inputs,
        "sizing_inputs",
        ("is_paper_trading", "floor_clamp_applied"),
    )
    if reason is not None:
        return reason
    if sizing_inputs is None:
        return "missing_sizing_inputs"
    if sizing_inputs.get("side") not in {"yes", "no"}:
        return "invalid_sizing_inputs_side"

    actual = _mapping(record.get("actual"))
    reason = _required_numbers(
        actual,
        "actual",
        ("source_multiplier", "kelly_fraction", "kelly_dollars", "capped_dollars"),
    )
    if reason is not None:
        return reason
    reason = _required_bools(actual, "actual", ("paper_placeholder_applied",))
    if reason is not None:
        return reason
    if actual is None:
        return "missing_actual"

    neutral = _mapping(record.get("source_neutral"))
    for key in (
        "status",
        "sizing_error",
    ):
        reason = _required(neutral, key, "source_neutral")
        if reason is not None:
            return reason
    if neutral is None:
        return "missing_source_neutral"
    if neutral["status"] != "scorable" or neutral["sizing_error"] is not None:
        return "source_neutral_unscorable"
    reason = _required_numbers(
        neutral,
        "source_neutral",
        ("source_multiplier", "kelly_fraction", "kelly_dollars", "capped_dollars"),
    )
    if reason is not None:
        return reason
    reason = _required_bools(neutral, "source_neutral", ("paper_placeholder_applied",))
    if reason is not None:
        return reason
    if neutral["source_multiplier"] != 1.0:
        return "invalid_source_neutral_multiplier"

    multipliers = (
        float(receipt["applied_multiplier"]),
        float(sizing_inputs["source_multiplier"]),
        float(actual["source_multiplier"]),
    )
    if len(set(multipliers)) != 1:
        return "source_multiplier_mismatch"

    gate = _mapping(record.get("gate"))
    reason = _required_bools(gate, "gate", ("enqueued",))
    if reason is not None:
        return reason
    reason = _required(gate, "trade_blocked_reason", "gate")
    if reason is not None:
        return reason
    if gate is None or (
        gate["trade_blocked_reason"] is not None
        and not isinstance(gate["trade_blocked_reason"], str)
    ):
        return "invalid_gate_trade_blocked_reason"
    return None


def _counterfactual_reason(record: dict[str, Any], channel: str) -> str | None:
    if channel == "source":
        return _source_reason(record)
    neutral = _mapping(record.get(f"{channel}_neutral"))
    if neutral is None or neutral.get("status") != "scorable":
        return f"{channel}_counterfactual_unavailable"
    return f"{channel}_counterfactual_not_supported"


def _source_row(record: dict[str, Any]) -> dict[str, Any]:
    receipt = _mapping(record["source_receipt"]) or {}
    actual = _mapping(record["actual"]) or {}
    neutral = _mapping(record["source_neutral"]) or {}
    gate = _mapping(record["gate"]) or {}
    return {
        "feedback_decision_version": record["feedback_decision_version"],
        "lifecycle_id": record["lifecycle_id"],
        "decision_at": record["decision_at"],
        "ticker": record["ticker"],
        "source": record["source"],
        "source_receipt": {
            "status": receipt["status"],
            "canonical_basis_sha256": receipt["canonical_basis_sha256"],
            "delivered_event_count": receipt["delivered_event_count"],
            "effective_sample_count": receipt["effective_sample_count"],
            "algorithm_version": receipt["algorithm_version"],
            "applied_multiplier": receipt["applied_multiplier"],
        },
        "actual": {
            "source_multiplier": actual["source_multiplier"],
            "capped_dollars": actual["capped_dollars"],
            "paper_placeholder_applied": actual["paper_placeholder_applied"],
        },
        "neutral": {
            "source_multiplier": neutral["source_multiplier"],
            "capped_dollars": neutral["capped_dollars"],
            "paper_placeholder_applied": neutral["paper_placeholder_applied"],
        },
        "gate": {
            "enqueued": gate["enqueued"],
            "trade_blocked_reason": gate["trade_blocked_reason"],
        },
    }


def _empty_source_result() -> dict[str, Any]:
    return {
        "actual_capped_dollars": 0.0,
        "neutral_capped_dollars": 0.0,
        "delta_capped_dollars": 0.0,
        "decision_rows": [],
    }


def _selected_timestamp(record: dict[str, Any]) -> datetime | None:
    decision_at = record.get("decision_at")
    if not isinstance(decision_at, str):
        return None
    try:
        return _parse_timestamp(decision_at)
    except ValueError:
        return None


def build_report(
    *,
    inputs: Sequence[Path],
    since: datetime | None,
    until: datetime | None,
    channels: Sequence[str],
) -> dict[str, Any]:
    input_metadata: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {
        "records_seen": 0,
        "feedback_decision_records": 0,
        "non_feedback_records": 0,
        "malformed_lines": 0,
        "selected": 0,
        "scorable": 0,
        "unscorable_by_reason": {},
    }
    unscorable: Counter[str] = Counter()
    source_by_venue: dict[str, dict[str, Any]] = {}
    seen_input_paths: set[Path] = set()
    seen_input_hashes: dict[str, Path] = {}
    seen_lifecycle_ids: set[str] = set()

    for input_path in inputs:
        resolved_path = input_path.resolve(strict=False)
        if resolved_path in seen_input_paths:
            unscorable["duplicate_input_path"] += 1
            continue
        seen_input_paths.add(resolved_path)
        raw = input_path.read_bytes()
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        input_metadata.append(
            {
                "path": str(input_path),
                "sha256": raw_sha256,
                "bytes": len(raw),
            }
        )
        if raw_sha256 in seen_input_hashes:
            unscorable["duplicate_input_content"] += 1
            continue
        seen_input_hashes[raw_sha256] = resolved_path
        for raw_line in raw.splitlines():
            if not raw_line.strip():
                continue
            coverage["records_seen"] += 1
            try:
                parsed = json.loads(raw_line, parse_constant=_reject_nonfinite_json_constant)
            except _NonFiniteJsonConstant:
                coverage["malformed_lines"] += 1
                unscorable["nonfinite_json_constant"] += 1
                continue
            except (TypeError, ValueError, UnicodeDecodeError):
                coverage["malformed_lines"] += 1
                unscorable["malformed_jsonl"] += 1
                continue
            if not isinstance(parsed, dict) or parsed.get("type") != "FEEDBACK_DECISION":
                coverage["non_feedback_records"] += 1
                continue
            coverage["feedback_decision_records"] += 1
            decision_at = _selected_timestamp(parsed)
            if decision_at is not None and since is not None and decision_at < since:
                continue
            if decision_at is not None and until is not None and decision_at > until:
                continue
            coverage["selected"] += 1
            if decision_at is None:
                unscorable["invalid_decision_at"] += 1
                continue
            lifecycle_id = parsed.get("lifecycle_id")
            if isinstance(lifecycle_id, str) and lifecycle_id:
                if lifecycle_id in seen_lifecycle_ids:
                    unscorable["duplicate_lifecycle_id"] += 1
                    continue
                seen_lifecycle_ids.add(lifecycle_id)

            for channel in channels:
                reason = _counterfactual_reason(parsed, channel)
                if reason is not None:
                    unscorable[reason] += 1
                    continue
                coverage["scorable"] += 1
                if channel != "source":
                    continue
                venue = str(parsed["venue"])
                result = source_by_venue.setdefault(venue, _empty_source_result())
                actual = _mapping(parsed["actual"]) or {}
                neutral = _mapping(parsed["source_neutral"]) or {}
                result["actual_capped_dollars"] += float(actual["capped_dollars"])
                result["neutral_capped_dollars"] += float(neutral["capped_dollars"])
                result["decision_rows"].append(_source_row(parsed))

    for result in source_by_venue.values():
        result["actual_capped_dollars"] = round(result["actual_capped_dollars"], 4)
        result["neutral_capped_dollars"] = round(result["neutral_capped_dollars"], 4)
        result["delta_capped_dollars"] = round(
            result["neutral_capped_dollars"] - result["actual_capped_dollars"],
            4,
        )
        result["decision_rows"].sort(
            key=lambda row: (row["decision_at"], row["ticker"], row["lifecycle_id"])
        )

    coverage["unscorable_by_reason"] = dict(sorted(unscorable.items()))
    results: dict[str, Any] = {}
    if "source" in channels:
        results["source"] = {
            "by_venue": dict(sorted(source_by_venue.items())),
        }
    for channel in channels:
        if channel != "source":
            results[channel] = {
                "status": "no_supported_captured_sizing_counterfactual",
                "by_venue": {},
            }

    return {
        "schema_version": 1,
        "read_only": True,
        "no_live_policy_change": True,
        "claim_scope": CLAIM_SCOPE,
        "inputs": input_metadata,
        "selection": {
            "since": since.isoformat() if since is not None else None,
            "until": until.isoformat() if until is not None else None,
            "channels": list(channels),
        },
        "coverage": coverage,
        "results": results,
        "gate_snapshot": {
            "captured_actual_only": True,
            "not_recomputed": True,
            "reason": "sizing can affect downstream executor checks",
        },
        "verdict": {"status": "not_profit_evidence"},
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        "--decision-input",
        dest="inputs",
        action="append",
        required=True,
        metavar="PATH",
        help="JSONL file containing FEEDBACK_DECISION records; repeatable",
    )
    parser.add_argument("--since", type=_parse_timestamp, help="inclusive ISO-8601 lower bound")
    parser.add_argument("--until", type=_parse_timestamp, help="inclusive ISO-8601 upper bound")
    parser.add_argument(
        "--channel",
        action="append",
        choices=CHANNELS,
        help="counterfactual channel to audit; repeatable, default source",
    )
    parser.add_argument(
        "--require-complete-snapshot",
        action="store_true",
        help="exit 2 when selected records are malformed, incomplete, or unscorable",
    )
    parser.add_argument("--output", type=Path, help="optional report JSON path")
    parser.add_argument("--json", action="store_true", help="emit report JSON to stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.since is not None and args.until is not None and args.since > args.until:
        parser.error("--since must be before --until")
    input_paths = [Path(value) for value in args.inputs]
    if args.output is not None:
        if any(
            _same_input_or_output_path(path, args.output) for path in input_paths
        ):
            parser.error("--output must not match an --input path")
    channels = tuple(dict.fromkeys(args.channel or ["source"]))
    report = build_report(
        inputs=input_paths,
        since=args.since,
        until=args.until,
        channels=channels,
    )
    if args.require_complete_snapshot and (
        report["coverage"]["malformed_lines"]
        or report["coverage"]["unscorable_by_reason"]
        or report["coverage"]["selected"] == 0
    ):
        if (
            report["coverage"]["selected"] == 0
            and not report["coverage"]["unscorable_by_reason"]
        ):
            report["coverage"]["unscorable_by_reason"] = {
                **report["coverage"]["unscorable_by_reason"],
                "no_selected_feedback_decisions": 1,
            }
        report["verdict"] = {"status": "incomplete_snapshot"}
        exit_code = 2
    else:
        exit_code = 0
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "feedback-neutral-replay"
            f" status={report['verdict']['status']}"
            f" selected={report['coverage']['selected']}"
            f" scorable={report['coverage']['scorable']}"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
