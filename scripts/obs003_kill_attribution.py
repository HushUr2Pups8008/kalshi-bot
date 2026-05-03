"""PROFIT-OBS-003 trade-log kill attribution.

Read-only parser for OPPORTUNITY exits across the full trade-log archive.
It joins OPPORTUNITY records to nearby visible exits and BLEND_DECISION
records to attribute silent exits to readiness/blender kill reasons.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATHS = (
    _REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",
    _REPO_ROOT / "logs/trades",
)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-obs003-kill-attribution.md"
_WINDOW_SECONDS = 60.0


def _jsonl_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in paths:
        if root.is_file() and root.suffix == ".jsonl":
            files.append(root)
        elif root.exists():
            files.extend(sorted(root.rglob("*.jsonl")))
    return sorted(files)


def _load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for file in _jsonl_files(paths):
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["_file"] = str(file)
            records.append(row)
    return records


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("market_ticker") or "")


def _typed(records: list[dict[str, Any]], typ: str) -> list[dict[str, Any]]:
    return [r for r in records if (r.get("type") or r.get("record_type")) == typ]


def _index_by_ticker(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        row["_parsed_ts"] = ts
        indexed[_ticker(row)].append(row)
    for rows in indexed.values():
        rows.sort(key=lambda r: r["_parsed_ts"])
    return indexed


def _nearest(
    indexed: dict[str, list[dict[str, Any]]],
    ticker: str,
    ts: datetime,
    *,
    require_reason: bool = False,
) -> dict[str, Any] | None:
    best: tuple[float, dict[str, Any]] | None = None
    for row in indexed.get(ticker, []):
        if require_reason and not row.get("trade_blocked_reason"):
            continue
        delta = abs((row["_parsed_ts"] - ts).total_seconds())
        if delta <= _WINDOW_SECONDS and (best is None or delta < best[0]):
            best = (delta, row)
    return None if best is None else best[1]


def _example(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "ticker": _ticker(row),
        "ts": row.get("ts"),
        "headline": row.get("headline"),
        "reason": reason,
    }


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    records = _load_records(roots)
    opportunities = _typed(records, "OPPORTUNITY")
    exits = _typed(records, "SKIPPED") + _typed(records, "PAPER_TRADE")
    blends = _typed(records, "BLEND_DECISION")
    exit_index = _index_by_ticker(exits)
    blend_index = _index_by_ticker(blends)

    accounted_types: Counter[str] = Counter()
    accounted_reasons: Counter[str] = Counter()
    silent_reasons: Counter[str] = Counter()
    ticker_reason: dict[str, Counter[str]] = defaultdict(Counter)
    unattributed: list[dict[str, Any]] = []

    for opp in opportunities:
        ts = _parse_ts(opp.get("ts"))
        ticker = _ticker(opp)
        if ts is None:
            unattributed.append(_example(opp, "missing_opportunity_timestamp"))
            continue
        exit_row = _nearest(exit_index, ticker, ts)
        if exit_row is not None:
            exit_type = str(exit_row.get("type") or exit_row.get("record_type"))
            accounted_types[exit_type] += 1
            accounted_reasons[str(exit_row.get("reason") or exit_type)] += 1
            continue
        blend = _nearest(blend_index, ticker, ts, require_reason=True)
        if blend is not None:
            reason = str(blend["trade_blocked_reason"])
            silent_reasons[reason] += 1
            ticker_reason[ticker][reason] += 1
            continue
        unattributed.append(_example(opp, "no_matching_exit_or_blocked_blend_decision"))

    silent_total = sum(silent_reasons.values())
    accounted_total = sum(accounted_types.values())
    total = len(opportunities)
    top_tickers = sorted(
        ticker_reason.items(),
        key=lambda item: (-sum(item[1].values()), item[0]),
    )[:10]
    balanced = accounted_total + silent_total + len(unattributed) == total
    return {
        "paths": [str(p) for p in roots],
        "jsonl_file_count": len(_jsonl_files(roots)),
        "opportunity_total": total,
        "accounted_exits_total": accounted_total,
        "accounted_exit_types": dict(accounted_types.most_common()),
        "accounted_exit_reasons": dict(accounted_reasons.most_common()),
        "silent_exits_total": silent_total,
        "silent_exit_reasons": dict(silent_reasons.most_common()),
        "unattributed_total": len(unattributed),
        "unattributed_examples": unattributed[:5],
        "per_ticker_silent_exit_drivers": {
            ticker: dict(counter.most_common())
            for ticker, counter in top_tickers
        },
        "validation": {"balanced": balanced},
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "PROFIT-OBS-003 kill attribution",
        f"Total OPPORTUNITY: {report['opportunity_total']}",
        f"Accounted exits: {report['accounted_exits_total']} {report['accounted_exit_types']}",
        f"Silent exits: {report['silent_exits_total']}",
        f"Unattributed: {report['unattributed_total']}",
        f"Validation balanced: {report['validation']['balanced']}",
        "",
        "Silent-exit reasons",
    ]
    lines += [f"- {k}: {v}" for k, v in report["silent_exit_reasons"].items()] or ["- none"]
    lines += ["", "Top ticker silent-exit drivers"]
    for ticker, drivers in report["per_ticker_silent_exit_drivers"].items():
        lines.append(f"- {ticker}: {drivers}")
    if report["unattributed_examples"]:
        lines += ["", "Unattributed examples"]
        for row in report["unattributed_examples"]:
            lines.append(f"- {row['ticker']} {row['ts']}: {row['reason']}")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PROFIT-OBS-003 Kill Attribution",
        "",
        "Read-only attribution over the full trade-log archive.",
        "",
        "## Summary",
        "",
        f"- OPPORTUNITY total: {report['opportunity_total']}",
        f"- Accounted exits: {report['accounted_exits_total']}",
        f"- Silent exits: {report['silent_exits_total']}",
        f"- Unattributed exits: {report['unattributed_total']}",
        f"- Join validation balanced: {report['validation']['balanced']}",
        "",
        "## Silent-Exit Reasons",
        "",
        "| trade_blocked_reason | count | share_of_silent |",
        "| --- | ---: | ---: |",
    ]
    silent_total = report["silent_exits_total"] or 1
    for reason, count in report["silent_exit_reasons"].items():
        lines.append(f"| {reason} | {count} | {count / silent_total:.1%} |")
    lines += [
        "",
        "## Accounted Exits",
        "",
        "| exit_type | count |",
        "| --- | ---: |",
    ]
    for typ, count in report["accounted_exit_types"].items():
        lines.append(f"| {typ} | {count} |")
    lines += [
        "",
        "## Top Ticker Silent-Exit Drivers",
        "",
        "| ticker | drivers |",
        "| --- | --- |",
    ]
    for ticker, drivers in report["per_ticker_silent_exit_drivers"].items():
        lines.append(f"| {ticker} | {json.dumps(drivers, sort_keys=True)} |")
    lines += ["", "## Unattributed Examples", ""]
    if report["unattributed_examples"]:
        for row in report["unattributed_examples"]:
            lines.append(f"- {row['ticker']} {row['ts']}: {row['reason']}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, action="append", dest="paths", help="trade-log root/file; repeatable")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--write-report", type=Path, help="write markdown report")
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
