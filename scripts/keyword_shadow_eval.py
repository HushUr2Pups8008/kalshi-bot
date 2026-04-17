"""
Keyword shadow evaluation -- passive offline analysis tool.

Measures what would have happened if a small set of handpicked candidate
phrases had been active keyword triggers, using the historical miss corpus
from ANALYSIS_REJECTED(reason=no_keywords) events in the preferred trade-log
root at logs/trades/.

PASSIVE ONLY -- this script does not modify live config, live behavior, or
any runtime data. It reads the trade-log root or a compatible legacy file and
prints a report. Nothing else.

Usage:
    python scripts/keyword_shadow_eval.py
    python scripts/keyword_shadow_eval.py --since 2026-04-10
    python scripts/keyword_shadow_eval.py --phrases "strait hormuz" "hormuz blockade"
    python scripts/keyword_shadow_eval.py --json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

from utils.reporting_helpers import warn_if_full_trade_root_scan
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records
import re

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"

# Tokeniser -- mirrors keyword_feedback.py so phrase tokens are consistent.
# Shadow phrases (e.g. "strait hormuz") are bigrams of tokenised tokens;
# matching must use the same tokenisation, not a raw substring check.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9']+")
_STOPWORDS = {
    "a", "after", "again", "all", "amid", "an", "and", "are", "as", "at",
    "be", "before", "but", "by", "for", "from", "has", "have", "here", "his",
    "in", "into", "is", "it", "its", "latest", "live", "more", "new", "not",
    "of", "on", "or", "over", "said", "says", "that", "the", "their", "this",
    "to", "updates", "was", "what", "when", "where", "who", "why", "will",
    "with",
}

# Default shadow phrases -- manually selected strongest specific candidates.
# These are the ONLY phrases evaluated. They are NOT live keywords.
# Do not auto-populate from keyword_feedback output -- only add after manual review.
DEFAULT_SHADOW_PHRASES: list[str] = [
    "strait hormuz",
    "hormuz blockade",
    "blockade iran",
]

# Flag concentration when a single ticker accounts for this fraction of hits.
# High concentration means the phrase is useful for one market, not broadly.
CONCENTRATION_FLAG_THRESHOLD = 0.50

# Promotion-readiness thresholds. Conservative by design -- only promote when
# evidence is broad (multi-source, multi-ticker) and not ticker-concentrated.
PROMOTE_MIN_HITS = 5
PROMOTE_MIN_SOURCES = 2
PROMOTE_MIN_SCORE = 20

BUCKET_PROMOTE = "promote candidate"
BUCKET_SHADOW = "continue shadowing"
BUCKET_REJECT = "reject for now"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Keyword shadow evaluation -- passive analysis only. "
            "Measures shadow phrase hits against ANALYSIS_REJECTED(no_keywords) miss corpus."
        )
    )
    parser.add_argument(
        "--path",
        default=str(DEFAULT_LOG_PATH),
        help="Path to trade-log file or root (default: logs/trades/; legacy logs/trades/trades.jsonl still supported)",
    )
    parser.add_argument("--since", help="Inclusive start date YYYY-MM-DD (UTC)")
    parser.add_argument("--until", help="Inclusive end date YYYY-MM-DD (UTC)")
    parser.add_argument(
        "--phrases",
        nargs="+",
        metavar="PHRASE",
        help="Shadow phrases to evaluate (default: DEFAULT_SHADOW_PHRASES in script)",
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
        help="Exclude KXTEST ticker and r/test source records",
    )
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Also emit structured JSON to stdout after the text report",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _tokenize(text: str) -> list[str]:
    """Return meaningful tokens from text (same logic as keyword_feedback.py)."""
    tokens = []
    for token in _TOKEN_RE.findall(text.lower()):
        if len(token) >= 3 and token not in _STOPWORDS:
            tokens.append(token)
    return tokens


def _phrase_matches(phrase: str, headline: str) -> bool:
    """True if phrase tokens appear as a consecutive subsequence in headline tokens.

    "strait hormuz" matches "Strait of Hormuz" because after stopword removal
    the token sequence ["strait", "hormuz"] is adjacent in both.
    """
    phrase_tokens = _tokenize(phrase)
    if not phrase_tokens:
        return False
    headline_tokens = _tokenize(headline)
    n = len(phrase_tokens)
    for i in range(len(headline_tokens) - n + 1):
        if headline_tokens[i : i + n] == phrase_tokens:
            return True
    return False


def is_test_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_miss_corpus(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool,
) -> tuple[list[dict[str, Any]], int, int]:
    """Return (miss_records, total_lines, malformed_lines).

    Only ANALYSIS_REJECTED(reason=no_keywords) records are returned.
    All other events are skipped.
    """
    records: list[dict[str, Any]] = []
    total = 0
    malformed = 0

    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue

        event_type = str(record.get("type") or record.get("event") or "").strip()
        if event_type == "ANALYSIS_REJECTED" and record.get("reason") == "no_keywords":
            records.append(record)

    total = read_stats.lines_total
    malformed = read_stats.lines_malformed

    return records, total, malformed


# ---------------------------------------------------------------------------
# Core evaluation (pure -- no I/O, testable)
# ---------------------------------------------------------------------------


def evaluate_phrases(
    records: list[dict[str, Any]],
    phrases: list[str],
    max_examples: int = 3,
) -> dict[str, Any]:
    """Check each miss record against each shadow phrase.

    Returns a structured result dict. Does not modify any live config.
    Match is a case-insensitive substring check: phrase.lower() in headline.lower().
    """
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

    # For each record, track which phrases matched (for overlap counting)
    event_phrase_hits: list[set[str]] = [set() for _ in records]

    for idx, record in enumerate(records):
        raw_headline = str(record.get("headline") or "").strip()
        source = str(record.get("source") or "").strip()
        ticker = str(record.get("ticker") or "").strip()

        for phrase in phrases:
            if _phrase_matches(phrase, raw_headline):
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

    # Per-phrase overlap: events where this phrase matched AND >=1 other phrase also matched
    phrase_overlap_hits: dict[str, int] = {phrase: 0 for phrase in phrases}
    for hits in event_phrase_hits:
        if len(hits) >= 2:
            for ph in hits:
                phrase_overlap_hits[ph] += 1

    # Overlap: events that matched 2+ phrases
    events_with_overlap = sum(1 for hits in event_phrase_hits if len(hits) >= 2)

    # Pair-level overlap counts
    pair_overlap: dict[str, int] = {}
    for hits in event_phrase_hits:
        hit_list = sorted(hits)
        for i in range(len(hit_list)):
            for j in range(i + 1, len(hit_list)):
                key = f"{hit_list[i]} + {hit_list[j]}"
                pair_overlap[key] = pair_overlap.get(key, 0) + 1

    # Per-phrase concentration
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


# ---------------------------------------------------------------------------
# Promotion-readiness scoring (pure -- no I/O, testable)
# ---------------------------------------------------------------------------


def score_phrases(phrase_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add score, bucket, and reason to each phrase result in-place. Pure, no I/O.

    Scoring formula (all factors are explicit and explainable):
      hits * 4         -- raw hit weight
      sources * 6      -- source diversity (multi-source = broader signal)
      tickers * 3      -- ticker diversity (multi-market = broader utility)
      - 10             -- concentration penalty (top ticker fraction >= threshold)
      - overlap_hits*2 -- overlap penalty (shared hits add less unique value)

    Bucket rules (conservative):
      promote candidate  -- score >= PROMOTE_MIN_SCORE AND hits >= PROMOTE_MIN_HITS
                            AND sources >= PROMOTE_MIN_SOURCES AND no concentration flag
      continue shadowing -- hits >= 1 but not eligible to promote
      reject for now     -- hits == 0
    """
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


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def _pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "n/a"
    return f"{numerator / denominator * 100:.1f}%"


def _print_phrase_block(pr: dict[str, Any], total: int, max_examples: int) -> None:
    """Print one phrase's stats block. Called once per phrase in the grouped report."""
    hits = pr["hits"]
    print(f"  {pr['phrase']}")
    print(f"    shadow hits   : {hits}  (of {total} misses, {_pct(hits, total)})")
    overlap_hits = pr.get("overlap_hits", 0)
    if hits > 0 and overlap_hits:
        print(f"    overlap hits  : {overlap_hits}  (hits also matched by another phrase)")
    if pr["sources"]:
        print(f"    sources       : {len(pr['sources'])}  [{', '.join(pr['sources'])}]")
    else:
        print("    sources       : 0")
    if pr["tickers"]:
        shown_tickers = pr["tickers"][:6]
        more = f" +{len(pr['tickers']) - 6} more" if len(pr["tickers"]) > 6 else ""
        print(f"    tickers hit   : {len(pr['tickers'])}  [{', '.join(shown_tickers)}{more}]")
    else:
        print("    tickers hit   : 0")
    conc = pr.get("concentration") or {}
    if conc:
        flag_note = "  [HIGH -- single-market concentration]" if conc.get("flag") else ""
        print(
            f"    concentration : {conc['top_ticker']} in"
            f" {conc['top_ticker_hits']}/{hits}"
            f" ({conc['fraction'] * 100:.0f}%){flag_note}"
        )
    score = pr.get("score")
    if score is not None:
        print(f"    score         : {score}  [{pr.get('bucket', '')}]")
        print(f"    reason        : {pr.get('reason', '')}")
    if hits > 0:
        print("    examples      :")
        for ex in pr["examples"][:max_examples]:
            # Encode to ASCII with replacement to avoid Windows console cp1252 errors
            safe = ex.encode("ascii", "replace").decode("ascii")
            print(f"      \"{safe}\"")
    else:
        print("    examples      : (none -- phrase had zero hits)")
    print()


def print_report(
    result: dict[str, Any],
    phrases: list[str],
    path: Path,
    total_lines: int,
    malformed: int,
    since: datetime | None,
    until: datetime | None,
    max_examples: int,
) -> None:
    sep = "=" * 70
    thin = "-" * 60
    print(sep)
    print("KEYWORD SHADOW EVALUATION (PASSIVE -- READ-ONLY)")
    print(sep)
    print("*** No live config changes were made. ***")
    print("*** This report is passive analysis only. ***")
    print()
    print(f"Log   : {path}")
    print(f"Lines : {total_lines}  (malformed: {malformed})")
    if since or until:
        since_str = since.isoformat() if since else "-inf"
        until_str = until.isoformat() if until else "+inf"
        print(f"Window: {since_str} .. {until_str}")
    print()

    total = result["total_miss_events"]
    print("Miss Corpus")
    print("  Source event type    : ANALYSIS_REJECTED(reason=no_keywords)")
    print(f"  Miss events in corpus: {total}")
    print()

    print(f"Shadow Phrases ({len(phrases)} evaluated)")
    for p in phrases:
        print(f"  '{p}'")
    print()

    # Grouped by bucket (promote -> shadow -> reject)
    bucket_order = [BUCKET_PROMOTE, BUCKET_SHADOW, BUCKET_REJECT]
    bucket_labels = {
        BUCKET_PROMOTE: "PROMOTE CANDIDATES",
        BUCKET_SHADOW:  "CONTINUE SHADOWING",
        BUCKET_REJECT:  "REJECT FOR NOW",
    }
    grouped: dict[str, list[dict[str, Any]]] = {b: [] for b in bucket_order}
    for pr in result["phrases"]:
        bucket = pr.get("bucket", BUCKET_SHADOW)
        grouped[bucket].append(pr)

    print("Results by Phrase")
    print(thin)
    for bucket in bucket_order:
        group = grouped[bucket]
        print(f"  {bucket_labels[bucket]}")
        if not group:
            print("    (none)")
            print()
            continue
        for pr in group:
            _print_phrase_block(pr, total, max_examples)

    print("Aggregate")
    print(thin)
    any_hit = result["events_with_any_hit"]
    no_hit = result["events_with_no_hit"]
    overlap = result["events_with_overlap"]
    print(f"  Total miss events              : {total}")
    print(f"  Matched by >=1 shadow phrase   : {any_hit}  ({_pct(any_hit, total)})")
    print(f"  Matched by 0 shadow phrases    : {no_hit}")
    print(f"  Matched by 2+ phrases (overlap): {overlap}")

    if result["pair_overlap"]:
        print()
        print("Phrase Overlap Detail")
        print(thin)
        for pair, count in sorted(result["pair_overlap"].items()):
            print(f"  {pair}: {count} event(s)")

    print()
    print("Evaluation Notes")
    print(thin)
    print("  This is passive shadow evaluation only.")
    print("  No phrases were added to live keyword config.")
    print("  No runtime behavior was changed.")
    print("  To promote a phrase: manually add it to GEOPOLITICAL_SIGNALS in")
    print("  config.py after reviewing concentration and false-positive risk.")
    print("  High concentration means a phrase is tied to one active market --")
    print("  review whether it is still useful after that market expires.")
    print(sep)


def build_json_result(
    result: dict[str, Any],
    phrases: list[str],
) -> dict[str, Any]:
    return {
        "shadow_phrases": phrases,
        "total_miss_events": result["total_miss_events"],
        "events_with_any_hit": result["events_with_any_hit"],
        "events_with_no_hit": result["events_with_no_hit"],
        "events_with_overlap": result["events_with_overlap"],
        "pair_overlap": result["pair_overlap"],
        "phrase_results": [
            {
                "phrase": pr["phrase"],
                "hits": pr["hits"],
                "sources": pr["sources"],
                "tickers": pr["tickers"],
                "ticker_counts": pr["ticker_counts"],
                "examples": pr["examples"],
                "concentration": pr["concentration"],
                "overlap_hits": pr.get("overlap_hits", 0),
                "score": pr.get("score"),
                "bucket": pr.get("bucket"),
                "reason": pr.get("reason"),
            }
            for pr in result["phrases"]
        ],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    args = parse_args()
    path = Path(args.path)
    since = parse_date_start(args.since)
    until = parse_date_end(args.until)
    warn_if_full_trade_root_scan(path, since=since, until=until)
    phrases = args.phrases if args.phrases else DEFAULT_SHADOW_PHRASES

    records, total_lines, malformed = load_miss_corpus(
        path, since=since, until=until, exclude_test=args.exclude_test
    )
    result = evaluate_phrases(records, phrases, max_examples=args.max_examples)
    score_phrases(result["phrases"])

    print_report(
        result,
        phrases=phrases,
        path=path,
        total_lines=total_lines,
        malformed=malformed,
        since=since,
        until=until,
        max_examples=args.max_examples,
    )

    if args.emit_json:
        import sys
        json_result = build_json_result(result, phrases)
        print()
        print("JSON Output:")
        json.dump(json_result, sys.stdout, indent=2)
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
