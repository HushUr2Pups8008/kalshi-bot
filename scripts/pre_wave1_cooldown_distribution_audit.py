"""Pre-Wave-1 cooldown SKIPPED distribution audit for OBS-005 monitoring."""

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
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _reason(row: dict[str, Any]) -> str:
    return str(row.get("reason") or row.get("trade_blocked_reason") or "")


def _bucket(reason: str) -> str:
    lowered = reason.lower()
    if "paper cooldown" in lowered:
        return "paper cooldown"
    if "live cooldown" in lowered or lowered.startswith("cooldown"):
        return "live cooldown"
    if "cooldown" in lowered:
        return "cooldown"
    return "other"


def analyze(path: Path = _DEFAULT_INPUT) -> dict[str, Any]:
    cooldown_rows = []
    for row in _rows(path):
        reason = _reason(row)
        if row.get("type") == "SKIPPED" and "cooldown" in reason.lower():
            cooldown_rows.append(row)
    return {
        "input_path": str(path),
        "cooldown_skips": len(cooldown_rows),
        "cooldown_reason_counts": dict(Counter(_bucket(_reason(row)) for row in cooldown_rows)),
        "by_ticker": dict(Counter(str(row.get("ticker") or "<missing>") for row in cooldown_rows)),
    }


def render_markdown(report: dict[str, Any]) -> str:
    reasons = [
        f"- `{key}`: {value}"
        for key, value in sorted(report["cooldown_reason_counts"].items())
    ] or ["- None."]
    tickers = [
        f"- `{key}`: {value}"
        for key, value in sorted(report["by_ticker"].items(), key=lambda item: (-item[1], item[0]))[:20]
    ] or ["- None."]
    return "\n".join([
        "# Pre-Wave-1 Cooldown Distribution Audit",
        "",
        f"Input: `{report['input_path']}`",
        "",
        "## Summary",
        "",
        f"- Cooldown SKIPPED rows: `{report['cooldown_skips']}`",
        "",
        "## Cooldown Reasons",
        "",
        "\n".join(reasons),
        "",
        "## Tickers",
        "",
        "\n".join(tickers),
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
