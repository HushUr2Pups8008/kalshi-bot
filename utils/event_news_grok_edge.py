"""Paper-only Grok edge scan. ``live=True`` returns before any call and never submits an order."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from numbers import Real
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

import requests

from utils.event_news_desk_flags import (
    EVENT_NEWS_GROK_EDGE_SCAN,
    EVENT_NEWS_GROK_EXIT_WATCH,
    EVENT_NEWS_GROK_KALSHI_SERIES,
    HARD_MAX_CONTRACTS,
    event_news_desk_flag_enabled,
    event_news_yes_floor_cents,
)
from utils.event_news_grok_flags import EVENT_NEWS_BAND_FLOOR_CENTS
from utils.event_news_maker_shadow import resting_bid_cents
from utils.event_news_grok_veto import (
    GROK_MODEL,
    VETO_DECISION_LOG_PATH,
    XAI_API_KEY_ENV,
    grade_veto_decisions,
)
from utils.event_news_research import (
    EVENT_NEWS_COHORT_ID,
    event_news_fee_net_edge,
    event_news_paper_score_decision,
    event_news_series_ticker,
    snapshot_ask_cents,
)
from utils.series_universe import (
    effective_mode,
    get_universe,
    market_edge_min,
    series_ticker_matches,
    x_search_series,
)
from utils.logger import get_logger

log = get_logger("event_news_grok_edge")
_SPEND_LOCK = threading.Lock()

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
GROK_EDGE_TIMEOUT_SECONDS = 20.0
GROK_DAILY_USD_CAP = "GROK_DAILY_USD_CAP"
DEFAULT_DAILY_USD_CAP = Decimal("3")
_HARD_USD_CAP = Decimal("3")
_PAPER_FLOOR_USD = Decimal("0.75")
_PROFIT_SHARE = Decimal("0.25")
_PNL_CACHE_NAME = "grok_settled_fee_net.json"
_PM_PAUSE_UNTIL = datetime(2026, 11, 3, tzinfo=timezone.utc)
_SETTLED_RESULTS = frozenset({"yes", "no", "determined", "finalized", "settled"})
EVENT_NEWS_GROK_EDGE_SCAN_MINUTES = "EVENT_NEWS_GROK_EDGE_SCAN_MINUTES"
DEFAULT_SCAN_MINUTES = 180
MEASURED_CYCLE_COST_USD = Decimal("0.30324255")
MIN_SCAN_MINUTES = 5
PAPER_QUEUE_CONTRACTS = 4
MIN_CALL_RESERVE_USD = Decimal("0.05")
RESERVE_P90_MULTIPLIER = Decimal("1.5")
COST_TICKS_PER_USD = Decimal("10000000000")
MEASURED_COST_WINDOW = 20
AGREE_ABS = Decimal("0.05")
MAX_MARKETS_PER_CYCLE = 1
SCAN_TOP_N = 3
SCAN_CLOSE_WINDOW = timedelta(hours=72)
PAPER_CLOSE_WINDOW = timedelta(days=30)
SCAN_ASK_MAX_CENTS = 94
SCREEN_EDGE_CENTS = 5
ASK_MOVE_CENTS = 5
_PAPER_SCORE_ENV = {
    "EVENT_NEWS_YES_FLOOR_CENTS": "55",
    "EVENT_NEWS_EXCLUDE_EXACT_35": "true",
}
GROK_MAX_SEARCH_RESULTS = "GROK_MAX_SEARCH_RESULTS"
DEFAULT_MAX_SEARCH_RESULTS = 8
MAX_SEARCH_RESULTS_CAP = 20
# x_search handles, no @. Truth Social is not an X handle; Factbase is its change trigger.
TRUMP_X_HANDLES = ("realDonaldTrump", "POTUS", "WhiteHouse")
_SCAN_TOOLS = ("web_search", "x_search")

CALL_LOG_PATH = Path("logs/state/grok_edge_calls.jsonl")
CANDIDATE_LOG_PATH = Path("logs/state/grok_edge_candidates.jsonl")
COMPARISON_LOG_PATH = Path("logs/state/grok_edge_comparisons.jsonl")
EXIT_LOG_PATH = Path("logs/state/grok_edge_exits.jsonl")
MARK_LOG_PATH = EXIT_LOG_PATH.with_name("grok_edge_marks.jsonl")
POSITION_SCORE_LOG_PATH = Path("logs/state/grok_edge_position_scores.jsonl")
SPEND_PATH = Path("logs/state/grok_edge_spend.json")
PAPER_DB_PATH = Path("logs/state/grok_paper.db")
RAW_DIR = Path("logs/state/grok_raw")
CACHE_PATH = Path("logs/state/grok_edge_cache.json")
SCREEN_LOG_PATH = Path("logs/state/grok_edge_screen.jsonl")
CROSS_VENUE_LOG_PATH = Path("logs/state/cross_venue.jsonl")
DAILY_REPORT_PATH = Path("logs/reports/grok-edge-daily.md")
_POLYMARKET_US = "polymarket_us"
_NEAR_CERTAIN_ASK_CENTS = 91
_CACHE_LOCK = threading.Lock()
_SETTLED_STATUSES = frozenset({"finalized", "settled", "determined"})
_DB_LOCK = threading.Lock()

_GROUNDED_SYSTEM = (
    "Use web search and X search. "
    "prob is the probability the market resolves YES, from 0 to 1. "
    "confidence is from 0 to 1. sources is a list of http urls you used. "
    "rationale is one short sentence. A prob without a source url is invalid."
)
_UNGROUNDED_SYSTEM = (
    "Do not browse. "
    "prob is the probability the market resolves YES, from 0 to 1. "
    "confidence is from 0 to 1. sources is an empty list. "
    "rationale is one short sentence."
)
_EDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "prob": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "sources": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["prob", "confidence", "sources", "rationale"],
}
_EXIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "contrary": {"type": "boolean"},
        "prob": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "sources": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["contrary", "prob", "confidence", "sources", "rationale"],
}
_SERIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "latest_post_id": {"type": "string"},
        "summary": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "markets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ticker": {"type": "string"},
                    "prob": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
                "required": ["ticker", "prob", "confidence", "rationale"],
            },
        },
    },
    "required": ["latest_post_id", "summary", "sources", "markets"],
}
_SECRET_KEY = re.compile(
    r"(authorization|api[_-]?key|token|secret|signature|password)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GrokEdgeCall:
    ticker: str
    grounded: bool
    probability: Decimal | None
    confidence: Decimal | None
    verdict: str
    sources: tuple[str, ...]
    tools: tuple[str, ...]
    prompt_hash: str
    latency_ms: int
    cost_usd: Decimal
    skip_reason: str | None
    detail: str = ""

def _yaml_edge_min(series: str) -> Decimal | None:
    return market_edge_min(SimpleNamespace(series_ticker=series, ticker=series))

def _floored_cents(dollars: Decimal) -> int:
    return int((dollars * Decimal(100)).to_integral_value(rounding=ROUND_FLOOR))

def _view_venue(view: Mapping[str, Any]) -> str:
    raw = view.get("venue")
    if hasattr(raw, "value"):
        raw = raw.value
    venue = str(raw or "kalshi").strip().lower()
    if venue == _POLYMARKET_US:
        return _POLYMARKET_US
    return "kalshi"

def _kalshi_series_paid(view: Mapping[str, Any], environ: Mapping[str, str] | None) -> bool:
    """Polymarket paper scans ignore the Kalshi series switch."""
    if _view_venue(view) == _POLYMARKET_US:
        return True
    return event_news_desk_flag_enabled(
        EVENT_NEWS_GROK_KALSHI_SERIES,
        live=False,
        environ=environ,
    )

def _fee_net(
    probability: float | Decimal | str,
    ask_cents: int,
    contracts: int = 1,
    venue: str = "kalshi",
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Kalshi stays on event_news_fee_net_edge. Polymarket uses gateway.fee."""
    if venue != _POLYMARKET_US:
        return event_news_fee_net_edge(probability, ask_cents, contracts)
    if isinstance(ask_cents, bool) or type(ask_cents) is not int:
        return None
    if isinstance(contracts, bool) or type(contracts) is not int or contracts < 1:
        return None
    if ask_cents < 1 or ask_cents > 99:
        return None
    try:
        p_side = Decimal(str(probability))
    except Exception:
        return None
    if not p_side.is_finite() or p_side <= 0 or p_side >= 1:
        return None
    price = Decimal(ask_cents) / Decimal(100)
    quantity = Decimal(contracts)
    try:
        from trading.venue_gateway import PolymarketGateway

        fee = PolymarketGateway(None).fee(price, quantity)
    except Exception:
        return None
    if not isinstance(fee, Decimal) or not fee.is_finite() or fee < 0:
        return None
    gross = p_side - price
    return gross, fee, gross - (fee / quantity)

def paper_queue_decision(
    *,
    probability: Decimal | None,
    ask_cents: int | None,
    side: str,
    contracts: int = PAPER_QUEUE_CONTRACTS,
    edge_min: Decimal | None = None,
    yes_floor_cents: int = 55,
    venue: str = "kalshi",
) -> tuple[str, int | None]:
    """Return (queue, edge) or (skip_reason, edge). Fee-net must clear yaml edge_min."""
    if probability is None:
        return "no_probability", None
    if ask_cents is None or isinstance(ask_cents, bool) or not isinstance(ask_cents, int):
        return "bad_ask", None
    if ask_cents < EVENT_NEWS_BAND_FLOOR_CENTS:
        return "ask_band", None
    if side == "yes" and ask_cents < yes_floor_cents:
        return "yes_floor", None
    if (
        isinstance(contracts, bool)
        or not isinstance(contracts, int)
        or contracts < 1
        or contracts > HARD_MAX_CONTRACTS
    ):
        return "size_cap", None
    if venue == _POLYMARKET_US and ask_cents >= _NEAR_CERTAIN_ASK_CENTS:
        return "near_certain_ask", None
    scored = _fee_net(probability, ask_cents, contracts, venue)
    if scored is None or edge_min is None:
        edge = None if scored is None else _floored_cents(scored[2])
        return "bad_edge", edge
    edge = _floored_cents(scored[2])
    if scored[2] < edge_min:
        return "edge", edge
    return "queue", edge

def verdicts_agree(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= AGREE_ABS

def _polymarket_paused(series: str, now: datetime | None = None) -> bool:
    name = str(series or "").strip().upper()
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return name.startswith("PM") and moment.astimezone(timezone.utc) < _PM_PAUSE_UNTIL

def politics_scan_series(extra: Sequence[str] = (), now: datetime | None = None) -> tuple[str, ...]:
    universe = get_universe()
    if universe.failed:
        return ()
    allowed = [
        entry.series
        for entry in universe.entries
        if entry.mode != "off" and not _polymarket_paused(entry.series, now)
    ]
    allowed_set = set(allowed)
    seen: list[str] = []
    for raw in (*allowed, *extra):
        series = str(raw or "").strip().upper()
        if not series or series in seen or series not in allowed_set:
            continue
        seen.append(series)
    return tuple(seen)

def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _ask_cents(raw: Any) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        cents = raw
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        cents = int(round(value * 100)) if value <= 1 else int(round(value))
    if 1 <= cents <= 99:
        return cents
    return None

def _market_asks(market: Any) -> tuple[int | None, int | None]:
    if isinstance(market, Mapping):
        return _ask_cents(market.get("yes_ask")), _ask_cents(market.get("no_ask"))
    return snapshot_ask_cents(market)

def _market_liquidity(market: Any) -> Decimal:
    names = ("liquidity_dollars", "volume_fp", "volume_24h_fp", "volume", "open_interest")
    for name in names:
        raw = market.get(name) if isinstance(market, Mapping) else getattr(market, name, None)
        number = _decimal(raw)
        if number is not None and number > 0:
            return number
    return Decimal("0")

def _market_identity(market: Any) -> tuple[str, str, str, datetime | None]:
    if isinstance(market, Mapping):
        ticker = str(market.get("ticker") or "").strip().upper()
        series = str(market.get("series") or market.get("series_ticker") or "").strip().upper()
        status = str(market.get("status") or "").strip().lower()
        close = _parse_utc(market.get("close_time"))
    else:
        ticker = str(getattr(market, "ticker", "") or "").strip().upper()
        series = event_news_series_ticker(market)
        status = str(getattr(market, "status", "") or "").strip().lower()
        close = _parse_utc(getattr(market, "close_time", None))
        if close is None:
            close = _parse_utc(getattr(market, "expected_expiration_time", None))
    if not series and ticker:
        series = ticker.split("-", 1)[0]
    return ticker, series, status, close

def rank_politics_markets(
    markets: Sequence[Any],
    *,
    now: datetime,
    series: Sequence[str] | None = None,
    close_window: timedelta | None = None,
) -> list[Any]:
    """36c-94c asks inside the close window. Response status ``open`` is not tradeable."""
    allowed = {item.strip().upper() for item in (series or politics_scan_series(now=now)) if item.strip()}
    window_end = now + (SCAN_CLOSE_WINDOW if close_window is None else close_window)
    ranked: list[tuple[Decimal, datetime, str, Any]] = []
    for market in markets:
        ticker, market_series, status, close = _market_identity(market)
        if status != "active" or close is None:
            continue
        if market_series not in allowed:
            continue
        if close <= now or close > window_end:
            continue
        yes_ask, no_ask = _market_asks(market)
        asks = [
            ask
            for ask in (yes_ask, no_ask)
            if ask is not None and EVENT_NEWS_BAND_FLOOR_CENTS <= ask <= SCAN_ASK_MAX_CENTS
        ]
        if not asks:
            continue
        ranked.append((-_market_liquidity(market), close, ticker, market))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in ranked]

def comparison_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = [row for row in rows if _pair(row) is not None]
    empty = {
        "n": 0, "agree_pct": None, "settled_n": 0,
        "grounded_hits": None, "ungrounded_hits": None, "status": "not_enough_data",
    }
    if not usable:
        return empty
    agreements = settled = grounded_hits = ungrounded_hits = 0
    for row in usable:
        pair = _pair(row)
        assert pair is not None
        grounded, ungrounded = pair
        agreements += int(verdicts_agree(grounded, ungrounded))
        outcome = _outcome(row.get("outcome"))
        if outcome is None:
            continue
        settled += 1
        left, right = abs(grounded - outcome), abs(ungrounded - outcome)
        grounded_hits += int(left <= right)
        ungrounded_hits += int(right <= left)
    return {
        "n": len(usable),
        "agree_pct": Decimal(agreements) / Decimal(len(usable)) * Decimal(100),
        "settled_n": settled,
        "grounded_hits": grounded_hits if settled else None,
        "ungrounded_hits": ungrounded_hits if settled else None,
        "status": "ok" if settled else "not_enough_data",
    }

_YES_PROBABILITY_BANDS = (
    (Decimal(0), Decimal("0.2"), "0.0-0.2"),
    (Decimal("0.2"), Decimal("0.4"), "0.2-0.4"),
    (Decimal("0.4"), Decimal("0.6"), "0.4-0.6"),
    (Decimal("0.6"), Decimal("0.8"), "0.6-0.8"),
    (Decimal("0.8"), Decimal("1.0000001"), "0.8-1.0"),
)

def brier_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [
        (probability, outcome)
        for row in rows
        if (probability := _decimal(row.get("probability"))) is not None
        and (outcome := _outcome(row.get("outcome"))) is not None
    ]
    if not scored:
        return {"status": "not_enough_data", "n": 0, "brier": None, "buckets": []}
    brier = sum((p - o) ** 2 for p, o in scored) / Decimal(len(scored))
    buckets = []
    for low, high, name in _YES_PROBABILITY_BANDS:
        members = [(p, o) for p, o in scored if low <= p < high]
        count = len(members)
        buckets.append({
            "bucket": name,
            "n": count,
            "mean_probability": None if not count else sum(p for p, _o in members) / Decimal(count),
            "outcome_rate": None if not count else sum(o for _p, o in members) / Decimal(count),
        })
    return {"status": "ok", "n": len(scored), "brier": brier, "buckets": buckets}

def ev_by_edge_bucket(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Decimal]] = {"below_8": [], "8_11": [], "12_19": [], "20_plus": []}
    for row in rows:
        edge, pnl = row.get("edge_cents"), _decimal(row.get("pnl_dollars"))
        if isinstance(edge, bool) or not isinstance(edge, int) or pnl is None:
            continue
        key = "below_8" if edge < 8 else "8_11" if edge < 12 else "12_19" if edge < 20 else "20_plus"
        groups[key].append(pnl)
    if not any(groups.values()):
        return {"status": "not_enough_data", "buckets": []}
    buckets = []
    for name, values in groups.items():
        item: dict[str, Any] = {
            "bucket": name, "n": len(values), "mean_ev": None,
            "ci95_low": None, "ci95_high": None, "status": "not_enough_data",
        }
        if len(values) >= 2:
            count = Decimal(len(values))
            mean = sum(values) / count
            half = Decimal("1.96") * (sum((v - mean) ** 2 for v in values) / (count - 1)).sqrt() / count.sqrt()
            item.update(mean_ev=mean, ci95_low=mean - half, ci95_high=mean + half, status="ok")
        buckets.append(item)
    return {"status": "ok", "buckets": buckets}

def exit_versus_hold(
    *, side: str, bid_cents: int, settlement_yes: bool, contracts: int, entry_ask_cents: int,
) -> dict[str, int] | None:
    bad_count = isinstance(contracts, bool) or not isinstance(contracts, int)
    if side not in {"yes", "no"} or bad_count or not 1 <= contracts <= HARD_MAX_CONTRACTS:
        return None
    entry = event_news_fee_net_edge(Decimal("0.5"), entry_ask_cents, contracts)
    exited = event_news_fee_net_edge(Decimal("0.5"), bid_cents, contracts)
    if entry is None or exited is None:
        return None
    quantity = Decimal(contracts)
    entry_cost = quantity * Decimal(entry_ask_cents) / Decimal(100) + entry[1]
    exit_proceeds = quantity * Decimal(bid_cents) / Decimal(100) - exited[1]
    held = quantity * Decimal(1 if (side == "yes") == settlement_yes else 0)
    def _cents(dollars: Decimal) -> int:
        return int((dollars * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))

    return {"exit_cents": _cents(exit_proceeds - entry_cost), "hold_cents": _cents(held - entry_cost)}

def grok_max_search_results(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get(GROK_MAX_SEARCH_RESULTS) or "").strip()
    if not raw.isdigit():
        return DEFAULT_MAX_SEARCH_RESULTS
    value = int(raw)
    if value < 1:
        return DEFAULT_MAX_SEARCH_RESULTS
    return min(value, MAX_SEARCH_RESULTS_CAP)

def _search_turns(limit: int) -> int:
    return 2 if limit <= 8 else 3

def _trump_x_series(series: str) -> bool:
    return any(series_ticker_matches(series, name) for name in x_search_series())

def _grounded_tool_payload(
    series: str,
    now: datetime | None,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    start = (moment - SCAN_CLOSE_WINDOW).date().isoformat()
    end = (moment.date() + timedelta(days=1)).isoformat()
    x_tool: dict[str, Any] = {"type": "x_search", "from_date": start, "to_date": end}
    if _trump_x_series(series):
        x_tool["allowed_x_handles"] = list(TRUMP_X_HANDLES)
    return [{"type": "web_search"}, x_tool], _search_turns(limit)

def _search_limit_sentence(limit: int) -> str:
    return (
        f" Use at most {limit} sources. "
        f"Set web_search num_results and x_keyword_search limit to {limit} or less. "
        "Do not fetch threads or user profiles. Do not search twice."
    )

def latest_truth_social_fingerprint(timeout: float = 8.0) -> str | None:
    try:
        import urllib.parse

        from analysis.truth_social_forecast import _ENDPOINT, _fetch_json

        params = urllib.parse.urlencode(
            {
                "platform": "truth social",
                "sort": "date",
                "sort_order": "desc",
                "page": 1,
                "format": "json",
            }
        )
        payload = _fetch_json(f"{_ENDPOINT}?{params}", timeout=timeout)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    record = rows[0]
    observed = _parse_utc(record.get("date"))
    if observed is None:
        return None
    if datetime.now(timezone.utc) - observed > SCAN_CLOSE_WINDOW:
        return "none"
    url = str(record.get("post_url") or record.get("url") or "").strip()
    stamp = str(record.get("date") or "").strip()
    return url or stamp or None

def _read_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"tickers": {}}
    if not isinstance(data, dict):
        return {"tickers": {}}
    if not isinstance(data.get("tickers"), dict):
        data["tickers"] = {}
    return data

def _cache_get(path: Path, ticker: str) -> dict[str, Any] | None:
    row = _read_cache(path)["tickers"].get(ticker)
    return row if isinstance(row, dict) else None

def _cache_put(path: Path, ticker: str, row: Mapping[str, Any]) -> None:
    with _CACHE_LOCK:
        data = _read_cache(path)
        data["tickers"][ticker] = dict(row)
        try:
            _write_json(path, data)
        except OSError:
            return

def _ask_moved(old: Any, new: Any) -> bool:
    if isinstance(old, bool) or isinstance(new, bool):
        return False
    if not isinstance(old, int) or not isinstance(new, int):
        return False
    return abs(old - new) >= ASK_MOVE_CENTS

def _cache_fresh(
    row: Mapping[str, Any] | None,
    view: Mapping[str, Any],
    fingerprint: str | None,
    now: datetime,
) -> bool:
    if not row:
        return False
    cached_at = _parse_utc(row.get("ts"))
    if cached_at is None or _day(cached_at) != _day(now):
        return False
    sources = row.get("sources")
    if not isinstance(sources, list) or not sources:
        return False
    if fingerprint is not None and str(row.get("post_id") or "") != fingerprint:
        return False
    if _ask_moved(row.get("yes_ask"), view.get("yes_ask")):
        return False
    if _ask_moved(row.get("no_ask"), view.get("no_ask")):
        return False
    scans = row.get("scans")
    count = len(scans) if isinstance(scans, list) else 0
    if count < 2:
        return False
    if stable_scan_probability(scans) is None:
        return count >= 3
    return _decimal(row.get("prob")) is not None

def stable_scan_probability(scans: Sequence[Mapping[str, Any]]) -> Decimal | None:
    probs: list[Decimal] = []
    for row in scans:
        if not isinstance(row, Mapping):
            continue
        value = _decimal(row.get("prob"))
        if value is None:
            continue
        probs.append(value)
    if len(probs) < 2:
        return None
    probs = probs[-3:]
    if abs(probs[-1] - probs[-2]) > Decimal("0.10"):
        return None
    ordered = sorted(probs)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)

def _prior_scans(row: Mapping[str, Any] | None, now: datetime) -> list[dict[str, Any]]:
    if not row:
        return []
    cached_at = _parse_utc(row.get("ts"))
    if cached_at is None or _day(cached_at) != _day(now):
        return []
    scans = row.get("scans")
    if isinstance(scans, list):
        kept = [
            {"prob": format(value, "f"), "post_id": str(item.get("post_id") or "")}
            for item in scans
            if isinstance(item, dict) and (value := _decimal(item.get("prob"))) is not None
        ]
        return kept[-3:]
    legacy = _decimal(row.get("prob"))
    if legacy is None:
        return []
    return [{"prob": format(legacy, "f"), "post_id": str(row.get("post_id") or "")}]

def _tie_break_due(
    row: Mapping[str, Any] | None,
    view: Mapping[str, Any],
    fingerprint: str | None,
    now: datetime,
) -> bool:
    if not row:
        return False
    cached_at = _parse_utc(row.get("ts"))
    if cached_at is None or _day(cached_at) != _day(now):
        return False
    sources = row.get("sources")
    if not isinstance(sources, list) or not sources:
        return False
    if fingerprint is not None and str(row.get("post_id") or "") != fingerprint:
        return False
    if _ask_moved(row.get("yes_ask"), view.get("yes_ask")):
        return False
    if _ask_moved(row.get("no_ask"), view.get("no_ask")):
        return False
    scans = _prior_scans(row, now)
    if len(scans) != 2:
        return False
    return stable_scan_probability(scans) is None

def _tie_break_budget_ok(
    spend_path: Path,
    call_log_path: Path,
    environ: Mapping[str, str] | None,
    now: datetime | None,
) -> bool:
    """True when one grounded reserve still fits. An unreadable ledger does not."""
    env = os.environ if environ is None else environ
    cap = _call_cap(_daily_cap(env), "series", _trailing_fee_net(spend_path, now))
    reserve = call_reserve_usd(call_log_path, grounded=True)
    if reserve <= 0 or cap < 0:
        return False
    today = _day(now)
    try:
        existing = (
            json.loads(spend_path.read_text(encoding="utf-8"))
            if spend_path.exists()
            else {}
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(existing, dict):
        return False
    if existing.get("date") != today:
        spent = Decimal("0")
    else:
        spent = _decimal(existing.get("spent_usd"))
        if spent is None or spent < 0:
            return False
    return spent + reserve <= cap

def _next_scans(
    path: Path,
    view: Mapping[str, Any],
    probability: Decimal,
    fingerprint: str | None,
    moment: datetime,
) -> list[dict[str, Any]]:
    prior = _prior_scans(_cache_get(path, str(view["ticker"])), moment)
    sample = {"prob": format(probability, "f"), "post_id": fingerprint or ""}
    return [*prior, sample][-3:]

def _screen_side(probability: Decimal, ask: int | None, venue: str = "kalshi") -> int | None:
    if type(ask) is not int:
        return None
    scored = _fee_net(probability, ask, PAPER_QUEUE_CONTRACTS, venue)
    if scored is None:
        return None
    return _floored_cents(scored[2])

def _screen_edges(
    probability: Decimal,
    yes_ask: int | None,
    no_ask: int | None,
    venue: str = "kalshi",
) -> tuple[int | None, int | None]:
    return (
        _screen_side(probability, yes_ask, venue),
        _screen_side(Decimal(1) - probability, no_ask, venue),
    )

def _screen_passes(yes_edge: int | None, no_edge: int | None) -> bool:
    return (yes_edge is not None and yes_edge >= SCREEN_EDGE_CENTS) or (
        no_edge is not None and no_edge >= SCREEN_EDGE_CENTS
    )

def _log_screen(path: Path, record: Mapping[str, Any]) -> None:
    _append_jsonl(path, record)

def _series_prompt(views: Sequence[Mapping[str, Any]], limit: int) -> str:
    lead = "These Kalshi markets share one series. "
    if views and _view_venue(views[0]) == _POLYMARKET_US:
        lead = "These Polymarket US markets share one series. "
    lines = [
        lead + "Search once for the posts and news they share. "
        f"Use at most {limit} sources. "
        "latest_post_id is the newest Truth Social or X post id, or an empty string. "
        "summary is the shared fact. Include the Truth Social post count when these are count buckets. "
        "markets has one object per ticker. prob is that ticker's YES probability from 0 to 1."
    ]
    for view in views:
        lines.append(_market_prompt(view))
    return "\n".join(lines)

def _cached_call(view: Mapping[str, Any], row: Mapping[str, Any]) -> GrokEdgeCall | None:
    probability = _decimal(row.get("prob"))
    sources = row.get("sources")
    if probability is None or not isinstance(sources, list) or not sources:
        return None
    http_sources = tuple(
        str(item) for item in sources if str(item).startswith(("http://", "https://"))
    )
    if not http_sources:
        return None
    return GrokEdgeCall(
        ticker=str(view["ticker"]),
        grounded=True,
        probability=probability,
        confidence=_decimal(row.get("confidence")),
        verdict="YES" if probability >= Decimal("0.5") else "NO",
        sources=http_sources,
        tools=_SCAN_TOOLS,
        prompt_hash="cache",
        latency_ms=0,
        cost_usd=Decimal("0"),
        skip_reason=None,
        detail="cache",
    )

def _remember_grounded(
    path: Path,
    view: Mapping[str, Any],
    probability: Decimal,
    confidence: Decimal | None,
    sources: Sequence[str],
    fingerprint: str | None,
    summary: str,
    now: datetime | None,
    scans: Sequence[Mapping[str, Any]],
) -> None:
    decision = stable_scan_probability(scans)
    _cache_put(
        path,
        str(view["ticker"]),
        {
            "ts": _stamp(now),
            "prob": None if decision is None else format(decision, "f"),
            "raw_prob": format(probability, "f"),
            "scans": [dict(item) for item in scans],
            "confidence": None if confidence is None else format(confidence, "f"),
            "sources": list(sources),
            "yes_ask": view.get("yes_ask"),
            "no_ask": view.get("no_ask"),
            "post_id": fingerprint or "",
            "series": view.get("series") or "",
            "summary": summary[:500],
        },
    )

def _ground_miss(reason: str, prompt_hash: str = "") -> dict[str, Any]:
    return {
        "skip_reason": reason,
        "sources": [],
        "markets": [],
        "summary": "",
        "prompt_hash": prompt_hash,
    }

def _ground_series(
    views: Sequence[Mapping[str, Any]],
    *,
    environ: Mapping[str, str] | None,
    post: Callable[..., Any] | None,
    call_log_path: Path,
    spend_path: Path,
    raw_dir: Path,
    now: datetime | None,
) -> dict[str, Any] | None:
    if not views:
        return None
    limit = grok_max_search_results(environ)
    series = str(views[0].get("series") or "")
    result = _call_grok(
        ticker=series or str(views[0].get("ticker") or ""),
        prompt=_series_prompt(views, limit),
        system=_GROUNDED_SYSTEM,
        grounded=True,
        environ=environ,
        post=post,
        call_log_path=call_log_path,
        spend_path=spend_path,
        raw_dir=raw_dir,
        now=now,
        kind="series",
        series=series,
        expected_tickers=tuple(str(view.get("ticker") or "") for view in views),
        venue=_view_venue(views[0]),
    )
    if result is None or result.skip_reason or not result.detail:
        reason = "malformed" if result is None else (result.skip_reason or "malformed")
        return _ground_miss(reason, "" if result is None else result.prompt_hash)
    try:
        parsed = json.loads(result.detail)
    except json.JSONDecodeError:
        return _ground_miss("malformed", result.prompt_hash)
    if not isinstance(parsed, dict):
        return _ground_miss("malformed", result.prompt_hash)
    sources = parsed.get("sources")
    markets = parsed.get("markets")
    if not isinstance(sources, list) or not sources or not isinstance(markets, list):
        return _ground_miss("malformed", result.prompt_hash)
    parsed["skip_reason"] = None
    parsed["prompt_hash"] = result.prompt_hash
    return parsed

def run_paper_edge_scan(
    markets: Sequence[Any],
    *,
    live: bool,
    allowlist: Sequence[str],
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    call_log_path: Path = CALL_LOG_PATH,
    candidate_log_path: Path = CANDIDATE_LOG_PATH,
    comparison_log_path: Path = COMPARISON_LOG_PATH,
    spend_path: Path = SPEND_PATH,
    db_path: Path | None = None,
    raw_dir: Path = RAW_DIR,
    max_markets: int = MAX_MARKETS_PER_CYCLE,
    now: datetime | None = None,
    open_tickers: Sequence[str] = (),
    cache_path: Path = CACHE_PATH,
    screen_log_path: Path = SCREEN_LOG_PATH,
    post_lookup: Callable[[], str | None] | None = None,
) -> dict[str, int]:
    counts = {
        "examined": 0,
        "queued": 0,
        "skipped": 0,
        "screen_skipped": 0,
        "cache_hits": 0,
        "grounded_calls": 0,
    }
    if live or not event_news_desk_flag_enabled(
        EVENT_NEWS_GROK_EDGE_SCAN,
        live=False,
        environ=environ,
    ):
        return {"examined": 0, "queued": 0, "skipped": 0}
    allowed = {str(series).strip().upper() for series in allowlist if str(series).strip()}
    floor = event_news_yes_floor_cents(environ)
    moment = now or datetime.now(timezone.utc)
    fingerprint = latest_truth_social_fingerprint() if post_lookup is None else post_lookup()
    open_set = {str(ticker).strip() for ticker in open_tickers if str(ticker).strip()}
    pending: list[dict[str, Any]] = []
    def _skip(view, reason, *, prompt_hash="", probability=None, sources=()):
        _store_decision(
            view, reason=reason, candidate_log_path=candidate_log_path, db_path=db_path,
            now=moment, probability=probability, prompt_hash=prompt_hash, sources=sources,
        )
        counts["skipped"] += 1

    for market in markets:
        view = _market_view(market)
        if view is None or view["series"] not in allowed or view["status"] != "active":
            continue
        quote_block = _paper_gate_reason(market, view, None, now=moment)
        if quote_block:
            _skip(view, quote_block)
            continue
        cached = _cache_get(cache_path, view["ticker"])
        if _cache_fresh(cached, view, fingerprint, moment):
            counts["cache_hits"] += 1
            cached_scans = cached.get("scans") if isinstance(cached, Mapping) else None
            decision = stable_scan_probability(cached_scans if isinstance(cached_scans, list) else [])
            _log_screen(
                screen_log_path,
                {
                    "ts": _stamp(moment),
                    "ticker": view["ticker"],
                    "series": view["series"],
                    "venue": _view_venue(view),
                    "decision": "cache_hit",
                    "reason": "cache_fresh",
                    "yes_ask": view.get("yes_ask"),
                    "no_ask": view.get("no_ask"),
                    "cached_prob": None if cached is None else cached.get("prob"),
                    "post_id": fingerprint,
                },
            )
            if decision is None:
                raw = _decimal(cached.get("raw_prob")) if isinstance(cached, Mapping) else None
                _skip(view, "prob_unstable", prompt_hash="cache", probability=raw)
                continue
            reused = _cached_call(view, cached or {})
            cached_block = None
            if reused is not None and reused.probability is not None:
                cached_block = _paper_gate_reason(market, view, float(reused.probability), now=moment)
            if cached_block:
                _skip(
                    view,
                    cached_block,
                    prompt_hash="cache",
                    probability=None if reused is None else reused.probability,
                    sources=() if reused is None else reused.sources,
                )
            elif reused is not None and _queue_best_side(
                view,
                reused,
                yes_floor=floor,
                candidate_log_path=candidate_log_path,
                db_path=db_path,
                now=moment,
            ):
                counts["queued"] += 1
            else:
                counts["skipped"] += 1
            continue
        if not _kalshi_series_paid(view, environ):
            counts["examined"] += 1
            _log_screen(
                screen_log_path,
                {
                    "ts": _stamp(moment),
                    "ticker": view["ticker"],
                    "series": view["series"],
                    "venue": _view_venue(view),
                    "decision": "skip",
                    "reason": "kalshi_series_off",
                    "yes_ask": view.get("yes_ask"),
                    "no_ask": view.get("no_ask"),
                    "post_id": fingerprint,
                },
            )
            _store_decision(
                view,
                reason="kalshi_series_off",
                candidate_log_path=candidate_log_path,
                db_path=db_path,
                now=moment,
                ask=view["yes_ask"],
            )
            counts["screen_skipped"] += 1
            counts["skipped"] += 1
            if counts["examined"] >= max_markets:
                break
            continue
        if _tie_break_due(cached, view, fingerprint, moment):
            counts["examined"] += 1
            affordable = _tie_break_budget_ok(
                spend_path, call_log_path, environ, moment
            )
            _log_screen(
                screen_log_path,
                {
                    "ts": _stamp(moment),
                    "ticker": view["ticker"],
                    "series": view["series"],
                    "venue": _view_venue(view),
                    "decision": "ground" if affordable else "skip",
                    "reason": "tie_break" if affordable else "prob_unstable",
                    "yes_ask": view.get("yes_ask"),
                    "no_ask": view.get("no_ask"),
                    "post_id": fingerprint,
                },
            )
            if affordable:
                pending.append(
                    {
                        "view": view,
                        "ungrounded": None,
                        "market": market,
                        "tie_break": True,
                    }
                )
            else:
                _skip(view, "prob_unstable")
            if counts["examined"] >= max_markets:
                break
            continue
        counts["examined"] += 1
        ungrounded = _call_grok(
            ticker=view["ticker"],
            prompt=_market_prompt(view),
            system=_UNGROUNDED_SYSTEM,
            grounded=False,
            environ=environ,
            post=post,
            call_log_path=call_log_path,
            spend_path=spend_path,
            raw_dir=raw_dir,
            now=moment,
            venue=_view_venue(view),
        )
        held = view["ticker"] in open_set
        yes_edge = no_edge = None
        probability = None
        if (
            ungrounded is not None
            and ungrounded.skip_reason is None
            and ungrounded.probability is not None
        ):
            probability = ungrounded.probability
            yes_edge, no_edge = _screen_edges(
                probability, view["yes_ask"], view["no_ask"], _view_venue(view)
            )
        passed = held or (probability is not None and _screen_passes(yes_edge, no_edge))
        reason = "open_position" if held else ("edge" if passed else "below_screen")
        if probability is None and not held:
            reason = (
                ungrounded.skip_reason
                if ungrounded is not None and ungrounded.skip_reason
                else "no_probability"
            )
        _log_screen(
            screen_log_path,
            {
                "ts": _stamp(moment),
                "ticker": view["ticker"],
                "series": view["series"],
                "venue": _view_venue(view),
                "decision": "ground" if passed else "skip",
                "reason": reason,
                "ungrounded_probability": None if probability is None else str(probability),
                "yes_edge_cents": yes_edge,
                "no_edge_cents": no_edge,
                "yes_ask": view.get("yes_ask"),
                "no_ask": view.get("no_ask"),
                "post_id": fingerprint,
            },
        )
        if passed:
            pending.append({"view": view, "ungrounded": ungrounded, "market": market})
        else:
            _skip(
                view,
                reason,
                prompt_hash="" if ungrounded is None else ungrounded.prompt_hash,
                probability=probability,
            )
            counts["screen_skipped"] += 1
        if counts["examined"] >= max_markets:
            break
    grouped: dict[str, list[dict[str, Any]]] = {}
    series_order: list[str] = []
    for item in pending:
        series = str(item["view"]["series"])
        if series not in grouped:
            series_order.append(series)
            grouped[series] = []
        grouped[series].append(item)
    for series in series_order:
        items = grouped[series]
        tie_only = bool(items) and all(item.get("tie_break") for item in items)
        if tie_only and not _tie_break_budget_ok(
            spend_path, call_log_path, environ, moment
        ):
            for item in items:
                _skip(item["view"], "prob_unstable")
            continue
        parsed = _ground_series(
            [item["view"] for item in items],
            environ=environ,
            post=post,
            call_log_path=call_log_path,
            spend_path=spend_path,
            raw_dir=raw_dir,
            now=moment,
        )
        counts["grounded_calls"] += 1
        failed = not isinstance(parsed, dict) or bool(parsed.get("skip_reason"))
        if failed:
            reason = "malformed"
            prompt_hash = ""
            if isinstance(parsed, dict):
                reason = str(parsed.get("skip_reason") or "malformed")
                prompt_hash = str(parsed.get("prompt_hash") or "")
            if tie_only and reason == "spend_cap":
                reason = "prob_unstable"
            for item in items:
                _skip(item["view"], reason, prompt_hash=prompt_hash)
            continue
        by_ticker = {
            str(row.get("ticker") or ""): row
            for row in parsed.get("markets") or []
            if isinstance(row, dict)
        }
        sources = tuple(parsed.get("sources") or ())
        summary = str(parsed.get("summary") or "")
        prompt_hash = str(parsed.get("prompt_hash") or "")
        for item in items:
            view = item["view"]
            row = by_ticker.get(view["ticker"])
            probability = None if row is None else _decimal(row.get("prob"))
            ungrounded = item["ungrounded"]
            if probability is None or not sources:
                _skip(
                    view,
                    "malformed" if probability is None else "no_probability",
                    prompt_hash=prompt_hash,
                    sources=sources,
                )
                continue
            confidence = None if row is None else _decimal(row.get("confidence"))
            if (
                ungrounded is not None
                and ungrounded.probability is not None
                and ungrounded.skip_reason is None
            ):
                _append_jsonl(
                    comparison_log_path,
                    {
                        "ts": _stamp(moment),
                        "ticker": view["ticker"],
                        "venue": _view_venue(view),
                        "grounded_probability": str(probability),
                        "ungrounded_probability": str(ungrounded.probability),
                        "agree": verdicts_agree(probability, ungrounded.probability),
                        "outcome": None,
                    },
                )
            scans = _next_scans(cache_path, view, probability, fingerprint, moment)
            decision = stable_scan_probability(scans)
            _remember_grounded(
                cache_path,
                view,
                probability,
                confidence,
                sources,
                fingerprint,
                summary,
                moment,
                scans,
            )
            if decision is None:
                _skip(
                    view,
                    "prob_unstable",
                    probability=probability,
                    prompt_hash=prompt_hash,
                    sources=sources,
                )
                continue
            grounded = GrokEdgeCall(
                ticker=view["ticker"],
                grounded=True,
                probability=decision,
                confidence=confidence,
                verdict="YES" if decision >= Decimal("0.5") else "NO",
                sources=sources,
                tools=_SCAN_TOOLS,
                prompt_hash=prompt_hash or "series",
                latency_ms=0,
                cost_usd=Decimal("0"),
                skip_reason=None,
            )
            blocked = _paper_gate_reason(item.get("market"), view, float(decision), now=moment)
            if blocked:
                _skip(
                    view,
                    blocked,
                    probability=decision,
                    prompt_hash=prompt_hash,
                    sources=sources,
                )
                continue
            if _queue_best_side(
                view,
                grounded,
                yes_floor=floor,
                candidate_log_path=candidate_log_path,
                db_path=db_path,
                now=moment,
            ):
                counts["queued"] += 1
            else:
                counts["skipped"] += 1
    return counts

def paper_position_rows(positions: Sequence[Any] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in positions or ():
        if isinstance(row, Mapping):
            ticker = str(row.get("ticker") or row.get("market_ticker") or "").strip()
            raw = row.get("position_fp", row.get("position"))
        else:
            ticker = str(
                getattr(row, "ticker", "") or getattr(row, "market_ticker", "") or ""
            ).strip()
            raw = getattr(row, "position_fp", None)
            if raw is None:
                raw = getattr(row, "position", None)
        if not ticker or isinstance(raw, bool) or raw is None:
            continue
        try:
            count = float(raw)
        except (TypeError, ValueError):
            continue
        if count == 0.0:
            continue
        item = {"ticker": ticker, "side": "no" if count < 0 else "yes"}
        if isinstance(row, Mapping) and row.get("market_exposure_dollars") is not None:
            item["market_exposure_dollars"] = row.get("market_exposure_dollars")
            item["position_fp"] = raw
        rows.append(item)
    return rows

def edge_scan_interval_seconds(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get(EVENT_NEWS_GROK_EDGE_SCAN_MINUTES) or "").strip()
    minutes = int(raw) if raw.isdigit() else DEFAULT_SCAN_MINUTES
    if minutes < MIN_SCAN_MINUTES:
        minutes = MIN_SCAN_MINUTES
    return minutes * 60

def run_paper_exit_watch(
    positions: Sequence[Mapping[str, Any]],
    *,
    live: bool,
    market_lookup: Callable[[str], Any],
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    call_log_path: Path = CALL_LOG_PATH,
    exit_log_path: Path = EXIT_LOG_PATH,
    mark_log_path: Path = MARK_LOG_PATH,
    spend_path: Path = SPEND_PATH,
    raw_dir: Path = RAW_DIR,
    now: datetime | None = None,
) -> int:
    """Log a free bid and ask mark. Does not call Grok, cancel, or sell."""
    if live or not event_news_desk_flag_enabled(
        EVENT_NEWS_GROK_EXIT_WATCH,
        live=False,
        environ=environ,
    ):
        return 0
    logged = 0
    for position in positions:
        ticker = str(position.get("ticker") or "").strip()
        side = str(position.get("side") or "").strip().lower()
        if not ticker or side not in {"yes", "no"}:
            continue
        market = market_lookup(ticker)
        bid = _bid_cents(market, side)
        if bid is None:
            continue
        _append_jsonl(
            mark_log_path,
            {
                "ts": _stamp(now),
                "ticker": ticker,
                "side": side,
                "bid_cents": bid,
                "ask_cents": _quote_cents(market, side, "ask"),
                "paid": False,
            },
        )
        logged += 1
    return logged

def _position_view(ticker: str, market: Any) -> dict[str, Any]:
    view = _market_view(market) if market is not None else None
    if view is not None and view.get("ticker"):
        if not view.get("series"):
            view["series"] = ticker.split("-", 1)[0]
        return view
    return {
        "ticker": ticker,
        "series": ticker.split("-", 1)[0],
        "status": "active",
        "yes_ask": None,
        "no_ask": None,
        "title": ticker,
        "rules": "",
        "close_time": "",
    }

def _score_record(
    *,
    ticker: str,
    side: str,
    ask: int | None,
    probability: Decimal | None,
    sources: Sequence[str],
    skip_reason: str | None,
    floor: int,
    now: datetime | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts": _stamp(now),
        "ticker": ticker,
        "side": side,
        "ask_cents": ask,
        "decision": "not_scored",
        "skip_reason": skip_reason or "no_call",
        "grok_probability": None,
        "edge_cents": None,
        "sources": [],
        "paper_only": True,
    }
    if probability is None or not sources:
        return record
    sided = probability if side == "yes" else Decimal(1) - probability
    reason, edge = paper_queue_decision(
        probability=sided,
        ask_cents=ask,
        side=side,
        contracts=PAPER_QUEUE_CONTRACTS,
        edge_min=_yaml_edge_min(ticker.split("-", 1)[0]),
        yes_floor_cents=floor,
    )
    record.update(
        {
            "decision": "take" if reason == "queue" else "veto",
            "skip_reason": None if reason == "queue" else reason,
            "grok_probability": str(probability),
            "edge_cents": edge,
            "sources": list(sources),
        }
    )
    return record

def score_open_positions(
    positions: Sequence[Any] | None,
    *,
    live: bool,
    market_lookup: Callable[[str], Any],
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    call_log_path: Path = CALL_LOG_PATH,
    score_log_path: Path = POSITION_SCORE_LOG_PATH,
    spend_path: Path = SPEND_PATH,
    db_path: Path | None = None,
    raw_dir: Path = RAW_DIR,
    now: datetime | None = None,
    cache_path: Path = CACHE_PATH,
    screen_log_path: Path = SCREEN_LOG_PATH,
    post_lookup: Callable[[], str | None] | None = None,
) -> dict[str, int]:
    """Score open positions. Does not order. A skipped call is not_scored, not a veto."""
    counts = {"scored": 0, "skipped": 0, "cache_hits": 0, "grounded_calls": 0}
    if live or not event_news_desk_flag_enabled(
        EVENT_NEWS_GROK_EDGE_SCAN,
        live=False,
        environ=environ,
    ):
        return {"scored": 0, "skipped": 0}
    floor = event_news_yes_floor_cents(environ)
    moment = now or datetime.now(timezone.utc)
    fingerprint = latest_truth_social_fingerprint() if post_lookup is None else post_lookup()
    pending: list[dict[str, Any]] = []
    for position in paper_position_rows(positions):
        ticker = position["ticker"]
        side = position["side"]
        try:
            market = market_lookup(ticker)
        except Exception:
            market = None
        view = _position_view(ticker, market)
        ask = _mark_cents(market, side, position)
        cached = _cache_get(cache_path, ticker)
        if _cache_fresh(cached, view, fingerprint, moment):
            counts["cache_hits"] += 1
            cached_scans = cached.get("scans") if isinstance(cached, Mapping) else None
            unstable = (
                stable_scan_probability(
                    cached_scans if isinstance(cached_scans, list) else []
                )
                is None
            )
            reused = None if unstable else _cached_call(view, cached or {})
            probability = None if reused is None else reused.probability
            sources = () if reused is None else reused.sources
            if unstable:
                skip_reason = "prob_unstable"
            elif probability is None:
                skip_reason = "cache_empty"
            else:
                skip_reason = None
            record = _score_record(
                ticker=ticker,
                side=side,
                ask=ask,
                probability=probability,
                sources=sources,
                skip_reason=skip_reason,
                floor=floor,
                now=moment,
            )
            _log_screen(
                screen_log_path,
                {
                    "ts": _stamp(moment),
                    "ticker": ticker,
                    "series": view["series"],
                    "decision": "cache_hit",
                    "reason": "open_position_cache",
                    "cached_prob": None if cached is None else cached.get("prob"),
                    "post_id": fingerprint,
                },
            )
            if record["decision"] == "not_scored":
                counts["skipped"] += 1
            else:
                counts["scored"] += 1
            _append_jsonl(score_log_path, record)
            _insert_paper_row(db_path, "position_scores", record)
            continue
        if blocked := _paper_gate_reason(market, view, None, now=moment):
            record = _score_record(ticker=ticker, side=side, ask=ask, probability=None, sources=(), skip_reason=blocked, floor=floor, now=moment)
            counts["skipped"] += 1
            _append_jsonl(score_log_path, record)
            _insert_paper_row(db_path, "position_scores", record)
            continue
        if not _kalshi_series_paid(view, environ):
            record = _score_record(
                ticker=ticker,
                side=side,
                ask=ask,
                probability=None,
                sources=(),
                skip_reason="kalshi_series_off",
                floor=floor,
                now=moment,
            )
            counts["skipped"] += 1
            _append_jsonl(score_log_path, record)
            _insert_paper_row(db_path, "position_scores", record)
            continue
        if _tie_break_due(cached, view, fingerprint, moment):
            if not _tie_break_budget_ok(spend_path, call_log_path, environ, moment):
                record = _score_record(
                    ticker=ticker,
                    side=side,
                    ask=ask,
                    probability=None,
                    sources=(),
                    skip_reason="prob_unstable",
                    floor=floor,
                    now=moment,
                )
                counts["skipped"] += 1
                _append_jsonl(score_log_path, record)
                _insert_paper_row(db_path, "position_scores", record)
                continue
            _log_screen(
                screen_log_path,
                {
                    "ts": _stamp(moment),
                    "ticker": ticker,
                    "series": view["series"],
                    "decision": "ground",
                    "reason": "tie_break",
                    "yes_ask": view.get("yes_ask"),
                    "no_ask": view.get("no_ask"),
                    "post_id": fingerprint,
                },
            )
            pending.append(
                {
                    "view": view,
                    "side": side,
                    "ask": ask,
                    "ticker": ticker,
                    "tie_break": True,
                }
            )
            continue
        _call_grok(
            ticker=ticker,
            prompt=_market_prompt(view),
            system=_UNGROUNDED_SYSTEM,
            grounded=False,
            environ=environ,
            post=post,
            call_log_path=call_log_path,
            spend_path=spend_path,
            raw_dir=raw_dir,
            now=moment,
        )
        _log_screen(
            screen_log_path,
            {
                "ts": _stamp(moment),
                "ticker": ticker,
                "series": view["series"],
                "decision": "ground",
                "reason": "open_position",
                "yes_ask": view.get("yes_ask"),
                "no_ask": view.get("no_ask"),
                "post_id": fingerprint,
            },
        )
        pending.append({"view": view, "side": side, "ask": ask, "ticker": ticker})
    grouped: dict[str, list[dict[str, Any]]] = {}
    series_order: list[str] = []
    for item in pending:
        series = str(item["view"]["series"])
        if series not in grouped:
            series_order.append(series)
            grouped[series] = []
        grouped[series].append(item)
    for series in series_order:
        items = grouped[series]
        tie_only = bool(items) and all(item.get("tie_break") for item in items)
        if tie_only and not _tie_break_budget_ok(
            spend_path, call_log_path, environ, moment
        ):
            for item in items:
                record = _score_record(
                    ticker=item["ticker"],
                    side=item["side"],
                    ask=item["ask"],
                    probability=None,
                    sources=(),
                    skip_reason="prob_unstable",
                    floor=floor,
                    now=moment,
                )
                counts["skipped"] += 1
                _append_jsonl(score_log_path, record)
                _insert_paper_row(db_path, "position_scores", record)
            continue
        parsed = _ground_series(
            [item["view"] for item in items],
            environ=environ,
            post=post,
            call_log_path=call_log_path,
            spend_path=spend_path,
            raw_dir=raw_dir,
            now=moment,
        )
        counts["grounded_calls"] += 1
        by_ticker = {} if parsed is None else {
            str(row.get("ticker") or ""): row
            for row in parsed.get("markets") or []
            if isinstance(row, dict)
        }
        sources = () if parsed is None else tuple(
            str(item) for item in parsed.get("sources") or ()
        )
        summary = "" if parsed is None else str(parsed.get("summary") or "")
        for item in items:
            row = by_ticker.get(item["ticker"])
            probability = None if row is None else _decimal(row.get("prob"))
            confidence = None if row is None else _decimal(row.get("confidence"))
            if item.get("tie_break"):
                if probability is None or not sources:
                    reason = "malformed"
                    if isinstance(parsed, dict) and parsed.get("skip_reason"):
                        reason = str(parsed.get("skip_reason") or "malformed")
                    if reason == "spend_cap":
                        reason = "prob_unstable"
                    record = _score_record(
                        ticker=item["ticker"],
                        side=item["side"],
                        ask=item["ask"],
                        probability=None,
                        sources=(),
                        skip_reason=reason,
                        floor=floor,
                        now=moment,
                    )
                    counts["skipped"] += 1
                else:
                    scans = _next_scans(
                        cache_path,
                        item["view"],
                        probability,
                        fingerprint,
                        moment,
                    )
                    decision = stable_scan_probability(scans)
                    _remember_grounded(
                        cache_path,
                        item["view"],
                        probability,
                        confidence,
                        sources,
                        fingerprint,
                        summary,
                        moment,
                        scans,
                    )
                    if decision is None:
                        record = _score_record(
                            ticker=item["ticker"],
                            side=item["side"],
                            ask=item["ask"],
                            probability=None,
                            sources=(),
                            skip_reason="prob_unstable",
                            floor=floor,
                            now=moment,
                        )
                        counts["skipped"] += 1
                    else:
                        record = _score_record(
                            ticker=item["ticker"],
                            side=item["side"],
                            ask=item["ask"],
                            probability=decision,
                            sources=sources,
                            skip_reason=None,
                            floor=floor,
                            now=moment,
                        )
                        counts["scored"] += 1
                _append_jsonl(score_log_path, record)
                _insert_paper_row(db_path, "position_scores", record)
                continue
            record = _score_record(
                ticker=item["ticker"],
                side=item["side"],
                ask=item["ask"],
                probability=probability,
                sources=sources,
                skip_reason=None if probability is not None and sources else "malformed",
                floor=floor,
                now=moment,
            )
            if probability is not None and sources:
                _remember_grounded(
                    cache_path,
                    item["view"],
                    probability,
                    confidence,
                    sources,
                    fingerprint,
                    summary,
                    moment,
                    _next_scans(cache_path, item["view"], probability, fingerprint, moment),
                )
                counts["scored"] += 1
            else:
                counts["skipped"] += 1
            _append_jsonl(score_log_path, record)
            _insert_paper_row(db_path, "position_scores", record)
    return counts

def write_daily_edge_report(
    *,
    candidate_log_path: Path = CANDIDATE_LOG_PATH,
    comparison_log_path: Path = COMPARISON_LOG_PATH,
    spend_path: Path = SPEND_PATH,
    market_lookup: Callable[[str], Any],
    report_path: Path = DAILY_REPORT_PATH,
    now: datetime | None = None,
    polymarket_lookup: Callable[[str], Any] | None = None,
    db_path: Path = PAPER_DB_PATH,
    call_log_path: Path = CALL_LOG_PATH,
    decision_log_path: Path | None = None,
) -> str:
    """Join settled GETs to logged probabilities. Does not read or write orders."""
    candidates = _read_jsonl(candidate_log_path)
    comparisons = _read_jsonl(comparison_log_path)
    spend_line = _spend_line(spend_path) + "\n" + _profit_share_line(spend_path, call_log_path, now)
    settlements = _settlements_for(
        (*candidates, *comparisons),
        market_lookup,
        polymarket_lookup=polymarket_lookup,
        stored=_earliest_polymarket_settlements(db_path),
    )
    joined = _joined_settled_rows(candidates, comparisons, settlements)
    text = _daily_report_text(joined, spend_line, now=now).rstrip("\n")
    log_path = VETO_DECISION_LOG_PATH if decision_log_path is None else decision_log_path
    text += _veto_scorecard_text(_read_jsonl(log_path), market_lookup)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    return text

def contract_fee_net_dollars(
    side: str,
    ask_cents: int,
    result_yes: bool,
    venue: str = "kalshi",
) -> Decimal | None:
    if side not in {"yes", "no"}:
        return None
    if venue == _POLYMARKET_US:
        scored = _fee_net(Decimal("0.5"), ask_cents, 1, venue)
    else:
        scored = event_news_fee_net_edge(Decimal("0.5"), ask_cents, 1)
    if scored is None:
        return None
    fee = scored[1]
    price = Decimal(ask_cents) / Decimal(100)
    won = (side == "yes") == result_yes
    if won:
        return Decimal(1) - price - fee
    return -price - fee

def quote_from_market(market: Any) -> dict[str, Any] | None:
    view = _market_view(market)
    yes_ask = None if view is None else view.get("yes_ask")
    if view is None or type(yes_ask) is not int:
        return None
    return view

def _complement_cents(bid: int | None) -> int | None:
    if type(bid) is not int or not 1 <= bid <= 99:
        return None
    ask = 100 - bid
    if 1 <= ask <= 99:
        return ask
    return None

def _report_quote(market: Any) -> dict[str, Any] | None:
    """One market's asks. A missing NO ask is 100 minus the YES bid."""
    view = quote_from_market(market)
    if view is None:
        yes_only = _market_view(market)
        if yes_only is None:
            return None
        view = dict(yes_only)
        if type(view.get("yes_ask")) is not int:
            view["yes_ask"] = _complement_cents(_bid_cents(market, "no"))
        if type(view.get("yes_ask")) is not int:
            return None
    else:
        view = dict(view)
    if type(view.get("no_ask")) is not int:
        derived = _complement_cents(_bid_cents(market, "yes"))
        if derived is not None:
            view["no_ask"] = derived
    return view

def _canonical_close(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _plain_number(raw: str) -> str:
    return format(Decimal(raw).normalize(), "f")

def _outcome_key(title: str) -> tuple[str, ...] | None:
    plain = str(title or "").replace("\u2013", "-").replace("\u2014", "-").lower()
    ranged = re.search(
        r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*-\s*(\d{1,2}(?:\.\d+)?)(?!\d)",
        plain,
    )
    if ranged is not None:
        return ("range", _plain_number(ranged.group(1)), _plain_number(ranged.group(2)))
    for phrase, kind in (
        ("more than", "above"),
        ("greater than", "above"),
        ("less than", "below"),
        ("exactly", "exact"),
        ("below", "below"),
        ("under", "below"),
        ("above", "above"),
        ("over", "above"),
    ):
        found = re.search(rf"{phrase}\s+(\d+(?:\.\d+)?)", plain)
        if found is not None:
            return (kind, _plain_number(found.group(1)))
    by_date = re.search(r"\bby\s+([a-z]+)\s+(\d{1,2}),\s*(\d{4})\b", plain)
    if by_date is not None:
        return ("by", by_date.group(1), str(int(by_date.group(2))), by_date.group(3))
    return None

def _settlement_source(title: str, rules: str) -> str | None:
    blob = f"{title}\n{rules}".lower()
    if "rcp" in blob:
        return "rcp"
    if "transit" in blob and "hormuz" in blob:
        return "hormuz-transit"
    if "normal" in blob and "hormuz" in blob:
        return "hormuz-normal"
    return None

def _contract_identity(view: Mapping[str, Any]) -> tuple[Any, ...] | None:
    close = _canonical_close(view.get("close_time"))
    outcome = _outcome_key(str(view.get("title") or ""))
    source = _settlement_source(str(view.get("title") or ""), str(view.get("rules") or ""))
    if close is None or outcome is None or source is None:
        return None
    return (close[:10], source, outcome)

def _source_date(view: Mapping[str, Any]) -> tuple[str, str] | None:
    close = _canonical_close(view.get("close_time"))
    source = _settlement_source(str(view.get("title") or ""), str(view.get("rules") or ""))
    if close is None or source is None:
        return None
    return close[:10], source

def _unmatched_reason(view: Mapping[str, Any], others: Sequence[Mapping[str, Any]]) -> str:
    left = _source_date(view)
    if left is None:
        return "unparsed"
    other_dates = [item for item in (_source_date(other) for other in others) if item]
    parts = _contract_identity(view)
    parsed = [item for item in (_contract_identity(other) for other in others) if item]
    if parts is not None and sum(item == parts for item in parsed) > 1:
        return "ambiguous"
    comparable = [item for item in parsed if parts and item[0] == parts[0] and item[1] == parts[1]]
    if comparable:
        return "no_counterpart"
    if any(item[1] != left[1] for item in other_dates):
        return "different_source"
    if any(item[0] != left[0] for item in other_dates):
        return "different_settlement_date"
    return "no_counterpart" if parts is not None else "unparsed"

def _pair_edge(yes_view: Mapping[str, Any], no_view: Mapping[str, Any]) -> Decimal | None:
    yes_ask = yes_view.get("yes_ask")
    no_ask = no_view.get("no_ask")
    if type(yes_ask) is not int or type(no_ask) is not int:
        return None
    yes_fee = _fee_net(Decimal("0.5"), yes_ask, 1, _view_venue(yes_view))
    no_fee = _fee_net(Decimal("0.5"), no_ask, 1, _view_venue(no_view))
    if yes_fee is None or no_fee is None:
        return None
    return Decimal(1) - (Decimal(yes_ask) / Decimal(100)) - (Decimal(no_ask) / Decimal(100)) - yes_fee[1] - no_fee[1]

def _cross_venue_row(
    *,
    now: datetime | None,
    event: str,
    pair_series: str,
    kalshi: Mapping[str, Any] | None,
    polymarket: Mapping[str, Any] | None,
    edge_min: Decimal | None,
    reason: str = "",
) -> dict[str, Any]:
    best: Decimal | None = None
    if kalshi is not None and polymarket is not None:
        for yes_view, no_view in ((kalshi, polymarket), (polymarket, kalshi)):
            edge = _pair_edge(yes_view, no_view)
            if edge is not None and (best is None or edge > best):
                best = edge
    matched = kalshi is not None and polymarket is not None
    label = "matched" if matched else ("not comparable" if reason in ("different_source", "different_settlement_date") else "no match")
    return {
        "ts": _stamp(now),
        "event": event,
        "pair_series": pair_series,
        "match": label,
        "reason": reason,
        "kalshi_ticker": "" if kalshi is None else str(kalshi.get("ticker") or ""),
        "polymarket_ticker": "" if polymarket is None else str(polymarket.get("ticker") or ""),
        "kalshi_yes_cents": None if kalshi is None else kalshi.get("yes_ask"),
        "polymarket_yes_cents": None if polymarket is None else polymarket.get("yes_ask"),
        "kalshi_no_cents": None if kalshi is None else kalshi.get("no_ask"),
        "polymarket_no_cents": None if polymarket is None else polymarket.get("no_ask"),
        "fee_net_gap": None if best is None else format(best, "f"),
        "pair_clears": best is not None and edge_min is not None and best >= edge_min,
    }

def cross_venue_report(
    markets: Sequence[Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    universe = get_universe()
    if universe.failed:
        return []
    by_series: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        view = _report_quote(market)
        if view is None:
            continue
        series = str(view.get("series") or "").strip().upper()
        if series:
            by_series.setdefault(series, []).append(view)
    rows: list[dict[str, Any]] = []
    for entry in universe.entries:
        if entry.venue != _POLYMARKET_US or not entry.pair_series:
            continue
        try:
            edge_min = Decimal(entry.edge_min)
        except Exception:
            edge_min = None
        kalshi_views = by_series.get(entry.pair_series, [])
        poly_views = by_series.get(entry.series, [])
        used_k: set[str] = set()
        used_p: set[str] = set()
        explicit = {left: right for left, right in entry.pair_map}

        def _add(left: Mapping[str, Any], right: Mapping[str, Any]) -> None:
            used_k.add(str(left.get("ticker") or ""))
            used_p.add(str(right.get("ticker") or ""))
            rows.append(
                _cross_venue_row(
                    now=now,
                    event=entry.series,
                    pair_series=entry.pair_series,
                    kalshi=left,
                    polymarket=right,
                    edge_min=edge_min,
                )
            )

        for kalshi in kalshi_views:
            slug = explicit.get(str(kalshi.get("ticker") or ""))
            found = [item for item in poly_views if slug and str(item.get("ticker") or "") == slug]
            if len(found) != 1 or str(found[0].get("ticker") or "") in used_p or _source_date(kalshi) is None or _source_date(kalshi) != _source_date(found[0]):
                continue
            own, other = _contract_identity(kalshi), _contract_identity(found[0])
            if own is not None and other is not None and own != other:
                continue
            _add(kalshi, found[0])
        for kalshi in kalshi_views:
            if str(kalshi.get("ticker") or "") in used_k:
                continue
            identity = _contract_identity(kalshi)
            found = [
                item for item in poly_views
                if identity and str(item.get("ticker") or "") not in used_p and _contract_identity(item) == identity
            ]
            if len(found) == 1:
                _add(kalshi, found[0])

        def _skip(view: Mapping[str, Any], others: Sequence[Mapping[str, Any]], kalshi_side: bool) -> None:
            ticker = str(view.get("ticker") or "")
            if ticker in (used_k if kalshi_side else used_p):
                return
            rows.append(
                _cross_venue_row(
                    now=now,
                    event=entry.series,
                    pair_series=entry.pair_series,
                    kalshi=view if kalshi_side else None,
                    polymarket=None if kalshi_side else view,
                    edge_min=edge_min,
                    reason=_unmatched_reason(view, others),
                )
            )

        for kalshi in kalshi_views:
            _skip(kalshi, poly_views, True)
        for poly in poly_views:
            _skip(poly, kalshi_views, False)
    return rows

def write_cross_venue(
    markets: Sequence[Any],
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    rows = cross_venue_report(markets, now=now)
    target = CROSS_VENUE_LOG_PATH if path is None else path
    for row in rows:
        if row.get("reason"):
            log.info("cross venue unmatched event=%s ticker=%s reason=%s", row["event"], row["kalshi_ticker"] or row["polymarket_ticker"], row["reason"])
        _append_jsonl(target, row)
    return rows

def scorecard_interval(values: Sequence[Decimal]) -> dict[str, Decimal | int | None]:
    count = len(values)
    mean = None if count == 0 else sum(values, Decimal(0)) / Decimal(count)
    if count < 2 or mean is None:
        return {"n": count, "mean": mean, "ci95_low": None, "ci95_high": None}
    variance = sum((item - mean) ** 2 for item in values) / Decimal(count - 1)
    half = Decimal("1.96") * variance.sqrt() / Decimal(count).sqrt()
    return {"n": count, "mean": mean, "ci95_low": mean - half, "ci95_high": mean + half}

def promotion_label(*, n: int, cluster_n: int, cluster_low: Decimal | None, q: Decimal | None) -> str:
    """Report-only. This does not edit the universe file."""
    short = [name for name, failed in (("settled trades", n < 15), ("clustered events", cluster_n < 15)) if failed]
    if short:
        return f"not enough data (n={n}, clustered n={cluster_n}) {', '.join(short)}"
    failed = []
    if not isinstance(cluster_low, Decimal) or cluster_low <= 0:
        failed.append("clustered CI")
    if not isinstance(q, Decimal) or q > Decimal("0.10"):
        failed.append("BH q")
    return "recommend off " + ", ".join(failed) if failed else "eligible for live"

def _cell(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")

def _side_band_table(samples: Sequence[tuple]) -> list[str]:
    """Settled paper and live fee-net. Same YES bands as brier_report. n<15 is withheld."""
    groups: dict[str, list[Decimal]] = {"yes": [], "no": []}
    groups.update({name: [] for _low, _high, name in _YES_PROBABILITY_BANDS})
    for item in samples:
        side = str(item[4] or "")
        if side in ("yes", "no"):
            groups[side].append(item[1])
        for low, high, name in _YES_PROBABILITY_BANDS:
            if low <= item[2] < high:
                groups[name].append(item[1])
                break
    lines = ["", "Fee-net by side and by Grok YES probability.", "| bucket | n | fee_net |", "|---|---:|---:|"]
    for label in ("yes", "no", *(name for _low, _high, name in _YES_PROBABILITY_BANDS)):
        values = groups[label]
        count = len(values)
        shown = f"not enough data (n={count})" if count < 15 else _cell(sum(values, Decimal(0)))
        lines.append(f"| {label} | {count} | {shown} |")
    return lines

def _cluster_means(samples: Sequence[tuple]) -> list[Decimal]:
    groups: dict[str, list[Decimal]] = {}
    for item in samples:
        text = str(item[-1] or "").upper()
        found = re.search(r"\d{2}[A-Z]{3}\d{2}|\d{4}-\d{2}-\d{2}", text)
        groups.setdefault(text[: found.end()] if found else text, []).append(item[1])
    return [sum(chunk, Decimal(0)) / Decimal(len(chunk)) for chunk in groups.values()]

def _ev_p(values: Sequence[Decimal]) -> Decimal | None:
    stats = scorecard_interval(values)
    mean, low, high = stats["mean"], stats["ci95_low"], stats["ci95_high"]
    if not isinstance(mean, Decimal) or not isinstance(low, Decimal) or not isinstance(high, Decimal):
        return None
    half = (high - low) / 2
    if half == 0:
        return Decimal(0) if mean > 0 else Decimal(1)
    z = float(mean * Decimal("1.96") / half)
    return Decimal(repr(0.5 * math.erfc(z / math.sqrt(2))))

def _bh_q(pairs: Sequence[tuple[Any, Decimal]]) -> dict[Any, Decimal]:
    ordered = sorted(pairs, key=lambda item: item[1])
    found, ceiling, m = {}, Decimal(1), len(ordered)
    for rank in range(m, 0, -1):
        key, p_value = ordered[rank - 1]
        ceiling = min(ceiling, min(Decimal(1), p_value * Decimal(m) / Decimal(rank)))
        found[key] = ceiling
    return found

def _guard_cell(value: Decimal | None, count: int) -> str:
    number = _cell(value)
    if count >= 15:
        return number
    return f"not enough data (n={count})" if not number else f"not enough data (n={count}) {number}"

def _veto_scorecard_text(rows: Sequence[Mapping[str, Any]], market_lookup: Callable[[str], Any]) -> str:
    settled: dict[str, str] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if ticker and ticker not in settled:
            try:
                result = _settlement_result(market_lookup(ticker))
            except Exception:
                result = None
            if result in {"yes", "no"}:
                settled[ticker] = result
    return grade_veto_decisions(list(rows), settled)

def _scorecard_modes() -> dict[str, tuple[str, str]]:
    universe = get_universe()
    if universe.failed:
        return {}
    return {entry.series: (entry.mode, effective_mode(entry)) for entry in universe.entries}

def _earliest_polymarket_settlements(db_path: Path) -> dict[str, str]:
    if not db_path.is_file():
        return {}
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        names = {row[1] for row in conn.execute("PRAGMA table_info(paper_candidates)")}
        if "ticker" not in names or "venue" not in names or "settlement_result" not in names:
            return {}
        rows = conn.execute(
            """
            SELECT ticker, settlement_result FROM (
                SELECT ticker, settlement_result,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker
                           ORDER BY ts ASC, rowid ASC
                       ) AS rn
                FROM paper_candidates
                WHERE lower(venue) = 'polymarket_us'
                  AND lower(settlement_result) IN ('yes', 'no')
            ) WHERE rn = 1
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    found: dict[str, str] = {}
    for ticker, result in rows:
        text = str(result or "").strip().lower()
        slug = str(ticker or "").strip()
        if slug and text in {"yes", "no"}:
            found[slug] = text
    return found

def _prefer_earliest_settlement(db_path: Path, rows: list[tuple], stored_index: int) -> list[tuple]:
    earliest = _earliest_polymarket_settlements(db_path)
    if not earliest:
        return rows
    adjusted: list[tuple] = []
    for row in rows:
        saved = earliest.get(str(row[0] or "").strip())
        if not saved:
            adjusted.append(row)
            continue
        mutable = list(row)
        mutable[stored_index] = saved
        adjusted.append(tuple(mutable))
    return adjusted

def _queued_candidate_rows(db_path: Path) -> list[tuple]:
    if not db_path.is_file():
        return []
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        names = {row[1] for row in conn.execute("PRAGMA table_info(paper_candidates)")}
        if "ticker" not in names:
            return []
        series_sql = "series" if "series" in names else "''"
        venue_sql = "COALESCE(venue, 'kalshi')" if "venue" in names else "'kalshi'"
        stored_sql = "settlement_result" if "settlement_result" in names else "NULL"
        fetched = conn.execute(
            "SELECT ticker, series, side, ask_cents, grok_probability, venue, "
            "settlement_result FROM ("
            "SELECT ticker, " + series_sql
            + " AS series, side, ask_cents, grok_probability, "
            + venue_sql
            + " AS venue, "
            + stored_sql
            + " AS settlement_result, ROW_NUMBER() OVER ("
            "PARTITION BY ticker ORDER BY ts DESC, rowid DESC) rn FROM paper_candidates "
            "WHERE queued = 1 AND side IS NOT NULL AND ask_cents IS NOT NULL "
            "AND grok_probability IS NOT NULL) WHERE rn = 1"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return _prefer_earliest_settlement(db_path, fetched, 6)

def settlement_venue(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ("", "kalshi"):
        return "kalshi"
    if text == _POLYMARKET_US:
        return _POLYMARKET_US
    raise ValueError("unknown_venue")

def outcome_from_polymarket_settlement(payload: Any) -> str | None:
    """1 is YES and 0 is NO. A missing payload is not settled. Any other number fails closed."""
    if payload is None:
        return None
    if not isinstance(payload, Mapping) or "settlement" not in payload:
        raise ValueError("settlement_unreadable")
    value = payload.get("settlement")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("settlement_unreadable")
    number = float(value)
    if number == 1.0:
        return "yes"
    if number == 0.0:
        return "no"
    raise ValueError("settlement_unreadable")

def resolve_settlement(
    venue: Any,
    ticker: str,
    *,
    market_lookup: Callable[[str], Any],
    polymarket_lookup: Callable[[str], Any] | None = None,
) -> str | None:
    name = settlement_venue(venue)
    if name == "kalshi":
        return _settlement_result(market_lookup(ticker))
    if polymarket_lookup is None:
        raise ValueError("polymarket_settlement_unavailable")
    try:
        payload = polymarket_lookup(ticker)
    except ValueError as exc:
        if "settlement not found" in str(exc):
            return None
        raise
    return outcome_from_polymarket_settlement(payload)

def _polymarket_candidate_rows(db_path: Path) -> list[tuple]:
    if not db_path.is_file():
        return []
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        names = {row[1] for row in conn.execute("PRAGMA table_info(paper_candidates)")}
        if "ticker" not in names or "venue" not in names:
            return []
        series_sql = "series" if "series" in names else "''"
        stored_sql = "settlement_result" if "settlement_result" in names else "NULL"
        fetched = conn.execute(
            "SELECT ticker, series, queued, settlement_result FROM ("
            "SELECT ticker, " + series_sql + " AS series, queued, "
            + stored_sql
            + " AS settlement_result, ROW_NUMBER() OVER ("
            "PARTITION BY ticker ORDER BY ts DESC, rowid DESC) rn "
            "FROM paper_candidates WHERE venue = 'polymarket_us') WHERE rn = 1"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return _prefer_earliest_settlement(db_path, fetched, 3)

def remember_polymarket_settlement(db_path: Path, ticker: str, result: str) -> None:
    if result not in {"yes", "no"} or not str(ticker or "").strip():
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        with sqlite3.connect(db_path) as conn:
            have = {row[1] for row in conn.execute("PRAGMA table_info(paper_candidates)")}
            if "settlement_result" not in have:
                conn.execute("ALTER TABLE paper_candidates ADD COLUMN settlement_result TEXT")
            existing = conn.execute(
                """
                SELECT settlement_result FROM paper_candidates
                WHERE ticker = ? AND lower(venue) = 'polymarket_us'
                  AND lower(settlement_result) IN ('yes', 'no')
                ORDER BY ts ASC, rowid ASC
                LIMIT 1
                """,
                (ticker,),
            ).fetchone()
            if existing is not None and str(existing[0]).strip().lower() != result:
                return
            conn.execute(
                """
                UPDATE paper_candidates
                SET settlement_result = ?
                WHERE ticker = ? AND venue = 'polymarket_us'
                  AND (settlement_result IS NULL OR settlement_result = '')
                """,
                (result, ticker),
            )

def polymarket_observation_rows(
    db_path: Path,
    *,
    market_lookup: Callable[[str], Any] | None = None,
    polymarket_lookup: Callable[[str], Any] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for ticker, series, queued, stored in _polymarket_candidate_rows(db_path):
        slug = str(ticker or "")
        saved = str(stored or "").strip().lower()
        if saved not in {"yes", "no"}:
            saved = ""
        label = "unreadable"
        if market_lookup is None and polymarket_lookup is None:
            label = saved or "not settled"
        else:
            try:
                live = resolve_settlement(
                    _POLYMARKET_US,
                    slug,
                    market_lookup=market_lookup or (lambda _ticker: None),
                    polymarket_lookup=polymarket_lookup,
                )
            except Exception:
                live = "unreadable"
            if saved in {"yes", "no"} and live in {"yes", "no"} and saved != live:
                label = "unreadable"
            elif live in {"yes", "no"}:
                remember_polymarket_settlement(db_path, slug, live)
                label = live
            elif saved in {"yes", "no"}:
                label = saved
            elif live is None:
                label = "not settled"
        rows.append(
            {
                "venue": _POLYMARKET_US,
                "series": str(series or "") or slug.split("-", 1)[0],
                "ticker": slug,
                "queued": "1" if queued else "0",
                "settlement": label,
            }
        )
    return rows

def _polymarket_section(rows: Sequence[Mapping[str, str]]) -> list[str]:
    if not rows:
        return []
    lines = [
        "",
        "## Polymarket paper rows",
        "",
        "Not in the promotion EV. A skip has no side. Settlement is the official price or not settled.",
        "",
        "| venue | series | ticker | queued | settlement |",
        "|---|---|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {venue} | {series} | {ticker} | {queued} | {settlement} |".format(
                venue=row.get("venue") or "",
                series=row.get("series") or "",
                ticker=row.get("ticker") or "",
                queued=row.get("queued") or "0",
                settlement=row.get("settlement") or "",
            )
        )
    return lines

_SHADOW_SERIES = ("KXTRUMPACT", "KXTRUTHSOCIAL")


def _market_close(market: Any) -> datetime | None:
    """Close used to pick the last shadow row. Settlement time is the fallback."""
    if market is None:
        return None
    names = ("close_time", "expiration_time", "settled_time")
    for name in names:
        raw = market.get(name) if isinstance(market, Mapping) else getattr(market, name, None)
        found = _parse_utc(raw)
        if found is not None:
            return found
    return None


def _count_shadow_lines(
    rows: Sequence[Mapping[str, Any]],
    market_lookup: Callable[[str], Any],
) -> list[str]:
    """Grade the free count shadow. No paid call and no mode change.

    The last row with ts before close is the forecast. YES if p_yes >= 0.5,
    else NO. The NO price is 100 minus yes_ask when that ask is 1 through 99.
    Brier scores p_yes and yes_ask/100 against the settlement. Fee-net is one
    contract at that price, using the scorecard fee.
    """
    pending: dict[str, list[tuple[datetime, int, Mapping[str, Any]]]] = {}
    for index, row in enumerate(rows):
        ticker = str(row.get("ticker") or "").strip()
        series = str(row.get("series") or "").strip().upper() or ticker.split("-", 1)[0].upper()
        when = _parse_utc(row.get("ts"))
        if series not in _SHADOW_SERIES or not ticker or when is None:
            continue
        pending.setdefault(ticker, []).append((when, index, row))
    graded: dict[str, list[tuple]] = {name: [] for name in _SHADOW_SERIES}
    for ticker, items in pending.items():
        series = str(items[0][2].get("series") or "").strip().upper() or ticker.split("-", 1)[0].upper()
        if series not in graded:
            continue
        try:
            market = market_lookup(ticker)
        except Exception:
            continue
        result = _settlement_result(market)
        close = _market_close(market)
        if result not in {"yes", "no"} or close is None:
            continue
        before = [item for item in items if item[0] < close]
        if not before:
            continue
        _when, _index, row = max(before, key=lambda item: (item[0], item[1]))
        p_yes = _decimal(row.get("probability"))
        ask = row.get("yes_ask")
        if p_yes is None or p_yes < 0 or p_yes > 1 or isinstance(ask, bool) or type(ask) is not int:
            continue
        if not 1 <= ask <= 99:
            continue
        outcome = Decimal(1 if result == "yes" else 0)
        side = "yes" if p_yes >= Decimal("0.5") else "no"
        price = ask if side == "yes" else 100 - ask
        pnl = contract_fee_net_dollars(side, price, result == "yes")
        if pnl is None:
            continue
        graded[series].append(
            ((side == "yes") == (result == "yes"), pnl, p_yes, outcome, side, Decimal(ask) / Decimal(100), ticker)
        )
    lines = [
        "",
        "## Count shadow",
        "",
        "Free count model for KXTRUTHSOCIAL and KXTRUMPACT. No paid call. No mode change.",
        "Last shadow row before close. YES when p_yes >= 0.5, else NO at 100 minus the yes ask.",
        "The yes ask must be 1 through 99. Brier is the mean squared error against the settlement.",
        "Fee-net is one contract at that ask with the scorecard fee. Promotion does not change modes.",
        "",
        "| series | n | shadow_brier | ask_brier | fee_net | cluster_n | bh_q | promotion |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    tested = []
    for series in _SHADOW_SERIES:
        samples = graded[series]
        if len(samples) >= 5:
            p_value = _ev_p([item[1] for item in samples])
            if p_value is not None:
                tested.append((series, p_value))
    bh = _bh_q(tested)
    for series in _SHADOW_SERIES:
        samples = graded[series]
        n = len(samples)
        if n == 0:
            blank = "not enough data (n=0)"
            lines.append(
                f"| {series} | 0 | {blank} | {blank} | {blank} | 0 |  | "
                "not enough data (n=0, clustered n=0) settled trades, clustered events |"
            )
            continue
        shadow_brier = sum((item[2] - item[3]) ** 2 for item in samples) / Decimal(n)
        ask_brier = sum((item[5] - item[3]) ** 2 for item in samples) / Decimal(n)
        fee_net = sum((item[1] for item in samples), Decimal(0))
        clustered = scorecard_interval(_cluster_means(samples))
        cluster_n = int(clustered["n"] or 0)
        cluster_low = clustered["ci95_low"] if isinstance(clustered["ci95_low"], Decimal) else None
        q = bh.get(series)
        label = promotion_label(n=n, cluster_n=cluster_n, cluster_low=cluster_low, q=q)
        lines.append(
            f"| {series} | {n} | {format(shadow_brier.normalize(), 'f')} | "
            f"{format(ask_brier.normalize(), 'f')} | {format(fee_net.normalize(), 'f')} | "
            f"{cluster_n} | {_cell(q)} | {label} |"
        )
    return lines


def write_paper_scorecard(
    *,
    db_path: Path,
    market_lookup: Callable[[str], Any],
    report_path: Path,
    now: datetime | None = None,
    polymarket_lookup: Callable[[str], Any] | None = None,
    veto_log_path: Path | None = None,
    shadow_log_path: Path | None = None,
) -> str:
    """Per-series fee-net scorecard. Promotion text does not change modes."""
    moment = now or datetime.now(timezone.utc)
    modes = _scorecard_modes()
    grouped: dict[tuple[str, str], list[tuple[bool, Decimal, Decimal, Decimal]]] = {}
    calibration: list[dict[str, Any]] = []
    for ticker, series, side, ask, probability_text, venue, stored in _queued_candidate_rows(
        db_path
    ):
        name = str(series or "") or str(ticker or "").split("-", 1)[0]
        try:
            venue_name = settlement_venue(venue)
        except Exception:
            continue
        p_yes = _decimal(probability_text)
        saved = str(stored or "").strip().lower()
        if saved not in {"yes", "no"}:
            saved = ""
        try:
            live = resolve_settlement(
                venue_name,
                str(ticker),
                market_lookup=market_lookup,
                polymarket_lookup=polymarket_lookup,
            )
        except Exception:
            live = "unreadable"
        if venue_name == _POLYMARKET_US:
            if saved in {"yes", "no"} and live in {"yes", "no"} and saved != live:
                continue
            if live in {"yes", "no"} and not saved:
                remember_polymarket_settlement(db_path, str(ticker), str(live))
            if live in {"yes", "no"}:
                result = saved or str(live)
            elif saved in {"yes", "no"}:
                result = saved
            else:
                continue
        elif live not in {"yes", "no"}:
            continue
        else:
            result = live
        if p_yes is None or isinstance(ask, bool) or not isinstance(ask, int):
            continue
        pnl = contract_fee_net_dollars(str(side or ""), ask, result == "yes", venue_name)
        if pnl is None:
            continue
        outcome = Decimal(1 if result == "yes" else 0)
        side_name = str(side or "").strip().lower()
        grouped.setdefault((venue_name, name), []).append(
            ((str(side) == "yes") == (result == "yes"), pnl, p_yes, outcome, side_name, str(ticker or ""))
        )
        calibration.append(
            {
                "ticker": str(ticker or ""),
                "series": name,
                "venue": venue_name,
                "p": format(p_yes, "f"),
                "outcome": int(outcome),
            }
        )
    lines = [
        f"# Paper scorecard {moment.strftime('%Y-%m-%d')}",
        "",
        "Report only. Eligible for live needs n>=15, clustered n>=15, clustered CI low > 0, and BH q at or below 0.10.",
        "Otherwise the cell names the miss. Modes do not change.",
        "EV is per-contract taker-fee-net. One settled row per ticker. CI is mean +/- 1.96 sample stdev / sqrt(n). n<2 has none. Same settlement date is one event.",
        "BH q tests mean fee-net > 0 when n>=5. Brier is the mean squared error of the YES probability against the settlement.",
        "",
        "| venue | series | mode | n | hits | hit_rate | ev_per_contract | ci95_low | ci95_high | brier | cluster_n | cluster_ci95_low | cluster_ci95_high | bh_q | series_tested | promotion |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    tested = []
    for key, samples in grouped.items():
        p_value = _ev_p([item[1] for item in samples]) if len(samples) >= 5 else None
        if p_value is not None:
            tested.append((key, p_value))
    bh = _bh_q(tested)
    if not grouped:
        lines.append("| | | | 0 | | | | | | | | | | | | no settled fee-net rows |")
    for venue_name, series in sorted(grouped):
        samples = grouped[(venue_name, series)]
        values = [item[1] for item in samples]
        hits = sum(item[0] for item in samples)
        stats = scorecard_interval(values)
        n = int(stats["n"] or 0)
        mode, effective = modes.get(series, ("", ""))
        low = stats["ci95_low"] if isinstance(stats["ci95_low"], Decimal) else None
        high = stats["ci95_high"] if isinstance(stats["ci95_high"], Decimal) else None
        mean = stats["mean"] if isinstance(stats["mean"], Decimal) else None
        rate = None if n == 0 else Decimal(hits) / Decimal(n)
        squared = sum((item[2] - item[3]) ** 2 for item in samples)
        brier = None if n == 0 else squared / Decimal(n)
        brier_cell = "" if brier is None else format(brier.normalize(), "f")
        clustered = scorecard_interval(_cluster_means(samples))
        cn = int(clustered["n"] or 0)
        clow = clustered["ci95_low"] if isinstance(clustered["ci95_low"], Decimal) else None
        chigh = clustered["ci95_high"] if isinstance(clustered["ci95_high"], Decimal) else None
        q = bh.get((venue_name, series))
        shown = promotion_label(n=n, cluster_n=cn, cluster_low=clow, q=q)
        lines.append(
            f"| {venue_name} | {series} | {effective or mode} | {n} | {hits} | {_cell(rate)} | "
            f"{_cell(mean)} | {_cell(low)} | {_cell(high)} | {brier_cell} | {cn} | "
            f"{_guard_cell(clow, cn)} | {_guard_cell(chigh, cn)} | {_cell(q)} | {len(bh)} | {shown} |"
        )
    lines.extend(_side_band_table([row for rows in grouped.values() for row in rows]))
    lines.extend(
        _polymarket_section(
            polymarket_observation_rows(
                db_path,
                market_lookup=market_lookup,
                polymarket_lookup=polymarket_lookup,
            )
        )
    )
    veto_rows = [] if veto_log_path is None else _read_jsonl(veto_log_path)
    lines.append(_veto_scorecard_text(veto_rows, market_lookup).strip("\n"))
    shadow_rows = [] if shadow_log_path is None else _read_jsonl(shadow_log_path)
    lines.extend(_count_shadow_lines(shadow_rows, market_lookup))
    text = "\n".join(lines) + "\n"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    report_path.with_suffix(".calibration.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in calibration),
        encoding="utf-8",
    )
    return text

def _paper_only_series(series: str) -> bool:
    universe = get_universe()
    if universe.failed:
        return False
    return any(
        entry.series == series and entry.mode == "paper" and effective_mode(entry) != "live"
        for entry in universe.entries
    )

def _book_field(market: Any, *names: str) -> Any:
    for name in names:
        present = name in market if isinstance(market, Mapping) else hasattr(market, name)
        if present:
            return market[name] if isinstance(market, Mapping) else getattr(market, name)
    return None

def _list_block(series: str, now: datetime | None = None) -> str | None:
    upper = str(series or "").strip().upper()
    if not upper:
        return "universe"
    universe = get_universe()
    if universe.failed:
        return "universe_unavailable"
    if any(entry.series == upper and entry.mode != "off" for entry in universe.entries):
        if _polymarket_paused(upper, now):
            return "paused"
        return None
    from config import MARKET_SERIES_BLOCKLIST_PREFIXES
    return "blocklist" if any(upper.startswith(item) for item in MARKET_SERIES_BLOCKLIST_PREFIXES) else "universe"

def _polymarket_side_block(side: str, ask: Any) -> str | None:
    if isinstance(ask, bool) or type(ask) is not int:
        return "bad_ask"
    if ask < EVENT_NEWS_BAND_FLOOR_CENTS:
        return "ask_band"
    if ask >= _NEAR_CERTAIN_ASK_CENTS:
        return "near_certain_ask"
    if side == "yes" and ask < 55:
        return "yes_floor"
    return None

def _polymarket_book_block(view: Mapping[str, Any], market: Any = None) -> str | None:
    reasons: list[str] = []
    for side in ("yes", "no"):
        raw = _book_field(market, f"{side}_ask_size_fp", f"{side}_ask_size")
        try:
            empty = raw is not None and float(raw) == 0.0
        except (TypeError, ValueError):
            empty = False
        ask = view.get(f"{side}_ask")
        ask = ask if type(ask) is int else _quote_cents(market, side, "ask")
        if (reason := "depth" if empty else _polymarket_side_block(side, ask)) is None:
            return None
        reasons.append(reason)
    return reasons[0] if reasons else "bad_ask"

def _paper_gate_reason(
    market: Any,
    view: Mapping[str, Any],
    probability: float | None,
    now: datetime | None = None,
) -> str | None:
    series = str(view.get("series") or "")
    if blocked := _list_block(series, now) or _polymarket_book_block(view, market):
        return blocked
    if _view_venue(view) == _POLYMARKET_US:
        return None
    if not _paper_only_series(series):
        return None
    scored = SimpleNamespace(
        ticker=view.get("ticker") or "",
        series_ticker=series,
        yes_ask=view.get("yes_ask"),
        no_ask=view.get("no_ask"),
        yes_ask_cents=view.get("yes_ask"),
        no_ask_cents=view.get("no_ask"),
        yes_ask_size=_book_field(market, "yes_ask_size", "yes_ask_size_fp"),
        no_ask_size=_book_field(market, "no_ask_size", "no_ask_size_fp"),
        open_interest_fp=_book_field(market, "open_interest_fp", "open_interest"),
        volume_24h_fp=_book_field(market, "volume_24h_fp", "volume_24h", "volume"),
        status=view.get("status") or "",
    )
    decision = event_news_paper_score_decision(
        scored,
        probability=probability,
        config=SimpleNamespace(
            paper_cohort_id=EVENT_NEWS_COHORT_ID,
            llm_excluded_price_bands=[(0.0, 0.35)],
            llm_allowed_price_bands=[(0.55, 0.99)],
        ),
        environ=_PAPER_SCORE_ENV,
        contracts=PAPER_QUEUE_CONTRACTS,
    )
    if decision["decision"] != "skip":
        return None
    ignored = {"edge_not_evaluated", "transcript_skip_bypassed_for_paper_score"}
    return next((str(reason) for reason in decision["reasons"] if reason not in ignored), None)

def _store_decision(
    view: Mapping[str, Any],
    *,
    reason: str,
    candidate_log_path: Path,
    db_path: Path | None,
    now: datetime | None,
    probability: Decimal | None = None,
    side: str | None = None,
    ask: int | None = None,
    edge: int | None = None,
    fee: int | None = None,
    queued: bool = False,
    prompt_hash: str = "",
    sources: Sequence[str] = (),
    yes_ask: Any = None,
) -> None:
    record: dict[str, Any] = {
        "ts": _stamp(now),
        "series": str(view.get("series") or ""),
        "ticker": view.get("ticker"),
        "side": side,
        "ask_cents": ask,
        "maker_limit_cents": resting_bid_cents(ask),
        "yes_ask_cents": view.get("yes_ask") if yes_ask is None else yes_ask,
        "grok_probability": None if probability is None else str(probability),
        "edge_cents": edge,
        "fee_cents": fee,
        "sources": list(sources),
        "contracts": PAPER_QUEUE_CONTRACTS,
        "queued": queued,
        "skip_reason": None if queued else reason,
        "reason_code": reason,
        "prompt_hash": prompt_hash,
        "model": GROK_MODEL,
        "venue": _view_venue(view),
    }
    _append_jsonl(candidate_log_path, record)
    _insert_paper_row(db_path, "paper_candidates", record)

def _queue_best_side(
    view: Mapping[str, Any],
    call: GrokEdgeCall,
    *,
    yes_floor: int,
    candidate_log_path: Path,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    assert call.probability is not None
    edge_min = _yaml_edge_min(str(view.get("series") or ""))
    best: tuple[str, int, int] | None = None
    best_reason = "no_side"
    for side, ask in (("yes", view["yes_ask"]), ("no", view["no_ask"])):
        probability = call.probability if side == "yes" else Decimal(1) - call.probability
        reason, edge = paper_queue_decision(
            probability=probability,
            ask_cents=ask,
            side=side,
            contracts=PAPER_QUEUE_CONTRACTS,
            edge_min=edge_min,
            yes_floor_cents=yes_floor,
            venue=_view_venue(view),
        )
        if reason != "queue" or edge is None or ask is None:
            best_reason = reason
            continue
        if best is None or edge > best[1]:
            best = (side, edge, ask)
    record_side = None
    record_ask = None
    record_edge = None
    record_fee = None
    queued = False
    reason = best_reason
    if best is not None:
        record_side, record_edge, record_ask = best
        sided = call.probability if record_side == "yes" else Decimal(1) - call.probability
        quoted = _fee_net(sided, record_ask, PAPER_QUEUE_CONTRACTS, _view_venue(view))
        record_fee = None if quoted is None else int(
            (quoted[1] * Decimal(100)).to_integral_value(rounding=ROUND_CEILING)
        )
        queued = True
        reason = "queue"
    _store_decision(
        view,
        probability=call.probability,
        side=record_side,
        ask=record_ask,
        edge=record_edge,
        fee=record_fee,
        queued=queued,
        reason=reason,
        prompt_hash=call.prompt_hash,
        sources=call.sources,
        candidate_log_path=candidate_log_path,
        db_path=db_path,
        now=now,
        yes_ask=view.get("yes_ask"),
    )
    return best is not None

def _schema_format(kind: str) -> dict[str, Any]:
    if kind == "exit":
        return {
            "type": "json_schema",
            "name": "grok_exit_watch",
            "strict": True,
            "schema": _EXIT_SCHEMA,
        }
    if kind == "series":
        return {
            "type": "json_schema",
            "name": "grok_series_context",
            "strict": True,
            "schema": _SERIES_SCHEMA,
        }
    return {
        "type": "json_schema",
        "name": "grok_edge_prob",
        "strict": True,
        "schema": _EDGE_SCHEMA,
    }

def _p90(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = int(
        (Decimal("0.9") * Decimal(len(ordered))).to_integral_value(rounding=ROUND_CEILING)
    )
    index = min(len(ordered), max(1, rank)) - 1
    return ordered[index]

def _measured_costs(path: Path, *, grounded: bool) -> list[Decimal]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    found: list[Decimal] = []
    for line in lines[-200:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("cost_source") != "provider":
            continue
        if bool(row.get("grounded")) != grounded:
            continue
        cost = _decimal(row.get("cost_usd"))
        if cost is not None and cost > 0:
            found.append(cost)
    return found[-MEASURED_COST_WINDOW:]

def call_reserve_usd(path: Path, *, grounded: bool) -> Decimal:
    """Reserve max(recent measured p90 x 1.5, $0.05) for this call class."""
    p90 = _p90(_measured_costs(path, grounded=grounded))
    if p90 is None:
        return MIN_CALL_RESERVE_USD
    scaled = p90 * RESERVE_P90_MULTIPLIER
    if scaled < MIN_CALL_RESERVE_USD:
        return MIN_CALL_RESERVE_USD
    return scaled

def _provider_usage(body: Any) -> tuple[Decimal | None, dict[str, Any]]:
    if not isinstance(body, dict):
        return None, {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    meta: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens"):
        raw = usage.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            meta[key] = raw
    if "cached_tokens" not in meta:
        for nest in (usage.get("prompt_tokens_details"), usage.get("input_tokens_details")):
            raw = nest.get("cached_tokens") if isinstance(nest, dict) else None
            if type(raw) is int and raw >= 0:
                meta["cached_tokens"] = raw
                break
    details = usage.get("server_side_tool_usage_details")
    if isinstance(details, dict):
        for key in ("web_search_calls", "x_search_calls", "x_posts_fetched", "x_users_fetched"):
            raw = details.get(key)
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                meta[key] = raw
    ticks = usage.get("cost_in_usd_ticks")
    if isinstance(ticks, int) and not isinstance(ticks, bool) and ticks >= 0:
        meta["cost_in_usd_ticks"] = ticks
        return Decimal(ticks) / COST_TICKS_PER_USD, meta
    reported = _reported_cost(body)
    if reported is not None:
        return reported, meta
    return None, meta

def _redact_reply(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEY.search(name):
                cleaned[name] = "[redacted]"
            else:
                cleaned[name] = _redact_reply(item)
        return cleaned
    if isinstance(value, list):
        return [_redact_reply(item) for item in value]
    if isinstance(value, str):
        text = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", value)
        return re.sub(r"(?i)\b(?:sk|xai)-[A-Za-z0-9_\-]+", "[redacted]", text)
    return value

def _save_raw_reply(
    directory: Path,
    *,
    ticker: str,
    kind: str,
    attempt: int,
    body: Any,
) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", ticker)[:80] or "market"
        path = directory / f"{stamp}_{safe}_{kind}_{attempt}.json"
        payload = body if isinstance(body, (dict, list)) else {"body": str(body)[:8000]}
        path.write_text(
            json.dumps(_redact_reply(payload), sort_keys=True, default=str),
            encoding="utf-8",
        )
    except OSError:
        return

def _parse_series_body(body: Any, expected: Sequence[str]) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    obj = _select_json_object(_response_text(body), kind="series")
    if obj is None:
        return None
    sources = list(_http_sources(body, obj))
    if not sources:
        return None
    markets: list[dict[str, Any]] = []
    raw_markets = obj.get("markets")
    if isinstance(raw_markets, list):
        for item in raw_markets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("ticker") or "").strip()
            probability = _unit_decimal(item.get("prob", item.get("probability")))
            if not name or probability is None:
                continue
            confidence = _unit_decimal(item.get("confidence"))
            markets.append(
                {
                    "ticker": name,
                    "prob": format(probability, "f"),
                    "confidence": None if confidence is None else format(confidence, "f"),
                    "rationale": str(item.get("rationale") or "")[:500],
                }
            )
    names = tuple(name for name in expected if name)
    if not markets and len(names) == 1:
        probability = _unit_decimal(obj.get("prob", obj.get("probability")))
        if probability is None:
            return None
        confidence = _unit_decimal(obj.get("confidence"))
        markets.append(
            {
                "ticker": names[0],
                "prob": format(probability, "f"),
                "confidence": None if confidence is None else format(confidence, "f"),
                "rationale": str(obj.get("rationale") or "")[:500],
            }
        )
    if not markets:
        return None
    return {
        "latest_post_id": str(obj.get("latest_post_id") or "")[:120],
        "summary": str(obj.get("summary") or obj.get("rationale") or "")[:500],
        "sources": sources,
        "markets": markets,
    }

def _call_grok(
    *,
    ticker: str,
    prompt: str,
    system: str,
    grounded: bool,
    environ: Mapping[str, str] | None,
    post: Callable[..., Any] | None,
    call_log_path: Path,
    spend_path: Path,
    now: datetime | None,
    kind: str = "scan",
    raw_dir: Path = RAW_DIR,
    series: str = "",
    expected_tickers: Sequence[str] = (),
    venue: str = "kalshi",
) -> GrokEdgeCall | None:
    env = os.environ if environ is None else environ
    hard = _call_cap(_daily_cap(env), "exit")
    cap = hard if kind == "exit" else _call_cap(_daily_cap(env), kind, _trailing_fee_net(spend_path, now))
    tools = _SCAN_TOOLS if grounded else ()
    search_limit = grok_max_search_results(env) if grounded else 0
    tool_series = series or (ticker.split("-", 1)[0] if ticker else "")
    tool_payload, turns = (
        _grounded_tool_payload(tool_series, now, search_limit) if grounded else ([], 0)
    )
    system_text = system + (_search_limit_sentence(search_limit) if grounded else "")
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    def _finish(
        skip: str | None,
        body: Any,
        cost: Decimal,
        *,
        cost_source: str,
        usage: Mapping[str, Any],
        attempt: int,
    ) -> GrokEdgeCall:
        detail = ""
        if kind == "series":
            parsed_series = (
                _parse_series_body(body, expected_tickers) if skip is None else None
            )
            if skip is None and parsed_series is None:
                skip = "malformed"
                _save_raw_reply(raw_dir, ticker=ticker, kind=kind, attempt=attempt, body=body)
            rationale = "" if parsed_series is None else str(parsed_series.get("summary") or "")
            sources = () if parsed_series is None else tuple(parsed_series["sources"])
            detail = "" if parsed_series is None else json.dumps(parsed_series, sort_keys=True)
            probability = None
            confidence = None
            verdict = ""
        else:
            parsed = _parse_edge_body(body, grounded=grounded, kind=kind) if skip is None else None
            if skip is None and parsed is None:
                skip = "malformed"
                _save_raw_reply(raw_dir, ticker=ticker, kind=kind, attempt=attempt, body=body)
            rationale = "" if parsed is None or len(parsed) < 5 else parsed[4]
            probability = None if parsed is None else parsed[0]
            confidence = None if parsed is None else parsed[1]
            verdict = "" if parsed is None else parsed[2]
            sources = () if parsed is None else parsed[3]
            detail = rationale
        call = GrokEdgeCall(
            ticker=ticker,
            grounded=grounded,
            probability=probability,
            confidence=confidence,
            verdict=verdict,
            sources=sources,
            tools=tools,
            prompt_hash=prompt_hash,
            latency_ms=0,
            cost_usd=cost,
            skip_reason=skip,
            detail=detail,
        )
        record = {
            "ts": _stamp(now),
            "kind": kind,
            "ticker": ticker,
            "prompt_hash": prompt_hash,
            "grounded": grounded,
            "tools": list(tools),
            "sources": list(call.sources),
            "verdict": call.verdict,
            "probability": None if call.probability is None else str(call.probability),
            "confidence": None if call.confidence is None else str(call.confidence),
            "rationale": rationale[:500],
            "attempt": attempt,
            "cost_usd": format(cost, "f"),
            "cost_source": cost_source,
            "skip_reason": skip,
            "venue": _POLYMARKET_US if venue == _POLYMARKET_US else "kalshi",
        }
        if search_limit:
            record["max_search_results"] = search_limit
            record["max_turns"] = turns
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_tokens",
            "cost_in_usd_ticks",
            "web_search_calls",
            "x_search_calls",
            "x_posts_fetched",
            "x_users_fetched",
        ):
            if key in usage:
                record[key] = usage[key]
        _append_jsonl(call_log_path, record)
        if skip:
            log.info(
                "[GROK_EDGE] skip=%s ticker=%s grounded=%s attempt=%s",
                skip,
                ticker,
                grounded,
                attempt,
            )
        return call
    def _attempt(user_prompt: str, attempt: int) -> GrokEdgeCall:
        reserve = call_reserve_usd(call_log_path, grounded=grounded)
        key = str(env.get(XAI_API_KEY_ENV) or "").strip()
        if not key:
            return _finish(
                "missing_key", None, Decimal("0"), cost_source="reserve", usage={}, attempt=attempt
            )
        reserved = _reserve_spend(spend_path, reserve, cap=cap, now=now)
        if reserved is None:
            return _finish(
                "spend_cap", None, Decimal("0"), cost_source="reserve", usage={}, attempt=attempt
            )
        payload: dict[str, Any] = {
            "model": GROK_MODEL,
            "temperature": 0,
            "input": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_prompt},
            ],
            "text": {"format": _schema_format(kind)},
        }
        if grounded:
            payload["tools"] = tool_payload
            payload["max_turns"] = turns
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        sender = post if post is not None else requests.post
        try:
            response = sender(
                XAI_RESPONSES_URL,
                json=payload,
                headers=headers,
                timeout=GROK_EDGE_TIMEOUT_SECONDS,
            )
        except requests.Timeout:
            return _finish(
                "timeout", None, reserved, cost_source="reserve", usage={}, attempt=attempt
            )
        except Exception:
            return _finish(
                "request_error", None, reserved, cost_source="reserve", usage={}, attempt=attempt
            )
        if getattr(response, "status_code", None) != 200:
            return _finish("http", None, reserved, cost_source="reserve", usage={}, attempt=attempt)
        try:
            body = response.json()
        except Exception:
            return _finish(
                "malformed", None, reserved, cost_source="reserve", usage={}, attempt=attempt
            )
        actual, usage = _provider_usage(body)
        cost = reserved
        source = "reserve"
        if actual is not None:
            _reconcile_spend(spend_path, reserved, actual, cap=hard, now=now)
            cost = actual
            source = "provider"
        return _finish(None, body, cost, cost_source=source, usage=usage, attempt=attempt)

    first = _attempt(prompt, 1)
    if first.skip_reason != "malformed":
        return first
    return _attempt(
        prompt + "\n\nPrevious reply did not parse. Return the schema object only.",
        2,
    )

def _parse_edge_body(
    body: Any,
    *,
    grounded: bool,
    kind: str,
) -> tuple[Decimal, Decimal | None, str, tuple[str, ...], str] | None:
    if not isinstance(body, dict):
        return None
    obj = _select_json_object(_response_text(body), kind=kind)
    if obj is None:
        return None
    sources = _http_sources(body, obj)
    rationale = str(obj.get("rationale") or "").strip()
    if kind == "exit":
        if not isinstance(obj.get("contrary"), bool):
            return None
        confidence = _unit_decimal(obj.get("confidence"))
        probability = _unit_decimal(obj.get("prob", obj.get("probability")))
        if probability is None:
            return None
        if obj["contrary"] is True and (confidence is None or not sources):
            return None
        verdict = "CONTRARY" if obj["contrary"] else "OK"
        return probability, confidence, verdict, sources, rationale
    probability = _unit_decimal(obj.get("prob", obj.get("probability")))
    if probability is None:
        return None
    if grounded and not sources:
        return None
    confidence = _unit_decimal(obj.get("confidence"))
    verdict = str(obj.get("verdict") or "").strip().upper()
    if verdict not in {"YES", "NO"}:
        verdict = "YES" if probability >= Decimal("0.5") else "NO"
    return probability, confidence, verdict, sources, rationale

def _market_view(market: Any) -> dict[str, Any] | None:
    if isinstance(market, Mapping):
        ticker = str(market.get("ticker") or "").strip()
        series = str(market.get("series") or market.get("series_ticker") or "").strip().upper()
        status = str(market.get("status") or "").strip().lower()
        yes_ask = market.get("yes_ask")
        no_ask = market.get("no_ask")
        title = str(market.get("title") or "")
        rules = str(market.get("rules") or "")
        close = str(market.get("close_time") or "")
        venue_raw = market.get("venue")
    else:
        ticker = str(getattr(market, "ticker", "") or "").strip()
        series = event_news_series_ticker(market)
        status = str(getattr(market, "status", "") or "").strip().lower()
        yes_ask, no_ask = snapshot_ask_cents(market)
        title = str(getattr(market, "title", "") or "")
        rules = str(getattr(market, "rules_primary", "") or "")
        close = str(getattr(market, "close_time", "") or "")
        venue_raw = getattr(market, "venue", None)
    if not ticker:
        return None
    return {
        "ticker": ticker,
        "series": series,
        "status": status,
        "yes_ask": _int_or_none(yes_ask),
        "no_ask": _int_or_none(no_ask),
        "title": title,
        "rules": rules,
        "close_time": close,
        "venue": _view_venue({"venue": venue_raw}),
    }

def _strike_text(ticker: str) -> str:
    suffix = ticker.rsplit("-", 1)[-1]
    if len(suffix) >= 2 and suffix[0] in "TE" and suffix[1].isdigit():
        return suffix[1:]
    return ""

def _market_prompt(view: Mapping[str, Any]) -> str:
    return (
        "Estimate the YES probability. "
        f"ticker={view['ticker']} strike={_strike_text(str(view['ticker']))} "
        f"title={view['title']} rules={view['rules']} close={view['close_time']}"
    )

def _quote_cents(market: Any, side: str, quote: str) -> int | None:
    if market is None:
        return None
    names = (f"{side}_{quote}_cents", f"{side}_{quote}_dollars", f"{side}_{quote}")
    for name in names:
        raw = market.get(name) if isinstance(market, Mapping) else getattr(market, name, None)
        cents = _ask_cents(raw)
        if cents is not None:
            return cents
    return None

def _mark_cents(market: Any, side: str, row: Any) -> int | None:
    """Executable ask, else live bid, else exposure over size. Size 0 is not an ask."""
    size_name = f"{side}_ask_size_fp"
    if isinstance(market, Mapping):
        size = market.get(size_name)
    else:
        size = None if market is None else getattr(market, size_name, None)
    try:
        empty = size is not None and float(size) == 0.0
    except (TypeError, ValueError):
        empty = False
    ask = None if empty else _quote_cents(market, side, "ask")
    if ask is not None:
        return ask
    bid = _quote_cents(market, side, "bid")
    if bid is not None:
        return bid
    if not isinstance(row, Mapping):
        return None
    try:
        count = abs(float(row.get("position_fp", row.get("position"))))
        cents = int(round(float(row["market_exposure_dollars"]) / count * 100))
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        return None
    return cents if 1 <= cents <= 99 else None

def _bid_cents(market: Any, side: str) -> int | None:
    return _quote_cents(market, side, "bid")

def _call_cap(cap: Decimal, kind: str, pnl: Decimal | None = None) -> Decimal:
    """Exit keeps the hard cap. Paper stops at the profit share and leaves $1 above $1."""
    hard = _HARD_USD_CAP if cap > _HARD_USD_CAP else cap
    if kind == "exit":
        return hard
    share = _PAPER_FLOOR_USD if pnl is None else max(_PAPER_FLOOR_USD, _PROFIT_SHARE * pnl)
    budget = min(hard, share)
    return budget if hard <= 1 else min(budget, hard - 1)

def _sum_fee_net_window(rows: Sequence[Any], now: datetime | None) -> Decimal | None:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    end = moment.astimezone(timezone.utc).date()
    start = end - timedelta(days=6)
    total = Decimal("0")
    for row in rows:
        if not isinstance(row, dict):
            return None
        when = _parse_utc(row.get("close"))
        amount = _decimal(row.get("fee_net"))
        status = str(row.get("status") or "").strip().lower()
        if when is None or amount is None or status not in _SETTLED_RESULTS:
            return None
        if start <= when.date() <= end:
            total += amount
    return total

def _trailing_fee_net(spend_path: Path, now: datetime | None) -> Decimal | None:
    path = spend_path.with_name(_PNL_CACHE_NAME)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    rows = data.get("rows") if isinstance(data, dict) else None
    return _sum_fee_net_window(rows, now) if isinstance(rows, list) else None

def _revenue_dollars(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return Decimal(value) / Decimal(100)
    if isinstance(value, str) and value.strip():
        try:
            number = Decimal(value.strip())
        except Exception:
            return None
        if number == number.to_integral_value():
            return number / Decimal(100)
    return None

def cache_settlement_fee_net(rows: Sequence[Any], path: Path | None = None, now: datetime | None = None) -> Decimal | None:
    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            return None
        result = str(raw.get("market_result") or "").strip().lower()
        if result not in {"yes", "no"}:
            continue
        ticker = str(raw.get("ticker") or "").strip()
        if not ticker or ticker in seen:
            return None
        seen.add(ticker)
        when = _parse_utc(raw.get("close_time") or raw.get("settled_time"))
        revenue = _revenue_dollars(raw.get("revenue"))
        yes_cost = _decimal(raw.get("yes_total_cost_dollars"))
        no_cost = _decimal(raw.get("no_total_cost_dollars"))
        fee = _decimal(raw.get("fee_cost"))
        if when is None or revenue is None or yes_cost is None or no_cost is None or fee is None:
            return None
        fee_net = revenue - yes_cost - no_cost - fee
        parsed.append({"close": _stamp(when), "fee_net": format(fee_net, "f"), "status": result})
    target = path or SPEND_PATH.with_name(_PNL_CACHE_NAME)
    try:
        _write_json(target, {"rows": parsed})
    except OSError:
        return None
    return _sum_fee_net_window(parsed, now)

def _week_spend(path: Path, now: datetime | None) -> Decimal | None:
    if not path.is_file():
        return None
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    end = moment.astimezone(timezone.utc).date()
    start = end - timedelta(days=6)
    total = Decimal("0")
    for row in _read_jsonl(path):
        when = _parse_utc(row.get("ts"))
        cost = _decimal(row.get("cost_usd"))
        if when is not None and cost is not None and cost >= 0 and start <= when.date() <= end:
            total += cost
    return total

def _profit_share_line(spend_path: Path, call_log_path: Path, now: datetime | None) -> str:
    spent = _week_spend(call_log_path, now)
    pnl = _trailing_fee_net(spend_path, now)
    spend_text = "not measured" if spent is None else f"{format(spent, 'f')} USD"
    pnl_text = "not measured" if pnl is None else f"{format(pnl, 'f')} USD"
    ratio, warn = "not measured", pnl is None and spent is not None and spent > 0
    if pnl is not None and spent is not None:
        if pnl > 0:
            ratio, warn = format(spent / pnl, "f"), spent > pnl * Decimal("0.5")
        elif spent > 0:
            ratio, warn = "not a profit", True
        else:
            ratio = "0"
    line = f"7d grok spend vs settled fee-net: spend {spend_text}, fee-net {pnl_text}, ratio {ratio}"
    if warn:
        line += "\nWARN grok spend is above half of the 7-day settled fee-net"
    return line

def _response_text(body: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        chunks.append(output_text)
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        chunks.append(part["text"])
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            chunks.append(message["content"])
    return "\n".join(chunks)

def _http_sources(body: Mapping[str, Any], obj: Mapping[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    def _add(value: Any) -> None:
        if isinstance(value, str) and (
            value.startswith("https://") or value.startswith("http://")
        ):
            text = value.strip()
            if text not in found:
                found.append(text)
        elif isinstance(value, dict):
            _add(value.get("url"))

    citations = body.get("citations")
    if isinstance(citations, list):
        for item in citations:
            _add(item)
    sources = obj.get("sources")
    if isinstance(sources, list):
        for item in sources:
            _add(item)
    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            _add(node.get("url"))
            for item in node.values():
                _walk(item)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body.get("output"))
    return tuple(found)

def _select_json_object(content: str, *, kind: str) -> dict[str, Any] | None:
    if kind == "exit":
        wanted = ("contrary",)
    elif kind == "series":
        wanted = ("markets", "prob", "probability")
    else:
        wanted = ("prob", "probability")
    decoder = json.JSONDecoder()
    chosen: dict[str, Any] | None = None
    envelope: dict[str, Any] | None = None
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if any(key in obj for key in wanted):
            chosen = obj
        if kind == "series" and isinstance(obj.get("markets"), list):
            envelope = obj
    if kind == "series" and envelope is not None:
        return envelope
    return chosen

def _reserve_spend(
    path: Path,
    amount: Decimal,
    *,
    cap: Decimal,
    now: datetime | None,
) -> Decimal | None:
    """Debit ``amount`` if the UTC day still has room. None means do not call."""
    with _SPEND_LOCK:
        return _reserve_spend_unlocked(path, amount, cap=cap, now=now)

def _reserve_spend_unlocked(
    path: Path,
    amount: Decimal,
    *,
    cap: Decimal,
    now: datetime | None,
) -> Decimal | None:
    if amount <= 0 or cap < 0:
        return None
    today = _day(now)
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(existing, dict):
        return None
    if existing.get("date") != today:
        spent = Decimal("0")
    else:
        spent = _decimal(existing.get("spent_usd"))
        if spent is None or spent < 0:
            return None
    if spent + amount > cap:
        return None
    updated = {"date": today, "spent_usd": str(spent + amount)}
    try:
        _write_json(path, updated)
    except OSError:
        return None
    return amount

def _reconcile_spend(
    path: Path,
    reserved: Decimal,
    actual: Decimal,
    *,
    cap: Decimal,
    now: datetime | None,
) -> None:
    """Replace the reserve with provider cost. Pin the ledger when a larger cost does not fit."""
    if actual < 0 or reserved < 0:
        return
    delta = actual - reserved
    if delta == 0:
        return
    with _SPEND_LOCK:
        if delta > 0:
            added = _reserve_spend_unlocked(path, delta, cap=cap, now=now)
            if added is None:
                _pin_spend_to_cap_unlocked(path, cap, now)
            return
        _credit_spend_unlocked(path, -delta, now=now)

def _credit_spend_unlocked(path: Path, amount: Decimal, *, now: datetime | None) -> None:
    if amount <= 0:
        return
    today = _day(now)
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(existing, dict) or existing.get("date") != today:
        return
    spent = _decimal(existing.get("spent_usd"))
    if spent is None or spent < amount:
        return
    try:
        _write_json(path, {"date": today, "spent_usd": format(spent - amount, "f")})
    except OSError:
        return

def _pin_spend_to_cap(path: Path, cap: Decimal, now: datetime | None) -> None:
    """Fill today's ledger so a later reserve cannot fit."""
    with _SPEND_LOCK:
        _pin_spend_to_cap_unlocked(path, cap, now)

def _pin_spend_to_cap_unlocked(path: Path, cap: Decimal, now: datetime | None) -> None:
    if cap < 0:
        return
    today = _day(now)
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(existing, dict):
        return
    if existing.get("date") != today:
        spent = Decimal("0")
    else:
        spent = _decimal(existing.get("spent_usd"))
        if spent is None or spent < 0:
            return
    if spent >= cap:
        return
    try:
        _write_json(path, {"date": today, "spent_usd": str(cap)})
    except OSError:
        return

def _reported_cost(body: Any) -> Decimal | None:
    if not isinstance(body, dict):
        return None
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    for container in (body, usage):
        for key in ("cost_in_usd", "cost_usd"):
            parsed = _decimal(container.get(key))
            if parsed is not None and parsed > 0:
                return parsed
    return None

def _daily_cap(env: Mapping[str, str]) -> Decimal:
    raw = str(env.get(GROK_DAILY_USD_CAP) or "").strip()
    if not raw:
        return DEFAULT_DAILY_USD_CAP
    try:
        cap = Decimal(raw)
    except Exception:
        return DEFAULT_DAILY_USD_CAP
    if not cap.is_finite() or cap < 0:
        return DEFAULT_DAILY_USD_CAP
    return cap

def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows

def _insert_paper_row(db_path: Path | None, table: str, record: Mapping[str, Any]) -> None:
    if db_path is None:
        return
    if table not in {"paper_candidates", "position_scores"}:
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_candidates (
                    id INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    side TEXT,
                    ask_cents INTEGER,
                    yes_ask_cents INTEGER,
                    grok_probability TEXT,
                    edge_cents INTEGER,
                    sources_json TEXT NOT NULL,
                    queued INTEGER NOT NULL,
                    skip_reason TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS position_scores (
                    id INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    side TEXT NOT NULL,
                    ask_cents INTEGER,
                    grok_probability TEXT,
                    edge_cents INTEGER,
                    decision TEXT NOT NULL,
                    skip_reason TEXT,
                    sources_json TEXT NOT NULL
                )
                """
            )
            sources = json.dumps(list(record.get("sources") or []), ensure_ascii=True)
            if table == "paper_candidates":
                have = {row[1] for row in conn.execute("PRAGMA table_info(paper_candidates)")}
                for name, kind in (
                    ("series", "TEXT"),
                    ("fee_cents", "INTEGER"),
                    ("prompt_hash", "TEXT"),
                    ("model", "TEXT"),
                    ("reason_code", "TEXT"),
                    ("venue", "TEXT"),
                    ("settlement_result", "TEXT"),
                ):
                    if name not in have:
                        conn.execute(f"ALTER TABLE paper_candidates ADD COLUMN {name} {kind}")
                conn.execute(
                    """
                    INSERT INTO paper_candidates (
                        ts, ticker, side, ask_cents, yes_ask_cents, grok_probability,
                        edge_cents, sources_json, queued, skip_reason,
                        series, fee_cents, prompt_hash, model, reason_code, venue
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.get("ts") or ""),
                        str(record.get("ticker") or ""),
                        record.get("side"),
                        _int_or_none(record.get("ask_cents")),
                        _int_or_none(record.get("yes_ask_cents")),
                        record.get("grok_probability"),
                        _int_or_none(record.get("edge_cents")),
                        sources,
                        1 if record.get("queued") is True else 0,
                        record.get("skip_reason"),
                        record.get("series"),
                        _int_or_none(record.get("fee_cents")),
                        record.get("prompt_hash"),
                        record.get("model"),
                        record.get("reason_code"),
                        _view_venue(record),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO position_scores (
                        ts, ticker, side, ask_cents, grok_probability, edge_cents,
                        decision, skip_reason, sources_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.get("ts") or ""),
                        str(record.get("ticker") or ""),
                        str(record.get("side") or ""),
                        _int_or_none(record.get("ask_cents")),
                        record.get("grok_probability"),
                        _int_or_none(record.get("edge_cents")),
                        str(record.get("decision") or "not_scored"),
                        record.get("skip_reason"),
                        sources,
                    ),
                )

def _spend_line(path: Path) -> str:
    if not path.is_file():
        return "spend: not measured"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "spend: not measured"
    if not isinstance(data, dict):
        return "spend: not measured"
    spent = _decimal(data.get("spent_usd"))
    if spent is None or spent < 0:
        return "spend: not measured"
    day = str(data.get("date") or "").strip()
    when = f" on {day}" if day else ""
    return f"spend: {format(spent, 'f')} USD{when}"

def _settlement_result(market: Any) -> str | None:
    if market is None:
        return None
    if isinstance(market, Mapping):
        status = str(market.get("status") or "").strip().lower()
        result = str(market.get("result") or "").strip().lower()
    else:
        status = str(getattr(market, "status", "") or "").strip().lower()
        result = str(getattr(market, "result", "") or "").strip().lower()
    if status not in _SETTLED_STATUSES or result not in {"yes", "no"}:
        return None
    return result

def _settlements_for(
    rows: Sequence[Any],
    market_lookup: Callable[[str], Any],
    *,
    polymarket_lookup: Callable[[str], Any] | None = None,
    stored: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Settle each row on its own venue. A later blank venue cannot send that ticker to Kalshi."""
    found: dict[str, str] = {}
    seen: set[str] = set()
    saved_map = stored or {}
    for raw in rows:
        if isinstance(raw, Mapping):
            ticker = str(raw.get("ticker") or "").strip()
            venue = raw.get("venue")
        else:
            ticker = str(raw or "").strip()
            venue = ""
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        try:
            venue_name = settlement_venue(venue)
        except Exception:
            continue
        try:
            result = resolve_settlement(
                venue_name,
                ticker,
                market_lookup=market_lookup,
                polymarket_lookup=polymarket_lookup,
            )
        except Exception:
            continue
        saved = str(saved_map.get(ticker) or "").strip().lower()
        if saved not in {"yes", "no"}:
            saved = ""
        if (
            venue_name == _POLYMARKET_US
            and saved in {"yes", "no"}
            and result in {"yes", "no"}
            and saved != result
        ):
            continue
        if result in {"yes", "no"}:
            found[ticker] = result
    return found

def _joined_settled_rows(
    candidates: Sequence[Mapping[str, Any]],
    comparisons: Sequence[Mapping[str, Any]],
    settlements: Mapping[str, str],
) -> list[dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in candidates:
        ticker = str(row.get("ticker") or "").strip()
        probability = _unit_decimal(row.get("grok_probability"))
        if not ticker or probability is None or ticker not in settlements:
            continue
        by_ticker[ticker] = {
            "ticker": ticker,
            "probability": probability,
            "outcome": Decimal(1) if settlements[ticker] == "yes" else Decimal(0),
            "yes_ask_cents": _int_or_none(row.get("yes_ask_cents")),
            "side": row.get("side"),
            "ask_cents": _int_or_none(row.get("ask_cents")),
            "edge_cents": _int_or_none(row.get("edge_cents")),
            "queued": row.get("queued") is True,
            "contracts": _int_or_none(row.get("contracts")) or PAPER_QUEUE_CONTRACTS,
        }
    for row in comparisons:
        ticker = str(row.get("ticker") or "").strip()
        if ticker not in by_ticker or ticker not in settlements:
            continue
        grounded = _unit_decimal(row.get("grounded_probability"))
        ungrounded = _unit_decimal(row.get("ungrounded_probability"))
        if grounded is None or ungrounded is None:
            continue
        by_ticker[ticker]["grounded_probability"] = grounded
        by_ticker[ticker]["ungrounded_probability"] = ungrounded
        by_ticker[ticker]["outcome"] = Decimal(1) if settlements[ticker] == "yes" else Decimal(0)
    return list(by_ticker.values())

def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return "not enough data"
    return format(value, "f")

def _daily_report_text(
    rows: Sequence[Mapping[str, Any]],
    spend_line: str,
    *,
    now: datetime | None,
) -> str:
    del now
    if not rows:
        return (
            "# Grok edge daily\n\n"
            "n: not enough data\n"
            "hit rate: not enough data\n"
            "Brier vs ask: not enough data\n"
            "fee-net paper EV: not enough data\n"
            f"{spend_line}\n"
        )
    hits = 0
    brier_rows = []
    ask_rows = []
    comparison_rows = []
    ev_rows = []
    for row in rows:
        probability = row["probability"]
        outcome = row["outcome"]
        brier_rows.append({"probability": probability, "outcome": outcome})
        if (probability >= Decimal("0.5") and outcome == 1) or (
            probability < Decimal("0.5") and outcome == 0
        ):
            hits += 1
        yes_ask = row.get("yes_ask_cents")
        if isinstance(yes_ask, int) and 1 <= yes_ask <= 99:
            ask_rows.append(
                {"probability": Decimal(yes_ask) / Decimal(100), "outcome": outcome}
            )
        if "grounded_probability" in row and "ungrounded_probability" in row:
            comparison_rows.append(
                {
                    "grounded_probability": row["grounded_probability"],
                    "ungrounded_probability": row["ungrounded_probability"],
                    "outcome": outcome,
                }
            )
        if row.get("queued") is True and isinstance(row.get("edge_cents"), int):
            side = str(row.get("side") or "")
            ask = row.get("ask_cents")
            contracts = row.get("contracts")
            if side in {"yes", "no"} and isinstance(ask, int) and isinstance(contracts, int):
                held = exit_versus_hold(
                    side=side,
                    bid_cents=ask,
                    settlement_yes=outcome == 1,
                    contracts=contracts,
                    entry_ask_cents=ask,
                )
                if held is not None:
                    ev_rows.append(
                        {
                            "edge_cents": row["edge_cents"],
                            "pnl_dollars": Decimal(held["hold_cents"]) / Decimal(100),
                        }
                    )
    count = len(rows)
    grok_brier = brier_report(brier_rows)
    ask_brier = brier_report(ask_rows)
    compared = comparison_summary(comparison_rows)
    ev = ev_by_edge_bucket(ev_rows)
    lines = [
        "# Grok edge daily",
        "",
        f"n: {count}",
        f"hit rate: {hits} of {count}",
        (
            "Brier vs ask: "
            f"grok {_decimal_text(_decimal(grok_brier.get('brier')))} "
            f"ask {_decimal_text(_decimal(ask_brier.get('brier')))}"
        ),
    ]
    if compared.get("status") == "ok" and compared.get("settled_n"):
        lines.append(
            "grounded closer than ungrounded: "
            f"{compared.get('grounded_hits')} of {compared.get('settled_n')}"
        )
    else:
        lines.append("grounded closer than ungrounded: not enough data")
    measured_ev = [
        bucket
        for bucket in (ev.get("buckets") or [])
        if bucket.get("status") == "ok"
    ]
    if not measured_ev:
        lines.append("fee-net paper EV: not enough data")
    else:
        lines.append("fee-net paper EV:")
        for bucket in ev.get("buckets") or []:
            lines.append(
                "  {bucket}: n={n} mean={mean} ci95_low={low} ci95_high={high} status={status}".format(
                    bucket=bucket.get("bucket"),
                    n=bucket.get("n"),
                    mean=_decimal_text(_decimal(bucket.get("mean_ev"))),
                    low=_decimal_text(_decimal(bucket.get("ci95_low"))),
                    high=_decimal_text(_decimal(bucket.get("ci95_high"))),
                    status=bucket.get("status"),
                )
            )
    lines.append(spend_line)
    lines.append("")
    return "\n".join(lines)

def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)

def _stamp(now: datetime | None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _day(now: datetime | None) -> str:
    return _stamp(now)[:10]

def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, Real):
        number = Decimal(str(value))
    elif isinstance(value, str):
        try:
            number = Decimal(value)
        except Exception:
            return None
    else:
        return None
    if not number.is_finite():
        return None
    return number

def _unit_decimal(value: Any) -> Decimal | None:
    number = _decimal(value)
    if number is None or number < 0 or number > 1:
        return None
    return number

def _outcome(value: Any) -> Decimal | None:
    number = _unit_decimal(value)
    if number == Decimal(0) or number == Decimal(1):
        return Decimal(int(number))
    return None

def _pair(row: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    grounded = _unit_decimal(row.get("grounded_probability"))
    ungrounded = _unit_decimal(row.get("ungrounded_probability"))
    if grounded is None or ungrounded is None:
        return None
    return grounded, ungrounded

def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
