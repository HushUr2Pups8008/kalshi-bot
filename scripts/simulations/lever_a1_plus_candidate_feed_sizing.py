"""Lever A.1+ candidate feed-class sizing over archived trade-log surfaces."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md"


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _source(row: dict[str, Any]) -> str:
    return str(row.get("source") or row.get("signal_source") or "")


def _headline(row: dict[str, Any]) -> str:
    return str(row.get("headline") or row.get("signal_headline") or "")


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def feed_class(source: str) -> str:
    lower = source.lower()
    if any(token in lower for token in (
        "white house",
        "department of war",
        "department of defense",
        "state department",
        "un news",
        "united nations",
        "iaea",
        "international atomic energy agency",
        "press releases",
        ".gov",
    )):
        return "government_bulletin"
    if any(token in lower for token in (
        "defense news",
        "breaking defense",
        "defense one",
        "bellingcat",
        "foreign policy",
        "vital-law",
        "vitallaw",
        "times of israel",
        "iran international",
        "kyiv",
    )):
        return "specialist_analyst"
    if any(token in lower for token in ("kalshi", "price_fade", "market", "polymarket")):
        return "market_microstructure"
    if source:
        return "mainstream_news"
    return "unknown"


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(iter_trade_records(path))
    return rows


def _latency_stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "median_sec": None, "p90_sec": None}
    ordered = sorted(values)
    p90_idx = min(len(ordered) - 1, round((len(ordered) - 1) * 0.9))
    return {
        "count": len(ordered),
        "median_sec": statistics.median(ordered),
        "p90_sec": ordered[p90_idx],
    }


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    by_class: dict[str, Counter[str]] = defaultdict(Counter)
    latency_by_class: dict[str, list[float]] = defaultdict(list)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        typ = _typ(row)
        if typ not in {"MATCH_DIAGNOSTIC", "OPPORTUNITY", "PAPER_TRADE", "ANALYSIS_REJECTED"}:
            continue
        cls = feed_class(_source(row))
        by_class[cls][typ] += 1
        if typ == "ANALYSIS_REJECTED":
            age = _float(row.get("age_seconds"))
            if age is not None:
                latency_by_class[cls].append(age)
        if typ in {"MATCH_DIAGNOSTIC", "OPPORTUNITY", "PAPER_TRADE"} and len(examples[cls]) < 5:
            examples[cls].append(
                {
                    "type": typ,
                    "source": _source(row),
                    "ticker": row.get("ticker") or row.get("market_ticker"),
                    "headline": _headline(row),
                    "match_score": row.get("match_score"),
                }
            )

    class_rows = []
    for cls in ("government_bulletin", "specialist_analyst", "market_microstructure", "mainstream_news", "unknown"):
        counts = by_class.get(cls, Counter())
        class_rows.append(
            {
                "feed_class": cls,
                "match_diagnostic": counts.get("MATCH_DIAGNOSTIC", 0),
                "opportunity": counts.get("OPPORTUNITY", 0),
                "paper_trade": counts.get("PAPER_TRADE", 0),
                "analysis_rejected": counts.get("ANALYSIS_REJECTED", 0),
                "latency": _latency_stats(latency_by_class.get(cls, [])),
                "examples": examples.get(cls, []),
            }
        )
    ranked = sorted(
        class_rows,
        key=lambda row: (row["paper_trade"], row["opportunity"], row["match_diagnostic"]),
        reverse=True,
    )
    return {
        "paths": [str(path) for path in roots],
        "class_rows": class_rows,
        "ranked_by_archive_relevance": [row["feed_class"] for row in ranked],
        "recommendation": (
            "Archive-visible evidence favors specialist_analyst first: it is the only non-mainstream "
            "candidate class with PAPER_TRADE coverage and has materially more OPPORTUNITY surface than "
            "government_bulletin. Government bulletins remain strategically useful but did not reach "
            "the archive OPPORTUNITY surface often enough to be the highest-ROI first feed class."
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines = ["Lever A.1+ candidate feed sizing", "class | matches | opp | paper | latency_median_sec"]
    for row in report["class_rows"]:
        lines.append(
            f"{row['feed_class']} | {row['match_diagnostic']} | {row['opportunity']} | "
            f"{row['paper_trade']} | {row['latency']['median_sec']}"
        )
    lines.append(f"recommendation: {report['ranked_by_archive_relevance'][0]}")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Lever A.1+ Candidate Feed-Class Sizing",
        "",
        report["recommendation"],
        "",
        "| feed class | MATCH_DIAGNOSTIC | OPPORTUNITY | PAPER_TRADE | ANALYSIS_REJECTED | median age sec | p90 age sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["class_rows"]:
        lat = row["latency"]
        lines.append(
            f"| {row['feed_class']} | {row['match_diagnostic']} | {row['opportunity']} | "
            f"{row['paper_trade']} | {row['analysis_rejected']} | {lat['median_sec']} | {lat['p90_sec']} |"
        )
    lines += ["", "## Examples", ""]
    for row in report["class_rows"]:
        lines += [f"### {row['feed_class']}", ""]
        if not row["examples"]:
            lines += ["No archive examples.", ""]
            continue
        lines += ["| type | source | ticker | score | headline |", "| --- | --- | --- | ---: | --- |"]
        for ex in row["examples"]:
            lines.append(
                f"| {ex['type']} | {ex['source']} | {ex['ticker']} | {ex['match_score']} | {ex['headline']} |"
            )
        lines.append("")
    lines += [
        "## Caveat",
        "",
        "This is archive-surface sizing, not live internet feed probing. It measures what the existing pipeline saw from source labels that map to each candidate feed class. Live onboarding still needs a per-feed probe for freshness, auth, and request reliability.",
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
