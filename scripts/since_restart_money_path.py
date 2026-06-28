"""Offline since-restart money-path report.

Joins restart-window JSONL telemetry into candidate chains:
OPPORTUNITY -> GATE_SUMMARY -> BLEND_DECISION -> SKIPPED/PAPER_TRADE/LIVE_ORDER.
The script is read-only: it only reads JSONL/app-log inputs and prints a report.
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CHAIN_TYPES = {
    "OPPORTUNITY",
    "GATE_SUMMARY",
    "BLEND_DECISION",
    "SKIPPED",
    "PAPER_TRADE",
    "LIVE_ORDER",
    "ANALYSIS_REJECTED",
    "PAPER_RESOLUTION",
    "MATCH_LLM_REVIEW",
}
TERMINAL_TYPES = {"SKIPPED", "PAPER_TRADE", "LIVE_ORDER"}
G6_REASON = "G6_recency_score"
POLYMARKET_FEEDBACK_MIN_RESOLVED = 10


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_app_log_ts(line: str) -> datetime | None:
    if len(line) < 23:
        return None
    raw = line[:23]
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S,%f").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def _event_type(record: dict[str, Any]) -> str | None:
    value = record.get("type") or record.get("event") or record.get("event_type")
    return value if isinstance(value, str) else None


def _record_ts(record: dict[str, Any]) -> datetime | None:
    for key in ("ts", "timestamp", "time", "created_at"):
        parsed = parse_timestamp(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _ticker(record: dict[str, Any]) -> str | None:
    for key in ("ticker", "market_ticker", "market", "symbol"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0]
    return None


def _source(record: dict[str, Any]) -> str | None:
    for key in ("source", "source_name", "provider"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _reason(record: dict[str, Any]) -> str | None:
    for key in ("reason", "skip_reason", "rejection_reason", "failed_gate"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _failed_gates(record: dict[str, Any] | None) -> list[str]:
    if not record:
        return []
    values: list[str] = []
    for key in ("failed_gates", "failures", "failed_gate", "failing_gate", "reason"):
        value = record.get(key)
        if isinstance(value, str) and value:
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str) and item)
    gate_chain = record.get("gate_chain")
    if isinstance(gate_chain, list):
        for item in gate_chain:
            if not isinstance(item, str) or "FAIL" not in item:
                continue
            gate = item.split(":", 1)[0].strip()
            if gate:
                values.append(gate)
    return values


def _gate_passed(record: dict[str, Any] | None, failed: list[str]) -> bool | None:
    if not record:
        return None
    value = record.get("passed")
    if isinstance(value, bool):
        return value
    if failed:
        return False
    binding = record.get("binding_constraint")
    if binding == "passed":
        return True
    if isinstance(binding, str) and binding:
        return False
    gate_chain = record.get("gate_chain")
    if isinstance(gate_chain, list) and gate_chain:
        return True
    return None


def _first_present(records: Iterable[dict[str, Any] | None], key: str) -> Any:
    for record in records:
        if record and record.get(key) is not None:
            return record[key]
    return None


def _edge(record: dict[str, Any] | None, keys: tuple[str, ...]) -> Any:
    if not record:
        return None
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def _iter_jsonl_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    for candidate in sorted(path.rglob("*")):
        if candidate.is_file() and candidate.name.endswith((".jsonl", ".jsonl.gz")):
            yield candidate


def _iter_records(path: Path) -> Iterable[dict[str, Any]]:
    for file_path in _iter_jsonl_files(path):
        opener = gzip.open if file_path.name.endswith(".gz") else open
        try:
            with opener(file_path, "rt", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        yield record
        except OSError:
            continue


def _within(ts: datetime, since: datetime, until: datetime | None) -> bool:
    if ts < since:
        return False
    return until is None or ts <= until


def _windowed_events(
    path: Path, since: datetime, until: datetime | None
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for sequence, record in enumerate(_iter_records(path)):
        event_type = _event_type(record)
        ts = _record_ts(record)
        if event_type not in CHAIN_TYPES or ts is None or not _within(ts, since, until):
            continue
        events.append({
            "sequence": sequence,
            "type": event_type,
            "ts": ts,
            "ticker": _ticker(record),
            "raw": record,
        })
    events.sort(key=lambda event: (event["ts"], event["sequence"]))
    return events


def _nearest_later(
    events: Iterable[dict[str, Any]],
    *,
    ticker: str | None,
    after_event: dict[str, Any],
    types: set[str],
    used_sequences: set[int],
) -> dict[str, Any] | None:
    for event in events:
        if event["sequence"] in used_sequences:
            continue
        if event["type"] not in types:
            continue
        if (
            event["ts"],
            event["sequence"],
        ) <= (after_event["ts"], after_event["sequence"]):
            continue
        if ticker is not None and event["ticker"] != ticker:
            continue
        return event
    return None


def _candidate_row(
    opportunity: dict[str, Any],
    events: list[dict[str, Any]],
    used_sequences: set[int],
) -> dict[str, Any]:
    ticker = opportunity["ticker"]
    gate = _nearest_later(
        events,
        ticker=ticker,
        after_event=opportunity,
        types={"GATE_SUMMARY"},
        used_sequences=used_sequences,
    )
    if gate:
        used_sequences.add(gate["sequence"])
    blend_after = gate or opportunity
    blend = _nearest_later(
        events,
        ticker=ticker,
        after_event=blend_after,
        types={"BLEND_DECISION"},
        used_sequences=used_sequences,
    )
    if blend:
        used_sequences.add(blend["sequence"])
    terminal_after = blend or blend_after
    terminal = _nearest_later(
        events,
        ticker=ticker,
        after_event=terminal_after,
        types=TERMINAL_TYPES,
        used_sequences=used_sequences,
    )
    if terminal:
        used_sequences.add(terminal["sequence"])

    raw_opp = opportunity["raw"]
    raw_gate = gate["raw"] if gate else None
    raw_blend = blend["raw"] if blend else None
    raw_terminal = terminal["raw"] if terminal else None
    failed = _failed_gates(raw_gate)
    terminal_reason = _reason(raw_terminal or {})
    g6_records = (raw_gate, raw_blend, raw_terminal, raw_opp)
    recency_score = _first_present(g6_records, "recency_score")
    recency_threshold = _first_present(g6_records, "recency_threshold")
    recency_distance = _first_present(g6_records, "recency_distance")
    if recency_distance is None and recency_score is not None and recency_threshold is not None:
        try:
            recency_distance = float(recency_score) - float(recency_threshold)
        except (TypeError, ValueError):
            recency_distance = None

    g6_reason = terminal_reason == G6_REASON or G6_REASON in failed
    measurement_gap = bool(
        g6_reason
        and recency_score is None
        and recency_threshold is None
        and recency_distance is None
    )

    return {
        "opportunity_ts": _iso(opportunity["ts"]),
        "ticker": ticker,
        "source": _source(raw_opp),
        "opportunity_edge": _edge(raw_opp, ("edge", "observed_edge", "raw_edge")),
        "gate_ts": _iso(gate["ts"]) if gate else None,
        "gate_passed": _gate_passed(raw_gate, failed),
        "gate_failed": failed,
        "blend_ts": _iso(blend["ts"]) if blend else None,
        "blend_venue": raw_blend.get("venue") if raw_blend else None,
        "readiness_edge": _edge(
            raw_blend, ("readiness_edge", "executed_edge", "edge", "raw_edge")
        ),
        "terminal_ts": _iso(terminal["ts"]) if terminal else None,
        "terminal_type": terminal["type"] if terminal else None,
        "terminal_venue": raw_terminal.get("venue") if raw_terminal else None,
        "terminal_reason": terminal_reason,
        "recency_score": recency_score,
        "recency_threshold": recency_threshold,
        "recency_distance": recency_distance,
        "measurement_gap": measurement_gap,
    }


def _summarize_no_keywords(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_source: Counter[str] = Counter()
    tickers: Counter[str] = Counter()
    count = 0
    for event in events:
        if event["type"] != "ANALYSIS_REJECTED":
            continue
        raw = event["raw"]
        if _reason(raw) != "no_keywords":
            continue
        count += 1
        by_source[_source(raw) or "UNKNOWN"] += 1
        tickers[event["ticker"] or "UNKNOWN"] += 1
    return {
        "count": count,
        "by_source": dict(sorted(by_source.items())),
        "tickers": dict(sorted(tickers.items())),
    }


def _iter_app_log_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    for candidate in sorted(path.rglob("*")):
        if candidate.is_file() and candidate.suffix in {".log", ".txt"}:
            yield candidate


def _summarize_app_warnings(
    path: Path | None, since: datetime, until: datetime | None
) -> dict[str, Any]:
    summary = {"markets_not_found": {"count": 0, "examples": []}}
    if path is None:
        return summary
    examples: list[str] = []
    count = 0
    for file_path in _iter_app_log_files(path):
        try:
            with open(file_path, encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.rstrip("\n")
                    if "Markets not found" not in line:
                        continue
                    ts = _parse_app_log_ts(line)
                    if ts is not None and not _within(ts, since, until):
                        continue
                    count += 1
                    if len(examples) < 5:
                        examples.append(line)
        except OSError:
            continue
    summary["markets_not_found"] = {"count": count, "examples": examples}
    return summary


def _is_polymarket(record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    venue = record.get("venue")
    if venue == "polymarket_us":
        return True
    ticker = _ticker(record)
    return isinstance(ticker, str) and ticker.startswith("PM-")


def _trade_id(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    value = record.get("trade_id") or record.get("id")
    return value if isinstance(value, str) and value else None


def _find_later_event(
    events: Iterable[dict[str, Any]],
    *,
    ticker: str | None,
    trade_id: str | None,
    after_event: dict[str, Any],
    event_type: str,
) -> dict[str, Any] | None:
    for event in events:
        if event["type"] != event_type:
            continue
        if (
            event["ts"],
            event["sequence"],
        ) <= (after_event["ts"], after_event["sequence"]):
            continue
        raw = event["raw"]
        if trade_id and _trade_id(raw) == trade_id:
            return event
        if ticker is not None and event["ticker"] == ticker:
            return event
    return None


def _summarize_polymarket_settlement_feedback(
    events: list[dict[str, Any]],
    *,
    min_resolved: int = POLYMARKET_FEEDBACK_MIN_RESOLVED,
) -> dict[str, Any]:
    proof_rows: list[dict[str, Any]] = []
    for event in events:
        if event["type"] != "PAPER_TRADE" or not _is_polymarket(event["raw"]):
            continue
        ticker = event["ticker"]
        trade_id = _trade_id(event["raw"])
        resolution = _find_later_event(
            events,
            ticker=ticker,
            trade_id=trade_id,
            after_event=event,
            event_type="PAPER_RESOLUTION",
        )
        if resolution is None or not _is_polymarket(resolution["raw"]):
            continue
        feedback = _find_later_event(
            events,
            ticker=ticker,
            trade_id=None,
            after_event=resolution,
            event_type="MATCH_LLM_REVIEW",
        )
        raw_resolution = resolution["raw"]
        raw_feedback = feedback["raw"] if feedback else {}
        proof_rows.append({
            "ticker": ticker,
            "trade_id": trade_id,
            "paper_trade_ts": _iso(event["ts"]),
            "resolution_ts": _iso(resolution["ts"]),
            "pnl_dollars": raw_resolution.get("pnl_dollars"),
            "outcome": raw_resolution.get("outcome") or raw_resolution.get("result"),
            "feedback_ts": _iso(feedback["ts"]) if feedback else None,
            "market_prefix": raw_feedback.get("market_prefix"),
        })
    resolved_count = len(proof_rows)
    return {
        "status": "sufficient_sample"
        if resolved_count >= min_resolved
        else "insufficient_sample",
        "resolved_count": resolved_count,
        "min_resolved_required": min_resolved,
        "proof_rows": proof_rows,
    }


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _summarize_legacy_resolution_gap(
    path: Path,
    *,
    process_start: datetime | None,
    log_boot: datetime | None,
) -> dict[str, Any]:
    if process_start is None or log_boot is None or process_start >= log_boot:
        return {"count": 0, "pnl_total": 0.0, "tickers": []}
    events = _windowed_events(path, process_start, log_boot)
    rows = [event for event in events if event["type"] == "PAPER_RESOLUTION"]
    pnl_total = 0.0
    tickers: list[str] = []
    for event in rows:
        pnl = _safe_float(event["raw"].get("pnl_dollars"))
        if pnl is not None:
            pnl_total += pnl
        if event["ticker"]:
            tickers.append(event["ticker"])
    return {
        "count": len(rows),
        "pnl_total": pnl_total,
        "tickers": tickers[:10],
    }


def build_money_path_report(
    jsonl_path: str | Path,
    *,
    since: str | datetime,
    until: str | datetime | None = None,
    process_start: str | datetime | None = None,
    log_boot: str | datetime | None = None,
    app_log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a read-only restart-window money-path report."""

    since_dt = parse_timestamp(since) if isinstance(since, str) else since
    until_dt = parse_timestamp(until) if isinstance(until, str) else until
    if since_dt is None:
        raise ValueError("since must be an ISO timestamp")
    if until is not None and until_dt is None:
        raise ValueError("until must be an ISO timestamp")
    process_start_dt = (
        parse_timestamp(process_start) if isinstance(process_start, str) else process_start
    )
    log_boot_dt = parse_timestamp(log_boot) if isinstance(log_boot, str) else log_boot
    since_dt = since_dt.astimezone(timezone.utc)
    until_dt = until_dt.astimezone(timezone.utc) if until_dt else None
    process_start_dt = process_start_dt.astimezone(timezone.utc) if process_start_dt else None
    log_boot_dt = log_boot_dt.astimezone(timezone.utc) if log_boot_dt else None

    path = Path(jsonl_path)
    events = _windowed_events(path, since_dt, until_dt)
    used_sequences: set[int] = set()
    candidates = [
        _candidate_row(event, events, used_sequences)
        for event in events
        if event["type"] == "OPPORTUNITY"
    ]
    terminal_counts = Counter(
        row["terminal_type"] for row in candidates if row["terminal_type"] is not None
    )
    app_path = Path(app_log_path) if app_log_path is not None else None
    return {
        "window": {"since": _iso(since_dt), "until": _iso(until_dt)},
        "boundaries": {
            "process_start_utc": _iso(process_start_dt),
            "log_boot_utc": _iso(log_boot_dt),
        },
        "summary": {
            "candidates": len(candidates),
            "terminal_counts": dict(sorted(terminal_counts.items())),
            "measurement_gaps": sum(1 for row in candidates if row["measurement_gap"]),
        },
        "candidates": candidates,
        "legacy_resolutions_between_process_start_and_log_boot": _summarize_legacy_resolution_gap(
            path,
            process_start=process_start_dt,
            log_boot=log_boot_dt,
        ),
        "no_keywords": _summarize_no_keywords(events),
        "polymarket_settlement_feedback": _summarize_polymarket_settlement_feedback(
            events
        ),
        "app_warnings": _summarize_app_warnings(app_path, since_dt, until_dt),
    }


def format_text_report(report: dict[str, Any]) -> str:
    lines = [
        "Since-restart money path",
        f"Window: {report['window']['since']} -> {report['window']['until'] or 'open'}",
        f"Candidates: {report['summary']['candidates']}",
    ]
    boundaries = report.get("boundaries") or {}
    if boundaries.get("process_start_utc") or boundaries.get("log_boot_utc"):
        lines.append(f"Process-start boundary: {boundaries.get('process_start_utc') or 'unknown'}")
        lines.append(f"Latest log-boot boundary: {boundaries.get('log_boot_utc') or 'unknown'}")
    gap = report.get("legacy_resolutions_between_process_start_and_log_boot") or {}
    if gap.get("count", 0):
        lines.append(
            "Legacy resolutions before latest boot: "
            f"{gap.get('count', 0)}, pnl={gap.get('pnl_total')}"
        )
    terminal_counts = report["summary"]["terminal_counts"]
    terminal_text = ", ".join(f"{key}={value}" for key, value in terminal_counts.items())
    lines.append(f"Terminal: {terminal_text or 'none'}")
    lines.append(f"G6 measurement gaps: {report['summary']['measurement_gaps']}")
    no_keywords = report["no_keywords"]
    lines.append(f"no_keywords: {no_keywords['count']}")
    pm_feedback = report["polymarket_settlement_feedback"]
    lines.append(
        "Polymarket settlement feedback: "
        f"{pm_feedback['status']} "
        f"({pm_feedback['resolved_count']}/"
        f"{pm_feedback['min_resolved_required']} resolved)"
    )
    proof_rows = pm_feedback.get("proof_rows", []) or []
    if proof_rows:
        lines.append("Polymarket settlement proof rows:")
        for row in proof_rows[:5]:
            lines.append(
                "  "
                f"{row.get('ticker') or 'n/a'} "
                f"trade_id={row.get('trade_id') or 'n/a'} "
                f"pnl={row.get('pnl_dollars')} "
                f"feedback={row.get('feedback_ts') or 'none'} "
                f"prefix={row.get('market_prefix') or 'none'}"
            )
    markets_missing = report["app_warnings"]["markets_not_found"]
    lines.append(f"Markets not found warnings: {markets_missing['count']}")
    if report["candidates"]:
        lines.append("")
        lines.append("Candidates:")
    for row in report["candidates"]:
        reason = row["terminal_reason"] or "none"
        terminal = row["terminal_type"] or "none"
        venue = row["terminal_venue"] or row["blend_venue"] or "unknown"
        gap = str(row["measurement_gap"]).lower()
        lines.append(
            f"- {row['opportunity_ts']} {row['ticker']} terminal={terminal} "
            f"venue={venue} reason={reason} measurement_gap={gap}"
        )
    return "\n".join(lines) + "\n"


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl_path", type=Path, help="JSONL file or directory tree")
    parser.add_argument("--since", required=True, help="inclusive ISO timestamp")
    parser.add_argument("--until", help="inclusive ISO timestamp")
    parser.add_argument("--app-log", type=Path, help="optional app log file or directory")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    report = build_money_path_report(
        args.jsonl_path,
        since=args.since,
        until=args.until,
        app_log_path=args.app_log,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_text_report(report), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
