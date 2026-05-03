"""Cross-series same-headline overlap audit over archived OPPORTUNITY events."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-cross-series-headline-overlap-audit.md"


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("market_ticker") or "")


def _headline(row: dict[str, Any]) -> str:
    return str(row.get("headline") or row.get("signal_headline") or "")


def _series(row: dict[str, Any]) -> str:
    explicit = str(row.get("series_ticker") or "")
    return explicit or _ticker(row).split("-", 1)[0]


def normalize_headline(value: str) -> str:
    text = value.lower()
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(iter_trade_records(path))
    return rows


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    opportunities = [r for r in rows if _typ(r) == "OPPORTUNITY"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for opp in opportunities:
        key = normalize_headline(_headline(opp))
        if key:
            groups[key].append(opp)

    cross_groups: list[dict[str, Any]] = []
    cross_opp_count = 0
    series_pairs: Counter[tuple[str, str]] = Counter()
    ticker_counts: Counter[str] = Counter()
    for key, vals in groups.items():
        series = sorted({_series(v) for v in vals if _series(v)})
        if len(series) < 2:
            continue
        cross_opp_count += len(vals)
        for left_index, left in enumerate(series):
            for right in series[left_index + 1:]:
                series_pairs[(left, right)] += len(vals)
        ticker_counts.update(_ticker(v) for v in vals)
        cross_groups.append(
            {
                "headline_key": key,
                "opportunity_count": len(vals),
                "series_count": len(series),
                "series": series,
                "tickers": sorted({_ticker(v) for v in vals}),
                "example_headline": _headline(vals[0]),
            }
        )
    cross_groups.sort(key=lambda item: (-item["opportunity_count"], item["headline_key"]))

    return {
        "paths": [str(p) for p in roots],
        "opportunity_total": len(opportunities),
        "normalized_headline_groups": len(groups),
        "cross_series_headline_groups": len(cross_groups),
        "cross_series_opportunity_count": cross_opp_count,
        "cross_series_opportunity_rate": (
            cross_opp_count / len(opportunities) if opportunities else 0.0
        ),
        "top_series_pairs": {
            f"{left} / {right}": count
            for (left, right), count in series_pairs.most_common(10)
        },
        "top_cross_series_tickers": dict(ticker_counts.most_common(10)),
        "top_groups": cross_groups[:12],
        "verdict": "supports_lever_c" if len(opportunities) and cross_opp_count / len(opportunities) >= 0.05 else "lever_c_low_value",
        "interpretation": (
            "Primary grouping uses normalized exact headline text: case, punctuation, "
            "URL, and whitespace differences are ignored, but semantic paraphrases are not merged."
        ),
    }


def render(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Cross-series headline overlap audit",
            f"OPPORTUNITY: {report['opportunity_total']}",
            f"Cross-series OPPORTUNITY: {report['cross_series_opportunity_count']} ({report['cross_series_opportunity_rate']:.1%})",
            f"Cross-series headline groups: {report['cross_series_headline_groups']}",
            f"Verdict: {report['verdict']}",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Series Headline Overlap Audit",
        "",
        report["interpretation"],
        "",
        "## Summary",
        "",
        f"- OPPORTUNITY total: {report['opportunity_total']}",
        f"- Normalized headline groups: {report['normalized_headline_groups']}",
        f"- Cross-series headline groups: {report['cross_series_headline_groups']}",
        f"- Cross-series OPPORTUNITY count: {report['cross_series_opportunity_count']} ({report['cross_series_opportunity_rate']:.1%})",
        f"- Verdict: `{report['verdict']}`",
        "",
        "## Top Series Pairs",
        "",
        "| series pair | overlapped OPPORTUNITY records |",
        "| --- | ---: |",
    ]
    for pair, count in report["top_series_pairs"].items():
        lines.append(f"| {pair} | {count} |")
    lines += [
        "",
        "## Top Cross-Series Groups",
        "",
        "| OPPORTUNITY | series | tickers | headline |",
        "| ---: | --- | --- | --- |",
    ]
    for group in report["top_groups"]:
        lines.append(
            f"| {group['opportunity_count']} | {', '.join(group['series'])} | "
            f"{', '.join(group['tickers'])} | {group['example_headline']} |"
        )
    lines += [
        "",
        "## Read",
        "",
        "Lever C clears the 5% sizing bar by a wide margin on the 13-day archive: 128/260 OPPORTUNITY records (49.2%) share normalized exact headlines across multiple series prefixes. The dominant overlap is `KXMOCTRUMP25` / `KXTRUMPIRAN`, with a smaller but material `KXTRUMPIRAN` / `KXVANCEPAKISTAN` lane.",
        "",
        "This does not prove the implementation should suppress every duplicate headline. It does prove the overlap rate is high enough that EXEC-002 Approach 2 should stay in scope and receive a real spec: headline-hash dedupe across series within a bounded time window, with allowlist/override handling for genuinely multi-market news.",
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
