"""Pre-Wave-1 per-ticker PAPER_TRADE rate baseline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_INPUT = _REPO_ROOT / "logs/trades/live/trades.jsonl"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def analyze(path: Path = _DEFAULT_INPUT) -> dict[str, Any]:
    trades = [row for row in _rows(path) if row.get("type") == "PAPER_TRADE"]
    timestamps = [ts for row in trades if (ts := _parse_ts(row.get("ts")))]
    if len(timestamps) >= 2:
        window_days = max(1.0, (max(timestamps) - min(timestamps)).total_seconds() / 86400.0)
    else:
        window_days = 1.0
    counts = Counter(str(row.get("ticker") or "<missing>") for row in trades)
    tickers = [
        {
            "ticker": ticker,
            "trade_count": count,
            "trades_per_day": round(count / window_days, 3),
        }
        for ticker, count in counts.most_common()
    ]
    return {
        "input_path": str(path),
        "total_paper_trades": len(trades),
        "window_days": round(window_days, 3),
        "tickers": tickers,
    }


def render_markdown(report: dict[str, Any]) -> str:
    rows = ["| ticker | trades | trades/day |", "|---|---:|---:|"]
    for row in report["tickers"][:50]:
        rows.append(f"| `{row['ticker']}` | {row['trade_count']} | {row['trades_per_day']} |")
    if not report["tickers"]:
        rows.append("| none | 0 | 0 |")
    return "\n".join([
        "# Pre-Wave-1 Per-Ticker Trade-Rate Baseline",
        "",
        f"Input: `{report['input_path']}`",
        "",
        "## Summary",
        "",
        f"- PAPER_TRADE rows: `{report['total_paper_trades']}`",
        f"- Window days: `{report['window_days']}`",
        "",
        "## Per-Ticker Rates",
        "",
        "\n".join(rows),
    ]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=_DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = analyze(args.input)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    else:
        sys.stdout.write(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
