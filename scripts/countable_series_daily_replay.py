"""Replay KXTRUTHSOCIALD on the existing daily count model.

The shipped model is yes_probability with days_left equal to the fraction of
the day still left. There is no separate hour-of-week model. Posts come from
the same free Factbase feed the shadow already uses. No Grok and no orders.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.countable_series_backtest import book_cents, pick_candle, quote_edge, side_ask
from trading.fees import FeeRole, Venue, fee_coefficient_for, fee_schedule_at
from utils.countable_series_shadow import _fit, _posts, parse_contract, yes_probability
from utils.event_news_grok_edge import contract_fee_net_dollars

ET = ZoneInfo("America/New_York")
LOOKBACK_DAYS = 60
_KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_HOSTS = frozenset({"api.elections.kalshi.com", "rollcall.com"})
FetchText = Callable[[str], str]
Candles = Callable[[str, int, int], list[dict]]


class Stop(Exception):
    """A settled result disagreed with the Factbase count. The table is not a decision."""


def _label(contract: dict) -> str:
    if contract["how"] == "between":
        return f"{contract['low']}-{contract['high']}"
    return f"{contract['how']}{contract['strike']}"


def _outcome(count: int, contract: dict) -> int:
    how = contract["how"]
    if how == "between":
        return 1 if int(contract["low"]) <= count <= int(contract["high"]) else 0
    if how == "gt":
        return 1 if count > int(contract["strike"]) else 0
    if how == "ge":
        return 1 if count >= int(contract["strike"]) else 0
    if how == "lt":
        return 1 if count < int(contract["strike"]) else 0
    raise Stop("how")


def _exposure(posts: list[datetime], day: date, moment: datetime) -> tuple[int, float]:
    start = datetime(day.year, day.month, day.day, tzinfo=ET)
    end = start + timedelta(days=1)
    local = moment.astimezone(ET)
    stop = min(local, end)
    so_far = sum(1 for item in posts if start <= item.astimezone(ET) < stop)
    if local >= end:
        left = 0.0
    elif local <= start:
        left = 1.0
    else:
        left = (end - local).total_seconds() / 86400
    return so_far, left


def _day_count(posts: list[datetime], day: date) -> int:
    start = datetime(day.year, day.month, day.day, tzinfo=ET)
    end = start + timedelta(days=1)
    return sum(1 for item in posts if start <= item.astimezone(ET) < end)


def _clocks(day: date) -> tuple[tuple[str, datetime], ...]:
    opened = datetime(day.year, day.month, day.day, tzinfo=ET)
    return (("day_open", opened), ("noon", opened.replace(hour=12)))


def group_markets(markets: list[dict]) -> tuple[dict[date, list[tuple[dict, dict]]], list[date]]:
    """A day is settled only when every market on it has a yes or no result."""
    grouped: dict[date, list[tuple[dict, dict]]] = {}
    for market in markets:
        ticker = str(market.get("ticker") or "")
        rules = str(market.get("rules_primary") or market.get("rules") or "")
        contract = parse_contract(rules, str(market.get("strike_type") or ""))
        if contract is None or contract["start"] != contract["end"]:
            snippet = " ".join(rules.split())[:160]
            raise Stop(f"rules {ticker} {snippet}")
        day = contract["start"]
        assert isinstance(day, date)
        grouped.setdefault(day, []).append((market, contract))
    settled: dict[date, list[tuple[dict, dict]]] = {}
    open_days: list[date] = []
    for day, items in grouped.items():
        results = [str(market.get("result") or "").lower() in {"yes", "no"} for market, _contract in items]
        if results and all(results):
            settled[day] = items
        else:
            open_days.append(day)
    return settled, open_days


def score_days(
    posts: list[datetime],
    markets: list[dict],
    candles_of: Candles,
    *,
    capped: bool,
) -> dict:
    """Score settled days at midnight and noon. A finalized mismatch raises Stop."""
    settled, open_days = group_markets(markets)
    rows: dict[str, list[dict]] = {"day_open": [], "noon": []}
    fits: list[tuple[date, float, float]] = []
    disagreements: list[str] = []
    for day in sorted(settled):
        fitted = _fit(posts, day, capped, day - timedelta(days=LOOKBACK_DAYS))
        if fitted is None:
            raise Stop(f"short_history {day.isoformat()}")
        fits.append((day, fitted[0], fitted[1]))
        count = _day_count(posts, day)
        opened, noon = _clocks(day)
        span_start = int(opened[1].timestamp()) - 3600
        span_end = int(noon[1].timestamp()) + 3600
        for market, contract in settled[day]:
            ticker = str(market.get("ticker") or "")
            outcome = _outcome(count, contract)
            result = str(market.get("result") or "").lower()
            if result in {"yes", "no"} and (result == "yes") != bool(outcome):
                disagreements.append(ticker)
            candles = candles_of(ticker, span_start, span_end)
            for name, moment in (opened, noon):
                candle = pick_candle(candles, int(moment.timestamp()))
                label = _label(contract)
                if candle is None:
                    rows[name].append({"ticker": ticker, "label": label, "missing": True})
                    continue
                so_far, left = _exposure(posts, day, moment)
                scored = yes_probability(so_far, left, fitted[0], fitted[1], contract)
                if scored is None:
                    rows[name].append({"ticker": ticker, "label": label, "missing": True})
                    continue
                probability = scored[1]
                yes_ask = book_cents(candle, "yes_ask")
                yes_bid = book_cents(candle, "yes_bid")
                chosen = side_ask(probability, yes_ask, yes_bid)
                edge = None
                realized = None
                if chosen is not None:
                    side_probability = probability if chosen[0] == "yes" else 1.0 - probability
                    quoted = quote_edge(side_probability, chosen[1])
                    if quoted is not None:
                        edge = quoted[0]
                        realized = contract_fee_net_dollars(chosen[0], chosen[1], outcome == 1)
                spread = None if yes_ask is None or yes_bid is None else yes_ask - yes_bid
                rows[name].append(
                    {
                        "ticker": ticker,
                        "label": label,
                        "p": probability,
                        "outcome": outcome,
                        "edge": edge,
                        "realized": realized,
                        "spread": spread,
                        "missing": False,
                    }
                )
    if disagreements:
        raise Stop("result_mismatch " + " ".join(sorted(disagreements)))
    return {"fits": fits, "rows": rows, "settled_days": sorted(settled), "open_days": sorted(open_days)}


def summarize(name: str, rows: list[dict]) -> list[str]:
    scored = [row for row in rows if not row.get("missing") and "p" in row]
    missing = sum(1 for row in rows if row.get("missing"))
    if not scored:
        return [f"snapshot {name} brier= n=0 missing={missing}"]
    n = len(scored)
    brier = sum((row["p"] - row["outcome"]) ** 2 for row in scored) / n
    lines = [f"snapshot {name} brier={brier:.6f} n={n} missing={missing}"]
    lines.append("bracket n mean_p freq brier")
    grouped: dict[str, list[dict]] = {}
    for row in scored:
        grouped.setdefault(str(row["label"]), []).append(row)
    for label in sorted(grouped):
        items = grouped[label]
        count = len(items)
        mean_p = sum(item["p"] for item in items) / count
        freq = sum(item["outcome"] for item in items) / count
        strike_brier = sum((item["p"] - item["outcome"]) ** 2 for item in items) / count
        lines.append(f"{label} {count} {mean_p:.6f} {freq:.6f} {strike_brier:.6f}")
    priced = [
        row for row in scored
        if isinstance(row.get("edge"), Decimal) and isinstance(row.get("realized"), Decimal)
    ]
    if priced:
        count = len(priced)
        ev = sum((row["edge"] for row in priced), Decimal(0))
        realized = sum((row["realized"] for row in priced), Decimal(0))
        lines.append(f"ev_fee_net sum={ev:.6f} mean={ev / count:.6f} n={count}")
        lines.append(f"realized_fee_net sum={realized:.6f} mean={realized / count:.6f} n={count}")
    spreads = [int(row["spread"]) for row in scored if isinstance(row.get("spread"), int)]
    if spreads:
        lines.append(f"spread_cents median={statistics.median(spreads):.1f} n={len(spreads)}")
    return lines


def decision_line(rows: list[dict]) -> str:
    """Skip the paper row only when the realized sum is negative and n is at least 15."""
    priced = [row for row in rows if isinstance(row.get("realized"), Decimal)]
    total = sum((row["realized"] for row in priced), Decimal(0))
    negative = bool(priced) and total < 0 and len(priced) >= 15
    return (
        f"paper_decision clock=day_open realized_sum={total:.6f} n={len(priced)} "
        f"clearly_negative={'yes' if negative else 'no'}"
    )


def _read(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _HOSTS:
        raise ValueError("host")
    request = urllib.request.Request(url, headers={"User-Agent": "kalshi-bot-paper-backtest"})
    delay = 5
    last = "status"
    for _attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if getattr(response, "status", 200) != 200:
                    raise OSError("status")
                return response.read()
        except urllib.error.HTTPError as exc:
            last = f"status {exc.code}"
            if exc.code not in {429, 503}:
                raise OSError(last) from None
            time.sleep(delay)
            delay = min(delay * 2, 45)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OSError(type(exc).__name__) from None
    raise OSError(last)


def fetch_json(url: str) -> dict:
    payload = json.loads(_read(url).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("shape")
    return payload


def fetch_text(url: str) -> str:
    return _read(url).decode("utf-8")


def load_markets(fetch: Callable[[str], dict]) -> list[dict]:
    cursor = ""
    found: list[dict] = []
    for _page in range(20):
        params = {"limit": "200", "series_ticker": "KXTRUTHSOCIALD"}
        if cursor:
            params["cursor"] = cursor
        payload = fetch(_KALSHI + "/markets?" + urllib.parse.urlencode(params))
        markets = payload.get("markets")
        if not isinstance(markets, list):
            raise ValueError("shape")
        found.extend(row for row in markets if isinstance(row, dict))
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            return found
    raise ValueError("capped")


def load_candles(fetch: Callable[[str], dict], ticker: str, start: int, end: int) -> list[dict]:
    if not ticker.startswith("KXTRUTHSOCIALD-"):
        raise ValueError("ticker")
    query = urllib.parse.urlencode({"start_ts": str(start), "end_ts": str(end), "period_interval": "60"})
    url = f"{_KALSHI}/series/KXTRUTHSOCIALD/markets/{urllib.parse.quote(ticker)}/candlesticks?{query}"
    try:
        payload = fetch(url)
    except OSError as exc:
        if str(exc) == "status 404":
            return []
        raise
    candles = payload.get("candlesticks")
    if not isinstance(candles, list):
        raise ValueError("shape")
    return [row for row in candles if isinstance(row, dict)]


def main() -> int:
    try:
        markets = load_markets(fetch_json)
        settled, _open = group_markets(markets)
        if not settled:
            print("settled_days n=0")
            return 2
        earliest = min(settled)
        posts, capped = _posts("truth", earliest - timedelta(days=LOOKBACK_DAYS), fetch_text, time.sleep)
        print(f"feed factbase capped={'yes' if capped else 'no'} posts={len(posts)}")
        print(
            "window lookback_days=60 before each day, zeros included, "
            "countable_series_shadow._fit"
        )
        print("model days_left=hours_remaining/24. No separate hour-of-week model.")
        schedule = fee_schedule_at(venue=Venue.KALSHI, timestamp=datetime.now(timezone.utc))
        coefficient = fee_coefficient_for(schedule, FeeRole.TAKER)
        print(f"taker_fee quadratic schedule={schedule.name} coefficient={coefficient}")
        print("ask_rule yes=yes_ask.close no=100-yes_bid.close one contract")

        def candles_of(ticker: str, start: int, end: int) -> list[dict]:
            time.sleep(0.15)
            return load_candles(fetch_json, ticker, start, end)

        scored = score_days(posts, markets, candles_of, capped=capped)
    except Stop as exc:
        print(f"stop {exc}")
        return 2
    except (OSError, ValueError) as exc:
        print(f"fetch_failed {type(exc).__name__} {exc}")
        return 1
    days = scored["settled_days"]
    print(
        f"settled_days n={len(days)} first={days[0].isoformat()} last={days[-1].isoformat()}"
    )
    open_days = scored["open_days"]
    if open_days:
        print("open_days " + " ".join(day.isoformat() for day in open_days))
    for day, mean, var in scored["fits"]:
        print(f"fit {day.isoformat()} daily_mean={mean:.6f} daily_var={var:.6f}")
    for name in ("day_open", "noon"):
        for line in summarize(name, scored["rows"][name]):
            print(line)
    print(decision_line(scored["rows"]["day_open"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
