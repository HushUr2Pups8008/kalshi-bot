"""
Read-only keyword feedback audit for missed keyword-gate events.

This script mines recent ANALYSIS_REJECTED(reason=no_keywords) records from
logs/trades/trades.jsonl and surfaces repeated headline phrases as candidate
keyword additions for human review.

Notes:
  - Candidate phrases are heuristic only and are not auto-promoted.
  - The primary miss corpus is ANALYSIS_REJECTED(reason=no_keywords).
  - SIGNAL_ANALYSIS_DETAIL(method=keyword_gate, keywords=[]) is counted only as
    corroborating context, not as the primary mining corpus, to avoid double
    counting.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades" / "trades.jsonl"
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9']+")
STOPWORDS = {
    "a",
    "after",
    "again",
    "all",
    "amid",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "before",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "here",
    "his",
    "in",
    "into",
    "is",
    "it",
    "its",
    "latest",
    "live",
    "more",
    "new",
    "not",
    "of",
    "on",
    "or",
    "over",
    "says",
    "said",
    "that",
    "the",
    "their",
    "this",
    "to",
    "updates",
    "was",
    "what",
    "when",
    "where",
    "who",
    "why",
    "will",
    "with",
}
GENERIC_RISK_TOKENS = {
    "deal",
    "dealmaker",
    "fails",
    "fight",
    "fights",
    "latest",
    "live",
    "news",
    "peace",
    "plan",
    "plans",
    "talk",
    "talks",
    "updates",
    "watch",
}
NAMED_ENTITY_TOKENS = {
    "america",
    "beirut",
    "china",
    "donald",
    "hormuz",
    "iran",
    "iranian",
    "israel",
    "marco",
    "netanyahu",
    "rubio",
    "russia",
    "russian",
    "trump",
    "ukraine",
    "ukrainian",
    "zelensky",
}
EVENT_SPECIFIC_TOKENS = {
    "agreement",
    "blockade",
    "ceasefire",
    "closure",
    "collapse",
    "collapsed",
    "deal",
    "deadline",
    "fails",
    "hormuz",
    "missile",
    "nuclear",
    "ports",
    "resign",
    "resume",
    "sanctions",
    "ship",
    "ships",
    "strait",
    "strike",
    "tariff",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine keyword-gate misses for candidate phrases")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_LOG_PATH),
        help="Path to trades.jsonl (default: logs/trades/trades.jsonl)",
    )
    parser.add_argument("--since", help="Inclusive start date in YYYY-MM-DD")
    parser.add_argument("--until", help="Inclusive end date in YYYY-MM-DD")
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Max candidate phrases to show (default: 20)",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=2,
        help="Minimum miss count required for a phrase to be shown (default: 2)",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=3,
        help="Example headlines to show per phrase (default: 3)",
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
    source = str(record.get("source") or record.get("signal_source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


def tokenize_headline(headline: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall((headline or "").lower()):
        if len(token) < 3 or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def iter_candidate_phrases(headline: str) -> set[str]:
    tokens = tokenize_headline(headline)
    phrases: set[str] = set()
    for size in (2, 3):
        for idx in range(len(tokens) - size + 1):
            phrase_tokens = tokens[idx : idx + size]
            if any(len(token) < 3 for token in phrase_tokens):
                continue
            phrases.add(" ".join(phrase_tokens))
    return phrases


def phrase_risk_flags(phrase: str) -> list[str]:
    tokens = phrase.split()
    flags: list[str] = []
    generic_hits = [token for token in tokens if token in GENERIC_RISK_TOKENS]
    entity_hits = [token for token in tokens if token in NAMED_ENTITY_TOKENS]
    specific_hits = [token for token in tokens if token in EVENT_SPECIFIC_TOKENS]
    if generic_hits:
        flags.append("generic_terms")
    generic_count = sum(token in GENERIC_RISK_TOKENS for token in tokens)
    if generic_count == len(tokens) or (len(tokens) >= 3 and generic_count >= len(tokens) - 1):
        flags.append("broad_false_positive_risk")
    if all(token.isalpha() and len(token) <= 4 for token in tokens):
        flags.append("short_phrase")
    if entity_hits and not specific_hits and (len(set(entity_hits)) == 1 or len(tokens) <= 2):
        flags.append("overly_broad_named_entity")
    if len(set(tokens) & (NAMED_ENTITY_TOKENS | EVENT_SPECIFIC_TOKENS)) <= 1:
        flags.append("low_specificity")
    return flags


def phrase_category(phrase: str, risk_flags: list[str]) -> str:
    tokens = phrase.split()
    entity_hits = [token for token in tokens if token in NAMED_ENTITY_TOKENS]
    specific_hits = [token for token in tokens if token in EVENT_SPECIFIC_TOKENS]
    generic_hits = [token for token in tokens if token in GENERIC_RISK_TOKENS]

    if specific_hits and "broad_false_positive_risk" not in risk_flags:
        return "specific event phrase"
    if "broad_false_positive_risk" in risk_flags or (
        "generic_terms" in risk_flags and "low_specificity" in risk_flags and not specific_hits
    ):
        return "generic / high false-positive risk"
    if entity_hits:
        return "named-entity phrase"
    if generic_hits or "low_specificity" in risk_flags:
        return "broad geopolitical phrase"
    return "broad geopolitical phrase"


def candidate_score(
    *,
    count: int,
    source_count: int,
    ticker_count: int,
    category: str,
    risk_flags: list[str],
) -> int:
    score = count * 4 + source_count * 2 + ticker_count * 2
    if category == "specific event phrase":
        score += 8
    elif category == "named-entity phrase":
        score += 4
    elif category == "broad geopolitical phrase":
        score += 1

    penalty_map = {
        "generic_terms": 3,
        "broad_false_positive_risk": 8,
        "short_phrase": 2,
        "overly_broad_named_entity": 4,
        "low_specificity": 5,
    }
    score -= sum(penalty_map.get(flag, 0) for flag in risk_flags)
    return score


def review_bucket(category: str, risk_flags: list[str], score: int) -> str:
    if category == "specific event phrase" and "broad_false_positive_risk" not in risk_flags and score >= 10:
        return "strongest specific candidates"
    if category == "generic / high false-positive risk" or "broad_false_positive_risk" in risk_flags or score < 5:
        return "likely reject / too broad"
    return "watchlist / ambiguous candidates"


def phrase_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -row["candidate_score"],
        -row["count"],
        -len(row["sources"]),
        -len(row["tickers"]),
        len(row["risk_flags"]),
        row["phrase"],
    )


def summarize(path: Path, since: datetime | None, until: datetime | None, exclude_test: bool = False) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "no_keyword_misses": 0,
        "corroborating_keyword_gate_records": 0,
        "phrases": [],
        "grouped_phrases": {},
    }

    phrase_hits: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "phrase": "",
            "count": 0,
            "examples": [],
            "sources": set(),
            "tickers": set(),
        }
    )

    if not path.exists():
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

            event_type = str(record.get("type") or "").strip()
            if event_type == "SIGNAL_ANALYSIS_DETAIL":
                method = str(record.get("method") or "").strip()
                keywords = record.get("keywords")
                if method == "keyword_gate" and isinstance(keywords, list) and not keywords:
                    stats["corroborating_keyword_gate_records"] += 1

            if event_type != "ANALYSIS_REJECTED" or record.get("reason") != "no_keywords":
                continue

            headline = str(record.get("headline") or "").strip()
            source = str(record.get("source") or "").strip()
            ticker = str(record.get("ticker") or "").strip()
            if not headline:
                continue

            stats["no_keyword_misses"] += 1
            for phrase in iter_candidate_phrases(headline):
                row = phrase_hits[phrase]
                row["phrase"] = phrase
                row["count"] += 1
                row["sources"].add(source)
                if ticker:
                    row["tickers"].add(ticker)
                if headline not in row["examples"] and len(row["examples"]) < 5:
                    row["examples"].append(headline)

    phrase_rows: list[dict[str, Any]] = []
    for row in phrase_hits.values():
        risk_flags = phrase_risk_flags(row["phrase"])
        category = phrase_category(row["phrase"], risk_flags)
        score = candidate_score(
            count=row["count"],
            source_count=len(row["sources"]),
            ticker_count=len(row["tickers"]),
            category=category,
            risk_flags=risk_flags,
        )
        phrase_rows.append(
            {
                "phrase": row["phrase"],
                "count": row["count"],
                "examples": row["examples"],
                "sources": sorted(source for source in row["sources"] if source),
                "tickers": sorted(row["tickers"]),
                "risk_flags": risk_flags,
                "category": category,
                "candidate_score": score,
                "review_bucket": review_bucket(category, risk_flags, score),
            }
        )

    phrase_rows.sort(key=phrase_sort_key)
    stats["phrases"] = phrase_rows
    stats["unique_candidate_phrases"] = len(phrase_rows)
    grouped: dict[str, list[dict[str, Any]]] = {
        "strongest specific candidates": [],
        "watchlist / ambiguous candidates": [],
        "likely reject / too broad": [],
    }
    for row in phrase_rows:
        grouped[row["review_bucket"]].append(row)
    stats["grouped_phrases"] = grouped
    return stats


def format_phrase_row(row: dict[str, Any], max_examples: int) -> list[str]:
    risk = ", ".join(row["risk_flags"]) if row["risk_flags"] else "(none)"
    lines = [
        f"  {row['phrase']}  score={row['candidate_score']}  count={row['count']}  category={row['category']}  sources={len(row['sources'])}  tickers={len(row['tickers'])}  risk={risk}"
    ]
    if row["sources"]:
        lines.append(f"    sources: {', '.join(row['sources'])}")
    if row["tickers"]:
        lines.append(f"    tickers: {', '.join(row['tickers'][:5])}")
    for example in row["examples"][:max_examples]:
        lines.append(f"    example: {example}")
    return lines


def print_summary(stats: dict[str, Any], top: int, min_count: int, max_examples: int, since: datetime | None, until: datetime | None) -> None:
    print("KEYWORD FEEDBACK AUDIT")
    print(f"Path: {stats['path']}")
    if since or until:
        print(
            "Window: "
            f"{since.isoformat() if since else '-inf'} .. {until.isoformat() if until else '+inf'}"
        )
    print(f"Lines read: {stats['lines_total']}")
    print(f"Malformed lines skipped: {stats['lines_malformed']}")
    print(f"Records included: {stats['records_kept']}")
    print()
    print("Available Miss Corpus")
    print("  Primary corpus: ANALYSIS_REJECTED(reason=no_keywords)")
    print("  Corroborating context: SIGNAL_ANALYSIS_DETAIL(method=keyword_gate, keywords=[])")
    print("  Reliable fields: headline, source, ticker, ts, reason, match_score")
    print("  Not reliably available: market title on the miss event itself")
    print()
    print("Summary")
    print(f"  No-keyword misses considered     : {stats['no_keyword_misses']}")
    print(f"  Corroborating keyword-gate rows  : {stats['corroborating_keyword_gate_records']}")
    print(f"  Unique candidate phrases surfaced: {stats['unique_candidate_phrases']}")
    print()
    print("Review Buckets")
    print("  Heuristic only. These buckets are review aids, not live recommendations.")
    for section in (
        "strongest specific candidates",
        "watchlist / ambiguous candidates",
        "likely reject / too broad",
    ):
        print()
        print(section.title())
        shown = 0
        for row in stats["grouped_phrases"][section]:
            if row["count"] < min_count:
                continue
            for line in format_phrase_row(row, max_examples=max_examples):
                print(line)
            shown += 1
            if shown >= top:
                break
        if shown == 0:
            print("  (none)")
    print()
    print("Risk Notes")
    print("  generic_terms: phrase contains broad news language that may over-fire.")
    print("  broad_false_positive_risk: most tokens are generic, so review carefully.")
    print("  short_phrase: phrase is short enough to be noisy across unrelated headlines.")
    print("  overly_broad_named_entity: phrase centers on a single entity with little event detail.")
    print("  low_specificity: phrase has weak event/entity density and may be too diffuse.")
    print("  Suggested signal groups are intentionally omitted here unless reviewed manually.")


def main() -> int:
    args = parse_args()
    path = Path(args.path)
    since = parse_date_start(args.since)
    until = parse_date_end(args.until)
    stats = summarize(path, since=since, until=until, exclude_test=args.exclude_test)
    print_summary(
        stats,
        top=args.top,
        min_count=args.min_count,
        max_examples=args.max_examples,
        since=since,
        until=until,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
