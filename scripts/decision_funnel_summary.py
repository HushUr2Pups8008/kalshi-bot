"""
Concise read-only decision funnel summary for structured trade logs.

Uses the preferred trade-log root at logs/trades/ to show what is directly observable from the
structured event stream:
  - total records considered
  - signal / opportunity counts where present
  - executor skips by reason
  - paper-trade records and live submissions
  - news vs fade path contributions where inferable

This tool does not modify runtime or trading behavior.
The preferred source is logs/trades/. The legacy monolithic
logs/trades/trades.jsonl path is still supported during the cutover
validation window.
"""

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
    add_path_arg,
    add_since_arg,
    add_top_arg,
    add_until_arg,
    is_test_record_source_or_signal_source as is_test_record,
    parse_date_end,
    parse_date_start,
)
from utils.diagnostic_reporting_helpers import format_counter, print_standard_trade_log_header
from utils.reporting_helpers import DEFAULT_CURRENT_STATE_WINDOW_HOURS, resolve_recent_window
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records


# Default to the active live file -- the funnel summary is a current-run metric.
# Pass --path logs/trades to include archive data for multi-day analysis.
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl"
DECISION_EVENT_TYPES = {
    "ANALYSIS_REJECTED",
    "SIGNAL",
    "OPPORTUNITY",
    "SKIPPED",
    "PAPER_TRADE",
    "LIVE_ORDER",
}
_LIFECYCLE_ATTRIBUTION_EVENT_TYPES = frozenset(
    {
        "OPPORTUNITY",
        "SKIPPED",
        "PAPER_TRADE",
        "LIVE_ORDER",
        "LIVE_SUBMISSION_INTENT",
        "LIVE_SUBMISSION_UNKNOWN",
    }
)
_PAPER_TRADE_EVENT_TYPES = frozenset({"PAPER_TRADE"})
_LIVE_SUBMISSION_EVENT_TYPES = frozenset({"LIVE_ORDER"})
_LIVE_SUBMISSION_INTENT_EVENT_TYPES = frozenset({"LIVE_SUBMISSION_INTENT"})
_LIVE_SUBMISSION_UNKNOWN_EVENT_TYPES = frozenset({"LIVE_SUBMISSION_UNKNOWN"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize kalshi-bot decision funnel")
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text=(
            "Path to trade-log file or root "
            "(default: logs/trades/live/trades.jsonl; pass logs/trades/ for archive scans; "
            f"default window: last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours when dates omitted; "
            "legacy logs/trades/trades.jsonl still supported)"
        ),
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(
        parser,
        help_text=f"Inclusive end date in YYYY-MM-DD (default: now when both dates omitted; last {DEFAULT_CURRENT_STATE_WINDOW_HOURS} hours)",
    )
    parser.add_argument(
        "--since-utc",
        help="Inclusive exact ISO-8601 UTC start timestamp; cannot be combined with --since/--until",
    )
    parser.add_argument(
        "--until-utc",
        help="Inclusive exact ISO-8601 UTC end timestamp; cannot be combined with --since/--until",
    )
    add_top_arg(parser, default=5, help_text="Max rows to show in top breakdowns (default: 5)")
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    args = parser.parse_args()
    if (args.since_utc or args.until_utc) and (args.since or args.until):
        parser.error("--since-utc/--until-utc cannot be combined with --since/--until")
    return args


def parse_utc_timestamp(value: str) -> datetime:
    """Parse an explicit ISO-8601 timestamp into UTC for an exact report window."""
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp must include a UTC offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def infer_signal_type(record: dict[str, Any]) -> str:
    explicit = record.get("signal_type")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    source = str(record.get("source") or record.get("signal_source") or "").strip().lower()
    url = str(record.get("url") or "").strip().lower()
    reasoning = str(record.get("reasoning") or "").strip().upper()
    headline = str(record.get("headline") or record.get("signal_headline") or "").strip().upper()

    if (
        source == "price_fade"
        or url.startswith("kalshi://price_fade/")
        or "[PRICE_FADE]" in reasoning
        or headline.startswith("[PRICE_FADE]")
    ):
        return "price_fade"
    if "[FADE/" in reasoning:
        return "fade_tweet"
    return "news"


def stale_age_bucket(age_seconds: Any) -> str | None:
    if not isinstance(age_seconds, (int, float)):
        return None
    if age_seconds < 0:
        return None
    if age_seconds < 5 * 60:
        return "0-5m"
    if age_seconds < 15 * 60:
        return "5-15m"
    if age_seconds < 30 * 60:
        return "15-30m"
    if age_seconds < 60 * 60:
        return "30-60m"
    return "60m+"


def pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(100.0 * numerator / denominator):.1f}%"


def _text(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or default


def _nested_text(record: dict[str, Any], *keys: str, default: str = "unknown") -> str:
    for key in keys:
        if "." in key:
            value: Any = record
            for part in key.split("."):
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
        else:
            value = record.get(key)
        text = _text(value, default="")
        if text:
            return text
    return default


def _lifecycle_id(record: dict[str, Any]) -> str | None:
    value = record.get("lifecycle_id")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _lifecycle_identity(record: dict[str, Any]) -> tuple[str, str, str] | None:
    values = tuple(_text(record.get(field), default="") for field in ("venue", "ticker", "side"))
    if not all(values):
        return None
    return values


def _record_fingerprint(record: dict[str, Any]) -> str:
    # A new timestamp makes a repeated opportunity ambiguous without an attempt ID.
    return json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _skip_lifecycle_terminal(record: dict[str, Any]) -> str:
    reason = _text(record.get("reason"), default="").casefold()
    category = _text(record.get("skip_category"), default="").casefold()
    if reason.startswith("g7_") or category.startswith("g7_"):
        return "g7"
    capped_dollars = record.get("capped_dollars")
    try:
        is_zero_cap = capped_dollars is not None and float(capped_dollars) == 0.0
    except (TypeError, ValueError):
        is_zero_cap = False
    if is_zero_cap or "capped_dollars=0" in reason.replace(" ", ""):
        return "zero_cap"
    return "other"


def _record_text(record: dict[str, Any], field: str) -> str | None:
    value = record.get(field)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _terminal_receipt_ids(event_type: str, record: dict[str, Any]) -> tuple[str, ...]:
    receipt_fields_by_event = {
        "PAPER_TRADE": (("trade_id", "trade_id"),),
        # submission_id is created before POST, so it only correlates the intent
        # and must never stand in for a verified venue order receipt.
        "LIVE_ORDER": (("order_id", "order_id"),),
        "LIVE_SUBMISSION_UNKNOWN": (
            ("submission_id", "submission_id"),
            ("venue_order_id", "order_id"),
        ),
    }
    return tuple(
        f"{receipt_field}:{value}"
        for field, receipt_field in receipt_fields_by_event[event_type]
        if (value := _record_text(record, field)) is not None
    )


def _terminal_semantic_key(event_type: str, record: dict[str, Any]) -> str:
    fields_by_event = {
        "PAPER_TRADE": ("trade_id", "ticker", "venue", "side", "contracts", "price_cents", "cost_dollars"),
        "LIVE_ORDER": (
            "order_id",
            "submission_id",
            "ticker",
            "venue",
            "side",
            "contracts",
            "price_cents",
            "cost_dollars",
            "status",
            "filled",
        ),
        "LIVE_SUBMISSION_UNKNOWN": (
            "submission_id",
            "venue_order_id",
            "ticker",
            "venue",
            "side",
            "contracts",
            "price_cents",
            "cost_dollars",
            "outcome",
        ),
    }
    payload = {field: record.get(field) for field in fields_by_event[event_type]}
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _skip_semantic_key(record: dict[str, Any]) -> str:
    return json.dumps(
        {
            "terminal": _skip_lifecycle_terminal(record),
            "reason": _record_text(record, "reason"),
            "skip_category": _record_text(record, "skip_category"),
            "capped_dollars": record.get("capped_dollars"),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _same_window_lifecycle_attribution(
    records: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Attribute only explicit same-window lifecycle links; never infer them."""
    ids_by_event: dict[str, set[str]] = {event_type: set() for event_type in _LIFECYCLE_ATTRIBUTION_EVENT_TYPES}
    skip_terminal_by_id: dict[str, set[str]] = {}
    skip_semantics_by_id: dict[str, set[str]] = {}
    identity_by_id: dict[str, tuple[str, str, str]] = {}
    conflicted_lifecycle_ids: set[str] = set()
    identity_incomplete_lifecycle_ids: set[str] = set()
    opportunity_fingerprints_by_id: dict[str, set[str]] = {}
    reused_opportunity_lifecycle_ids: set[str] = set()
    unattributed_event_counts: Counter[str] = Counter()
    terminal_semantics_by_event_and_id: dict[str, dict[str, set[str]]] = {
        event_type: {}
        for event_type in _PAPER_TRADE_EVENT_TYPES | _LIVE_SUBMISSION_EVENT_TYPES | _LIVE_SUBMISSION_UNKNOWN_EVENT_TYPES
    }
    terminal_receipt_lifecycle_ids: dict[str, set[str]] = {}
    terminal_missing_receipt_ids: set[str] = set()
    intent_submission_ids_by_id: dict[str, set[str]] = {}
    intent_missing_submission_ids: set[str] = set()
    terminal_submission_ids_by_id: dict[str, set[str]] = {}
    terminal_missing_submission_ids: set[str] = set()
    paper_trade_event_rows = 0
    paper_trade_lifecycle_rows: list[str] = []
    live_submission_event_rows = 0
    live_submission_lifecycle_rows: list[str] = []
    live_submission_intent_event_rows = 0
    live_submission_intent_lifecycle_rows: list[str] = []
    unknown_live_submission_event_rows = 0
    unknown_live_submission_lifecycle_rows: list[str] = []

    for event_type, record in records:
        lifecycle_id = _lifecycle_id(record)
        if event_type in _PAPER_TRADE_EVENT_TYPES:
            paper_trade_event_rows += 1
        elif event_type in _LIVE_SUBMISSION_EVENT_TYPES:
            live_submission_event_rows += 1
        elif event_type in _LIVE_SUBMISSION_INTENT_EVENT_TYPES:
            live_submission_intent_event_rows += 1
        elif event_type in _LIVE_SUBMISSION_UNKNOWN_EVENT_TYPES:
            unknown_live_submission_event_rows += 1
        if lifecycle_id is None:
            unattributed_event_counts[event_type] += 1
            continue
        ids_by_event[event_type].add(lifecycle_id)
        identity = _lifecycle_identity(record)
        if identity is None:
            identity_incomplete_lifecycle_ids.add(lifecycle_id)
        else:
            known_identity = identity_by_id.setdefault(lifecycle_id, identity)
            if known_identity != identity:
                conflicted_lifecycle_ids.add(lifecycle_id)
        if event_type == "OPPORTUNITY":
            fingerprints = opportunity_fingerprints_by_id.setdefault(lifecycle_id, set())
            fingerprint = _record_fingerprint(record)
            if fingerprints and fingerprint not in fingerprints:
                reused_opportunity_lifecycle_ids.add(lifecycle_id)
            fingerprints.add(fingerprint)
        if event_type in _PAPER_TRADE_EVENT_TYPES:
            paper_trade_lifecycle_rows.append(lifecycle_id)
        elif event_type in _LIVE_SUBMISSION_EVENT_TYPES:
            live_submission_lifecycle_rows.append(lifecycle_id)
        elif event_type in _LIVE_SUBMISSION_INTENT_EVENT_TYPES:
            live_submission_intent_lifecycle_rows.append(lifecycle_id)
        elif event_type in _LIVE_SUBMISSION_UNKNOWN_EVENT_TYPES:
            unknown_live_submission_lifecycle_rows.append(lifecycle_id)
        if event_type == "SKIPPED":
            skip_terminal_by_id.setdefault(lifecycle_id, set()).add(_skip_lifecycle_terminal(record))
            skip_semantics_by_id.setdefault(lifecycle_id, set()).add(_skip_semantic_key(record))
        elif event_type in terminal_semantics_by_event_and_id:
            terminal_semantics_by_event_and_id[event_type].setdefault(lifecycle_id, set()).add(
                _terminal_semantic_key(event_type, record)
            )
            receipt_ids = _terminal_receipt_ids(event_type, record)
            if receipt_ids:
                for receipt_id in receipt_ids:
                    terminal_receipt_lifecycle_ids.setdefault(receipt_id, set()).add(lifecycle_id)
            else:
                terminal_missing_receipt_ids.add(lifecycle_id)
            if event_type in _LIVE_SUBMISSION_EVENT_TYPES | _LIVE_SUBMISSION_UNKNOWN_EVENT_TYPES:
                submission_id = _record_text(record, "submission_id")
                if submission_id is None:
                    terminal_missing_submission_ids.add(lifecycle_id)
                else:
                    terminal_submission_ids_by_id.setdefault(lifecycle_id, set()).add(submission_id)
        elif event_type in _LIVE_SUBMISSION_INTENT_EVENT_TYPES:
            submission_id = _record_text(record, "submission_id")
            if submission_id is None:
                intent_missing_submission_ids.add(lifecycle_id)
            else:
                intent_submission_ids_by_id.setdefault(lifecycle_id, set()).add(submission_id)

    quarantined_lifecycle_ids = (
        conflicted_lifecycle_ids | identity_incomplete_lifecycle_ids | reused_opportunity_lifecycle_ids
    )
    opportunity_ids = ids_by_event["OPPORTUNITY"] - quarantined_lifecycle_ids
    linked_skip_ids = opportunity_ids & ids_by_event["SKIPPED"]
    paper_trade_ids = ids_by_event["PAPER_TRADE"]
    live_submission_ids = ids_by_event["LIVE_ORDER"]
    live_submission_intent_ids = ids_by_event["LIVE_SUBMISSION_INTENT"]
    unknown_live_submission_ids = ids_by_event["LIVE_SUBMISSION_UNKNOWN"]
    linked_paper_trade_ids = opportunity_ids & paper_trade_ids
    linked_live_submission_ids = opportunity_ids & live_submission_ids
    linked_live_submission_intent_ids = opportunity_ids & live_submission_intent_ids
    linked_unknown_live_submission_ids = opportunity_ids & unknown_live_submission_ids
    terminal_id_sets = (
        linked_skip_ids,
        linked_paper_trade_ids,
        linked_live_submission_ids,
        linked_unknown_live_submission_ids,
    )
    terminal_type_conflict_ids = {
        lifecycle_id
        for lifecycle_id in opportunity_ids
        if sum(lifecycle_id in terminal_ids for terminal_ids in terminal_id_sets) > 1
    }
    terminal_evidence_conflict_ids = set(terminal_missing_receipt_ids)
    terminal_evidence_conflict_ids |= {
        lifecycle_id
        for semantics_by_id in terminal_semantics_by_event_and_id.values()
        for lifecycle_id, semantic_keys in semantics_by_id.items()
        if len(semantic_keys) > 1
    }
    terminal_evidence_conflict_ids |= {
        lifecycle_id for lifecycle_id, semantic_keys in skip_semantics_by_id.items() if len(semantic_keys) > 1
    }
    terminal_evidence_conflict_ids |= {
        lifecycle_id
        for lifecycle_ids in terminal_receipt_lifecycle_ids.values()
        if len(lifecycle_ids) > 1
        for lifecycle_id in lifecycle_ids
    }
    for lifecycle_id in linked_live_submission_intent_ids:
        intent_submission_ids = intent_submission_ids_by_id.get(lifecycle_id, set())
        terminal_submission_ids = terminal_submission_ids_by_id.get(lifecycle_id, set())
        has_live_submission_terminal = lifecycle_id in (linked_live_submission_ids | linked_unknown_live_submission_ids)
        if (
            lifecycle_id in intent_missing_submission_ids
            or len(intent_submission_ids) != 1
            or lifecycle_id in linked_skip_ids
            or lifecycle_id in linked_paper_trade_ids
            or (has_live_submission_terminal and lifecycle_id in terminal_missing_submission_ids)
            or (has_live_submission_terminal and not terminal_submission_ids <= intent_submission_ids)
        ):
            terminal_evidence_conflict_ids.add(lifecycle_id)
    terminal_evidence_conflict_ids &= opportunity_ids
    outcome_conflict_ids = terminal_type_conflict_ids | terminal_evidence_conflict_ids
    clean_skip_ids = linked_skip_ids - outcome_conflict_ids
    g7_skip_ids = {lifecycle_id for lifecycle_id in clean_skip_ids if "g7" in skip_terminal_by_id[lifecycle_id]}
    zero_cap_skip_ids = {
        lifecycle_id for lifecycle_id in clean_skip_ids - g7_skip_ids if "zero_cap" in skip_terminal_by_id[lifecycle_id]
    }
    other_skip_ids = clean_skip_ids - g7_skip_ids - zero_cap_skip_ids
    paper_trade_outcome_ids = linked_paper_trade_ids - outcome_conflict_ids
    live_submission_outcome_ids = linked_live_submission_ids - outcome_conflict_ids
    unknown_live_submission_outcome_ids = linked_unknown_live_submission_ids - outcome_conflict_ids
    resolved_live_submission_intent_ids = {
        lifecycle_id
        for lifecycle_id in linked_live_submission_intent_ids
        if intent_submission_ids_by_id.get(lifecycle_id, set()) & terminal_submission_ids_by_id.get(lifecycle_id, set())
    }
    unresolved_live_submission_intent_ids = linked_live_submission_intent_ids - resolved_live_submission_intent_ids
    paper_trade_linked_event_rows = sum(
        lifecycle_id in linked_paper_trade_ids for lifecycle_id in paper_trade_lifecycle_rows
    )
    live_submission_linked_event_rows = sum(
        lifecycle_id in linked_live_submission_ids for lifecycle_id in live_submission_lifecycle_rows
    )
    live_submission_intent_linked_event_rows = sum(
        lifecycle_id in linked_live_submission_intent_ids for lifecycle_id in live_submission_intent_lifecycle_rows
    )
    unknown_live_submission_linked_event_rows = sum(
        lifecycle_id in linked_unknown_live_submission_ids for lifecycle_id in unknown_live_submission_lifecycle_rows
    )
    if paper_trade_event_rows == 0:
        paper_trade_lifecycle_status = "not_observed"
    elif paper_trade_event_rows == paper_trade_linked_event_rows:
        paper_trade_lifecycle_status = "complete"
    else:
        paper_trade_lifecycle_status = "unavailable"

    return {
        "opportunity_lifecycle_count": len(opportunity_ids),
        "g7_skip_lifecycle_count": len(g7_skip_ids),
        "zero_cap_skip_lifecycle_count": len(zero_cap_skip_ids),
        "other_skip_lifecycle_count": len(other_skip_ids),
        "pending_opportunity_lifecycle_count": len(
            opportunity_ids
            - linked_skip_ids
            - linked_paper_trade_ids
            - linked_live_submission_ids
            - linked_unknown_live_submission_ids
            - linked_live_submission_intent_ids
        ),
        "orphan_skip_lifecycle_count": len(ids_by_event["SKIPPED"] - opportunity_ids - quarantined_lifecycle_ids),
        "paper_trade_opportunity_lifecycle_count": len(paper_trade_outcome_ids),
        "live_submission_opportunity_lifecycle_count": len(live_submission_outcome_ids),
        "unknown_live_submission_opportunity_lifecycle_count": len(unknown_live_submission_outcome_ids),
        "unresolved_live_submission_intent_opportunity_lifecycle_count": len(unresolved_live_submission_intent_ids),
        "outcome_conflict_lifecycle_count": len(outcome_conflict_ids),
        "terminal_evidence_conflict_lifecycle_count": len(terminal_evidence_conflict_ids),
        "orphan_paper_trade_lifecycle_count": len(paper_trade_ids - opportunity_ids - quarantined_lifecycle_ids),
        "orphan_live_submission_lifecycle_count": len(
            live_submission_ids - opportunity_ids - quarantined_lifecycle_ids
        ),
        "orphan_unknown_live_submission_lifecycle_count": len(
            unknown_live_submission_ids - opportunity_ids - quarantined_lifecycle_ids
        ),
        "orphan_live_submission_intent_lifecycle_count": len(
            live_submission_intent_ids - opportunity_ids - quarantined_lifecycle_ids
        ),
        "conflicted_lifecycle_count": len(conflicted_lifecycle_ids),
        "identity_incomplete_lifecycle_count": len(identity_incomplete_lifecycle_ids),
        "reused_opportunity_lifecycle_count": len(reused_opportunity_lifecycle_ids),
        "quarantined_lifecycle_count": len(quarantined_lifecycle_ids),
        "paper_trade_lifecycle_status": paper_trade_lifecycle_status,
        "paper_trade_event_rows": paper_trade_event_rows,
        "paper_trade_linked_event_rows": paper_trade_linked_event_rows,
        "live_submission_event_rows": live_submission_event_rows,
        "live_submission_linked_event_rows": live_submission_linked_event_rows,
        "live_submission_intent_event_rows": live_submission_intent_event_rows,
        "live_submission_intent_linked_event_rows": live_submission_intent_linked_event_rows,
        "unknown_live_submission_event_rows": unknown_live_submission_event_rows,
        "unknown_live_submission_linked_event_rows": unknown_live_submission_linked_event_rows,
        "unattributed_event_counts": unattributed_event_counts,
    }


def _reason_parts(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return ["unknown"]
    return [part for part in text.split("+") if part] or ["unknown"]


def _string_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _keyword_count(record: dict[str, Any]) -> int | None:
    raw_count = record.get("keyword_count")
    if raw_count is not None:
        try:
            return int(raw_count)
        except (TypeError, ValueError):
            return None
    keywords = record.get("keywords")
    if isinstance(keywords, list):
        return len(keywords)
    return None


def summarize(
    path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False, *, progress_tracker=None
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "event_counts": Counter(),
        "skip_reasons": Counter(),
        "skip_categories": Counter(),
        "analysis_rejected_reasons": Counter(),
        "analysis_rejected_categories": Counter(),
        "stale_news_age_buckets": Counter(),
        "stale_news_sources": Counter(),
        "stale_news_tickers": Counter(),
        "path_counts": Counter(),
        "execution_paths": Counter(),
        "match_diagnostics_total": 0,
        "signal_analysis_detail_total": 0,
        "signal_analysis_detail_organic_total": 0,
        "signal_analysis_detail_synthetic_total": 0,
        "match_to_signal_detail_gap": 0,
        "match_diagnostic_pre_llm_gate": Counter(),
        "match_diagnostic_sources": Counter(),
        "match_diagnostic_tickers": Counter(),
        "match_llm_reviews_total": 0,
        "match_llm_review_verdicts": Counter(),
        "false_positive_neutral_empty_keyword_sources": Counter(),
        "false_positive_neutral_empty_keyword_tickers": Counter(),
        "false_positive_neutral_empty_keyword_prefixes": Counter(),
        "false_positive_neutral_empty_keyword_reviews": 0,
        "match_suppressed_reasons": Counter(),
        "match_suppressed_sources": Counter(),
        "match_suppressed_tickers": Counter(),
        "match_suppressed_tokens": Counter(),
        "match_suppressed_column_coverage": Counter(),
        "match_suppressed_venues": Counter(),
        "match_suppressed_examples": [],
        "match_weight_applied_total": 0,
        "match_weight_tokens": Counter(),
        "match_weight_prefixes": Counter(),
        "match_weight_sources": Counter(),
        "match_weight_score_delta_total": 0.0,
        "opportunity_sources": Counter(),
        "opportunity_source_classes": Counter(),
        "opportunity_retrieval_modes": Counter(),
        "opportunity_evidence_ids": Counter(),
        "opportunity_settlement_source_matches": Counter(),
        "skip_sources": Counter(),
        "skip_source_classes": Counter(),
        "skip_retrieval_modes": Counter(),
        "skip_evidence_ids": Counter(),
        "skip_settlement_source_matches": Counter(),
    }
    lifecycle_records: list[tuple[str, dict[str, Any]]] = []

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if progress_tracker is not None:
            progress_tracker.tick()
        if exclude_test and is_test_record(record):
            continue

        stats["records_kept"] += 1
        event_type = str(record.get("type") or "UNKNOWN").strip() or "UNKNOWN"
        stats["event_counts"][event_type] += 1
        if event_type in _LIFECYCLE_ATTRIBUTION_EVENT_TYPES:
            lifecycle_records.append((event_type, record))

        signal_type = infer_signal_type(record)
        if event_type in DECISION_EVENT_TYPES:
            stats["path_counts"][signal_type] += 1

        if event_type == "MATCH_DIAGNOSTIC":
            stats["match_diagnostics_total"] += 1
            if record.get("would_fail_pre_llm_gate") is True:
                stats["match_diagnostic_pre_llm_gate"]["would_fail"] += 1
            elif record.get("would_fail_pre_llm_gate") is False:
                stats["match_diagnostic_pre_llm_gate"]["would_pass"] += 1
            else:
                stats["match_diagnostic_pre_llm_gate"]["unknown"] += 1
            stats["match_diagnostic_sources"][_text(record.get("source"))] += 1
            stats["match_diagnostic_tickers"][_text(record.get("ticker"))] += 1
        elif event_type == "SIGNAL_ANALYSIS_DETAIL":
            stats["signal_analysis_detail_total"] += 1
            if record.get("is_startup_probe") or record.get("is_synthetic_probe"):
                stats["signal_analysis_detail_synthetic_total"] += 1
            else:
                stats["signal_analysis_detail_organic_total"] += 1
        elif event_type == "MATCH_LLM_REVIEW":
            stats["match_llm_reviews_total"] += 1
            verdict = _text(record.get("verdict"))
            stats["match_llm_review_verdicts"][verdict] += 1
            if verdict == "false_positive_neutral":
                if _keyword_count(record) == 0:
                    stats["false_positive_neutral_empty_keyword_reviews"] += 1
                    stats["false_positive_neutral_empty_keyword_sources"][_text(record.get("source"))] += 1
                    stats["false_positive_neutral_empty_keyword_tickers"][_text(record.get("ticker"))] += 1
                    stats["false_positive_neutral_empty_keyword_prefixes"][_text(record.get("market_prefix"))] += 1
        elif event_type == "MATCH_SUPPRESSED":
            for reason in _reason_parts(record.get("reason")):
                stats["match_suppressed_reasons"][reason] += 1
            stats["match_suppressed_sources"][_text(record.get("source"))] += 1
            stats["match_suppressed_tickers"][_text(record.get("ticker"))] += 1
            for token in _string_values(record.get("matched_tokens")):
                stats["match_suppressed_tokens"][token] += 1
            for column in (
                "raw_score",
                "adjusted_score",
                "threshold",
                "token_weight_multiplier",
                "venue",
                "market_prefix",
            ):
                value = record.get(column)
                if value is not None and str(value).strip():
                    stats["match_suppressed_column_coverage"][column] += 1
            stats["match_suppressed_venues"][_text(record.get("venue"))] += 1
            if len(stats["match_suppressed_examples"]) < 5:
                stats["match_suppressed_examples"].append(
                    {
                        "ticker": _text(record.get("ticker")),
                        "venue": _text(record.get("venue")),
                        "raw_score": _float_or_none(record.get("raw_score")),
                        "adjusted_score": _float_or_none(record.get("adjusted_score")),
                        "threshold": _float_or_none(record.get("threshold")),
                        "token_weight_multiplier": _float_or_none(record.get("token_weight_multiplier")),
                        "reason": _text(record.get("reason")),
                    }
                )
        elif event_type == "MATCH_WEIGHT_APPLIED":
            stats["match_weight_applied_total"] += 1
            stats["match_weight_prefixes"][_text(record.get("market_prefix"))] += 1
            stats["match_weight_sources"][_text(record.get("source"))] += 1
            tokens = _string_values(record.get("tokens"))
            token_weights = record.get("token_weights")
            if isinstance(token_weights, dict):
                tokens.extend(str(token).strip() for token in token_weights if str(token).strip())
            for token in sorted(set(tokens)):
                stats["match_weight_tokens"][token] += 1
            pre_weight_score = _float_or_none(record.get("pre_weight_score"))
            post_weight_score = _float_or_none(record.get("post_weight_score"))
            if pre_weight_score is not None and post_weight_score is not None:
                stats["match_weight_score_delta_total"] += post_weight_score - pre_weight_score

        if event_type == "SKIPPED":
            reason = str(record.get("reason") or "unknown").strip() or "unknown"
            stats["skip_reasons"][reason] += 1
            category = str(record.get("skip_category") or "unknown").strip() or "unknown"
            stats["skip_categories"][category] += 1
            stats["skip_sources"][_nested_text(record, "source", "signal_meta.trigger_evidence_source")] += 1
            stats["skip_source_classes"][
                _nested_text(record, "source_class", "signal_meta.trigger_evidence_source_class")
            ] += 1
            stats["skip_retrieval_modes"][
                _nested_text(
                    record,
                    "retrieval_mode",
                    "signal_meta.retrieval_mode",
                    "signal_meta.source_lane",
                )
            ] += 1
            stats["skip_evidence_ids"][_nested_text(record, "evidence_id", "signal_meta.trigger_evidence_id")] += 1
            stats["skip_settlement_source_matches"][
                _nested_text(
                    record,
                    "settlement_source_match",
                    "signal_meta.settlement_source_match",
                )
            ] += 1
        elif event_type == "ANALYSIS_REJECTED":
            reason = str(record.get("reason") or "unknown").strip() or "unknown"
            stats["analysis_rejected_reasons"][reason] += 1
            category = str(record.get("rejection_category") or "").strip()
            if category:
                stats["analysis_rejected_categories"][category] += 1
            if reason == "stale_news":
                bucket = stale_age_bucket(record.get("age_seconds"))
                if bucket:
                    stats["stale_news_age_buckets"][bucket] += 1
                source = str(record.get("source") or record.get("signal_source") or "").strip()
                if source:
                    stats["stale_news_sources"][source] += 1
                ticker = str(record.get("ticker") or "").strip()
                if ticker:
                    stats["stale_news_tickers"][ticker] += 1
        elif event_type == "OPPORTUNITY":
            stats["opportunity_sources"][_nested_text(record, "source")] += 1
            stats["opportunity_source_classes"][_nested_text(record, "source_class")] += 1
            stats["opportunity_retrieval_modes"][
                _nested_text(
                    record,
                    "retrieval_mode",
                    "signal_meta.retrieval_mode",
                    "signal_meta.source_lane",
                )
            ] += 1
            stats["opportunity_evidence_ids"][
                _nested_text(record, "evidence_id", "signal_meta.trigger_evidence_id")
            ] += 1
            stats["opportunity_settlement_source_matches"][
                _nested_text(
                    record,
                    "settlement_source_match",
                    "signal_meta.settlement_source_match",
                )
            ] += 1
        if event_type in {"PAPER_TRADE", "LIVE_ORDER"}:
            stats["execution_paths"][signal_type] += 1

    stats["lines_total"] = read_stats.lines_total
    stats["lines_malformed"] = read_stats.lines_malformed
    stats["match_to_signal_detail_gap"] = max(
        0,
        stats["match_diagnostics_total"] - stats["signal_analysis_detail_total"],
    )
    stats["same_window_lifecycle_attribution"] = _same_window_lifecycle_attribution(lifecycle_records)

    return stats


def print_summary(stats: dict[str, Any], top: int, since: datetime | None, until: datetime | None) -> None:
    print_standard_trade_log_header(
        title="DECISION FUNNEL SUMMARY",
        path=stats["path"],
        since=since,
        until=until,
        lines_total=stats["lines_total"],
        lines_malformed=stats["lines_malformed"],
        records_kept=stats["records_kept"],
    )

    if stats["records_kept"] == 0:
        print()
        print("No matching log records found.")
        return

    signals = stats["event_counts"].get("SIGNAL", 0)
    analysis_rejected = stats["event_counts"].get("ANALYSIS_REJECTED", 0)
    opportunities = stats["event_counts"].get("OPPORTUNITY", 0)
    skipped = stats["event_counts"].get("SKIPPED", 0)
    paper_trades = stats["event_counts"].get("PAPER_TRADE", 0)
    live_submission_intents = stats["event_counts"].get("LIVE_SUBMISSION_INTENT", 0)
    live_orders = stats["event_counts"].get("LIVE_ORDER", 0)
    attribution = stats["same_window_lifecycle_attribution"]
    unknown_live_submission_opportunities = int(
        attribution.get("unknown_live_submission_opportunity_lifecycle_count") or 0
    )
    unknown_live_submission_event_rows = int(attribution.get("unknown_live_submission_event_rows") or 0)
    unknown_live_submission_linked_event_rows = int(attribution.get("unknown_live_submission_linked_event_rows") or 0)
    orphan_unknown_live_submissions = int(attribution.get("orphan_unknown_live_submission_lifecycle_count") or 0)
    unresolved_live_submission_intents = int(
        attribution.get("unresolved_live_submission_intent_opportunity_lifecycle_count") or 0
    )
    live_submission_intent_event_rows = int(attribution.get("live_submission_intent_event_rows") or 0)
    live_submission_intent_linked_event_rows = int(attribution.get("live_submission_intent_linked_event_rows") or 0)
    orphan_live_submission_intents = int(attribution.get("orphan_live_submission_intent_lifecycle_count") or 0)
    terminal_evidence_conflicts = int(attribution.get("terminal_evidence_conflict_lifecycle_count") or 0)

    print()
    print("Funnel")
    print(f"  Structured records considered : {stats['records_kept']}")
    print(f"  Pre-signal rejections         : {analysis_rejected}")
    print(f"  Signals logged                : {signals}")
    print(f"  Opportunities logged          : {opportunities}")
    if analysis_rejected:
        print("  No-signal exits               : partially observable via ANALYSIS_REJECTED")
    else:
        print("  No-signal exits               : not directly observable in trades.jsonl")
    print(f"  Executor skips               : {skipped}")
    print(f"  Paper-trade records          : {paper_trades}")
    print(f"  Live submission intents      : {live_submission_intents}")
    print(f"  Live order submissions       : {live_orders}")

    signal_detail_total = int(stats.get("signal_analysis_detail_total", 0) or 0)
    signal_detail_organic = int(
        stats.get("signal_analysis_detail_organic_total", signal_detail_total) or 0
    )
    signal_detail_synthetic = int(stats.get("signal_analysis_detail_synthetic_total", 0) or 0)
    print()
    print("Raw Funnel (report-window event rows; not lifecycle conversion)")
    print(f"  EARLY_FRESH_PASS            : {stats['event_counts'].get('EARLY_FRESH_PASS', 0)}")
    print(f"  MATCH_WEIGHT_APPLIED        : {stats['match_weight_applied_total']}")
    print(f"  MATCH_DIAGNOSTIC            : {stats['match_diagnostics_total']}")
    print(f"  MATCH_SUPPRESSED            : {stats['event_counts'].get('MATCH_SUPPRESSED', 0)}")
    print(
        "  SIGNAL_ANALYSIS_DETAIL      : "
        f"{signal_detail_total} (organic={signal_detail_organic}, synthetic={signal_detail_synthetic})"
    )
    print(f"  ANALYSIS_REJECTED           : {analysis_rejected}")
    print(f"  SIGNAL                      : {signals}")
    print(f"  OPPORTUNITY                 : {opportunities}")
    print(f"  PAPER_TRADE                 : {paper_trades}")

    print()
    print("Same-window lifecycle attribution")
    print(f"  Linkable opportunities        : {attribution['opportunity_lifecycle_count']}")
    print(
        "  Linked outcomes               : "
        f"G7={attribution['g7_skip_lifecycle_count']} "
        f"zero_cap={attribution['zero_cap_skip_lifecycle_count']} "
        f"other={attribution['other_skip_lifecycle_count']} "
        f"paper_trades={attribution['paper_trade_opportunity_lifecycle_count']} "
        f"live_submissions={attribution['live_submission_opportunity_lifecycle_count']} "
        f"unknown_live_submissions={unknown_live_submission_opportunities} "
        f"intents_without_matching_terminal_journal={unresolved_live_submission_intents} "
        f"conflicts={attribution['outcome_conflict_lifecycle_count']} "
        f"receipt_conflicts={terminal_evidence_conflicts} "
        f"pending={attribution['pending_opportunity_lifecycle_count']}"
    )
    print(
        "  Paper-trade lifecycle coverage : "
        f"{attribution['paper_trade_lifecycle_status']} "
        f"({attribution['paper_trade_linked_event_rows']}/{attribution['paper_trade_event_rows']} linked)"
    )
    print(
        "  Live submission linkage       : "
        f"{attribution['live_submission_linked_event_rows']}/{attribution['live_submission_event_rows']} "
        "records linked; not fill or P&L evidence"
    )
    print(
        "  Live submission unknown       : "
        f"{unknown_live_submission_linked_event_rows}/"
        f"{unknown_live_submission_event_rows} "
        "records linked; reconciliation required"
    )
    print(
        "  Live submission intent lineage: "
        f"{live_submission_intent_linked_event_rows}/{live_submission_intent_event_rows} records linked; "
        f"{unresolved_live_submission_intents} without matching terminal journal; "
        "not fill or P&L evidence; reconciliation required"
    )
    print(
        "  Linkage exclusions             : "
        f"identity_conflicts={attribution['conflicted_lifecycle_count']} "
        f"identity_incomplete={attribution['identity_incomplete_lifecycle_count']} "
        f"reused_opportunity={attribution['reused_opportunity_lifecycle_count']} "
        f"quarantined={attribution['quarantined_lifecycle_count']} "
        f"terminal_evidence_conflicts={terminal_evidence_conflicts} "
        f"orphan_skips={attribution['orphan_skip_lifecycle_count']} "
        f"orphan_paper_trades={attribution['orphan_paper_trade_lifecycle_count']} "
        f"orphan_live_submissions={attribution['orphan_live_submission_lifecycle_count']} "
        f"orphan_unknown_live_submissions={orphan_unknown_live_submissions} "
        f"orphan_live_intents={orphan_live_submission_intents}"
    )
    print("  Same-window scope              : lifecycle links only; settlement and mark P&L excluded")

    print()
    print("Match Attribution")
    print(f"  Match diagnostics            : {stats['match_diagnostics_total']}")
    print(f"  Match LLM reviews            : {stats['match_llm_reviews_total']}")
    print(f"  Signal analysis detail rows  : {stats['signal_analysis_detail_total']}")
    print(f"  Match -> analysis detail gap : {stats['match_to_signal_detail_gap']}")
    print(f"  Match suppressions           : {stats['event_counts'].get('MATCH_SUPPRESSED', 0)}")
    print(f"  Match weight applications    : {stats['match_weight_applied_total']}")
    if stats["match_weight_applied_total"]:
        print(f"  Weight score delta total     : {stats['match_weight_score_delta_total']:.4f}")

    print()
    print("Match LLM Review Verdicts")
    for line in format_counter(stats["match_llm_review_verdicts"], top=top):
        print(line)
    print(f"  False-positive neutral empty-keyword reviews: {stats['false_positive_neutral_empty_keyword_reviews']}")

    print()
    print(f"Empty-Keyword False-Neutral Sources (top {top})")
    for line in format_counter(
        stats["false_positive_neutral_empty_keyword_sources"],
        top=top,
    ):
        print(line)

    print()
    print(f"Empty-Keyword False-Neutral Tickers (top {top})")
    for line in format_counter(
        stats["false_positive_neutral_empty_keyword_tickers"],
        top=top,
    ):
        print(line)

    print()
    print(f"Empty-Keyword False-Neutral Market Prefixes (top {top})")
    for line in format_counter(
        stats["false_positive_neutral_empty_keyword_prefixes"],
        top=top,
    ):
        print(line)

    print()
    print("Pre-LLM quality gate")
    for line in format_counter(
        stats["match_diagnostic_pre_llm_gate"], top=max(top, len(stats["match_diagnostic_pre_llm_gate"]))
    ):
        print(line)

    print()
    print(f"Match Diagnostic Sources (top {top})")
    for line in format_counter(stats["match_diagnostic_sources"], top=top):
        print(line)

    print()
    print(f"Match Diagnostic Tickers (top {top})")
    for line in format_counter(stats["match_diagnostic_tickers"], top=top):
        print(line)

    print()
    print("Match Suppression Reasons")
    for line in format_counter(stats["match_suppressed_reasons"], top=top):
        print(line)

    print()
    print(f"Match Suppression Tokens (top {top})")
    for line in format_counter(stats["match_suppressed_tokens"], top=top):
        print(line)

    print()
    print("Match Suppression Column Coverage")
    suppression_total = stats["event_counts"].get("MATCH_SUPPRESSED", 0)
    for column in (
        "raw_score",
        "adjusted_score",
        "threshold",
        "token_weight_multiplier",
        "venue",
        "market_prefix",
    ):
        covered = stats["match_suppressed_column_coverage"].get(column, 0)
        print(f"  {column:<24}: {covered}/{suppression_total}")

    print()
    print(f"Match Suppression Venues (top {top})")
    for line in format_counter(stats["match_suppressed_venues"], top=top):
        print(line)

    print()
    print("Match Suppression Examples")
    if stats["match_suppressed_examples"]:
        for row in stats["match_suppressed_examples"][:top]:
            print(
                "  "
                f"{row['ticker']} venue={row['venue']} "
                f"raw={row['raw_score']} adjusted={row['adjusted_score']} "
                f"threshold={row['threshold']} multiplier={row['token_weight_multiplier']} "
                f"reason={row['reason']}"
            )
    else:
        print("  (none)")

    print()
    print(f"Match Weight Tokens (top {top})")
    for line in format_counter(stats["match_weight_tokens"], top=top):
        print(line)

    print()
    print(f"Match Weight Prefixes (top {top})")
    for line in format_counter(stats["match_weight_prefixes"], top=top):
        print(line)

    print()
    print("Pre-Signal Rejection Reasons")
    for line in format_counter(stats["analysis_rejected_reasons"], top=top):
        print(line)

    print()
    print("Pre-Signal Rejection Branches")
    for line in format_counter(stats["analysis_rejected_categories"], top=top):
        print(line)

    print()
    print("Stale-News Age Buckets")
    for line in format_counter(stats["stale_news_age_buckets"], top=max(top, len(stats["stale_news_age_buckets"]))):
        print(line)

    print()
    print(f"Stale-News Sources (top {top})")
    for line in format_counter(stats["stale_news_sources"], top=top):
        print(line)

    print()
    print(f"Stale-News Tickers (top {top})")
    for line in format_counter(stats["stale_news_tickers"], top=top):
        print(line)

    print()
    print("Executor Skip Categories")
    for line in format_counter(stats["skip_categories"], top=top):
        print(line)

    print()
    print("Executor Skip Reasons")
    for line in format_counter(stats["skip_reasons"], top=top):
        print(line)

    print()
    print("Path Contribution (decision-stage records)")
    for line in format_counter(stats["path_counts"], top=max(top, len(stats["path_counts"]))):
        print(line)

    print()
    print("Trade and Submission Path Contribution")
    print("  Scope: paper-trade records and live submissions; live submissions are not fill or P&L evidence.")
    for line in format_counter(stats["execution_paths"], top=max(top, len(stats["execution_paths"]))):
        print(line)

    print()
    print("Opportunity Attribution: Sources")
    for line in format_counter(stats["opportunity_sources"], top=top):
        print(line)

    print()
    print("Opportunity Attribution: Source Classes")
    for line in format_counter(stats["opportunity_source_classes"], top=top):
        print(line)

    print()
    print("Opportunity Attribution: Retrieval Modes")
    for line in format_counter(stats["opportunity_retrieval_modes"], top=top):
        print(line)

    print()
    print("Opportunity Attribution: Evidence IDs")
    for line in format_counter(stats["opportunity_evidence_ids"], top=top):
        print(line)

    print()
    print("Opportunity Attribution: Settlement-Source Match")
    for line in format_counter(stats["opportunity_settlement_source_matches"], top=top):
        print(line)

    print()
    print("Skip Attribution: Sources")
    for line in format_counter(stats["skip_sources"], top=top):
        print(line)

    print()
    print("Skip Attribution: Source Classes")
    for line in format_counter(stats["skip_source_classes"], top=top):
        print(line)

    print()
    print("Skip Attribution: Retrieval Modes")
    for line in format_counter(stats["skip_retrieval_modes"], top=top):
        print(line)

    print()
    print("Skip Attribution: Evidence IDs")
    for line in format_counter(stats["skip_evidence_ids"], top=top):
        print(line)

    print()
    print("Skip Attribution: Settlement-Source Match")
    for line in format_counter(stats["skip_settlement_source_matches"], top=top):
        print(line)

    print()
    print("Observed Event Types")
    for line in format_counter(stats["event_counts"], top=max(top, len(stats["event_counts"]))):
        print(line)


def main() -> int:
    args = parse_args()

    try:
        since_input = parse_utc_timestamp(args.since_utc) if args.since_utc else parse_date_start(args.since)
        until_input = parse_utc_timestamp(args.until_utc) if args.until_utc else parse_date_end(args.until)
    except ValueError as exc:
        raise SystemExit(f"Invalid time bound: {exc}") from exc

    since, until, _ = resolve_recent_window(since_input, until_input)

    if since and until and since > until:
        raise SystemExit("--since must be on or before --until")

    stats = summarize(Path(args.path), since, until, exclude_test=args.exclude_test)
    print_summary(stats, top=max(1, args.top), since=since, until=until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
