"""Pre-Wave-1 SKIPPED-rate baseline for OBS-003 monitoring."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_INPUT = _REPO_ROOT / "logs/trades/live/trades.jsonl"


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def analyze(path: Path = _DEFAULT_INPUT) -> dict[str, Any]:
    counts = Counter(str(row.get("type", "<missing>")) for row in _rows(path))
    skip_reasons = Counter(
        str(row.get("reason") or row.get("trade_blocked_reason") or "<missing>")
        for row in _rows(path)
        if row.get("type") == "SKIPPED"
    )
    opportunity = counts.get("OPPORTUNITY", 0)
    skipped = counts.get("SKIPPED", 0)
    paper_trade = counts.get("PAPER_TRADE", 0)
    denominator = opportunity + skipped + paper_trade
    return {
        "input_path": str(path),
        "total": {
            "opportunity": opportunity,
            "skipped": skipped,
            "paper_trade": paper_trade,
            "denominator": denominator,
            "skipped_rate_pct": round(100.0 * skipped / denominator, 1) if denominator else 0.0,
        },
        "skip_reasons": dict(skip_reasons),
        "event_counts": dict(counts),
    }


def render_markdown(report: dict[str, Any]) -> str:
    total = report["total"]
    reasons = [f"- `{key}`: {value}" for key, value in sorted(report["skip_reasons"].items())] or ["- None."]
    return "\n".join([
        "# Pre-Wave-1 SKIPPED-Rate Baseline",
        "",
        f"Input: `{report['input_path']}`",
        "",
        "## Summary",
        "",
        f"- OPPORTUNITY: `{total['opportunity']}`",
        f"- SKIPPED: `{total['skipped']}`",
        f"- PAPER_TRADE: `{total['paper_trade']}`",
        f"- Denominator: `{total['denominator']}`",
        f"- SKIPPED rate: `{total['skipped_rate_pct']}%`",
        "",
        "## Skip Reasons",
        "",
        "\n".join(reasons),
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
