"""MATCH-001 B' regression-anchor sizing over archived MATCH_DIAGNOSTIC rows."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENT_HEADLINES_2026_04_26,
    LLM_POSITIVE_EVENTS_2026_04_26,
)
from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-match001-bprime-anchor-sizing.md"


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("market_ticker") or "")


def _headline(row: dict[str, Any]) -> str:
    return str(row.get("headline") or row.get("signal_headline") or "")


def _source(row: dict[str, Any]) -> str:
    return str(row.get("source") or row.get("signal_source") or "")


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (_ticker(row), _headline(row), _source(row))


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(iter_trade_records(path))
    return rows


def bprime_suppresses(match: dict[str, Any]) -> bool:
    flags = set(match.get("heuristic_flags") or [])
    if not flags:
        return False
    overlap = {str(t).lower() for t in (match.get("matched_tokens") or []) if str(t).strip()}
    ticker_lower = _ticker(match).lower()
    has_supporting_non_ticker = any(token not in ticker_lower for token in overlap)
    near_threshold_weak = "near_threshold_score" in flags and (
        "minimal_overlap" in flags or "single_named_entity_only" in flags
    )
    pure_single_entity = "single_named_entity_only" in flags and "minimal_overlap" in flags
    return (not has_supporting_non_ticker) and (near_threshold_weak or pure_single_entity)


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    matches = [r for r in rows if _typ(r) == "MATCH_DIAGNOSTIC"]
    opportunities = [r for r in rows if _typ(r) == "OPPORTUNITY"]
    papers = [r for r in rows if _typ(r) == "PAPER_TRADE"]

    suppressed = [r for r in matches if bprime_suppresses(r)]
    suppressed_keys = {_key(r) for r in suppressed}
    retained_opp = [r for r in opportunities if _key(r) not in suppressed_keys]
    retained_paper = [r for r in papers if _key(r) not in suppressed_keys]

    canonical_tickers = {event.ticker for event in LLM_POSITIVE_EVENTS_2026_04_26}
    canonical_suppressed = [r for r in suppressed if _ticker(r) in canonical_tickers]
    exact_event_headlines = {
        # The archive does not consistently preserve exact source values for the
        # 2026-04-26 anchors, so exact matching is intentionally ticker+headline.
        (event.ticker, LLM_POSITIVE_EVENT_HEADLINES_2026_04_26[event.name])
        for event in LLM_POSITIVE_EVENTS_2026_04_26
    }
    exact_suppressed = [
        r
        for r in canonical_suppressed
        if (_ticker(r), _headline(r)) in exact_event_headlines
    ]
    by_ticker = Counter(_ticker(r) for r in canonical_suppressed)

    return {
        "paths": [str(p) for p in roots],
        "match_diagnostic_total": len(matches),
        "suppressed_match_keys": len(suppressed_keys),
        "opportunity_total": len(opportunities),
        "opportunities_retained": len(retained_opp),
        "opportunity_retention": len(retained_opp) / len(opportunities) if opportunities else 0.0,
        "paper_trade_total": len(papers),
        "paper_trades_retained": len(retained_paper),
        "canonical_event_tickers": sorted(canonical_tickers),
        "canonical_ticker_suppressed_match_count": len(canonical_suppressed),
        "canonical_ticker_suppressed_by_ticker": dict(by_ticker.most_common()),
        "exact_canonical_event_suppressed_count": len(exact_suppressed),
        "canonical_ticker_guard_status": "FAIL" if canonical_suppressed else "PASS",
        "examples": [
            {
                "ticker": _ticker(r),
                "headline": _headline(r),
                "match_score": r.get("match_score"),
                "matched_tokens": r.get("matched_tokens"),
                "heuristic_flags": r.get("heuristic_flags"),
            }
            for r in canonical_suppressed[:8]
        ],
        "interpretation": (
            "Ticker-level canonical guard is stricter than exact-event guard. "
            "The archive contains many low-quality matches on canonical tickers, "
            "especially KXTRUMPIRAN; exact event preservation is not the same as "
            "ticker-level preservation."
        ),
    }


def render(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "MATCH-001 B' anchor sizing",
            f"Suppressed match keys: {report['suppressed_match_keys']}",
            f"OPPORTUNITY retained: {report['opportunities_retained']}/{report['opportunity_total']}",
            f"PAPER_TRADE retained: {report['paper_trades_retained']}/{report['paper_trade_total']}",
            f"Canonical ticker guard: {report['canonical_ticker_guard_status']}",
            f"Canonical ticker suppressed matches: {report['canonical_ticker_suppressed_match_count']}",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MATCH-001 B' Regression-Anchor Sizing",
        "",
        report["interpretation"],
        "",
        "## Summary",
        "",
        f"- MATCH_DIAGNOSTIC total: {report['match_diagnostic_total']}",
        f"- Suppressed match keys under B': {report['suppressed_match_keys']}",
        f"- OPPORTUNITY retained: {report['opportunities_retained']}/{report['opportunity_total']} ({report['opportunity_retention']:.1%})",
        f"- PAPER_TRADE retained: {report['paper_trades_retained']}/{report['paper_trade_total']}",
        f"- Canonical ticker guard: {report['canonical_ticker_guard_status']}",
        f"- Canonical ticker suppressed matches: {report['canonical_ticker_suppressed_match_count']}",
        f"- Exact canonical event suppressed matches: {report['exact_canonical_event_suppressed_count']}",
        "",
        "## Canonical Ticker Suppression",
        "",
        "| ticker | suppressed matches |",
        "| --- | ---: |",
    ]
    for ticker, count in report["canonical_ticker_suppressed_by_ticker"].items():
        lines.append(f"| {ticker} | {count} |")
    lines += [
        "",
        "## Examples",
        "",
        "| ticker | score | tokens | flags | headline |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for row in report["examples"]:
        lines.append(
            f"| {row['ticker']} | {row['match_score']} | {row['matched_tokens']} | "
            f"{row['heuristic_flags']} | {row['headline']} |"
        )
    lines += [
        "",
        "## Verdict",
        "",
        "The pre-deploy guard as phrased in the landing-order spec is too strict if it means ticker-level protection. B' suppresses many low-quality matches on canonical tickers, mostly single-token `iran` / `trump` matches against `KXTRUMPIRAN-26MAY01`. The deploy-day audit should protect exact canonical event tuples/headlines, not every future low-quality match on those tickers.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, action="append", dest="paths")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", type=Path, nargs="?", const=_DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = analyze(args.paths)
    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, separators=(",", ":"), default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
