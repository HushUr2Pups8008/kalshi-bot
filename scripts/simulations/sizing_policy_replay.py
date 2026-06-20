#!/usr/bin/env python3
"""Replay resolved paper evidence against offline sizing policy grids.

Read-only simulator. Consumes JSON/JSONL evidence and reports counterfactual
Kelly sizing, exposure, cooldown, per-prefix cap, and resolved P&L metrics.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.kelly import contracts_from_dollars, kelly_bet

try:
    from config import cfg
except Exception:  # pragma: no cover - CLI fallback for isolated use
    cfg = None

try:
    from trading.executor import classify_skip_category
except Exception:  # pragma: no cover - keep replay usable without executor deps

    def classify_skip_category(reason: str | None) -> str:
        text = str(reason or "").lower()
        if "cooldown" in text:
            return "cooldown"
        if "concentration" in text or "per-prefix cap" in text:
            return "concentration"
        if "illiquid" in text or "near limit" in text or "price unavailable" in text:
            return "liquidity"
        return "unknown" if not text.strip() else "other"


@dataclass(frozen=True)
class PolicyParams:
    kelly_fraction: float
    floor_clamp_kelly_multiplier: float
    max_ticker_exposure_pct: float
    paper_ticker_cooldown: int
    max_open_positions_per_prefix: int

    @property
    def policy_id(self) -> str:
        return (
            f"kelly={self.kelly_fraction:g}|floor={self.floor_clamp_kelly_multiplier:g}|"
            f"ticker_exposure={self.max_ticker_exposure_pct:g}|cooldown={self.paper_ticker_cooldown}|"
            f"prefix_cap={self.max_open_positions_per_prefix}"
        )


@dataclass
class OpenPosition:
    ticker: str
    prefix: str
    notional: float
    closes_at: datetime | None


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def _record_ticker(record: dict[str, Any]) -> str:
    return str(_first(record, "ticker", "market_ticker", "market_id") or "UNKNOWN")


def _record_prefix(record: dict[str, Any]) -> str:
    raw = _first(record, "prefix", "ticker_prefix", "series_ticker", "series", "pm_domain_key")
    if raw:
        return str(raw)
    ticker = _record_ticker(record)
    return ticker.rsplit("-", 1)[0] if "-" in ticker else ticker


def _record_venue(record: dict[str, Any]) -> str:
    return str(_first(record, "venue", "market_venue", "exchange") or "kalshi")


def _record_side(record: dict[str, Any]) -> str:
    side = str(_first(record, "side", "executed_side", "chosen_side") or "yes").lower()
    return "no" if side == "no" else "yes"


def _yes_price_cents(record: dict[str, Any]) -> float | None:
    side = _record_side(record)
    raw = _float(
        _first(
            record,
            "yes_price_cents",
            "market_yes_price_cents",
            "yes_ask_cents",
            "price_cents",
            "executed_price_cents",
        )
    )
    if raw is None:
        return None
    if side == "no" and "yes_price_cents" not in record and "market_yes_price_cents" not in record:
        return 100.0 - raw
    return raw


def _executed_price_cents(record: dict[str, Any], yes_price: float) -> float:
    explicit = _float(_first(record, "executed_price_cents", "price_cents"))
    if explicit is not None:
        return explicit
    return 100.0 - yes_price if _record_side(record) == "no" else yes_price


def _estimated_probability(record: dict[str, Any]) -> float | None:
    raw = _float(
        _first(
            record,
            "estimated_probability",
            "probability",
            "p_yes",
            "estimated_prob",
            "executed_probability",
        )
    )
    if raw is None:
        return None
    if raw > 1.0:
        raw /= 100.0
    return raw if 0.0 <= raw <= 1.0 else None


def _source_multiplier(record: dict[str, Any]) -> float:
    explicit = _float(record.get("source_multiplier"))
    if explicit is not None:
        return explicit
    source_class = str(record.get("source_class") or "").lower()
    if source_class in {"official", "primary", "exchange"}:
        return 1.0
    if source_class in {"social", "reddit", "twitter", "x"}:
        return 0.75
    return 1.0


def _close_expired(open_positions: list[OpenPosition], now: datetime | None) -> list[OpenPosition]:
    if now is None:
        return open_positions
    return [position for position in open_positions if position.closes_at is None or position.closes_at > now]


def _is_floor_clamped(record: dict[str, Any]) -> bool:
    return bool(
        record.get("floor_clamped")
        or record.get("probability_floor_clamped")
        or record.get("estimated_probability_clamped")
    )


def _pnl_per_contract(record: dict[str, Any]) -> float:
    pnl = _float(_first(record, "pnl", "net_pnl", "realized_pnl", "settled_pnl"), 0.0) or 0.0
    original_contracts = max(1, _int(_first(record, "contracts", "contract_count", "quantity"), 1))
    return pnl / original_contracts


def _drawdown(cumulative: Iterable[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for value in cumulative:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    return max_drawdown


def load_evidence(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                records.extend(record for record in payload if isinstance(record, dict))
            elif isinstance(payload, dict):
                maybe_records = payload.get("records") or payload.get("trades")
                if isinstance(maybe_records, list):
                    records.extend(record for record in maybe_records if isinstance(record, dict))
                else:
                    records.append(payload)
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    return sorted(records, key=lambda record: str(record.get("ts") or record.get("timestamp") or ""))


def _available_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _select_expr(columns: set[str], aliases: tuple[str, ...], fallback: str) -> str:
    for column in aliases:
        if column in columns:
            return column
    return fallback


def load_paper_db(path: Path) -> list[dict[str, Any]]:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        columns = _available_columns(conn, "paper_trades")
        ts_expr = _select_expr(columns, ("ts", "created_at", "timestamp"), "NULL")
        ticker_expr = _select_expr(columns, ("ticker", "market_ticker"), "NULL")
        venue_expr = _select_expr(columns, ("venue",), "'kalshi'")
        side_expr = _select_expr(columns, ("side", "executed_side"), "'YES'")
        price_expr = _select_expr(
            columns,
            ("entry_price_cents", "executed_price_cents", "price_cents", "market_yes_price"),
            "NULL",
        )
        prob_expr = _select_expr(
            columns,
            ("estimated_prob", "estimated_probability", "probability"),
            "NULL",
        )
        edge_expr = _select_expr(columns, ("executed_edge", "edge"), "NULL")
        pnl_expr = _select_expr(columns, ("pnl_dollars", "pnl", "realized_pnl"), "NULL")
        contracts_expr = _select_expr(columns, ("contracts", "quantity"), "1")
        source_class_expr = _select_expr(columns, ("source_class",), "'unknown'")
        resolution_expr = _select_expr(
            columns,
            ("resolution_ts", "resolved_ts", "settled_ts", "close_ts", "close_time"),
            ts_expr,
        )
        resolved_filter = "resolved = 1" if "resolved" in columns else "pnl_dollars IS NOT NULL"
        rows = conn.execute(
            f"""
            SELECT
                {ts_expr} AS ts,
                {ticker_expr} AS ticker,
                {venue_expr} AS venue,
                {side_expr} AS side,
                {price_expr} AS price_cents,
                {prob_expr} AS estimated_probability,
                {edge_expr} AS edge,
                {pnl_expr} AS pnl,
                {contracts_expr} AS contracts,
                {source_class_expr} AS source_class,
                {resolution_expr} AS resolution_ts
            FROM paper_trades
            WHERE {resolved_filter}
            """
        )
        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            if not record.get("ticker") or record.get("pnl") is None:
                continue
            records.append(record)
        return sorted(records, key=lambda record: str(record.get("ts") or ""))


def _simulate_policy(
    records: list[dict[str, Any]],
    policy: PolicyParams,
    *,
    bankroll: float,
    max_bet_dollars: float,
    min_edge: float,
    min_bet_dollars: float,
    time_discount_half_life: float,
    time_discount_floor: float,
) -> dict[str, Any]:
    skipped = Counter()
    last_traded: dict[str, datetime] = {}
    open_positions: list[OpenPosition] = []
    cumulative: list[float] = []
    simulated_trades: list[dict[str, Any]] = []
    concentration_hits: list[dict[str, Any]] = []
    venue_split: dict[str, Counter[str]] = defaultdict(Counter)
    net_pnl = 0.0

    for record in records:
        ticker = _record_ticker(record)
        prefix = _record_prefix(record)
        venue = _record_venue(record)
        side = _record_side(record)
        ts = _parse_ts(_first(record, "ts", "timestamp", "created_at"))
        open_positions = _close_expired(open_positions, ts)

        reason: str | None = None
        yes_price = _yes_price_cents(record)
        probability = _estimated_probability(record)
        if yes_price is None or probability is None:
            reason = "price unavailable or probability unavailable"
        elif yes_price < 2.0 or yes_price > 98.0:
            reason = f"price {yes_price:.1f}c is near limit (too illiquid)"

        if reason is None and ts is not None and ticker in last_traded:
            elapsed = (ts - last_traded[ticker]).total_seconds()
            if elapsed < policy.paper_ticker_cooldown:
                reason = (
                    f"paper cooldown: last trade {elapsed / 3600:.1f}h ago "
                    f"(cooldown={policy.paper_ticker_cooldown // 3600}h)"
                )

        if reason is None and policy.max_open_positions_per_prefix > 0:
            open_in_prefix = [position for position in open_positions if position.prefix == prefix]
            if len(open_in_prefix) >= policy.max_open_positions_per_prefix:
                reason = (
                    f"per-prefix cap: {len(open_in_prefix)} open in {prefix} "
                    f"(cap={policy.max_open_positions_per_prefix})"
                )

        contracts = 0
        notional = 0.0
        if reason is None:
            assert yes_price is not None
            assert probability is not None
            _, kelly_dollars, capped_dollars = kelly_bet(
                estimated_probability=probability,
                market_price_cents=yes_price,
                bankroll=bankroll,
                kelly_fraction=policy.kelly_fraction,
                max_bet_dollars=max_bet_dollars,
                min_bet_dollars=min_bet_dollars,
                min_edge=min_edge,
                source_multiplier=_source_multiplier(record),
                confidence=_float(record.get("confidence"), 1.0) or 1.0,
                days_to_close=_float(record.get("days_to_close"), 14.0) or 14.0,
                time_discount_half_life=time_discount_half_life,
                time_discount_floor=time_discount_floor,
            )
            dollars = capped_dollars
            if _is_floor_clamped(record):
                dollars *= policy.floor_clamp_kelly_multiplier
            executed_price = _executed_price_cents(record, yes_price)
            contracts = contracts_from_dollars(dollars, executed_price) if dollars > 0 else 0
            notional = contracts * executed_price / 100.0
            if kelly_dollars <= 0.0 or capped_dollars <= 0.0 or contracts <= 0:
                reason = "capped_dollars=0 (below minimum bet size)"
            else:
                cap = bankroll * policy.max_ticker_exposure_pct
                current_ticker_exposure = sum(
                    position.notional for position in open_positions if position.ticker == ticker
                )
                if cap >= 0 and current_ticker_exposure + notional > cap:
                    reason = (
                        f"concentration: ticker exposure ${current_ticker_exposure + notional:.2f} "
                        f"exceeds cap ${cap:.2f}"
                    )

        if reason is not None:
            category = classify_skip_category(reason)
            skipped[category] += 1
            if category == "concentration":
                concentration_hits.append(
                    {
                        "ticker": ticker,
                        "prefix": prefix,
                        "category": category,
                        "reason": reason,
                    }
                )
            continue

        trade_pnl = _pnl_per_contract(record) * contracts
        net_pnl += trade_pnl
        cumulative.append(net_pnl)
        simulated_trades.append(
            {
                "ticker": ticker,
                "prefix": prefix,
                "venue": venue,
                "side": side,
                "contracts": contracts,
                "notional": round(notional, 2),
                "pnl": round(trade_pnl, 4),
                "source_class": str(record.get("source_class") or "unknown"),
            }
        )
        venue_split[venue]["trades"] += 1
        venue_split[venue]["net_pnl_cents"] += int(round(trade_pnl * 100))
        if ts is not None:
            last_traded[ticker] = ts
        open_positions.append(
            OpenPosition(
                ticker=ticker,
                prefix=prefix,
                notional=notional,
                closes_at=_parse_ts(_first(record, "resolution_ts", "settled_ts", "close_ts", "close_time")),
            )
        )

    venue_payload = {
        venue: {"trades": counts["trades"], "net_pnl": round(counts["net_pnl_cents"] / 100.0, 4)}
        for venue, counts in sorted(venue_split.items())
    }
    return {
        "policy": asdict(policy),
        "policy_id": policy.policy_id,
        "simulated_trade_count": len(simulated_trades),
        "simulated_trades": simulated_trades,
        "skipped_by_category": dict(sorted(skipped.items())),
        "net_pnl": round(net_pnl, 4),
        "expectancy": round(net_pnl / len(simulated_trades), 4) if simulated_trades else 0.0,
        "max_drawdown": round(_drawdown(cumulative), 4),
        "venue_split": venue_payload,
        "concentration_hits": concentration_hits,
    }


def evaluate_grid(
    records: list[dict[str, Any]],
    policies: list[PolicyParams],
    *,
    bankroll: float | None = None,
    max_bet_dollars: float | None = None,
    min_edge: float | None = None,
    min_bet_dollars: float | None = None,
) -> dict[str, Any]:
    bankroll = bankroll if bankroll is not None else float(getattr(cfg, "bankroll", 500.0))
    max_bet_dollars = max_bet_dollars if max_bet_dollars is not None else float(getattr(cfg, "max_bet_dollars", 75.0))
    min_edge = min_edge if min_edge is not None else float(getattr(cfg, "min_edge", 0.04))
    min_bet_dollars = min_bet_dollars if min_bet_dollars is not None else float(getattr(cfg, "min_bet_dollars", 0.0))
    time_discount_half_life = float(getattr(cfg, "time_discount_half_life", 14.0))
    time_discount_floor = float(getattr(cfg, "time_discount_floor", 0.20))
    return {
        "read_only": True,
        "no_live_policy_change": True,
        "input": {"records": len(records)},
        "results": [
            _simulate_policy(
                records,
                policy,
                bankroll=bankroll,
                max_bet_dollars=max_bet_dollars,
                min_edge=min_edge,
                min_bet_dollars=min_bet_dollars,
                time_discount_half_life=time_discount_half_life,
                time_discount_floor=time_discount_floor,
            )
            for policy in policies
        ],
    }


def _parse_float_grid(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def _parse_int_grid(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def _policy_grid(args: argparse.Namespace) -> list[PolicyParams]:
    policies: list[PolicyParams] = []
    for kelly_fraction in _parse_float_grid(args.kelly_fraction):
        for floor_multiplier in _parse_float_grid(args.floor_clamp_kelly_multiplier):
            for exposure_pct in _parse_float_grid(args.max_ticker_exposure_pct):
                for cooldown in _parse_int_grid(args.paper_ticker_cooldown):
                    for prefix_cap in _parse_int_grid(args.max_open_positions_per_prefix):
                        policies.append(
                            PolicyParams(
                                kelly_fraction=kelly_fraction,
                                floor_clamp_kelly_multiplier=floor_multiplier,
                                max_ticker_exposure_pct=exposure_pct,
                                paper_ticker_cooldown=cooldown,
                                max_open_positions_per_prefix=prefix_cap,
                            )
                        )
    return policies


def _default_input_paths() -> list[Path]:
    return [
        _REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl",
        _REPO_ROOT / "logs" / "trades" / "paper" / "trades.jsonl",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, dest="inputs", help="JSON/JSONL evidence path")
    parser.add_argument(
        "--paper-db",
        action="append",
        type=Path,
        dest="paper_dbs",
        help="read-only paper_trades.db snapshot",
    )
    parser.add_argument("--kelly-fraction", default=str(getattr(cfg, "kelly_fraction", 0.5)))
    parser.add_argument(
        "--floor-clamp-kelly-multiplier",
        default=str(getattr(cfg, "floor_clamp_kelly_multiplier", 0.5)),
    )
    parser.add_argument("--max-ticker-exposure-pct", default=str(getattr(cfg, "max_ticker_exposure_pct", 0.20)))
    parser.add_argument("--paper-ticker-cooldown", default=str(getattr(cfg, "paper_ticker_cooldown", 14400)))
    parser.add_argument(
        "--max-open-positions-per-prefix",
        default=str(getattr(cfg, "max_open_positions_per_prefix", 2)),
    )
    parser.add_argument("--bankroll", type=float, default=float(getattr(cfg, "bankroll", 500.0)))
    parser.add_argument("--max-bet-dollars", type=float, default=float(getattr(cfg, "max_bet_dollars", 75.0)))
    parser.add_argument("--min-edge", type=float, default=float(getattr(cfg, "min_edge", 0.04)))
    parser.add_argument("--min-bet-dollars", type=float, default=float(getattr(cfg, "min_bet_dollars", 0.0)))
    parser.add_argument("--json", action="store_true", help="Print JSON report after guardrail lines")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records: list[dict[str, Any]] = []
    for paper_db in args.paper_dbs or []:
        records.extend(load_paper_db(paper_db))
    paths = args.inputs or ([] if args.paper_dbs else _default_input_paths())
    records.extend(load_evidence(paths))
    report = evaluate_grid(
        records,
        _policy_grid(args),
        bankroll=args.bankroll,
        max_bet_dollars=args.max_bet_dollars,
        min_edge=args.min_edge,
        min_bet_dollars=args.min_bet_dollars,
    )
    print("read_only=true")
    print("no_live_policy_change=true")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for result in report["results"]:
            print(
                f"{result['policy_id']} trades={result['simulated_trade_count']} "
                f"skipped={result['skipped_by_category']} net_pnl={result['net_pnl']} "
                f"expectancy={result['expectancy']} max_drawdown={result['max_drawdown']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
