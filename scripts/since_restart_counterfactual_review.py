#!/usr/bin/env python3
"""Read-only counterfactual review for since-restart reject/skip targets."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    is_test_record_source_or_signal_source as is_test_record,
)
from utils.trade_log_reader import iter_trade_records

TARGET_NO_KEYWORDS_CATEGORY = "post_llm_neutral_empty_keywords"
TARGET_ILLIQUID_REASON = "price 1.0c is near limit (too illiquid)"
POLICY_GUARDRAIL = "no_live_policy_change_without_counterfactual_evidence"


def _parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_ts(record: dict[str, Any]) -> datetime | None:
    return _parse_ts(record.get("ts") or record.get("timestamp"))


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _empty_llm_corpus(record: dict[str, Any]) -> str | None:
    direction = str(record.get("llm_direction") or "").strip().lower()
    magnitude = str(record.get("llm_magnitude") or "").strip().lower()
    if direction in {"yes", "no"} and magnitude != "none":
        return "directional_empty_llm"
    if direction == "neutral" or magnitude == "none":
        return "neutral_empty_llm"
    return None


def _failed_gates(record: dict[str, Any] | None) -> list[str]:
    if not record:
        return []
    if isinstance(record.get("gate_failed"), list):
        return [str(value) for value in record["gate_failed"]]
    chain = record.get("gate_chain")
    if isinstance(chain, list):
        return [str(value).split(":", 1)[0] for value in chain if "FAIL" in str(value).upper()]
    failed = record.get("failed_gate")
    return [str(failed)] if failed else []


def _nearest(
    events: list[dict[str, Any]],
    *,
    ticker: str,
    after: datetime | None = None,
    before: datetime | None = None,
    event_types: set[str],
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") not in event_types:
            continue
        if str(event.get("ticker") or event.get("market_ticker") or "") != ticker:
            continue
        ts = event.get("_ts")
        if after is not None and (ts is None or ts <= after):
            continue
        if before is not None and (ts is None or ts >= before):
            continue
        matches.append(event)
    if not matches:
        return None
    return min(matches, key=lambda row: row["_ts"] - after) if after else max(matches, key=lambda row: row["_ts"])


def _bucket_for_chain(opportunity: dict[str, Any] | None, terminal: dict[str, Any] | None) -> str:
    if opportunity is None:
        return "no_downstream_chain"
    if terminal is None:
        return "measurement_gap"
    if terminal.get("type") in {"PAPER_TRADE", "LIVE_ORDER"}:
        return "later_chain_executed"
    return "later_chain_skipped_other_gate"


def _chain_after(events: list[dict[str, Any]], ticker: str, ts: datetime) -> dict[str, Any]:
    opportunity = _nearest(events, ticker=ticker, after=ts, event_types={"OPPORTUNITY"})
    gate = _nearest(events, ticker=ticker, after=ts, event_types={"GATE_SUMMARY"})
    blend = _nearest(events, ticker=ticker, after=ts, event_types={"BLEND_DECISION"})
    terminal = _nearest(events, ticker=ticker, after=ts, event_types={"SKIPPED", "PAPER_TRADE", "LIVE_ORDER"})
    return {
        "opportunity_ts": _iso(opportunity.get("_ts")) if opportunity else None,
        "gate_ts": _iso(gate.get("_ts")) if gate else None,
        "gate_failed": _failed_gates(gate),
        "blend_ts": _iso(blend.get("_ts")) if blend else None,
        "blend_venue": blend.get("venue") if blend else None,
        "terminal_ts": _iso(terminal.get("_ts")) if terminal else None,
        "terminal_type": terminal.get("type") if terminal else None,
        "terminal_reason": terminal.get("reason") if terminal else None,
        "counterfactual_bucket": _bucket_for_chain(opportunity, terminal),
    }


def _chain_before(events: list[dict[str, Any]], ticker: str, ts: datetime) -> dict[str, Any]:
    opportunity = _nearest(events, ticker=ticker, before=ts, event_types={"OPPORTUNITY"})
    gate = _nearest(events, ticker=ticker, before=ts, event_types={"GATE_SUMMARY"})
    blend = _nearest(events, ticker=ticker, before=ts, event_types={"BLEND_DECISION"})
    return {
        "opportunity_ts": _iso(opportunity.get("_ts")) if opportunity else None,
        "gate_ts": _iso(gate.get("_ts")) if gate else None,
        "gate_failed": _failed_gates(gate),
        "blend_ts": _iso(blend.get("_ts")) if blend else None,
        "blend_venue": blend.get("venue") if blend else None,
    }


def build_counterfactual_report(
    path: str | Path,
    *,
    since: str | datetime,
    until: str | datetime | None = None,
    exclude_test: bool = False,
    limit: int | None = None,
    ticker: str | None = None,
) -> dict[str, Any]:
    since_dt = _parse_ts(since)
    until_dt = _parse_ts(until)
    if since_dt is None:
        raise ValueError("since is required")

    events: list[dict[str, Any]] = []
    for record in iter_trade_records(Path(path), since=since_dt, until=until_dt):
        if exclude_test and is_test_record(record):
            continue
        ts = _record_ts(record)
        if ts is None:
            continue
        if ticker and str(record.get("ticker") or record.get("market_ticker") or "") != ticker:
            continue
        row = dict(record)
        row["_ts"] = ts
        events.append(row)
    events.sort(key=lambda row: row["_ts"])

    targets: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("type")
        event_ticker = str(event.get("ticker") or event.get("market_ticker") or "")
        if (
            event_type == "ANALYSIS_REJECTED"
            and event.get("reason") == "no_keywords"
            and event.get("rejection_category") == TARGET_NO_KEYWORDS_CATEGORY
        ):
            chain = _chain_after(events, event_ticker, event["_ts"])
            targets.append({
                "target_type": "post_llm_neutral_empty_keywords",
                "reject_ts": _iso(event["_ts"]),
                "ticker": event_ticker,
                "source": event.get("source"),
                "headline": event.get("headline"),
                "reason": event.get("reason"),
                "rejection_category": event.get("rejection_category"),
                "signal_branch": event.get("signal_branch"),
                "method": event.get("method"),
                "llm_direction": event.get("llm_direction"),
                "llm_magnitude": event.get("llm_magnitude"),
                "llm_confidence": event.get("llm_confidence"),
                "match_score": event.get("match_score"),
                "keywords_empty": event.get("keywords") == [],
                "empty_llm_corpus": _empty_llm_corpus(event),
                **chain,
                "evidence_note": "retain gate unless downstream chain proves missed executable winner",
            })
        elif event_type == "SKIPPED" and event.get("reason") == TARGET_ILLIQUID_REASON:
            chain = _chain_before(events, event_ticker, event["_ts"])
            targets.append({
                "target_type": "near_limit_illiquidity_skip",
                "skip_ts": _iso(event["_ts"]),
                "ticker": event_ticker,
                "source": event.get("source"),
                "headline": event.get("headline"),
                "terminal_reason": event.get("reason"),
                "paper_mode_expected": True,
                "executed_price_cents": event.get("market_price") or event.get("entry_price_cents"),
                **chain,
                "counterfactual_bucket": "retain_liquidity_gate",
                "evidence_note": "near-limit price is not relaxed without executable-price and outcome proof",
            })
        if limit is not None and len(targets) >= limit:
            break

    buckets = Counter(row["counterfactual_bucket"] for row in targets)
    counts = Counter(row["target_type"] for row in targets)
    return {
        "window": {"since": _iso(since_dt), "until": _iso(until_dt)},
        "policy_guardrail": POLICY_GUARDRAIL,
        "target_counts": dict(sorted(counts.items())),
        "evidence_buckets": dict(sorted(buckets.items())),
        "targets": targets,
    }


def format_text_report(report: dict[str, Any]) -> str:
    lines = [
        "Since-restart counterfactual review",
        f"Window: {report['window']['since']} -> {report['window']['until'] or 'open'}",
        f"Policy guardrail: {report['policy_guardrail']}",
        "Target counts:",
    ]
    for key, count in report["target_counts"].items():
        lines.append(f"  {key}: {count}")
    lines.append("Evidence buckets:")
    for key, count in report["evidence_buckets"].items():
        lines.append(f"  {key}: {count}")
    if report["targets"]:
        lines.append("Targets:")
    for row in report["targets"]:
        ts = row.get("reject_ts") or row.get("skip_ts")
        lines.append(
            f"  {ts} {row.get('ticker') or 'n/a'} "
            f"{row['target_type']} bucket={row['counterfactual_bucket']}"
        )
    return "\n".join(lines) + "\n"


def _argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until")
    parser.add_argument("--ticker")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true")
    add_exclude_test_arg(parser, help_text="Exclude synthetic/test records")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argparser().parse_args(argv)
    report = build_counterfactual_report(
        args.path,
        since=args.since,
        until=args.until,
        exclude_test=args.exclude_test,
        limit=args.limit,
        ticker=args.ticker,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_text_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
