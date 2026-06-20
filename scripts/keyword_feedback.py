"""
Read-only keyword feedback audit for empty-keyword analysis exits.

This script mines recent ANALYSIS_REJECTED(reason=no_keywords) records from
the preferred trade-log root at logs/trades/ and surfaces repeated headline
phrases as candidate keyword additions for human review.

Notes:
  - Candidate phrases are heuristic only and are not auto-promoted.
  - The primary miss corpus is ANALYSIS_REJECTED(reason=no_keywords), split by
    rejection_category when the emitter provides it.
  - SIGNAL_ANALYSIS_DETAIL(method=llm, keywords=[]) rows are counted separately
    as directional-empty or neutral-empty LLM context. They are mined only when
    --include-empty-llm-detail-corpus is passed.
  - SIGNAL_ANALYSIS_DETAIL(method=keyword_gate, keywords=[]) is counted only as
    corroborating context, not as the primary mining corpus, to avoid double
    counting.
  - The legacy monolithic logs/trades/trades.jsonl path is still supported
    during the cutover validation window.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

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
from utils.keyword_diagnostics_helpers import tokenize_keyword_text
from utils.reporting_helpers import warn_if_full_trade_root_scan
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"
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
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text="Path to trade-log file or root (default: logs/trades/; legacy logs/trades/trades.jsonl still supported)",
    )
    add_since_arg(parser, help_text="Inclusive start date in YYYY-MM-DD")
    add_until_arg(parser, help_text="Inclusive end date in YYYY-MM-DD")
    add_top_arg(parser, default=20, help_text="Max candidate phrases to show (default: 20)")
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
        "--include-empty-llm-detail-corpus",
        action="store_true",
        help=(
            "Also mine SIGNAL_ANALYSIS_DETAIL(method=llm, keywords=[]) rows. "
            "Rows stay tagged by directional_empty_llm vs neutral_empty_llm."
        ),
    )
    add_exclude_test_arg(
        parser,
        help_text="Exclude synthetic/test records (source contains 'r/test' or ticker contains 'KXTEST')",
    )
    return parser.parse_args()


def tokenize_headline(headline: str) -> list[str]:
    return tokenize_keyword_text(headline)


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


def _empty_keywords(value: Any) -> bool:
    return isinstance(value, list) and not value


def _llm_empty_keyword_corpus(record: dict[str, Any]) -> str | None:
    direction = str(record.get("llm_direction") or "").strip().lower()
    magnitude = str(record.get("llm_magnitude") or "").strip().lower()
    if direction in {"yes", "no"} and magnitude != "none":
        return "directional_empty_llm"
    if direction == "neutral" or magnitude == "none":
        return "neutral_empty_llm"
    return None


def summarize(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool = False,
    *,
    include_empty_llm_detail_corpus: bool = False,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": path,
        "lines_total": 0,
        "lines_malformed": 0,
        "records_kept": 0,
        "no_keyword_misses": 0,
        "corroborating_keyword_gate_records": 0,
        "no_keyword_rejection_categories": Counter(),
        "empty_keyword_llm_directional_rows": 0,
        "empty_keyword_llm_neutral_rows": 0,
        "top_empty_keyword_llm_directional_sources": [],
        "top_empty_keyword_llm_neutral_sources": [],
        "phrases": [],
        "grouped_phrases": {},
        "top_no_keyword_sources": [],
        "top_no_keyword_tickers": [],
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
    source_counts: Counter[str] = Counter()
    ticker_counts: Counter[str] = Counter()
    directional_empty_sources: Counter[str] = Counter()
    neutral_empty_sources: Counter[str] = Counter()

    def add_phrase_hits(record: dict[str, Any], *, corpus: str) -> None:
        headline = str(record.get("headline") or "").strip()
        source = str(record.get("source") or "").strip()
        ticker = str(record.get("ticker") or "").strip()
        if not headline:
            return
        for phrase in iter_candidate_phrases(headline):
            row = phrase_hits[phrase]
            row["phrase"] = phrase
            row["count"] += 1
            row.setdefault("corpora", Counter())[corpus] += 1
            row["sources"].add(source)
            if ticker:
                row["tickers"].add(ticker)
            if headline not in row["examples"] and len(row["examples"]) < 5:
                row["examples"].append(headline)

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue

        stats["records_kept"] += 1

        event_type = str(record.get("type") or "").strip()
        if event_type == "SIGNAL_ANALYSIS_DETAIL":
            method = str(record.get("method") or "").strip()
            keywords = record.get("keywords")
            if method == "keyword_gate" and _empty_keywords(keywords):
                stats["corroborating_keyword_gate_records"] += 1
            if method == "llm" and _empty_keywords(keywords):
                corpus = _llm_empty_keyword_corpus(record)
                if corpus == "directional_empty_llm":
                    stats["empty_keyword_llm_directional_rows"] += 1
                    source = str(record.get("source") or "").strip()
                    if source:
                        directional_empty_sources[source] += 1
                elif corpus == "neutral_empty_llm":
                    stats["empty_keyword_llm_neutral_rows"] += 1
                    source = str(record.get("source") or "").strip()
                    if source:
                        neutral_empty_sources[source] += 1
                if corpus and include_empty_llm_detail_corpus:
                    add_phrase_hits(record, corpus=corpus)

        if event_type != "ANALYSIS_REJECTED" or record.get("reason") != "no_keywords":
            continue

        headline = str(record.get("headline") or "").strip()
        source = str(record.get("source") or "").strip()
        ticker = str(record.get("ticker") or "").strip()
        if not headline:
            continue

        stats["no_keyword_misses"] += 1
        category = str(record.get("rejection_category") or "legacy_no_keywords").strip() or "legacy_no_keywords"
        stats["no_keyword_rejection_categories"][category] += 1
        if source:
            source_counts[source] += 1
        if ticker:
            ticker_counts[ticker] += 1
        add_phrase_hits(record, corpus=category)

    stats["lines_total"] = read_stats.lines_total
    stats["lines_malformed"] = read_stats.lines_malformed

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
                "corpora": dict(row.get("corpora", {})),
                "risk_flags": risk_flags,
                "category": category,
                "candidate_score": score,
                "review_bucket": review_bucket(category, risk_flags, score),
            }
        )

    phrase_rows.sort(key=phrase_sort_key)
    stats["phrases"] = phrase_rows
    stats["unique_candidate_phrases"] = len(phrase_rows)
    stats["top_no_keyword_sources"] = source_counts.most_common()
    stats["top_no_keyword_tickers"] = ticker_counts.most_common()
    stats["top_empty_keyword_llm_directional_sources"] = directional_empty_sources.most_common()
    stats["top_empty_keyword_llm_neutral_sources"] = neutral_empty_sources.most_common()
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
    print("  LLM-empty context: SIGNAL_ANALYSIS_DETAIL(method=llm, keywords=[])")
    print("  Reliable fields: headline, source, ticker, ts, reason, match_score")
    print("  Not reliably available: market title on the miss event itself")
    print()
    print("Summary")
    print(f"  No-keyword misses considered     : {stats['no_keyword_misses']}")
    print(f"  Corroborating keyword-gate rows  : {stats['corroborating_keyword_gate_records']}")
    print(
        "  Empty-keyword LLM detail rows    : "
        f"directional={stats['empty_keyword_llm_directional_rows']} "
        f"neutral={stats['empty_keyword_llm_neutral_rows']}"
    )
    print(f"  Unique candidate phrases surfaced: {stats['unique_candidate_phrases']}")
    categories = stats.get("no_keyword_rejection_categories") or Counter()
    if categories:
        print("  No-keyword rejection branches")
        for category, count in categories.most_common():
            print(f"    {category}: {count}")
    print()
    print("Top no-keyword miss sources")
    source_rows = stats.get("top_no_keyword_sources") or []
    if source_rows:
        for source, count in source_rows[:top]:
            print(f"  {source}: {count}")
    else:
        print("  (none)")
    print()
    print("Top no-keyword miss tickers")
    ticker_rows = stats.get("top_no_keyword_tickers") or []
    if ticker_rows:
        for ticker, count in ticker_rows[:top]:
            print(f"  {ticker}: {count}")
    else:
        print("  (none)")
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
    warn_if_full_trade_root_scan(path, since=since, until=until)
    stats = summarize(
        path,
        since=since,
        until=until,
        exclude_test=args.exclude_test,
        include_empty_llm_detail_corpus=args.include_empty_llm_detail_corpus,
    )
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
