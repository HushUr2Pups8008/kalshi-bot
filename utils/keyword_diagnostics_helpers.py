from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from utils.trade_log_reader import TradeLogReadStats, iter_trade_records

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9']+")
STOPWORDS = {
    "a", "after", "again", "all", "amid", "an", "and", "are", "as", "at",
    "be", "before", "but", "by", "for", "from", "has", "have", "here", "his",
    "in", "into", "is", "it", "its", "latest", "live", "more", "new", "not",
    "of", "on", "or", "over", "said", "says", "that", "the", "their", "this",
    "to", "updates", "was", "what", "when", "where", "who", "why", "will",
    "with",
}

DEFAULT_SHADOW_PHRASES: list[str] = [
    "strait hormuz",
    "hormuz blockade",
    "blockade iran",
]

CONCENTRATION_FLAG_THRESHOLD = 0.50
PROMOTE_MIN_HITS = 5
PROMOTE_MIN_SOURCES = 2
PROMOTE_MIN_SCORE = 20

BUCKET_PROMOTE = "promote candidate"
BUCKET_SHADOW = "continue shadowing"
BUCKET_REJECT = "reject for now"


def _default_is_test_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


def tokenize_keyword_text(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall((text or "").lower()):
        if len(token) >= 3 and token not in STOPWORDS:
            tokens.append(token)
    return tokens


def phrase_matches(phrase: str, headline: str) -> bool:
    phrase_tokens = tokenize_keyword_text(phrase)
    if not phrase_tokens:
        return False
    headline_tokens = tokenize_keyword_text(headline)
    n = len(phrase_tokens)
    for i in range(len(headline_tokens) - n + 1):
        if headline_tokens[i : i + n] == phrase_tokens:
            return True
    return False


def load_no_keyword_miss_corpus(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool,
    *,
    is_test_record: Callable[[dict[str, Any]], bool] = _default_is_test_record,
) -> tuple[list[dict[str, Any]], int, int]:
    records: list[dict[str, Any]] = []
    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue
        event_type = str(record.get("type") or record.get("event") or "").strip()
        if event_type == "ANALYSIS_REJECTED" and record.get("reason") == "no_keywords":
            records.append(record)
    return records, read_stats.lines_total, read_stats.lines_malformed


def evaluate_shadow_phrases(
    records: list[dict[str, Any]],
    phrases: list[str],
    max_examples: int = 3,
) -> dict[str, Any]:
    phrase_data: dict[str, dict[str, Any]] = {
        phrase: {
            "phrase": phrase,
            "hits": 0,
            "sources": set(),
            "tickers": set(),
            "ticker_counts": defaultdict(int),
            "examples": [],
        }
        for phrase in phrases
    }

    event_phrase_hits: list[set[str]] = [set() for _ in records]

    for idx, record in enumerate(records):
        raw_headline = str(record.get("headline") or "").strip()
        source = str(record.get("source") or "").strip()
        ticker = str(record.get("ticker") or "").strip()

        for phrase in phrases:
            if phrase_matches(phrase, raw_headline):
                data = phrase_data[phrase]
                data["hits"] += 1
                if source:
                    data["sources"].add(source)
                if ticker:
                    data["tickers"].add(ticker)
                    data["ticker_counts"][ticker] += 1
                if raw_headline not in data["examples"] and len(data["examples"]) < max_examples:
                    data["examples"].append(raw_headline)
                event_phrase_hits[idx].add(phrase)

    phrase_overlap_hits: dict[str, int] = {phrase: 0 for phrase in phrases}
    for hits in event_phrase_hits:
        if len(hits) >= 2:
            for ph in hits:
                phrase_overlap_hits[ph] += 1

    events_with_overlap = sum(1 for hits in event_phrase_hits if len(hits) >= 2)

    pair_overlap: dict[str, int] = {}
    for hits in event_phrase_hits:
        hit_list = sorted(hits)
        for i in range(len(hit_list)):
            for j in range(i + 1, len(hit_list)):
                key = f"{hit_list[i]} + {hit_list[j]}"
                pair_overlap[key] = pair_overlap.get(key, 0) + 1

    phrase_results: list[dict[str, Any]] = []
    for phrase in phrases:
        data = phrase_data[phrase]
        hits = data["hits"]
        ticker_counts = dict(data["ticker_counts"])
        concentration: dict[str, Any] = {}
        if hits > 0 and ticker_counts:
            top_ticker = max(ticker_counts, key=lambda t: ticker_counts[t])
            top_count = ticker_counts[top_ticker]
            fraction = top_count / hits
            concentration = {
                "top_ticker": top_ticker,
                "top_ticker_hits": top_count,
                "fraction": round(fraction, 3),
                "flag": fraction >= CONCENTRATION_FLAG_THRESHOLD,
            }
        phrase_results.append(
            {
                "phrase": phrase,
                "hits": hits,
                "sources": sorted(data["sources"]),
                "tickers": sorted(data["tickers"]),
                "ticker_counts": ticker_counts,
                "examples": data["examples"],
                "concentration": concentration,
                "overlap_hits": phrase_overlap_hits[phrase],
            }
        )

    total = len(records)
    events_with_any_hit = sum(1 for hits in event_phrase_hits if hits)
    events_with_no_hit = total - events_with_any_hit

    return {
        "phrases": phrase_results,
        "total_miss_events": total,
        "events_with_any_hit": events_with_any_hit,
        "events_with_no_hit": events_with_no_hit,
        "events_with_overlap": events_with_overlap,
        "pair_overlap": pair_overlap,
    }


def score_shadow_phrases(phrase_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for pr in phrase_results:
        hits = pr["hits"]
        source_count = len(pr["sources"])
        ticker_count = len(pr["tickers"])
        conc = pr.get("concentration") or {}
        concentration_flagged = conc.get("flag", False)
        overlap_hits = pr.get("overlap_hits", 0)

        score = hits * 4 + source_count * 6 + ticker_count * 3
        if concentration_flagged:
            score -= 10
        score -= overlap_hits * 2

        if hits == 0:
            bucket = BUCKET_REJECT
            reason = "zero hits in miss corpus"
        elif (
            score >= PROMOTE_MIN_SCORE
            and hits >= PROMOTE_MIN_HITS
            and source_count >= PROMOTE_MIN_SOURCES
            and not concentration_flagged
        ):
            bucket = BUCKET_PROMOTE
            reason = (
                f"score={score}, hits={hits}, sources={source_count},"
                f" tickers={ticker_count}, no concentration flag"
            )
        else:
            bucket = BUCKET_SHADOW
            causes: list[str] = []
            if score < PROMOTE_MIN_SCORE:
                causes.append(f"score {score} < {PROMOTE_MIN_SCORE}")
            if hits < PROMOTE_MIN_HITS:
                causes.append(f"hits {hits} < {PROMOTE_MIN_HITS}")
            if source_count < PROMOTE_MIN_SOURCES:
                causes.append(f"sources {source_count} < {PROMOTE_MIN_SOURCES}")
            if concentration_flagged:
                causes.append("concentration flag set")
            reason = "; ".join(causes) if causes else "below promote thresholds"

        pr["score"] = score
        pr["bucket"] = bucket
        pr["reason"] = reason

    return phrase_results
