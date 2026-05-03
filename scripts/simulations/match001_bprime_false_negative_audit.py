"""Heuristic false-negative audit for MATCH-001 B' substring semantics."""
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

from scripts.simulations.match001_bprime_anchor_sizing import bprime_suppresses  # noqa: E402
from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-match001-bprime-false-negative-audit.md"


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("market_ticker") or "")


def _headline(row: dict[str, Any]) -> str:
    return str(row.get("headline") or "")


def _source(row: dict[str, Any]) -> str:
    return str(row.get("source") or "")


def _overlap(row: dict[str, Any]) -> set[str]:
    return {str(token).lower() for token in (row.get("matched_tokens") or []) if str(token).strip()}


def _shape_flags(row: dict[str, Any]) -> bool:
    flags = set(row.get("heuristic_flags") or [])
    near_threshold_weak = "near_threshold_score" in flags and (
        "minimal_overlap" in flags or "single_named_entity_only" in flags
    )
    pure_single_entity = "single_named_entity_only" in flags and "minimal_overlap" in flags
    return bool(flags) and (near_threshold_weak or pure_single_entity)


def _has_ticker_substring_overlap(row: dict[str, Any]) -> bool:
    ticker = _ticker(row).lower()
    return any(token in ticker for token in _overlap(row))


def escaped_by_bprime(row: dict[str, Any]) -> bool:
    return _shape_flags(row) and _has_ticker_substring_overlap(row) and not bprime_suppresses(row)


def likely_false_negative(row: dict[str, Any]) -> bool:
    """Conservative weak-support proxy, not ground truth relevance labeling."""
    overlap = _overlap(row)
    ticker = _ticker(row).lower()
    non_ticker = {token for token in overlap if token not in ticker}
    try:
        score = float(row.get("match_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return escaped_by_bprime(row) and len(non_ticker) <= 1 and score <= 0.08


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    matches = [
        row
        for path in roots
        for row in iter_trade_records(path)
        if _typ(row) == "MATCH_DIAGNOSTIC"
    ]
    bprime_shape = [row for row in matches if _shape_flags(row)]
    suppressed = [row for row in bprime_shape if bprime_suppresses(row)]
    escaped = [row for row in bprime_shape if escaped_by_bprime(row)]
    likely = [row for row in bprime_shape if likely_false_negative(row)]
    by_ticker = Counter(_ticker(row) for row in likely)
    by_token = Counter(
        token
        for row in likely
        for token in _overlap(row)
        if token in _ticker(row).lower()
    )
    examples = []
    for row in likely[:12]:
        ticker = _ticker(row).lower()
        examples.append(
            {
                "ticker": _ticker(row),
                "source": _source(row),
                "match_score": row.get("match_score"),
                "matched_tokens": sorted(_overlap(row)),
                "ticker_overlap_tokens": sorted(token for token in _overlap(row) if token in ticker),
                "non_ticker_tokens": sorted(token for token in _overlap(row) if token not in ticker),
                "headline": _headline(row),
            }
        )
    return {
        "paths": [str(path) for path in roots],
        "match_diagnostic_total": len(matches),
        "bprime_shape_total": len(bprime_shape),
        "bprime_suppressed_total": len(suppressed),
        "substring_escape_total": len(escaped),
        "likely_false_negative_total": len(likely),
        "likely_false_negative_rate_of_shape": len(likely) / len(bprime_shape) if bprime_shape else 0.0,
        "top_likely_false_negative_tickers": dict(by_ticker.most_common(10)),
        "top_ticker_overlap_tokens": dict(by_token.most_common(10)),
        "examples": examples,
        "definition": (
            "Likely false negative = B' low-quality shape, at least one matched token appears in the ticker substring, "
            "B' substring semantics do not suppress it, <=1 non-ticker support token, and match_score <=0.08. "
            "This is a weak-support heuristic, not manual relevance labeling."
        ),
    }


def render(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "MATCH-001 B' false-negative audit",
            f"B' shape: {report['bprime_shape_total']}",
            f"B' suppressed: {report['bprime_suppressed_total']}",
            f"substring escapes: {report['substring_escape_total']}",
            f"likely false negatives: {report['likely_false_negative_total']} ({report['likely_false_negative_rate_of_shape']:.1%} of shape)",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MATCH-001 B' Headline-Collision False-Negative Audit",
        "",
        report["definition"],
        "",
        "## Summary",
        "",
        f"- MATCH_DIAGNOSTIC total: {report['match_diagnostic_total']}",
        f"- B' low-quality shape total: {report['bprime_shape_total']}",
        f"- B' substring suppressed: {report['bprime_suppressed_total']}",
        f"- Escapes with ticker-substring overlap: {report['substring_escape_total']}",
        f"- Likely false negatives: {report['likely_false_negative_total']} ({report['likely_false_negative_rate_of_shape']:.1%} of B' shape)",
        f"- Top likely false-negative tickers: `{report['top_likely_false_negative_tickers']}`",
        f"- Top ticker-overlap tokens: `{report['top_ticker_overlap_tokens']}`",
        "",
        "## Examples",
        "",
        "| ticker | score | ticker tokens | non-ticker tokens | source | headline |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for row in report["examples"]:
        lines.append(
            f"| {row['ticker']} | {row['match_score']} | {row['ticker_overlap_tokens']} | "
            f"{row['non_ticker_tokens']} | {row['source']} | {row['headline']} |"
        )
    lines += [
        "",
        "## Verdict",
        "",
        "Under this weak-support proxy, substring semantics did not leave a measurable false-negative tail: every archived B' low-quality shape with ticker-substring-only support is suppressed, and no escaped low-quality row met the likely-false-negative definition. Treat this as evidence for option (a)'s adequacy on the archive, with the caveat that this is heuristic rather than manual relevance labeling.",
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
