"""Paper-only Grok edge scan, exit watch, and calibration.

Live stays off unless the flag is explicitly true, and even then this
module never submits, cancels, or reprices an order. A missing, late, or
malformed Grok answer queues nothing. The sizing buckets are a proposal
stored on the candidate. The queued contract count stays at 4.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
from utils.event_news_research import event_news_series_ticker, snapshot_ask_cents
from utils.logger import get_logger

log = get_logger("event_news_grok_edge")

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
GROUNDED_RESERVE_USD = Decimal("0.50")
UNGROUNDED_RESERVE_USD = Decimal("0.05")
AGREE_ABS = Decimal("0.05")
STRONG_CONTRARY_CONFIDENCE = Decimal("0.70")
MAX_MARKETS_PER_CYCLE = 1
_SCAN_TOOLS = ("web_search", "x_search")

CALL_LOG_PATH = Path("logs/state/grok_edge_calls.jsonl")
CANDIDATE_LOG_PATH = Path("logs/state/grok_edge_candidates.jsonl")
COMPARISON_LOG_PATH = Path("logs/state/grok_edge_comparisons.jsonl")
EXIT_LOG_PATH = Path("logs/state/grok_edge_exits.jsonl")
SPEND_PATH = Path("logs/state/grok_edge_spend.json")

_GROUNDED_SYSTEM = (
    "Use web search and X search. Reply with one JSON object only. "
    "Keys are probability, confidence, verdict, and sources. "
    "probability and confidence are numbers from 0 to 1. "
    "verdict is YES or NO. sources is a list of http urls. "
    "A probability without a source url is invalid."
)
_UNGROUNDED_SYSTEM = (
    "Do not browse. Reply with one JSON object only. "
    "Keys are probability, confidence, verdict, and sources. "
    "probability and confidence are numbers from 0 to 1. "
    "verdict is YES or NO. sources is an empty list."
)
_EXIT_SYSTEM = (
    "Use web search and X search. Reply with one JSON object only. "
    "Keys are contrary, confidence, probability, and sources. "
    "contrary is true only for strong evidence against the open side. "
    "confidence and probability are numbers from 0 to 1. "
    "sources is a list of http urls."
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
    now: datetime | None = None,
) -> dict[str, int]:
    """Queue at most one paper candidate. Live and a failed Grok call queue none."""
    counts = {"examined": 0, "queued": 0, "skipped": 0}
    if live or not event_news_desk_flag_enabled(
        EVENT_NEWS_GROK_EDGE_SCAN,
        live=False,
        environ=environ,
    ):
        return counts
    allowed = {str(series).strip().upper() for series in allowlist if str(series).strip()}
    floor = event_news_yes_floor_cents(environ)
    edge_min = _edge_min(environ)
    for market in markets:
        view = _market_view(market)
        if view is None or view["series"] not in allowed or view["status"] != "active":
            continue
        counts["examined"] += 1
        prompt = _market_prompt(view)
        ungrounded = _call_grok(
            ticker=view["ticker"],
            prompt=prompt,
            system=_UNGROUNDED_SYSTEM,
            grounded=False,
            environ=environ,
            post=post,
            call_log_path=call_log_path,
            spend_path=spend_path,
            now=now,
        )
        grounded = _call_grok(
            ticker=view["ticker"],
            prompt=prompt,
            system=_GROUNDED_SYSTEM,
            grounded=True,
            environ=environ,
            post=post,
            call_log_path=call_log_path,
            spend_path=spend_path,
            now=now,
        )
        if grounded is None or grounded.probability is None or grounded.skip_reason:
            counts["skipped"] += 1
            if counts["examined"] >= MAX_MARKETS_PER_CYCLE:
                break
            continue
        if (
            ungrounded is not None
            and ungrounded.probability is not None
            and ungrounded.skip_reason is None
        ):
            _append_jsonl(
                comparison_log_path,
                {
                    "ts": _stamp(now),
                    "ticker": view["ticker"],
                    "grounded_probability": str(grounded.probability),
                    "ungrounded_probability": str(ungrounded.probability),
                    "agree": verdicts_agree(grounded.probability, ungrounded.probability),
                    "outcome": None,
                },
            )
        queued = _queue_best_side(
            view,
            grounded,
            edge_min=edge_min,
            yes_floor=floor,
            candidate_log_path=candidate_log_path,
            now=now,
        )
        if queued:
            counts["queued"] += 1
        else:
            counts["skipped"] += 1
        if counts["examined"] >= MAX_MARKETS_PER_CYCLE:
            break
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


def _queue_best_side(
    view: Mapping[str, Any],
    call: GrokEdgeCall,
    *,
    edge_min: int,
    yes_floor: int,
    candidate_log_path: Path,
    now: datetime | None,
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
    return best is not None


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
) -> GrokEdgeCall | None:
    env = os.environ if environ is None else environ
    tools = _SCAN_TOOLS if grounded else ()
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    reserve = GROUNDED_RESERVE_USD if grounded else UNGROUNDED_RESERVE_USD
    started = time.monotonic()

    def _finish(skip: str | None, body: Any, cost: Decimal) -> GrokEdgeCall:
        latency_ms = int(round((time.monotonic() - started) * 1000))
        parsed = _parse_edge_body(body, grounded=grounded, kind=kind) if skip is None else None
        if skip is None and parsed is None:
            skip = "malformed"
        call = GrokEdgeCall(
            ticker=ticker,
            grounded=grounded,
            probability=None if parsed is None else parsed[0],
            confidence=None if parsed is None else parsed[1],
            verdict="" if parsed is None else parsed[2],
            sources=() if parsed is None else parsed[3],
            tools=tools,
            prompt_hash=prompt_hash,
            latency_ms=latency_ms,
            cost_usd=cost,
            skip_reason=skip,
        )
        _append_jsonl(
            call_log_path,
            {
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
                "latency_ms": latency_ms,
                "cost_usd": str(cost),
                "skip_reason": skip,
            },
        )
        if skip:
            log.info(
                "[GROK_EDGE] skip=%s ticker=%s grounded=%s latency_ms=%s",
                skip,
                ticker,
                grounded,
                latency_ms,
            )
        return call

    key = str(env.get(XAI_API_KEY_ENV) or "").strip()
    if not key:
        return _finish("missing_key", None, Decimal("0"))
    reserved = _reserve_spend(spend_path, reserve, cap=_daily_cap(env), now=now)
    if reserved is None:
        return _finish("spend_cap", None, Decimal("0"))
    payload: dict[str, Any] = {
        "model": GROK_MODEL,
        "temperature": 0,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    if grounded:
        payload["tools"] = [{"type": name} for name in _SCAN_TOOLS]
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
        return _finish("timeout", None, reserved)
    except Exception:
        return _finish("request_error", None, reserved)
    if getattr(response, "status_code", None) != 200:
        return _finish("http", None, reserved)
    try:
        body = response.json()
    except Exception:
        return _finish("malformed", None, reserved)
    extra = _reported_cost(body)
    cost = reserved
    if extra is not None and extra > reserved:
        added = _reserve_spend(spend_path, extra - reserved, cap=_daily_cap(env), now=now)
        if added is not None:
            cost = reserved + added
    return _finish(None, body, cost)


def _parse_edge_body(
    body: Any,
    *,
    grounded: bool,
    kind: str,
) -> tuple[Decimal, Decimal | None, str, tuple[str, ...]] | None:
    if not isinstance(body, dict):
        return None
    obj = _last_json_object(_response_text(body))
    if obj is None:
        return None
    sources = _http_sources(body, obj)
    if kind == "exit":
        if not isinstance(obj.get("contrary"), bool):
            return None
        confidence = _unit_decimal(obj.get("confidence"))
        probability = _unit_decimal(obj.get("probability"))
        if probability is None:
            probability = Decimal("0")
        if obj["contrary"] is True and (confidence is None or not sources):
            return None
        verdict = "CONTRARY" if obj["contrary"] else "OK"
        return probability, confidence, verdict, sources
    probability = _unit_decimal(obj.get("probability"))
    if probability is None:
        return None
    if grounded and not sources:
        return None
    confidence = _unit_decimal(obj.get("confidence"))
    verdict = str(obj.get("verdict") or "").strip().upper()
    if verdict not in {"YES", "NO"}:
        verdict = "YES" if probability >= Decimal("0.5") else "NO"
    return probability, confidence, verdict, sources


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
    output_text = body.get("output_text")
    if isinstance(output_text, str):
        return output_text
    chunks: list[str] = []
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
    return tuple(found)


def _last_json_object(content: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found = obj
    return found


def _reserve_spend(
    path: Path,
    amount: Decimal,
    *,
    cap: Decimal,
    now: datetime | None,
) -> Decimal | None:
    """Debit ``amount`` if the UTC day still has room. None means do not call."""
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
