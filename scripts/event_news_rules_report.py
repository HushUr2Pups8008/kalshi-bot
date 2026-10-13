"""Rules-check flags against later outcomes. Live stays off below n=20."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from utils.event_news_rules_check import RULES_LOG_PATH

RECOMMEND_LIVE_MIN_N = 20


def rules_report(
    *,
    log_path: Path | None = None,
    outcomes: dict[str, Decimal] | None = None,
) -> dict[str, object]:
    target = log_path or RULES_LOG_PATH
    rows: list[dict[str, object]] = []
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
    known = outcomes or {}
    flagged = [row for row in rows if row.get("mismatch") is True]
    matched: list[Decimal] = []
    for row in flagged:
        fee_net = known.get(str(row.get("ticker") or ""))
        if fee_net is None and row.get("outcome") is not None:
            try:
                fee_net = Decimal(str(row.get("outcome")))
            except (InvalidOperation, ValueError):
                fee_net = None
        if fee_net is not None:
            matched.append(fee_net)
    n_outcome = len(matched)
    if n_outcome == 0:
        precision: object = "not_measured"
        delta: object = "not_measured"
    else:
        precision = sum(1 for fee_net in matched if fee_net < 0) / n_outcome
        delta = sum(matched, Decimal("0"))
    return {
        "n_rows": len(rows),
        "n_flagged": len(flagged),
        "n": n_outcome,
        "precision": precision,
        "flagged_fee_net": delta,
        "recommend_live": n_outcome >= RECOMMEND_LIVE_MIN_N,
    }


def format_rules_report(report: dict[str, object]) -> str:
    recommend = "yes" if report["recommend_live"] else "no"
    return "\n".join(
        [
            "# Event-news rules-check report",
            "",
            f"- n rows: {report['n_rows']}",
            f"- n flagged: {report['n_flagged']}",
            f"- n with outcome: {report['n']}",
            f"- flag precision: {report['precision']}",
            f"- flagged fee-net: {report['flagged_fee_net']}",
            f"- recommend live: {recommend}",
            "",
            "Recommend live only when n with outcome is at least 20.",
            "A flag is precise when the skipped trade later lost money.",
            "",
        ]
    )


def main() -> None:
    print(format_rules_report(rules_report()), end="")


if __name__ == "__main__":
    main()
