#!/usr/bin/env python3
"""Build an edge-replay decision dataset from resolved markets and local bot artifacts."""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
from pathlib import Path
from typing import Any


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"yes", "true", "1"}:
        return True
    if text in {"no", "false", "0"}:
        return False
    return None


def load_resolved_markets(path: Path) -> dict[str, dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["ticker"]): row for row in rows}


def _market_fields(ticker: str, markets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    market = markets[ticker]
    return {
        "ticker": ticker,
        "market_title": market.get("title"),
        "series_ticker": market.get("series_ticker") or ticker.split("-")[0],
        "resolved_yes": _as_bool(market.get("resolved_yes")),
        "resolution": "yes" if _as_bool(market.get("resolved_yes")) is True else "no",
    }


def _paper_trade_rows(db_path: Path, markets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)")}
        if not columns:
            return []
        rows = conn.execute("SELECT * FROM paper_trades ORDER BY ts ASC").fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        trade = dict(row)
        ticker = str(trade.get("ticker") or "")
        if ticker not in markets:
            continue
        item = {
            **_market_fields(ticker, markets),
            "decision_ts": trade.get("ts"),
            "decision_kind": "paper_trade",
            "side": str(trade.get("side") or "").lower() or None,
            "contracts": trade.get("contracts"),
            "model_prob": _as_float(trade.get("estimated_prob")),
            "market_yes_price": _as_float(trade.get("market_yes_price") or trade.get("price_cents")),
            "edge": _as_float(trade.get("edge")),
            "signal_source": trade.get("signal_source") or "unknown",
            "signal_type": trade.get("signal_type") or "unknown",
            "news_class": trade.get("news_class") or "unknown",
            "headline": trade.get("signal_headline"),
            "observed_pnl": _as_float(trade.get("pnl_dollars")),
            "observed_resolved": _as_bool(trade.get("resolved")),
            "fast_lane_p": _as_float(trade.get("fast_lane_p")),
            "accumulation_p": _as_float(trade.get("accumulation_p")),
            "structural_p": _as_float(trade.get("structural_p")),
        }
        out.append(item)
    return out


def _log_rows(paths: list[Path], markets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ticker = str(row.get("ticker") or "")
            if ticker not in markets:
                continue
            kind = str(row.get("type") or row.get("event") or "log").lower()
            out.append(
                {
                    **_market_fields(ticker, markets),
                    "decision_ts": row.get("ts") or row.get("created_at") or row.get("decided_at"),
                    "decision_kind": kind,
                    "side": str(row.get("side") or row.get("llm_direction") or "").lower() or None,
                    "contracts": row.get("contracts"),
                    "model_prob": _as_float(row.get("estimated_prob") or row.get("model_prob") or row.get("probability")),
                    "market_yes_price": _as_float(row.get("market_yes_price") or row.get("yes_price") or row.get("price_cents")),
                    "edge": _as_float(row.get("edge")),
                    "signal_source": row.get("signal_source") or row.get("source") or "unknown",
                    "signal_type": row.get("signal_type") or row.get("type") or "unknown",
                    "news_class": row.get("news_class") or row.get("source_class") or "unknown",
                    "headline": row.get("headline") or row.get("signal_headline"),
                    "trade_gate_reason": row.get("reason") or row.get("skip_reason"),
                }
            )
    return out


def _evidence_store_rows(db_path: Path, markets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"dossier_updates", "evidence"}.issubset(tables):
            return []
        rows = conn.execute(
            """
            SELECT
                du.market_ticker,
                du.dossier_version,
                du.created_ts,
                du.prior_estimate,
                du.new_estimate,
                du.update_delta,
                du.confidence_after,
                du.update_type AS dossier_update_type,
                du.llm_called,
                ev.source,
                ev.source_class,
                ev.headline,
                ev.ingested_ts,
                ev.update_type AS evidence_update_type
            FROM dossier_updates du
            LEFT JOIN evidence ev
                ON ev.evidence_id = du.trigger_evidence_id
            ORDER BY du.created_ts ASC
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        update = dict(row)
        ticker = str(update.get("market_ticker") or "")
        if ticker not in markets:
            continue
        out.append(
            {
                **_market_fields(ticker, markets),
                "decision_ts": update.get("created_ts") or update.get("ingested_ts"),
                "decision_kind": "dossier_update",
                "side": None,
                "contracts": None,
                "model_prob": _as_float(update.get("new_estimate")),
                "market_yes_price": None,
                "edge": _as_float(update.get("update_delta")),
                "signal_source": update.get("source") or "unknown",
                "signal_type": update.get("dossier_update_type") or update.get("evidence_update_type") or "unknown",
                "news_class": update.get("source_class") or "unknown",
                "headline": update.get("headline"),
                "confidence": _as_float(update.get("confidence_after")),
                "llm_called": _as_bool(update.get("llm_called")),
                "dossier_version": update.get("dossier_version"),
            }
        )
    return out


def expand_trade_logs(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in glob.glob(pattern, recursive=True))
    return sorted(set(paths))


def build_replay_dataset(
    *,
    markets_path: Path,
    paper_trades_db: Path | None,
    trade_logs: list[Path],
    evidence_store_db: Path | None = None,
) -> list[dict[str, Any]]:
    markets = load_resolved_markets(markets_path)
    rows: list[dict[str, Any]] = []
    if paper_trades_db is not None:
        rows.extend(_paper_trade_rows(paper_trades_db, markets))
    rows.extend(_log_rows(trade_logs, markets))
    if evidence_store_db is not None:
        rows.extend(_evidence_store_rows(evidence_store_db, markets))
    return sorted(rows, key=lambda row: (row.get("decision_ts") or "", row.get("ticker") or ""))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", required=True, type=Path, help="Normalized resolved markets JSON.")
    parser.add_argument("--paper-trades-db", type=Path, default=Path("data/paper_trades.db"))
    parser.add_argument("--evidence-store-db", type=Path, default=Path("data/evidence_store.db"))
    parser.add_argument("--trade-log", action="append", default=["logs/**/*.jsonl"], help="JSONL path/glob; repeatable.")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = build_replay_dataset(
        markets_path=args.markets,
        paper_trades_db=args.paper_trades_db,
        trade_logs=expand_trade_logs(args.trade_log),
        evidence_store_db=args.evidence_store_db,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
