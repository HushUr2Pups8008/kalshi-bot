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
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    add_path_arg,
    add_since_arg,
    add_until_arg,
    in_window,
    is_test_record_source_only as is_test_record,
    parse_date_end,
    parse_date_start,
    parse_iso_ts,
)
from utils.keyword_diagnostics_helpers import (
    BUCKET_PROMOTE,
    BUCKET_REJECT,
    BUCKET_SHADOW,
    CONCENTRATION_FLAG_THRESHOLD,
    DEFAULT_SHADOW_PHRASES,
    PROMOTE_MIN_HITS,
    PROMOTE_MIN_SCORE,
    PROMOTE_MIN_SOURCES,
    evaluate_shadow_phrases as evaluate_phrases,
    load_no_keyword_miss_corpus as load_miss_corpus,
    phrase_matches as _phrase_matches,
    score_shadow_phrases as score_phrases,
    tokenize_keyword_text as _tokenize,
)
from utils.reporting_helpers import warn_if_full_trade_root_scan

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"


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
    add_path_arg(
        parser,
        default=str(DEFAULT_LOG_PATH),
        help_text="Path to trade-log file or root (default: logs/trades/; legacy logs/trades/trades.jsonl still supported)",
    )
    add_since_arg(parser, help_text="Inclusive start date YYYY-MM-DD (UTC)")
    add_until_arg(parser, help_text="Inclusive end date YYYY-MM-DD (UTC)")
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
    add_exclude_test_arg(parser, help_text="Exclude KXTEST ticker and r/test source records")
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Also emit structured JSON to stdout after the text report",
    )
    return parser.parse_args()


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
        path,
        since=since,
        until=until,
        exclude_test=args.exclude_test,
        is_test_record=is_test_record,
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
