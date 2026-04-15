"""
Keyword promotion report -- passive governance layer over shadow evaluation.

This script reuses the Phase 6B shadow-evaluation inputs and produces explicit
human-review recommendations:

  - Promote
  - Watch
  - Reject

PASSIVE ONLY -- this script does not modify live config or runtime behavior.
Recommendations are review aids only.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.keyword_shadow_eval import (
    BUCKET_PROMOTE,
    DEFAULT_LOG_PATH,
    DEFAULT_SHADOW_PHRASES,
    PROMOTE_MIN_HITS,
    PROMOTE_MIN_SCORE,
    PROMOTE_MIN_SOURCES,
    evaluate_phrases,
    load_miss_corpus,
    parse_date_end,
    parse_date_start,
    score_phrases,
)


RECOMMEND_PROMOTE = "promote"
RECOMMEND_WATCH = "watch"
RECOMMEND_REJECT = "reject"
REJECT_MIN_SCORE = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a passive keyword promotion report from shadow-evaluated phrases"
    )
    parser.add_argument(
        "--path",
        default=str(DEFAULT_LOG_PATH),
        help="Path to trades.jsonl (default: logs/trades/trades.jsonl)",
    )
    parser.add_argument("--since", help="Inclusive start date YYYY-MM-DD (UTC)")
    parser.add_argument("--until", help="Inclusive end date YYYY-MM-DD (UTC)")
    parser.add_argument(
        "--phrases",
        nargs="+",
        metavar="PHRASE",
        help="Shadow phrases to evaluate (default: DEFAULT_SHADOW_PHRASES in keyword_shadow_eval.py)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Max rows to show per recommendation section (default: 20)",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=2,
        help="Example headlines to show per phrase (default: 2)",
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


def _pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "n/a"
    return f"{numerator / denominator * 100:.1f}%"


def recommend_phrase(phrase_result: dict[str, Any], total_miss_events: int) -> dict[str, Any]:
    hits = phrase_result["hits"]
    score = int(phrase_result.get("score") or 0)
    source_count = len(phrase_result.get("sources") or [])
    ticker_count = len(phrase_result.get("tickers") or [])
    overlap_hits = int(phrase_result.get("overlap_hits") or 0)
    concentration = phrase_result.get("concentration") or {}
    concentration_flag = bool(concentration.get("flag"))
    base_bucket = phrase_result.get("bucket")

    miss_share = (hits / total_miss_events) if total_miss_events else 0.0
    overlap_ratio = (overlap_hits / hits) if hits else 0.0

    if base_bucket == BUCKET_PROMOTE:
        recommendation = RECOMMEND_PROMOTE
        rationale = (
            f"meets promote thresholds: score {score} >= {PROMOTE_MIN_SCORE}, "
            f"hits {hits} >= {PROMOTE_MIN_HITS}, sources {source_count} >= {PROMOTE_MIN_SOURCES}, "
            "no concentration flag"
        )
    elif hits == 0 or score < REJECT_MIN_SCORE:
        recommendation = RECOMMEND_REJECT
        reasons: list[str] = []
        if hits == 0:
            reasons.append("zero shadow hits")
        if score < REJECT_MIN_SCORE:
            reasons.append(f"score {score} < {REJECT_MIN_SCORE}")
        rationale = "; ".join(reasons)
    else:
        recommendation = RECOMMEND_WATCH
        reasons = []
        if hits < PROMOTE_MIN_HITS:
            reasons.append(f"needs more hits ({hits}/{PROMOTE_MIN_HITS})")
        if source_count < PROMOTE_MIN_SOURCES:
            reasons.append(f"needs broader source coverage ({source_count}/{PROMOTE_MIN_SOURCES})")
        if score < PROMOTE_MIN_SCORE:
            reasons.append(f"score below promote threshold ({score}/{PROMOTE_MIN_SCORE})")
        if concentration_flag:
            reasons.append("concentration flag set")
        if overlap_hits:
            reasons.append(f"overlap burden {overlap_hits} hit(s)")
        rationale = "; ".join(reasons) if reasons else "promising but not yet promotion-ready"

    enriched = dict(phrase_result)
    enriched["recommendation"] = recommendation
    enriched["miss_corpus_pct"] = round(miss_share * 100, 1) if total_miss_events else 0.0
    enriched["overlap_ratio"] = round(overlap_ratio, 3) if hits else 0.0
    enriched["rationale"] = rationale
    return enriched


def recommendation_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -int(row.get("score") or 0),
        -int(row.get("hits") or 0),
        -len(row.get("sources") or []),
        -len(row.get("tickers") or []),
        row.get("phrase") or "",
    )


def summarize(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    phrases: list[str],
    exclude_test: bool,
    max_examples: int,
) -> dict[str, Any]:
    records, total_lines, malformed = load_miss_corpus(
        path,
        since=since,
        until=until,
        exclude_test=exclude_test,
    )
    result = evaluate_phrases(records, phrases, max_examples=max_examples)
    score_phrases(result["phrases"])

    recommendations = [
        recommend_phrase(pr, total_miss_events=result["total_miss_events"]) for pr in result["phrases"]
    ]
    recommendations.sort(key=recommendation_sort_key)

    grouped = {
        RECOMMEND_PROMOTE: [],
        RECOMMEND_WATCH: [],
        RECOMMEND_REJECT: [],
    }
    for row in recommendations:
        grouped[row["recommendation"]].append(row)

    return {
        "path": path,
        "since": since,
        "until": until,
        "total_lines": total_lines,
        "malformed": malformed,
        "phrases": phrases,
        "miss_corpus_size": result["total_miss_events"],
        "misses_matched_by_any_phrase": result["events_with_any_hit"],
        "events_with_overlap": result["events_with_overlap"],
        "pair_overlap": result["pair_overlap"],
        "recommendations": recommendations,
        "grouped": grouped,
    }


def _print_phrase_row(row: dict[str, Any], total_misses: int, max_examples: int) -> None:
    conc = row.get("concentration") or {}
    conc_text = "none"
    if conc:
        status = "HIGH" if conc.get("flag") else "ok"
        conc_text = f"{conc.get('top_ticker')} {conc.get('fraction', 0) * 100:.0f}% [{status}]"

    print(f"  {row['phrase']}")
    print(
        f"    recommendation : {row['recommendation']}  score={row['score']}  "
        f"hits={row['hits']} ({_pct(row['hits'], total_misses)})"
    )
    print(
        f"    coverage       : sources={len(row['sources'])}  tickers={len(row['tickers'])}  "
        f"overlap_hits={row['overlap_hits']}  concentration={conc_text}"
    )
    print(f"    rationale      : {row['rationale']}")
    if row["sources"]:
        print(f"    sources        : {', '.join(row['sources'])}")
    if row["tickers"]:
        print(f"    tickers        : {', '.join(row['tickers'][:6])}")
    if row["examples"]:
        print("    examples       :")
        for example in row["examples"][:max_examples]:
            safe = example.encode("ascii", "replace").decode("ascii")
            print(f'      "{safe}"')
    print()


def print_report(stats: dict[str, Any], top: int, max_examples: int) -> None:
    sep = "=" * 70
    print(sep)
    print("KEYWORD PROMOTION REPORT (PASSIVE -- READ-ONLY)")
    print(sep)
    print("*** No live config changes were made. ***")
    print("*** Recommendations are review aids only. ***")
    print()
    print(f"Log   : {stats['path']}")
    print(f"Lines : {stats['total_lines']}  (malformed: {stats['malformed']})")
    if stats["since"] or stats["until"]:
        since_str = stats["since"].isoformat() if stats["since"] else "-inf"
        until_str = stats["until"].isoformat() if stats["until"] else "+inf"
        print(f"Window: {since_str} .. {until_str}")
    print()
    print("Promotion Criteria")
    print(
        f"  Promote: Phase 6B promote candidate "
        f"(hits >= {PROMOTE_MIN_HITS}, sources >= {PROMOTE_MIN_SOURCES}, "
        f"score >= {PROMOTE_MIN_SCORE}, no concentration flag)"
    )
    print(
        f"  Watch  : at least one shadow hit, but missing one or more promote conditions"
    )
    print(
        f"  Reject : zero hits, or very weak evidence (score < {REJECT_MIN_SCORE})"
    )
    print()
    print("Summary")
    print(f"  Total phrases evaluated          : {len(stats['recommendations'])}")
    print(f"  Promote                          : {len(stats['grouped'][RECOMMEND_PROMOTE])}")
    print(f"  Watch                            : {len(stats['grouped'][RECOMMEND_WATCH])}")
    print(f"  Reject                           : {len(stats['grouped'][RECOMMEND_REJECT])}")
    print(f"  Miss corpus size                 : {stats['miss_corpus_size']}")
    print(
        f"  Misses matched by >=1 phrase     : {stats['misses_matched_by_any_phrase']} "
        f"({_pct(stats['misses_matched_by_any_phrase'], stats['miss_corpus_size'])})"
    )
    print()

    section_titles = {
        RECOMMEND_PROMOTE: "Promote",
        RECOMMEND_WATCH: "Watch",
        RECOMMEND_REJECT: "Reject",
    }
    for key in (RECOMMEND_PROMOTE, RECOMMEND_WATCH, RECOMMEND_REJECT):
        print(section_titles[key])
        group = stats["grouped"][key]
        if not group:
            print("  (none)")
            print()
            continue
        for row in group[:top]:
            _print_phrase_row(row, stats["miss_corpus_size"], max_examples=max_examples)

    print("Attribution Notes")
    print("  This is passive evaluation only.")
    print("  No phrase was added to live config.")
    print("  Recommendations are review aids only.")
    print("  Live promotion still requires explicit human approval and a config change.")
    print(sep)


def build_json_result(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "phrases": stats["phrases"],
        "miss_corpus_size": stats["miss_corpus_size"],
        "misses_matched_by_any_phrase": stats["misses_matched_by_any_phrase"],
        "events_with_overlap": stats["events_with_overlap"],
        "pair_overlap": stats["pair_overlap"],
        "recommendations": [
            {
                "phrase": row["phrase"],
                "recommendation": row["recommendation"],
                "score": row["score"],
                "hits": row["hits"],
                "miss_corpus_pct": row["miss_corpus_pct"],
                "sources": row["sources"],
                "tickers": row["tickers"],
                "concentration": row["concentration"],
                "overlap_hits": row["overlap_hits"],
                "rationale": row["rationale"],
            }
            for row in stats["recommendations"]
        ],
    }


def main() -> int:
    args = parse_args()
    path = Path(args.path)
    since = parse_date_start(args.since)
    until = parse_date_end(args.until)
    phrases = args.phrases if args.phrases else DEFAULT_SHADOW_PHRASES

    stats = summarize(
        path=path,
        since=since,
        until=until,
        phrases=phrases,
        exclude_test=args.exclude_test,
        max_examples=args.max_examples,
    )
    print_report(stats, top=args.top, max_examples=args.max_examples)
    if args.emit_json:
        print()
        print(json.dumps(build_json_result(stats), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
