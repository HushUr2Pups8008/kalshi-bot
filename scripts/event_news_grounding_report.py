"""Veto precision for stored grounding verdicts.

n is the number of veto rows with a later fee-net outcome. Live grounding
is recommended only when that n is at least 20. This script does not call
Kalshi or xAI.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from utils.event_news_grounding_log import GROUNDING_LOG_PATH

RECOMMEND_LIVE_MIN_N = 20


def _fee(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number


def grounding_report(
    *,
    log_path: Path | None = None,
    outcomes: dict[str, Decimal] | None = None,
) -> dict[str, object]:
    target = log_path or GROUNDING_LOG_PATH
    rows: list[dict[str, object]] = []
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
    known = outcomes or {}
    vetoed = [
        row
        for row in rows
        if row.get("kind") == "veto" and row.get("verdict") == "no"
    ]
    matched: list[tuple[dict[str, object], Decimal]] = []
    for row in vetoed:
        fee_net = known.get(str(row.get("ticker") or ""))
        if fee_net is None:
            fee_net = _fee(row.get("outcome"))
        if fee_net is not None:
            matched.append((row, fee_net))
    n_outcome = len(matched)
    if n_outcome == 0:
        precision: object = "not_measured"
        delta: object = "not_measured"
    else:
        true_vetoes = sum(1 for _row, fee_net in matched if fee_net < 0)
        precision = true_vetoes / n_outcome
        delta = sum((fee_net for _row, fee_net in matched), Decimal("0"))
    return {
        "n_rows": len(rows),
        "n_vetoed": len(vetoed),
        "n": n_outcome,
        "precision": precision,
        "vetoed_fee_net": delta,
        "recommend_live": n_outcome >= RECOMMEND_LIVE_MIN_N,
    }


def format_grounding_report(report: dict[str, object]) -> str:
    recommend = "yes" if report["recommend_live"] else "no"
    return "\n".join(
        [
            "# Event-news grounding report",
            "",
            f"- n rows: {report['n_rows']}",
            f"- n vetoed: {report['n_vetoed']}",
            f"- n with outcome: {report['n']}",
            f"- veto precision: {report['precision']}",
            f"- vetoed fee-net: {report['vetoed_fee_net']}",
            f"- recommend live: {recommend}",
            "",
            "Recommend live only when n with outcome is at least 20.",
            "A veto is precise when the skipped trade later lost money.",
            "",
        ]
    )


def main() -> None:
    text = format_grounding_report(grounding_report())
    print(text, end="")


if __name__ == "__main__":
    main()
