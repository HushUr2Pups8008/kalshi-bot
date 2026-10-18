"""Paper-only Grok edge scan, exit watch, and calibration.

``live=True`` returns before any call, even when the flag is the string
true. This module never submits, cancels, or reprices an order. A missing,
late, or malformed Grok answer queues nothing. The sizing buckets are a
proposal stored on the candidate. The queued contract count stays at 4.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from numbers import Real
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests

from utils.event_news_desk_flags import (
    EVENT_NEWS_GROK_EDGE_SCAN,
    EVENT_NEWS_GROK_EXIT_WATCH,
    HARD_MAX_CONTRACTS,
    event_news_desk_flag_enabled,
    event_news_yes_floor_cents,
)
from utils.event_news_grok_flags import EVENT_NEWS_BAND_FLOOR_CENTS
from utils.event_news_grok_veto import GROK_MODEL, XAI_API_KEY_ENV
from utils.event_news_research import (
    _NON_POLITICS_SERIES_PREFIXES,
    event_news_series_ticker,
    snapshot_ask_cents,
)
from utils.logger import get_logger

log = get_logger("event_news_grok_edge")
_SPEND_LOCK = threading.Lock()

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
GROK_EDGE_TIMEOUT_SECONDS = 20.0
GROK_DAILY_USD_CAP = "GROK_DAILY_USD_CAP"
DEFAULT_DAILY_USD_CAP = Decimal("3")
DEFAULT_EDGE_MIN_CENTS = 8
EVENT_NEWS_GROK_EDGE_MIN_CENTS = "EVENT_NEWS_GROK_EDGE_MIN_CENTS"
EVENT_NEWS_GROK_EDGE_SCAN_MINUTES = "EVENT_NEWS_GROK_EDGE_SCAN_MINUTES"
DEFAULT_SCAN_MINUTES = 30
MIN_SCAN_MINUTES = 5
PAPER_QUEUE_CONTRACTS = 4
TAKER_FEE_COEFFICIENT = Decimal("0.07")
MIN_CALL_RESERVE_USD = Decimal("0.05")
RESERVE_P90_MULTIPLIER = Decimal("1.5")
COST_TICKS_PER_USD = Decimal("10000000000")
MEASURED_COST_WINDOW = 20
AGREE_ABS = Decimal("0.05")
STRONG_CONTRARY_CONFIDENCE = Decimal("0.70")
MAX_MARKETS_PER_CYCLE = 1
SCAN_TOP_N = 3
SCAN_CLOSE_WINDOW = timedelta(hours=72)
SCAN_ASK_MAX_CENTS = 94
SCREEN_EDGE_CENTS = 5
ASK_MOVE_CENTS = 5
GROUNDED_CACHE_TTL = timedelta(hours=3)
GROK_MAX_SEARCH_RESULTS = "GROK_MAX_SEARCH_RESULTS"
DEFAULT_MAX_SEARCH_RESULTS = 8
MAX_SEARCH_RESULTS_CAP = 20
# Responses API x_search handles, without the @. Truth Social itself is not
# an X handle; the Factbase fingerprint is the Truth Social change trigger.
TRUMP_X_HANDLES = ("realDonaldTrump", "POTUS", "WhiteHouse")
_TRUMP_X_SERIES_PREFIXES = (
    "KXTRUTHSOCIAL",
    "KXTRUMP",
    "KXMOCTRUMP",
    "KXAPRPOTUS",
    "KXPOLLPOTUS",
    "KXVOTESAVEAMERICA",
)
_SCAN_TOOLS = ("web_search", "x_search")
# Desk families plus the pinned politics matcher reserve. The research
# prewarm fallback (KXDJI and the other macro series) is not part of this list.
POLITICS_SCAN_SERIES = (
    "KXTRUTHSOCIAL",
    "KXCANUSDEAL",
    "KXMOCTRUMP25",
    "KXTRUMPACT",
    "KXTRUMPSAY",
    "KXTRUMPMENTION",
    "KXAPRPOTUS",
    "KXPOLLPOTUS",
    "KXEFFTARIFF",
    "KXVOTESAVEAMERICA",
    "KXSBUDGETRES",
    "KXLEAVECONGRESS",
    "KXFISAEXTEND",
)

CALL_LOG_PATH = Path("logs/state/grok_edge_calls.jsonl")
CANDIDATE_LOG_PATH = Path("logs/state/grok_edge_candidates.jsonl")
COMPARISON_LOG_PATH = Path("logs/state/grok_edge_comparisons.jsonl")
EXIT_LOG_PATH = Path("logs/state/grok_edge_exits.jsonl")
POSITION_SCORE_LOG_PATH = Path("logs/state/grok_edge_position_scores.jsonl")
SPEND_PATH = Path("logs/state/grok_edge_spend.json")
PAPER_DB_PATH = Path("logs/state/grok_paper.db")
RAW_DIR = Path("logs/state/grok_raw")
CACHE_PATH = Path("logs/state/grok_edge_cache.json")
SCREEN_LOG_PATH = Path("logs/state/grok_edge_screen.jsonl")
DAILY_REPORT_PATH = Path("docs/reports/grok-edge-daily.md")
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
_EXIT_SYSTEM = (
    "Use web search and X search. "
    "contrary is true only for strong evidence against the open side. "
    "prob is the probability the market resolves YES, from 0 to 1. "
    "confidence is from 0 to 1. sources is a list of http urls."
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


def taker_fee_cents(ask_cents: int, contracts: int) -> int | None:
    """Ceil of the general Kalshi taker fee, in cents, for this size.

    Fee dollars are ``0.07 * contracts * p * (1-p)`` with ``p`` the ask.
    The cent ceiling matches the exchange round-up. A bad ask or size
    returns None so the caller skips instead of inventing a fee.
    """
    if isinstance(ask_cents, bool) or not isinstance(ask_cents, int):
        return None
    if isinstance(contracts, bool) or not isinstance(contracts, int):
        return None
    if ask_cents < 1 or ask_cents > 99 or contracts < 1:
        return None
    price = Decimal(ask_cents) / Decimal(100)
    raw = TAKER_FEE_COEFFICIENT * Decimal(contracts) * price * (Decimal(1) - price)
    cents = (raw * Decimal(100)).to_integral_value(rounding=ROUND_CEILING)
    return int(cents)


def fee_net_edge_cents(probability: Decimal, ask_cents: int, contracts: int) -> int | None:
    """Floored per-contract cents of probability minus ask minus the fee."""
    if not isinstance(probability, Decimal) or not probability.is_finite():
        return None
    if probability < 0 or probability > 1:
        return None
    fee = taker_fee_cents(ask_cents, contracts)
    if fee is None:
        return None
    gross = (probability * Decimal(100) - Decimal(ask_cents)) * Decimal(contracts)
    gross -= Decimal(fee)
    per_contract = gross / Decimal(contracts)
    return int(per_contract.to_integral_value(rounding=ROUND_FLOOR))


def proposed_contracts_for_edge(edge_cents: int) -> int | None:
    """Disabled sizing proposal. Nothing in the order path calls this.

    8c-11c would be 4, 12c-19c would be 6, and 20c or more would be 8.
    The result never exceeds the 8-contract cap. Below 8c proposes nothing.
    """
    if isinstance(edge_cents, bool) or not isinstance(edge_cents, int):
        return None
    if edge_cents < DEFAULT_EDGE_MIN_CENTS:
        return None
    if edge_cents < 12:
        return 4
    if edge_cents < 20:
        return 6
    if HARD_MAX_CONTRACTS != 8:
        return None
    return HARD_MAX_CONTRACTS


def paper_queue_decision(
    *,
    probability: Decimal | None,
    ask_cents: int | None,
    side: str,
    contracts: int = PAPER_QUEUE_CONTRACTS,
    edge_min_cents: int = DEFAULT_EDGE_MIN_CENTS,
    yes_floor_cents: int = 55,
) -> tuple[str, int | None]:
    """Return ``(queue, edge)`` or ``(skip_reason, edge)``.

    YES needs the floor. Every side needs an ask above the 0-35c band.
    Size above 8 is a skip, not a clamp that still queues.
    """
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
    edge = fee_net_edge_cents(probability, ask_cents, contracts)
    if edge is None:
        return "bad_edge", None
    if edge < edge_min_cents:
        return "edge", edge
    return "queue", edge


def verdicts_agree(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= AGREE_ABS


def _non_politics_series(series: str, ticker: str = "") -> bool:
    for prefix in _NON_POLITICS_SERIES_PREFIXES:
        if series.startswith(prefix) or ticker.startswith(prefix):
            return True
    return False


def politics_scan_series(extra: Sequence[str] = ()) -> tuple[str, ...]:
    """Politics series the sidecar may scan.

    The desk families and the pinned matcher reserve are included. A series
    the live book is actually holding is included too. KXDJI and the other
    non-politics prefixes are dropped, including when they arrive from the
    research prewarm fallback.
    """
    seen: list[str] = []
    for raw in (*POLITICS_SCAN_SERIES, *extra):
        series = str(raw or "").strip().upper()
        if not series or series in seen or _non_politics_series(series):
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
) -> list[Any]:
    """Open politics markets that close within 72h and have an ask from 36c to 94c.

    Higher liquidity (then volume) ranks first. An earlier close breaks a tie.
    Response status ``open`` is not tradeable. KXDJI is not eligible.
    """
    allowed = {item.strip().upper() for item in (series or politics_scan_series()) if item.strip()}
    window_end = now + SCAN_CLOSE_WINDOW
    ranked: list[tuple[Decimal, datetime, str, Any]] = []
    for market in markets:
        ticker, market_series, status, close = _market_identity(market)
        if status != "active" or close is None:
            continue
        if market_series not in allowed or _non_politics_series(market_series, ticker):
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
    """Agree rate, plus which call was closer on rows that have an outcome."""
    usable = [row for row in rows if _pair(row) is not None]
    if not usable:
        return {
            "n": 0,
            "agree_pct": None,
            "settled_n": 0,
            "grounded_hits": None,
            "ungrounded_hits": None,
            "status": "not_enough_data",
        }
    agreements = 0
    settled = 0
    grounded_hits = 0
    ungrounded_hits = 0
    for row in usable:
        pair = _pair(row)
        assert pair is not None
        grounded, ungrounded = pair
        if verdicts_agree(grounded, ungrounded):
            agreements += 1
        outcome = _outcome(row.get("outcome"))
        if outcome is None:
            continue
        settled += 1
        grounded_error = abs(grounded - outcome)
        ungrounded_error = abs(ungrounded - outcome)
        if grounded_error < ungrounded_error:
            grounded_hits += 1
        elif ungrounded_error < grounded_error:
            ungrounded_hits += 1
        else:
            grounded_hits += 1
            ungrounded_hits += 1
    report: dict[str, Any] = {
        "n": len(usable),
        "agree_pct": (Decimal(agreements) / Decimal(len(usable)) * Decimal(100)),
        "settled_n": settled,
        "grounded_hits": None,
        "ungrounded_hits": None,
        "status": "ok" if settled else "not_enough_data",
    }
    if settled:
        report["grounded_hits"] = grounded_hits
        report["ungrounded_hits"] = ungrounded_hits
    return report


def brier_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Brier score and five probability buckets. Empty input is not enough."""
    scored: list[tuple[Decimal, Decimal]] = []
    for row in rows:
        probability = _decimal(row.get("probability"))
        outcome = _outcome(row.get("outcome"))
        if probability is None or outcome is None:
            continue
        scored.append((probability, outcome))
    if not scored:
        return {"status": "not_enough_data", "n": 0, "brier": None, "buckets": []}
    total = sum((probability - outcome) ** 2 for probability, outcome in scored)
    brier = total / Decimal(len(scored))
    buckets = []
    edges = (
        (Decimal("0"), Decimal("0.2"), "0.0-0.2"),
        (Decimal("0.2"), Decimal("0.4"), "0.2-0.4"),
        (Decimal("0.4"), Decimal("0.6"), "0.4-0.6"),
        (Decimal("0.6"), Decimal("0.8"), "0.6-0.8"),
        (Decimal("0.8"), Decimal("1.0000001"), "0.8-1.0"),
    )
    for low, high, name in edges:
        members = [
            (probability, outcome)
            for probability, outcome in scored
            if low <= probability < high
        ]
        if not members:
            buckets.append({"bucket": name, "n": 0, "mean_probability": None, "outcome_rate": None})
            continue
        count = Decimal(len(members))
        buckets.append(
            {
                "bucket": name,
                "n": len(members),
                "mean_probability": sum(item[0] for item in members) / count,
                "outcome_rate": sum(item[1] for item in members) / count,
            }
        )
    return {"status": "ok", "n": len(scored), "brier": brier, "buckets": buckets}


def ev_by_edge_bucket(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Mean fee-net dollars per trade and a normal 95% interval.

    A bucket with fewer than two settled pnl values has no interval.
    """
    groups = {
        "below_8": [],
        "8_11": [],
        "12_19": [],
        "20_plus": [],
    }
    for row in rows:
        edge = row.get("edge_cents")
        pnl = _decimal(row.get("pnl_dollars"))
        if isinstance(edge, bool) or not isinstance(edge, int) or pnl is None:
            continue
        if edge < 8:
            groups["below_8"].append(pnl)
        elif edge < 12:
            groups["8_11"].append(pnl)
        elif edge < 20:
            groups["12_19"].append(pnl)
        else:
            groups["20_plus"].append(pnl)
    if not any(groups.values()):
        return {"status": "not_enough_data", "buckets": []}
    buckets = []
    for name, values in groups.items():
        if len(values) < 2:
            buckets.append(
                {
                    "bucket": name,
                    "n": len(values),
                    "mean_ev": None,
                    "ci95_low": None,
                    "ci95_high": None,
                    "status": "not_enough_data",
                }
            )
            continue
        count = Decimal(len(values))
        mean = sum(values) / count
        variance = sum((value - mean) ** 2 for value in values) / (count - 1)
        std = variance.sqrt()
        half = Decimal("1.96") * std / count.sqrt()
        buckets.append(
            {
                "bucket": name,
                "n": len(values),
                "mean_ev": mean,
                "ci95_low": mean - half,
                "ci95_high": mean + half,
                "status": "ok",
            }
        )
    return {"status": "ok", "buckets": buckets}


def exit_versus_hold(
    *,
    side: str,
    bid_cents: int,
    settlement_yes: bool,
    contracts: int,
    entry_ask_cents: int,
) -> dict[str, int] | None:
    """Paper cents for selling at the bid now versus holding to settlement.

    Both numbers subtract the entry taker fee. The exit also pays the fee
    on the bid. This does not send an order.
    """
    if side not in {"yes", "no"}:
        return None
    if contracts < 1 or contracts > HARD_MAX_CONTRACTS:
        return None
    entry_fee = taker_fee_cents(entry_ask_cents, contracts)
    exit_fee = taker_fee_cents(bid_cents, contracts)
    if entry_fee is None or exit_fee is None:
        return None
    entry_cost = contracts * entry_ask_cents + entry_fee
    exit_proceeds = contracts * bid_cents - exit_fee
    yes_wins = 1 if settlement_yes else 0
    held_pays = yes_wins if side == "yes" else 1 - yes_wins
    hold_proceeds = contracts * 100 * held_pays
    return {
        "exit_cents": exit_proceeds - entry_cost,
        "hold_cents": hold_proceeds - entry_cost,
    }


def grok_max_search_results(environ: Mapping[str, str] | None = None) -> int:
    """Search budget for one grounded call. Blank or invalid env keeps the default."""
    env = os.environ if environ is None else environ
    raw = str(env.get(GROK_MAX_SEARCH_RESULTS) or "").strip()
    if not raw.isdigit():
        return DEFAULT_MAX_SEARCH_RESULTS
    value = int(raw)
    if value < 1:
        return DEFAULT_MAX_SEARCH_RESULTS
    return min(value, MAX_SEARCH_RESULTS_CAP)


def _search_turns(limit: int) -> int:
    """One tool round at 8 results. A 16-result budget gets one extra round."""
    return 2 if limit <= 8 else 3


def _trump_x_series(series: str) -> bool:
    token = str(series or "").upper()
    return any(token.startswith(prefix) for prefix in _TRUMP_X_SERIES_PREFIXES)


def _grounded_tool_payload(
    series: str,
    now: datetime | None,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """Web plus X search. The current Responses tools have no max_search_results.

    Live Search's max_search_results is not on web_search or x_search. The
    documented controls are max_turns, allowed_x_handles, and from_date /
    to_date. The prompt tells the model to set num_results and limit to
    ``limit``. to_date is exclusive, so the window runs through the UTC day.
    """
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
    """Newest Factbase Truth Social post in the last 72h, or None when unseen.

    A failure returns None so the caller keeps the 3h cache. ``none`` means
    the feed answered and the newest row is older than 72h.
    """
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
    if cached_at is None or now - cached_at >= GROUNDED_CACHE_TTL:
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
    return _decimal(row.get("prob")) is not None


def _screen_edges(
    probability: Decimal,
    yes_ask: int | None,
    no_ask: int | None,
) -> tuple[int | None, int | None]:
    yes_edge = (
        fee_net_edge_cents(probability, yes_ask, PAPER_QUEUE_CONTRACTS)
        if isinstance(yes_ask, int)
        else None
    )
    no_edge = (
        fee_net_edge_cents(Decimal(1) - probability, no_ask, PAPER_QUEUE_CONTRACTS)
        if isinstance(no_ask, int)
        else None
    )
    return yes_edge, no_edge


def _screen_passes(yes_edge: int | None, no_edge: int | None) -> bool:
    return (yes_edge is not None and yes_edge >= SCREEN_EDGE_CENTS) or (
        no_edge is not None and no_edge >= SCREEN_EDGE_CENTS
    )


def _log_screen(path: Path, record: Mapping[str, Any]) -> None:
    _append_jsonl(path, record)


def _series_prompt(views: Sequence[Mapping[str, Any]], limit: int) -> str:
    lines = [
        "These Kalshi markets share one series. Search once for the posts and news they share. "
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
) -> None:
    _cache_put(
        path,
        str(view["ticker"]),
        {
            "ts": _stamp(now),
            "prob": format(probability, "f"),
            "confidence": None if confidence is None else format(confidence, "f"),
            "sources": list(sources),
            "yes_ask": view.get("yes_ask"),
            "no_ask": view.get("no_ask"),
            "post_id": fingerprint or "",
            "series": view.get("series") or "",
            "summary": summary[:500],
        },
    )


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
    """One grounded call for every ticker in this series. None queues nothing."""
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
    )
    if result is None or result.skip_reason or not result.detail:
        return None
    try:
        parsed = json.loads(result.detail)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    sources = parsed.get("sources")
    markets = parsed.get("markets")
    if not isinstance(sources, list) or not sources or not isinstance(markets, list):
        return None
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
    """Queue at most ``max_markets`` paper candidates. Live and a failed Grok call queue none.

    The cheap ungrounded call runs first. A grounded series call runs only
    when that edge is at least 5c on either side, or the ticker is open.
    One grounded call covers every ticker in the series that still needs it.
    A fresh 3h cache is reused unless the ask moved by 5c or a new Truth
    Social post was seen. Ungrounded probabilities never queue.
    """
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
    edge_min = _edge_min(environ)
    moment = now or datetime.now(timezone.utc)
    fingerprint = latest_truth_social_fingerprint() if post_lookup is None else post_lookup()
    open_set = {str(ticker).strip() for ticker in open_tickers if str(ticker).strip()}
    pending: list[dict[str, Any]] = []
    for market in markets:
        view = _market_view(market)
        if view is None or view["series"] not in allowed or view["status"] != "active":
            continue
        counts["examined"] += 1
        cached = _cache_get(cache_path, view["ticker"])
        if _cache_fresh(cached, view, fingerprint, moment):
            counts["cache_hits"] += 1
            _log_screen(
                screen_log_path,
                {
                    "ts": _stamp(moment),
                    "ticker": view["ticker"],
                    "series": view["series"],
                    "decision": "cache_hit",
                    "reason": "cache_fresh",
                    "yes_ask": view.get("yes_ask"),
                    "no_ask": view.get("no_ask"),
                    "cached_prob": None if cached is None else cached.get("prob"),
                    "post_id": fingerprint,
                },
            )
            reused = _cached_call(view, cached or {})
            if reused is not None and _queue_best_side(
                view,
                reused,
                edge_min=edge_min,
                yes_floor=floor,
                candidate_log_path=candidate_log_path,
                db_path=db_path,
                now=moment,
            ):
                counts["queued"] += 1
            else:
                counts["skipped"] += 1
        else:
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
                yes_edge, no_edge = _screen_edges(probability, view["yes_ask"], view["no_ask"])
            passed = held or (probability is not None and _screen_passes(yes_edge, no_edge))
            reason = "open_position" if held else ("edge" if passed else "below_screen")
            if probability is None and not held:
                reason = "ungrounded_failed"
            _log_screen(
                screen_log_path,
                {
                    "ts": _stamp(moment),
                    "ticker": view["ticker"],
                    "series": view["series"],
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
                pending.append({"view": view, "ungrounded": ungrounded})
            else:
                counts["skipped"] += 1
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
        sources = () if parsed is None else tuple(parsed.get("sources") or ())
        summary = "" if parsed is None else str(parsed.get("summary") or "")
        for item in items:
            view = item["view"]
            row = by_ticker.get(view["ticker"])
            probability = None if row is None else _decimal(row.get("prob"))
            ungrounded = item["ungrounded"]
            if probability is None or not sources:
                counts["skipped"] += 1
                continue
            confidence = None if row is None else _decimal(row.get("confidence"))
            grounded = GrokEdgeCall(
                ticker=view["ticker"],
                grounded=True,
                probability=probability,
                confidence=confidence,
                verdict="YES" if probability >= Decimal("0.5") else "NO",
                sources=sources,
                tools=_SCAN_TOOLS,
                prompt_hash="series",
                latency_ms=0,
                cost_usd=Decimal("0"),
                skip_reason=None,
            )
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
                        "grounded_probability": str(probability),
                        "ungrounded_probability": str(ungrounded.probability),
                        "agree": verdicts_agree(probability, ungrounded.probability),
                        "outcome": None,
                    },
                )
            _remember_grounded(
                cache_path,
                view,
                probability,
                confidence,
                sources,
                fingerprint,
                summary,
                moment,
            )
            if _queue_best_side(
                view,
                grounded,
                edge_min=edge_min,
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
    """Ticker and side for open Kalshi rows. A zero or unreadable size is dropped."""
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
        rows.append({"ticker": ticker, "side": "no" if count < 0 else "yes"})
    return rows


def edge_scan_interval_seconds(environ: Mapping[str, str] | None = None) -> int:
    """Scan period in seconds. The floor is 5 minutes so a bad env cannot spin."""
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
    spend_path: Path = SPEND_PATH,
    raw_dir: Path = RAW_DIR,
    now: datetime | None = None,
) -> int:
    """Log a paper exit at the bid. Does not cancel or sell."""
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
        prompt = (
            f"Open {side} position on {ticker}. "
            "Is there strong contrary evidence from Trump posts, the schedule, "
            "speeches, or news?"
        )
        result = _call_grok(
            ticker=ticker,
            prompt=prompt,
            system=_EXIT_SYSTEM,
            grounded=True,
            environ=environ,
            post=post,
            call_log_path=call_log_path,
            spend_path=spend_path,
            raw_dir=raw_dir,
            now=now,
            kind="exit",
        )
        if result is None or result.skip_reason or not result.sources:
            continue
        if result.verdict != "CONTRARY":
            continue
        if result.confidence is None or result.confidence < STRONG_CONTRARY_CONFIDENCE:
            continue
        _append_jsonl(
            exit_log_path,
            {
                "ts": _stamp(now),
                "ticker": ticker,
                "side": side,
                "bid_cents": bid,
                "confidence": str(result.confidence),
                "sources": list(result.sources),
                "hold_to_settle_cents": None,
                "paper_only": True,
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
    edge_min: int,
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
        edge_min_cents=edge_min,
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
    """Score open positions. Does not order.

    Each position gets the cheap ungrounded call first. Open positions then
    share one grounded series call, unless a 3h cache is still fresh.
    ``take`` means the grounded probability would queue that side. ``veto``
    means the call returned and the side would not queue. A skipped call
    is ``not_scored`` and is not treated as a veto.
    """
    counts = {"scored": 0, "skipped": 0, "cache_hits": 0, "grounded_calls": 0}
    if live or not event_news_desk_flag_enabled(
        EVENT_NEWS_GROK_EDGE_SCAN,
        live=False,
        environ=environ,
    ):
        return {"scored": 0, "skipped": 0}
    floor = event_news_yes_floor_cents(environ)
    edge_min = _edge_min(environ)
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
        ask = view["yes_ask" if side == "yes" else "no_ask"]
        cached = _cache_get(cache_path, ticker)
        if _cache_fresh(cached, view, fingerprint, moment):
            counts["cache_hits"] += 1
            reused = _cached_call(view, cached or {})
            probability = None if reused is None else reused.probability
            sources = () if reused is None else reused.sources
            record = _score_record(
                ticker=ticker,
                side=side,
                ask=ask,
                probability=probability,
                sources=sources,
                skip_reason=None if probability is not None else "cache_empty",
                edge_min=edge_min,
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
            record = _score_record(
                ticker=item["ticker"],
                side=item["side"],
                ask=item["ask"],
                probability=probability,
                sources=sources,
                skip_reason=None if probability is not None and sources else "malformed",
                edge_min=edge_min,
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
) -> str:
    """Join settled market GETs to logged Grok probabilities and write the report.

    Empty or unsettled input says not enough data. This does not read or
    write orders.
    """
    candidates = _read_jsonl(candidate_log_path)
    comparisons = _read_jsonl(comparison_log_path)
    spend_line = _spend_line(spend_path)
    settlements = _settlements_for(
        [row.get("ticker") for row in (*candidates, *comparisons)],
        market_lookup,
    )
    joined = _joined_settled_rows(candidates, comparisons, settlements)
    text = _daily_report_text(joined, spend_line, now=now)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    return text


def _queue_best_side(
    view: Mapping[str, Any],
    call: GrokEdgeCall,
    *,
    edge_min: int,
    yes_floor: int,
    candidate_log_path: Path,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    assert call.probability is not None
    best: tuple[str, int, int] | None = None
    best_reason = "no_side"
    for side, ask in (("yes", view["yes_ask"]), ("no", view["no_ask"])):
        probability = call.probability if side == "yes" else Decimal(1) - call.probability
        reason, edge = paper_queue_decision(
            probability=probability,
            ask_cents=ask,
            side=side,
            contracts=PAPER_QUEUE_CONTRACTS,
            edge_min_cents=edge_min,
            yes_floor_cents=yes_floor,
        )
        if reason != "queue" or edge is None or ask is None:
            best_reason = reason
            continue
        if best is None or edge > best[1]:
            best = (side, edge, ask)
    record: dict[str, Any] = {
        "ts": _stamp(now),
        "ticker": view["ticker"],
        "grok_probability": str(call.probability),
        "yes_ask_cents": view.get("yes_ask"),
        "sources": list(call.sources),
        "contracts": PAPER_QUEUE_CONTRACTS,
        "sizing_proposal_enabled": False,
        "queued": False,
        "skip_reason": best_reason,
    }
    if best is not None:
        side, edge, ask = best
        record.update(
            {
                "side": side,
                "ask_cents": ask,
                "edge_cents": edge,
                "queued": True,
                "skip_reason": None,
                "sizing_proposal_contracts": proposed_contracts_for_edge(edge),
            }
        )
    _append_jsonl(candidate_log_path, record)
    _insert_paper_row(db_path, "paper_candidates", record)
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
    """Provider cost from cost_in_usd_ticks, else cost_in_usd. Tokens are logged either way."""
    if not isinstance(body, dict):
        return None, {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    meta: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        raw = usage.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            meta[key] = raw
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
    """Shared grounded context. One ticker may still use the flat prob schema."""
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
) -> GrokEdgeCall | None:
    env = os.environ if environ is None else environ
    tools = _SCAN_TOOLS if grounded else ()
    search_limit = grok_max_search_results(env) if grounded else 0
    tool_series = series or (ticker.split("-", 1)[0] if ticker else "")
    tool_payload, turns = (
        _grounded_tool_payload(tool_series, now, search_limit) if grounded else ([], 0)
    )
    system_text = system + (_search_limit_sentence(search_limit) if grounded else "")
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    cap = _daily_cap(env)

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
        }
        if search_limit:
            record["max_search_results"] = search_limit
            record["max_turns"] = turns
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
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
            _reconcile_spend(spend_path, reserved, actual, cap=cap, now=now)
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
    else:
        ticker = str(getattr(market, "ticker", "") or "").strip()
        series = event_news_series_ticker(market)
        status = str(getattr(market, "status", "") or "").strip().lower()
        yes_ask, no_ask = snapshot_ask_cents(market)
        title = str(getattr(market, "title", "") or "")
        rules = str(getattr(market, "rules_primary", "") or "")
        close = str(getattr(market, "close_time", "") or "")
    if not ticker:
        return None
    return {
        "ticker": ticker,
        "series": series,
        "status": status,
        "yes_ask": _int_or_none(yes_ask),
        "no_ask": _int_or_none(no_ask),
        "title": title[:300],
        "rules": rules[:500],
        "close_time": close[:40],
    }


def _market_prompt(view: Mapping[str, Any]) -> str:
    return (
        "Estimate the probability this Kalshi market resolves YES. "
        "Consider recent Trump posts, the public schedule, speeches, and news. "
        f"ticker={view['ticker']} title={view['title']} "
        f"rules={view['rules']} close={view['close_time']}"
    )


def _bid_cents(market: Any, side: str) -> int | None:
    if market is None:
        return None
    if isinstance(market, Mapping):
        raw = market.get("yes_bid" if side == "yes" else "no_bid")
    else:
        name = "yes_bid_cents" if side == "yes" else "no_bid_cents"
        alt = "yes_bid" if side == "yes" else "no_bid"
        raw = getattr(market, name, None)
        if raw is None:
            raw = getattr(market, alt, None)
    cents = _int_or_none(raw)
    if cents is None:
        return None
    if isinstance(raw, float) and raw <= 1:
        cents = int(round(raw * 100))
    if 1 <= cents <= 99:
        return cents
    return None


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
    """Use the last object that has the schema fields. Ignore a trailing tool payload.

    A series reply nests one object per ticker, and those rows also contain
    prob. The envelope is the last object whose markets value is a list.
    A one-ticker flat body has no markets list, so it stays on the prob object.
    """
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
    """Replace this call's reserve with the provider cost.

    A smaller actual cost is credited back. A larger one is debited until the
    cap, and the ledger is pinned when the extra does not fit.
    """
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


def _edge_min(environ: Mapping[str, str] | None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get(EVENT_NEWS_GROK_EDGE_MIN_CENTS) or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_EDGE_MIN_CENTS


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
                conn.execute(
                    """
                    INSERT INTO paper_candidates (
                        ts, ticker, side, ask_cents, yes_ask_cents, grok_probability,
                        edge_cents, sources_json, queued, skip_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    tickers: Sequence[Any],
    market_lookup: Callable[[str], Any],
) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in tickers:
        ticker = str(raw or "").strip()
        if not ticker or ticker in found:
            continue
        try:
            market = market_lookup(ticker)
        except Exception:
            continue
        result = _settlement_result(market)
        if result is not None:
            found[ticker] = result
    return found


def _joined_settled_rows(
    candidates: Sequence[Mapping[str, Any]],
    comparisons: Sequence[Mapping[str, Any]],
    settlements: Mapping[str, str],
) -> list[dict[str, Any]]:
    """One row per settled ticker that has a logged grounded probability."""
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
