#!/usr/bin/env python3
"""Build an edge-replay decision dataset from resolved markets and local bot artifacts.

P-8 additions (LD-7 / CR-F, P0-REG-022): module-level helpers
``filter_post_p0_rows`` and ``assert_corpus_quality_or_raise`` ship the
post-P0 cohort cut and silent-50 contamination guard. See the dedicated
section below.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Union

logger = logging.getLogger(__name__)


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    """Return the first value in row where the key exists and the value is not None."""
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


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


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
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
            # Bug 1 fix: map llm_confidence from paper_trades DB into corpus.confidence
            "confidence": _as_float(trade.get("llm_confidence")),
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
            # Bug 2 fix (ticker alias): BLEND_DECISION emits market_ticker, not ticker
            ticker = str(row.get("ticker") or row.get("market_ticker") or "")
            if ticker not in markets:
                continue
            kind = str(row.get("type") or row.get("event") or "log").lower()
            if kind in {"paper_trade", "trade", "paper_execution"}:
                continue
            decision_ts = row.get("ts") or row.get("created_at") or row.get("decided_at")
            if kind == "log" and not decision_ts:
                continue
            out.append(
                {
                    **_market_fields(ticker, markets),
                    "decision_ts": decision_ts,
                    "decision_kind": kind,
                    "side": str(row.get("side") or row.get("llm_direction") or "").lower() or None,
                    "contracts": row.get("contracts"),
                    # Bug 2 fix (model_prob aliases): BLEND_DECISION emits blended_p;
                    # OPPORTUNITY emits model_probability or estimated_probability.
                    # Canonical key wins; _first_present falls through on None, NOT on falsy.
                    "model_prob": _as_float(
                        _first_present(
                            row,
                            "model_prob",
                            "estimated_prob",
                            "probability",
                            "blended_p",
                            "model_probability",
                            "estimated_probability",
                        )
                    ),
                    # Bug 2 fix (market_yes_price alias): SKIPPED emits market_price.
                    # _first_present used so 0.0 is not silently dropped by falsy or-chain.
                    "market_yes_price": _as_float(
                        _first_present(
                            row,
                            "market_yes_price",
                            "yes_price",
                            "price_cents",
                            "market_price",
                        )
                    ),
                    "edge": _as_float(row.get("edge")),
                    "signal_source": row.get("signal_source") or row.get("source") or "unknown",
                    "signal_type": row.get("signal_type") or row.get("type") or "unknown",
                    "news_class": row.get("news_class") or row.get("source_class") or "unknown",
                    "headline": row.get("headline") or row.get("signal_headline"),
                    "trade_gate_reason": row.get("reason") or row.get("skip_reason"),
                    # Stage 1b fix: BLEND_DECISION emits blended_confidence; canonical key wins.
                    # _first_present is None-aware so confidence=0.0 is NOT silently dropped.
                    "confidence": _as_float(
                        _first_present(
                            row,
                            "confidence",
                            "blended_confidence",
                            "llm_confidence",
                        )
                    ),
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


def load_historical_prices(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for ticker, rows in raw.items():
        normalized = []
        for row in rows:
            ts = _parse_ts(row.get("ts") or row.get("created_time") or row.get("trade_time"))
            price = _as_float(row.get("yes_price") or row.get("price") or row.get("yes_price_cents"))
            if ts is not None and price is not None:
                normalized.append({"ts": ts, "yes_price": price})
        by_ticker[ticker] = sorted(normalized, key=lambda row: row["ts"])
    return by_ticker


def price_at_decision(prices: dict[str, list[dict[str, Any]]], ticker: str, decision_ts: Any) -> float | None:
    target = _parse_ts(decision_ts)
    if target is None:
        return None
    candidates = [row for row in prices.get(ticker, []) if row["ts"] <= target]
    if not candidates:
        return None
    return float(candidates[-1]["yes_price"])


def apply_historical_prices(rows: list[dict[str, Any]], prices: dict[str, list[dict[str, Any]]]) -> None:
    for row in rows:
        if row.get("market_yes_price") is not None:
            continue
        price = price_at_decision(prices, str(row.get("ticker") or ""), row.get("decision_ts"))
        if price is None:
            continue
        row["market_yes_price"] = price
        model_prob = _as_float(row.get("model_prob"))
        if model_prob is not None:
            row["edge"] = model_prob - (price / 100.0)


def expand_trade_logs(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in glob.glob(pattern, recursive=True))
    return sorted(set(paths))


# --- P-8 LD-7 / CR-F cohort filtering ----------------------------------------
# These two helpers are the post-P0 cohort cut and the silent-50 contamination
# guard required by tests/test_kalshi_pricing_p0_replay.py. They are public,
# importable without running main(), and intentionally free of module-level
# state. Orchestrator wiring (reading bot_state.p0_price_fix_deployed_ts and
# passing it in) lands in a later packet — these helpers only consume the
# sentinel value, never read bot_state directly.


def filter_post_p0_rows(
    rows: Iterable[dict],
    sentinel: Union[str, datetime],
) -> Iterator[dict]:
    """Drop pre-P0 contaminated rows from a replay corpus by timestamp
    sentinel (LD-7 / CR-F). Keeps rows where ``row["decision_ts"]`` parses
    to a datetime >= ``sentinel``. Drops everything earlier.

    Authoritative cohort boundary is ``bot_state.p0_price_fix_deployed_ts``
    (the timestamp *value*, NOT field presence). SQLite ``paper_trades`` rows
    never carry ``p0_contract_version``; filtering on that field would
    silently leak pre-P0 SQLite rows. The ts sentinel is the only correct
    predicate.

    Args:
        rows: iterable of dicts with at minimum ``decision_ts`` (ISO-8601
            string).
        sentinel: ISO-8601 string OR aware ``datetime``. The boundary is
            inclusive: ``decision_ts >= sentinel`` keeps the row.

    Yields:
        Rows that meet the post-sentinel predicate. Rows missing
        ``decision_ts`` or with unparseable ``decision_ts`` are dropped
        (fail-closed) with a debug-level log entry.
    """
    if isinstance(sentinel, datetime):
        sentinel_dt = sentinel
    else:
        try:
            sentinel_dt = datetime.fromisoformat(str(sentinel).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"filter_post_p0_rows: unparseable sentinel {sentinel!r}: {exc}"
            ) from exc

    if sentinel_dt.tzinfo is None:
        raise ValueError(
            "filter_post_p0_rows: sentinel must be timezone-aware (UTC); "
            f"got naive {sentinel_dt!r}"
        )

    for row in rows:
        raw_ts = row.get("decision_ts")
        if raw_ts is None:
            logger.debug("filter_post_p0_rows: dropping row missing decision_ts")
            continue
        try:
            row_dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except ValueError:
            logger.debug(
                "filter_post_p0_rows: dropping row with unparseable decision_ts=%r",
                raw_ts,
            )
            continue
        if row_dt >= sentinel_dt:
            yield row


def assert_corpus_quality_or_raise(rows: Iterable[dict]) -> None:
    """Guard against a silent-50 contaminated replay corpus (P0-REG-022).

    A post-P0 replay corpus must contain either:
      * at least one row with ``price_cents != 50`` AND
        ``price_available is True``, OR
      * every ``price_cents == 50`` or ``price_available is False`` row
        carries an explicit ``skip_reason`` (or
        ``_fixture_encodes_real_50c`` marker per the OPPORTUNITY emit
        contract).

    Raises:
        AssertionError: when the corpus consists entirely of unmarked
            silent-50 / silent-unavailable rows, or is empty.
    """
    rows_list = list(rows)
    if not rows_list:
        raise AssertionError(
            "replay corpus is empty — POST_FIX_NEW cut produced zero rows"
        )

    real_executable_seen = any(
        isinstance(r.get("price_cents"), (int, float))
        and not isinstance(r.get("price_cents"), bool)
        and r["price_cents"] != 50
        and r.get("price_available") is True
        for r in rows_list
    )
    if real_executable_seen:
        return

    suspects = [
        r for r in rows_list
        if r.get("price_cents") == 50 or r.get("price_available") is False
    ]
    unmarked = [
        r for r in suspects
        if not (r.get("skip_reason") or r.get("_fixture_encodes_real_50c"))
    ]
    if unmarked:
        raise AssertionError(
            f"replay corpus failed P0-REG-022: {len(unmarked)} of "
            f"{len(rows_list)} rows are silent-50 / silent-unavailable "
            f"without explicit skip_reason or _fixture_encodes_real_50c marker"
        )


# --- end P-8 LD-7 / CR-F cohort filtering ------------------------------------


def build_replay_dataset(
    *,
    markets_path: Path,
    paper_trades_db: Path | None,
    trade_logs: list[Path],
    evidence_store_db: Path | None = None,
    historical_prices_path: Path | None = None,
) -> list[dict[str, Any]]:
    markets = load_resolved_markets(markets_path)
    rows: list[dict[str, Any]] = []
    if paper_trades_db is not None:
        rows.extend(_paper_trade_rows(paper_trades_db, markets))
    rows.extend(_log_rows(trade_logs, markets))
    if evidence_store_db is not None:
        rows.extend(_evidence_store_rows(evidence_store_db, markets))
    apply_historical_prices(rows, load_historical_prices(historical_prices_path))
    return sorted(rows, key=lambda row: (row.get("decision_ts") or "", row.get("ticker") or ""))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", required=True, type=Path, help="Normalized resolved markets JSON.")
    parser.add_argument("--paper-trades-db", type=Path, default=Path("data/paper_trades.db"))
    parser.add_argument("--evidence-store-db", type=Path, default=Path("data/evidence_store.db"))
    parser.add_argument("--historical-prices", type=Path, help="Optional JSON mapping ticker to decision-time YES price rows.")
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
        historical_prices_path=args.historical_prices,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
