"""
Read-only diagnostics for headline-to-market match quality.

Uses MATCH_DIAGNOSTIC structured records to surface approximate low-quality
matches that may be semantically irrelevant but still pass the current matcher.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.market_matcher import (
    PAPER_MIN_MATCH_SCORE,
    _GEO_NAMED_ENTITIES,
    _match_quality_flags,
    _meaningful_tokens,
    _tokenize,
)
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "trades.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize market-match quality diagnostics")
    parser.add_argument("--path", default=str(DEFAULT_LOG_PATH), help="Path to trades.jsonl")
    parser.add_argument("--since", help="Inclusive start date in YYYY-MM-DD")
    parser.add_argument("--until", help="Inclusive end date in YYYY-MM-DD")
    parser.add_argument("--top", type=int, default=10, help="Max rows in grouped sections")
    parser.add_argument("--recent", type=int, default=10, help="Max example rows per section")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Retroactively evaluate recent signal-bearing events using the same approximate match-quality heuristics",
    )
    parser.add_argument(
        "--event-limit",
        type=int,
        default=100,
        help="Max backfilled signal-bearing events to evaluate (default: 100)",
    )
    parser.add_argument(
        "--exclude-test",
        action="store_true",
        help="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def parse_iso_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date_start(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return dt.replace(tzinfo=timezone.utc)


def parse_date_end(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return datetime.combine(dt.date(), time.max, tzinfo=timezone.utc)


def in_window(ts: datetime | None, since: datetime | None, until: datetime | None) -> bool:
    if ts is None:
        return since is None and until is None
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    return True


def is_test_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


def safe_bool(value: Any) -> bool:
    return value is True


def safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def fmt_ts(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fmt_ratio_distribution(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "n/a"
    buckets = {
        "<0.20": 0,
        "0.20-0.39": 0,
        "0.40-0.59": 0,
        ">=0.60": 0,
    }
    for row in rows:
        ratio = row.get("overlap_ratio")
        if ratio is None:
            continue
        if ratio < 0.20:
            buckets["<0.20"] += 1
        elif ratio < 0.40:
            buckets["0.20-0.39"] += 1
        elif ratio < 0.60:
            buckets["0.40-0.59"] += 1
        else:
            buckets[">=0.60"] += 1
    return ", ".join(f"{label}={count}" for label, count in buckets.items())


def compute_match_heuristics(headline: str, market_title: str, match_score: float | None = None) -> dict[str, Any]:
    headline_tokens = _meaningful_tokens(_tokenize(headline))
    market_title_tokens = _meaningful_tokens(_tokenize(market_title))
    overlap = headline_tokens & market_title_tokens
    geo_overlap = overlap & _GEO_NAMED_ENTITIES
    generic_overlap = overlap - _GEO_NAMED_ENTITIES
    effective_score = match_score if match_score is not None else PAPER_MIN_MATCH_SCORE + 1.0
    heuristic_flags = _match_quality_flags(
        overlap=overlap,
        geo_overlap=geo_overlap,
        generic_overlap=generic_overlap,
        headline_meaningful=headline_tokens,
        market_title_meaningful=market_title_tokens,
        score=effective_score,
        min_score=PAPER_MIN_MATCH_SCORE,
    )
    return {
        "matched_tokens": sorted(overlap),
        "token_overlap_count": len(overlap),
        "geo_overlap_count": len(geo_overlap),
        "generic_overlap_count": len(generic_overlap),
        "headline_token_count": len(headline_tokens),
        "market_title_token_count": len(market_title_tokens),
        "overlap_ratio": len(overlap) / max(1, min(len(headline_tokens), len(market_title_tokens))),
        "low_match_quality": bool(heuristic_flags),
        "heuristic_flags": heuristic_flags,
    }


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "match_records": 0,
        "low_quality_matches": 0,
        "by_source": Counter(),
        "by_ticker": Counter(),
        "heuristic_flags": Counter(),
        "examples_bad": [],
        "examples_good": [],
        "match_score_available": True,
    }
    all_rows: list[dict[str, Any]] = []

    if not path.exists():
        stats["rows"] = all_rows
        return stats

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            stats["lines_total"] += 1
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats["lines_malformed"] += 1
                continue
            if not isinstance(record, dict):
                stats["lines_malformed"] += 1
                continue

            ts = parse_iso_ts(record.get("ts"))
            if not in_window(ts, since, until):
                continue
            if exclude_test and is_test_record(record):
                continue

            stats["records_kept"] += 1
            if str(record.get("type") or "").strip() != "MATCH_DIAGNOSTIC":
                continue

            stats["match_records"] += 1
            low_quality = safe_bool(record.get("low_match_quality"))
            source = str(record.get("source") or "").strip()
            ticker = str(record.get("ticker") or "").strip()
            match_score = safe_float(record.get("match_score"))
            if match_score is None:
                stats["match_score_available"] = False

            row = {
                "ts": ts,
                "source": source,
                "headline": str(record.get("headline") or "").strip(),
                "ticker": ticker,
                "market_title": str(record.get("market_title") or "").strip(),
                "match_score": match_score,
                "matched_tokens": list(record.get("matched_tokens") or []),
                "token_overlap_count": int(record.get("token_overlap_count") or 0),
                "geo_overlap_count": int(record.get("geo_overlap_count") or 0),
                "generic_overlap_count": int(record.get("generic_overlap_count") or 0),
                "headline_token_count": int(record.get("headline_token_count") or 0),
                "market_title_token_count": int(record.get("market_title_token_count") or 0),
                "overlap_ratio": safe_float(record.get("overlap_ratio")),
                "low_match_quality": low_quality,
                "heuristic_flags": list(record.get("heuristic_flags") or []),
            }
            all_rows.append(row)
            if low_quality:
                stats["low_quality_matches"] += 1
                if source:
                    stats["by_source"][source] += 1
                if ticker:
                    stats["by_ticker"][ticker] += 1
            for flag in row["heuristic_flags"]:
                stats["heuristic_flags"][flag] += 1

    stats["rows"] = sorted(
        all_rows,
        key=lambda row: row["ts"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    bad_rows = [row for row in stats["rows"] if row["low_match_quality"]]
    good_rows = [row for row in stats["rows"] if not row["low_match_quality"]]
    stats["examples_bad"] = bad_rows
    stats["examples_good"] = good_rows
    return stats


def _attach_market_titles(
    rows: list[dict[str, Any]],
    title_by_ticker: dict[str, str],
    opp_by_ticker: dict[str, list[dict[str, Any]]],
) -> None:
    for row in rows:
        if row.get("market_title"):
            continue
        ticker = row.get("ticker") or ""
        if ticker in title_by_ticker:
            row["market_title"] = title_by_ticker[ticker]
            row["title_source"] = "ticker_map"
            continue
        ts = row.get("ts")
        candidates = opp_by_ticker.get(ticker, [])
        for opp in candidates:
            opp_ts = opp.get("ts")
            if ts is not None and opp_ts is not None and opp_ts < ts:
                row["market_title"] = opp["market_title"]
                row["title_source"] = "nearest_opportunity"
                break


def summarize_backfill(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    *,
    event_limit: int,
    exclude_test: bool = False,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "evaluated_events": 0,
        "low_quality_matches": 0,
        "rows": [],
        "rows_missing_title": 0,
        "rows_missing_score": 0,
        "by_source": [],
        "by_ticker": [],
    }
    if not path.exists():
        return stats

    detail_rows: list[dict[str, Any]] = []
    paper_rows: list[dict[str, Any]] = []
    skip_queues: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
    title_by_ticker: dict[str, str] = {}
    opp_by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            stats["lines_total"] += 1
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats["lines_malformed"] += 1
                continue
            if not isinstance(record, dict):
                stats["lines_malformed"] += 1
                continue

            ts = parse_iso_ts(record.get("ts"))
            if not in_window(ts, since, until):
                continue
            if exclude_test and is_test_record(record):
                continue
            stats["records_kept"] += 1
            event_type = str(record.get("type") or "").strip()

            if event_type == "OPPORTUNITY":
                ticker = str(record.get("ticker") or "").strip()
                market_title = str(record.get("market_title") or "").strip()
                if ticker and market_title:
                    title_by_ticker[ticker] = market_title
                    opp_by_ticker[ticker].append({"ts": ts, "market_title": market_title})
            elif event_type == "SKIPPED":
                ticker = str(record.get("ticker") or "").strip()
                headline = str(record.get("headline") or "").strip()
                if ticker and headline:
                    skip_queues[(ticker, headline)].append(
                        {
                            "reason": str(record.get("reason") or "").strip(),
                            "edge": safe_float(record.get("edge")),
                            "ts": ts,
                        }
                    )
            elif event_type == "SIGNAL_ANALYSIS_DETAIL":
                detail_rows.append(
                    {
                        "event_type": event_type,
                        "ts": ts,
                        "source": str(record.get("source") or "").strip(),
                        "headline": str(record.get("headline") or "").strip(),
                        "ticker": str(record.get("ticker") or "").strip(),
                        "market_title": "",
                        "title_source": "unavailable",
                        "match_score": None,
                        "edge": None,
                        "skip_reason": None,
                    }
                )
            elif event_type == "PAPER_TRADE":
                paper_rows.append(
                    {
                        "event_type": event_type,
                        "ts": ts,
                        "source": str(record.get("signal_source") or "").strip(),
                        "headline": str(record.get("signal_headline") or "").strip(),
                        "ticker": str(record.get("ticker") or "").strip(),
                        "market_title": str(record.get("market_title") or "").strip(),
                        "title_source": "paper_trade",
                        "match_score": None,
                        "edge": safe_float(record.get("edge")),
                        "skip_reason": None,
                    }
                )

    rows = sorted(detail_rows + paper_rows, key=lambda row: row["ts"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    rows = rows[: max(1, event_limit)]
    _attach_market_titles(rows, title_by_ticker, opp_by_ticker)

    evaluated_rows: list[dict[str, Any]] = []
    for row in rows:
        key = (row.get("ticker") or "", row.get("headline") or "")
        if key in skip_queues and skip_queues[key]:
            row["skip_reason"] = skip_queues[key][0]["reason"]
            if row["edge"] is None:
                row["edge"] = skip_queues[key][0]["edge"]
        if not row.get("market_title"):
            stats["rows_missing_title"] += 1
            continue

        heuristics = compute_match_heuristics(
            row["headline"],
            row["market_title"],
            match_score=row.get("match_score"),
        )
        if row.get("match_score") is None:
            stats["rows_missing_score"] += 1
        row.update(heuristics)
        evaluated_rows.append(row)

    stats["rows"] = evaluated_rows
    stats["evaluated_events"] = len(evaluated_rows)
    stats["low_quality_matches"] = sum(1 for row in evaluated_rows if row["low_match_quality"])

    source_totals: Counter[str] = Counter()
    source_low: Counter[str] = Counter()
    ticker_totals: Counter[str] = Counter()
    ticker_low: Counter[str] = Counter()
    for row in evaluated_rows:
        source = row.get("source") or ""
        ticker = row.get("ticker") or ""
        if source:
            source_totals[source] += 1
            if row["low_match_quality"]:
                source_low[source] += 1
        if ticker:
            ticker_totals[ticker] += 1
            if row["low_match_quality"]:
                ticker_low[ticker] += 1

    stats["by_source"] = sorted(
        [
            {
                "source": source,
                "events": source_totals[source],
                "low_quality": source_low[source],
                "low_quality_rate": source_low[source] / source_totals[source],
            }
            for source in source_totals
        ],
        key=lambda row: (-row["low_quality_rate"], -row["events"], row["source"].lower()),
    )
    stats["by_ticker"] = sorted(
        [
            {
                "ticker": ticker,
                "events": ticker_totals[ticker],
                "low_quality": ticker_low[ticker],
                "low_quality_rate": ticker_low[ticker] / ticker_totals[ticker],
            }
            for ticker in ticker_totals
        ],
        key=lambda row: (-row["low_quality_rate"], -row["events"], row["ticker"].lower()),
    )
    return stats


def format_counter(counter: Counter[str], top: int) -> list[str]:
    if not counter:
        return ["  (none)"]
    width = max(len(str(count)) for count in counter.values())
    return [f"  {count:>{width}}  {label}" for label, count in counter.most_common(top)]


def format_examples(rows: list[dict[str, Any]], top: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    lines = []
    for row in rows[:top]:
        tokens = ",".join(row["matched_tokens"]) if row["matched_tokens"] else "(none)"
        flags = ",".join(row["heuristic_flags"]) if row["heuristic_flags"] else "(none)"
        lines.append(
            "  "
            f"{fmt_ts(row['ts'])}  "
            f"score={fmt_float(row['match_score'])}  "
            f"overlap={row['token_overlap_count']}  "
            f"ratio={fmt_float(row['overlap_ratio'])}  "
            f"source={row['source'] or 'n/a'}  "
            f"ticker={row['ticker'] or 'n/a'}  "
            f"matched_tokens={tokens}  "
            f"flags={flags}  "
            f"headline={row['headline'][:70] if row['headline'] else 'n/a'}  "
            f"market={row['market_title'][:70] if row['market_title'] else 'n/a'}"
        )
    return lines


def print_summary(stats: dict[str, Any], *, top: int, recent: int, since: datetime | None, until: datetime | None) -> None:
    print("MATCH QUALITY DIAGNOSTICS")
    print(f"Path: {stats['path']}")
    if since or until:
        since_text = since.date().isoformat() if since else "(beginning)"
        until_text = until.date().isoformat() if until else "(latest)"
        print(f"Date range: {since_text} -> {until_text}")
    print(f"Lines read: {stats['lines_total']}")
    print(f"Malformed lines skipped: {stats['lines_malformed']}")
    print(f"Records included: {stats['records_kept']}")

    if stats["match_records"] == 0:
        print()
        print("No MATCH_DIAGNOSTIC records found.")
        print("This report only reflects runtime windows after match diagnostics logging was enabled.")
        return

    low_pct = stats["low_quality_matches"] / stats["match_records"] if stats["match_records"] else None

    print()
    print("Attribution Notes")
    print("  Match quality heuristics are approximate and non-blocking; they do not affect runtime behavior.")
    if stats["match_score_available"]:
        print("  Match score is taken directly from MATCH_DIAGNOSTIC records.")
    else:
        print("  Match score was missing on some records; percentages remain reliable, but score examples may be incomplete.")

    print()
    print("Summary")
    print(f"  Total matched candidates     : {stats['match_records']}")
    print(f"  Low-quality flagged matches  : {stats['low_quality_matches']} ({fmt_pct(low_pct)})")

    print()
    print("Low-Quality Match Flags")
    for line in format_counter(stats["heuristic_flags"], top=max(top, len(stats["heuristic_flags"]))):
        print(line)

    print()
    print(f"Low-Quality Matches by Source (top {top})")
    for line in format_counter(stats["by_source"], top):
        print(line)

    print()
    print(f"Low-Quality Matches by Ticker (top {top})")
    for line in format_counter(stats["by_ticker"], top):
        print(line)

    print()
    print(f"Recent Low-Quality Examples (top {recent})")
    for line in format_examples(stats["examples_bad"], recent):
        print(line)

    print()
    print(f"Recent Higher-Quality Examples (top {recent})")
    for line in format_examples(stats["examples_good"], recent):
        print(line)


def format_backfill_examples(rows: list[dict[str, Any]], top: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    lines = []
    for row in rows[:top]:
        tokens = ",".join(row["matched_tokens"]) if row["matched_tokens"] else "(none)"
        flags = ",".join(row["heuristic_flags"]) if row["heuristic_flags"] else "(none)"
        lines.append(
            "  "
            f"{fmt_ts(row['ts'])}  "
            f"source={row['source'] or 'n/a'}  "
            f"ticker={row['ticker'] or 'n/a'}  "
            f"overlap={row['token_overlap_count']}  "
            f"ratio={fmt_float(row['overlap_ratio'])}  "
            f"edge={fmt_float(row.get('edge'))}  "
            f"skip_reason={(row.get('skip_reason') or 'n/a')[:50]}  "
            f"matched_tokens={tokens}  "
            f"flags={flags}  "
            f"headline={row['headline'][:60] if row['headline'] else 'n/a'}  "
            f"market={row['market_title'][:60] if row['market_title'] else 'n/a'}"
        )
    return lines


def format_backfill_groups(rows: list[dict[str, Any]], label: str, top: int) -> list[str]:
    if not rows:
        return ["  (none)"]
    shown = rows[:top]
    name_width = max(len(str(row[label])) for row in shown)
    event_width = max(len(str(row["events"])) for row in shown)
    low_width = max(len(str(row["low_quality"])) for row in shown)
    return [
        "  "
        f"{row[label]:<{name_width}}  "
        f"events={row['events']:>{event_width}}  "
        f"low_quality={row['low_quality']:>{low_width}}  "
        f"low_quality_rate={fmt_pct(row['low_quality_rate'])}"
        for row in shown
    ]


def print_backfill_summary(
    stats: dict[str, Any],
    *,
    top: int,
    recent: int,
    event_limit: int,
    since: datetime | None,
    until: datetime | None,
) -> None:
    print("MATCH QUALITY BACKFILL")
    print(f"Path: {stats['path']}")
    if since or until:
        since_text = since.date().isoformat() if since else "(beginning)"
        until_text = until.date().isoformat() if until else "(latest)"
        print(f"Date range: {since_text} -> {until_text}")
    print(f"Lines read: {stats['lines_total']}")
    print(f"Malformed lines skipped: {stats['lines_malformed']}")
    print(f"Records included: {stats['records_kept']}")
    print(f"Signal-bearing event limit: {event_limit}")

    print()
    print("Attribution Notes")
    print("  This is a read-only backfill using existing signal-bearing records and the same approximate overlap heuristics as runtime match diagnostics.")
    print("  Historical match score is usually unavailable, so backfilled low-quality flags rely on overlap/entity heuristics only.")
    print("  Market titles are reconstructed from PAPER_TRADE or OPPORTUNITY records when available; rows without a recoverable title are excluded.")
    print(f"  Rows excluded for missing market title: {stats['rows_missing_title']}")

    if stats["evaluated_events"] == 0:
        print()
        print("No backfillable signal-bearing rows with recoverable market titles were found.")
        return

    low_rows = [row for row in stats["rows"] if row["low_match_quality"]]
    high_rows = [row for row in stats["rows"] if not row["low_match_quality"]]
    low_edges = [row["edge"] for row in low_rows if isinstance(row.get("edge"), (int, float))]
    high_edges = [row["edge"] for row in high_rows if isinstance(row.get("edge"), (int, float))]
    avg_overlap = sum(row["overlap_ratio"] for row in stats["rows"]) / len(stats["rows"])

    print()
    print("Summary")
    print(f"  Total evaluated events       : {stats['evaluated_events']}")
    print(f"  Low-quality flagged matches  : {stats['low_quality_matches']} ({fmt_pct(stats['low_quality_matches'] / stats['evaluated_events'])})")
    print(f"  Average overlap ratio        : {fmt_float(avg_overlap)}")
    print(f"  Overlap ratio distribution   : {fmt_ratio_distribution(stats['rows'])}")

    print()
    print("Edge vs Match Quality")
    print(f"  Avg edge, low-quality        : {fmt_float(sum(low_edges) / len(low_edges) if low_edges else None)}")
    print(f"  Avg edge, med/high-quality   : {fmt_float(sum(high_edges) / len(high_edges) if high_edges else None)}")

    print()
    print(f"Top Low-Quality Examples (top {recent})")
    for line in format_backfill_examples(low_rows, recent):
        print(line)

    print()
    print(f"Top Higher-Quality Examples (top {recent})")
    for line in format_backfill_examples(high_rows, recent):
        print(line)

    print()
    print(f"Low-Quality Rate by Source (top {top})")
    for line in format_backfill_groups(stats["by_source"], "source", top):
        print(line)

    print()
    print(f"Low-Quality Rate by Ticker (top {top})")
    for line in format_backfill_groups(stats["by_ticker"], "ticker", top):
        print(line)


def main() -> int:
    args = parse_args()
    try:
        since = parse_date_start(args.since)
        until = parse_date_end(args.until)
    except ValueError as exc:
        raise SystemExit(f"Invalid date: {exc}") from exc
    if since and until and since > until:
        raise SystemExit("--since must be on or before --until")

    path = Path(args.path)
    if args.backfill:
        stats = summarize_backfill(
            path,
            since,
            until,
            event_limit=max(1, args.event_limit),
            exclude_test=args.exclude_test,
        )
        print_backfill_summary(
            stats,
            top=max(1, args.top),
            recent=max(1, args.recent),
            event_limit=max(1, args.event_limit),
            since=since,
            until=until,
        )
    else:
        stats = summarize(path, since, until, exclude_test=args.exclude_test)
        print_summary(stats, top=max(1, args.top), recent=max(1, args.recent), since=since, until=until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
