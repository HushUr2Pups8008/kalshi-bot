"""Write a keyless politics history corpus under logs/edge_replay.

The file is gitignored. CI discovery looks only at that directory, and
this script refuses any other output path. It reads paper rows and the
fill report. It does not call Kalshi or xAI, and it does not copy
headlines, reasoning, client order ids, or credentials.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_SECRET_RE = re.compile(
    r"(BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]|github_pat_|Authorization:|api_key)",
)


def _series_prefix(ticker: str) -> str:
    return str(ticker or "").strip().split("-", 1)[0].strip()


def _fee_net_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return format(parsed, "f")


def _assert_runtime_corpus_path(output: Path) -> None:
    if (
        output.parent.name == "edge_replay"
        and output.parent.parent.name == "logs"
        and output.name.startswith("corpus_")
        and output.suffix == ".jsonl"
    ):
        return
    raise ValueError("corpus output must be logs/edge_replay/corpus_*.jsonl")


def paper_history_rows(db_path: Path) -> list[dict[str, object]]:
    """Read ticker, side, and fee-net. Leave headlines in the database."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    rows: list[dict[str, object]] = []
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        fetched = conn.execute(
            """
            SELECT
                t.ticker AS ticker,
                t.side AS side,
                t.resolved AS resolved,
                a.fee_net_pnl_dollars AS fee_net_pnl_dollars
            FROM paper_trades AS t
            LEFT JOIN paper_trade_accounting AS a
              ON a.trade_id = t.trade_id
            ORDER BY t.ts, t.ticker
            """
        ).fetchall()
    for record in fetched:
        ticker = str(record["ticker"] or "").strip()
        side = str(record["side"] or "").strip().lower()
        if not ticker or side not in {"yes", "no"}:
            continue
        series = _series_prefix(ticker)
        rows.append(
            {
                "row_id": f"paper:{ticker}",
                "trade_id": f"paper:{ticker}",
                "ticker": ticker,
                "side": side,
                "market_families": [series] if series else [],
                "fee_net_pnl_dollars": _fee_net_text(record["fee_net_pnl_dollars"]),
                "evidence_class": "desk_history",
                "source": "paper_trade_accounting",
                "resolved": int(record["resolved"] or 0),
            }
        )
    return rows


def live_fill_rows(report_path: Path) -> list[dict[str, object]]:
    """Parse the committed fill report. Drop the client order id."""
    rows: list[dict[str, object]] = []
    text = report_path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5 or _TIMESTAMP_RE.match(parts[0]) is None:
            continue
        ticker = parts[1]
        side = parts[2].lower()
        if side not in {"yes", "no"} or not ticker:
            continue
        series = _series_prefix(ticker)
        rows.append(
            {
                "row_id": f"live:{ticker}",
                "trade_id": f"live:{ticker}",
                "ticker": ticker,
                "side": side,
                "market_families": [series] if series else [],
                "price_cents": parts[3],
                "contracts": parts[4],
                "fee_net_pnl_dollars": None,
                "evidence_class": "desk_history",
                "source": "live_fill_report",
                "resolved": 0,
            }
        )
    return rows


def export_politics_history_corpus(
    *,
    paper_db: Path | None,
    fill_report: Path | None,
    output: Path,
) -> int:
    """Write the corpus and return the row count."""
    _assert_runtime_corpus_path(output)
    rows: list[dict[str, object]] = []
    if paper_db is not None:
        rows.extend(paper_history_rows(paper_db))
    if fill_report is not None:
        rows.extend(live_fill_rows(fill_report))
    payload = "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in rows
    )
    if _SECRET_RE.search(payload):
        raise ValueError("politics history corpus matched a secret pattern")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paper-db", type=Path, default=None)
    parser.add_argument("--fill-report", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/edge_replay/corpus_politics_history.jsonl"),
    )
    args = parser.parse_args(argv)
    count = export_politics_history_corpus(
        paper_db=args.paper_db,
        fill_report=args.fill_report,
        output=args.output,
    )
    print(f"politics_history_corpus rows={count} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
