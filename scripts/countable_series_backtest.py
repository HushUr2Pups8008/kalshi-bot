"""Backtest KXEOWEEK from the Federal Register and the existing count model.

Numbered executive orders are bucketed Sunday through Saturday. The run stops
when those buckets miss the 11 settled weeks. The probability is the shadow
negative-binomial fit on the 60 days before each Sunday, zeros included.
Candlestick asks are public reads. No Grok and no orders.
"""

from __future__ import annotations

import json
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from trading.fees import FeeRole, Venue, fee_coefficient_for, fee_schedule_at
from utils.countable_series_shadow import _fit, yes_probability
from utils.event_news_grok_edge import contract_fee_net_dollars
from utils.event_news_research import event_news_fee_net_edge

ET = ZoneInfo("America/New_York")
LOOKBACK_DAYS = 60
SETTLED = (
    (date(2026, 8, 15), 1),
    (date(2026, 8, 22), 0),
    (date(2026, 8, 29), 3),
    (date(2026, 9, 5), 2),
    (date(2026, 9, 12), 2),
    (date(2026, 9, 19), 4),
    (date(2026, 9, 26), 0),
    (date(2026, 10, 3), 3),
    (date(2026, 10, 10), 1),
    (date(2026, 10, 17), 1),
    (date(2026, 10, 24), 2),
)
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_HOSTS = frozenset({"www.federalregister.gov", "api.elections.kalshi.com"})
_FR = (
    "https://www.federalregister.gov/api/v1/documents.json"
    "?per_page=1000&order=oldest"
    "&conditions[type][]=PRESDOCU"
    "&conditions[presidential_document_type][]=executive_order"
    "&conditions[signing_date][gte]=2025-01-01"
    "&fields[]=executive_order_number&fields[]=signing_date"
)
_KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
Fetch = Callable[[str], dict]


class Stop(Exception):
    """Week totals or a finalized result disagreed. The table is not scored."""


def week_ending(day: date) -> date:
    return day + timedelta(days=(5 - day.weekday()) % 7)


def bucket(days: list[date]) -> dict[date, int]:
    counts: dict[date, int] = {}
    for day in days:
        label = week_ending(day)
        counts[label] = counts.get(label, 0) + 1
    return counts


def week_mismatches(counts: dict[date, int]) -> list[tuple[date, int, int]]:
    return [(day, expected, counts.get(day, 0)) for day, expected in SETTLED if counts.get(day, 0) != expected]


def week_span(counts: dict[date, int], start: date, end: date) -> list[int]:
    found: list[int] = []
    cursor = start
    while cursor <= end:
        found.append(counts.get(cursor, 0))
        cursor += timedelta(days=7)
    return found


def ticker_prefix(saturday: date) -> str:
    return f"KXEOWEEK-{saturday:%y}{_MONTHS[saturday.month - 1]}{saturday:%d}"


def snapshots(sunday: date) -> tuple[tuple[str, datetime], ...]:
    """Sunday 00:00 ET and Wednesday 12:00 ET. The week ends the next Sunday."""
    opened = datetime(sunday.year, sunday.month, sunday.day, tzinfo=ET)
    return (("sunday_open", opened), ("wednesday", opened + timedelta(days=3, hours=12)))


def days_left(sunday: date, moment: datetime) -> float:
    end = datetime(sunday.year, sunday.month, sunday.day, tzinfo=ET) + timedelta(days=7)
    return (end - moment.astimezone(ET)).total_seconds() / 86400


def known(days: list[date], sunday: date, as_of: datetime) -> int:
    """Signed orders already knowable. A same-day order counts at noon ET or later."""
    saturday = sunday + timedelta(days=6)
    moment = as_of.astimezone(ET)
    total = 0
    for day in days:
        if day < sunday or day > saturday:
            continue
        if day < moment.date() or (day == moment.date() and moment.hour >= 12):
            total += 1
    return total


def prior(days: list[date], sunday: date) -> tuple[float, float] | None:
    """Shadow fit. Sixty days before Sunday, zeros included. Not the 2025 sample."""
    posts = [datetime(day.year, day.month, day.day, 12, tzinfo=ET) for day in days]
    return _fit(posts, sunday, False, sunday - timedelta(days=LOOKBACK_DAYS))


def dollars_to_cents(raw: object) -> int | None:
    if not isinstance(raw, str):
        return None
    try:
        value = Decimal(raw)
    except Exception:
        return None
    if not value.is_finite() or value < 0 or value > 1:
        return None
    return int((value * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))


def book_cents(candle: dict, name: str) -> int | None:
    book = candle.get(name)
    if not isinstance(book, dict):
        return None
    return dollars_to_cents(book.get("close_dollars"))


def pick_candle(candles: list[dict], target: int) -> dict | None:
    """The hourly close at the snapshot. A later hour is not that print."""
    for candle in candles:
        if candle.get("end_period_ts") == target:
            return candle
    return None


def quote_edge(probability: float, ask_cents: int) -> tuple[Decimal, Decimal] | None:
    """Ex-ante fee-net. The taker fee is the scorecard quote and does not depend on probability."""
    if type(ask_cents) is not int or not 1 <= ask_cents <= 99:
        return None
    probe = event_news_fee_net_edge(Decimal("0.5"), ask_cents, 1)
    if probe is None:
        return None
    fee = probe[1]
    chance = Decimal(f"{probability:.6f}")
    if chance < 0 or chance > 1:
        return None
    price = Decimal(ask_cents) / Decimal(100)
    return chance - price - fee, fee


def side_ask(probability: float, yes_ask: int | None, yes_bid: int | None) -> tuple[str, int] | None:
    """YES pays the yes ask. NO pays 100 minus the yes bid, the no ask on that candle."""
    side = "yes" if probability >= 0.5 else "no"
    ask = yes_ask if side == "yes" else (None if yes_bid is None else 100 - yes_bid)
    if type(ask) is not int or not 1 <= ask <= 99:
        return None
    return side, ask


def _deduped(markets: list[dict]) -> list[dict]:
    chosen: dict[str, dict] = {}
    for market in markets:
        ticker = str(market.get("ticker") or "")
        if not ticker:
            continue
        current = chosen.get(ticker)
        if current is None or (market.get("result") and not current.get("result")):
            chosen[ticker] = market
    return list(chosen.values())


def score_markets(signing: list[date], markets: list[dict], candles_of: Fetch | Callable[..., list]) -> dict:
    """Score the 11 weeks. A count miss or a finalized result that disagrees raises Stop."""
    counts = bucket(signing)
    bad = week_mismatches(counts)
    if bad:
        raise Stop(bad)
    rows: dict[str, list[dict]] = {"sunday_open": [], "wednesday": []}
    fits: list[tuple[date, float, float]] = []
    disagreements: list[str] = []
    for saturday, _expected in SETTLED:
        sunday = saturday - timedelta(days=6)
        fitted = prior(signing, sunday)
        if fitted is None:
            raise Stop(f"short_history {saturday.isoformat()}")
        fits.append((saturday, fitted[0], fitted[1]))
        prefix = ticker_prefix(saturday)
        week_count = counts.get(saturday, 0)
        opened, wednesday = snapshots(sunday)
        span_start = int(opened[1].timestamp()) - 3600
        span_end = int(wednesday[1].timestamp()) + 6 * 3600
        for market in _deduped(markets):
            ticker = str(market.get("ticker") or "")
            if not ticker.startswith(prefix + "-"):
                continue
            if str(market.get("strike_type") or "") != "greater":
                raise Stop(f"strike_type {ticker}")
            floor = market.get("floor_strike")
            if isinstance(floor, bool) or not isinstance(floor, (int, float)):
                raise Stop(f"floor {ticker}")
            if isinstance(floor, float) and not floor.is_integer():
                raise Stop(f"floor {ticker}")
            strike = int(floor)
            outcome = 1 if week_count > strike else 0
            result = str(market.get("result") or "").lower()
            if result in {"yes", "no"} and (result == "yes") != bool(outcome):
                disagreements.append(ticker)
            candles = candles_of(ticker, span_start, span_end)
            for name, moment in (opened, wednesday):
                target = int(moment.timestamp())
                candle = pick_candle(candles, target)
                if candle is None:
                    rows[name].append({"ticker": ticker, "strike": strike, "missing": True})
                    continue
                scored = yes_probability(
                    known(signing, sunday, moment),
                    days_left(sunday, moment),
                    fitted[0],
                    fitted[1],
                    {"how": "gt", "strike": strike},
                )
                if scored is None:
                    rows[name].append({"ticker": ticker, "strike": strike, "missing": True})
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
                        "strike": strike,
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
    return {"fits": fits, "rows": rows}


def summarize(name: str, rows: list[dict]) -> list[str]:
    scored = [row for row in rows if not row.get("missing") and "p" in row]
    missing = sum(1 for row in rows if row.get("missing"))
    if not scored:
        return [f"snapshot {name} brier= n=0 missing={missing}"]
    n = len(scored)
    brier = sum((row["p"] - row["outcome"]) ** 2 for row in scored) / n
    lines = [f"snapshot {name} brier={brier:.6f} n={n} missing={missing}"]
    lines.append("strike n mean_p freq brier")
    grouped: dict[int, list[dict]] = {}
    for row in scored:
        grouped.setdefault(int(row["strike"]), []).append(row)
    for strike in sorted(grouped):
        items = grouped[strike]
        count = len(items)
        mean_p = sum(item["p"] for item in items) / count
        freq = sum(item["outcome"] for item in items) / count
        strike_brier = sum((item["p"] - item["outcome"]) ** 2 for item in items) / count
        lines.append(f"{strike} {count} {mean_p:.6f} {freq:.6f} {strike_brier:.6f}")
    priced = [row for row in scored if isinstance(row.get("edge"), Decimal) and isinstance(row.get("realized"), Decimal)]
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


def fetch_json(url: str) -> dict:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _HOSTS:
        raise ValueError("host")
    request = urllib.request.Request(url, headers={"User-Agent": "kalshi-bot-paper-backtest"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise OSError(f"status {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise OSError(type(exc).__name__) from None
    if not isinstance(payload, dict):
        raise ValueError("shape")
    return payload


def load_signing_dates(fetch: Fetch) -> list[date]:
    url = _FR
    found: list[date] = []
    seen = 0
    for _page in range(5):
        payload = fetch(url)
        results = payload.get("results")
        if not isinstance(results, list):
            raise ValueError("shape")
        for row in results:
            if not isinstance(row, dict):
                continue
            seen += 1
            number = row.get("executive_order_number")
            raw = row.get("signing_date")
            if not number or not isinstance(raw, str):
                continue
            found.append(date.fromisoformat(raw))
        nxt = payload.get("next_page_url")
        if not nxt:
            total = payload.get("count")
            if isinstance(total, int) and seen < total:
                raise ValueError("capped")
            return found
        url = str(nxt)
    raise ValueError("capped")


def load_markets(fetch: Fetch) -> list[dict]:
    cursor = ""
    found: list[dict] = []
    for _page in range(10):
        params = {"limit": "200", "series_ticker": "KXEOWEEK"}
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


def load_candles(fetch: Fetch, ticker: str, start: int, end: int) -> list[dict]:
    if not ticker.startswith("KXEOWEEK-"):
        raise ValueError("ticker")
    query = urllib.parse.urlencode({"start_ts": str(start), "end_ts": str(end), "period_interval": "60"})
    url = f"{_KALSHI}/series/KXEOWEEK/markets/{urllib.parse.quote(ticker)}/candlesticks?{query}"
    payload = fetch(url)
    candles = payload.get("candlesticks")
    if not isinstance(candles, list):
        raise ValueError("shape")
    return [row for row in candles if isinstance(row, dict)]


def _regime_line(label: str, values: list[int]) -> str:
    total = sum(values)
    mean = 0.0 if not values else total / len(values)
    nonzero = [item for item in values if item]
    nonzero_mean = 0.0 if not nonzero else sum(nonzero) / len(nonzero)
    return (
        f"regime {label} n={len(values)} sum={total} mean={mean:.6f} "
        f"nonzero_n={len(nonzero)} nonzero_mean={nonzero_mean:.6f}"
    )


def _print_regime(counts: dict[date, int]) -> None:
    spans = (
        ("2025_all", date(2025, 1, 4), date(2025, 12, 27)),
        ("2026_through_2026-10-24_all", date(2026, 1, 3), date(2026, 10, 24)),
    )
    for label, start, end in spans:
        print(_regime_line(label, week_span(counts, start, end)))
    print(
        "window lookback_days=60 before each Sunday, zeros included, "
        "countable_series_shadow._fit. The 2025 sample is not the fit."
    )
    print("regime_note 2025 weekly counts are much higher than 2026. The fit stays in the later window.")


def _candles_of(ticker: str, start: int, end: int) -> list[dict]:
    try:
        return load_candles(fetch_json, ticker, start, end)
    except OSError as exc:
        if str(exc) == "status 404":
            return []
        raise


def main() -> int:
    try:
        signing = load_signing_dates(fetch_json)
        counts = bucket(signing)
        bad = week_mismatches(counts)
        if bad:
            for day, expected, got in bad:
                print(f"week_mismatch {day.isoformat()} expected={expected} got={got}")
            return 2
        print("week_check ok 11")
        _print_regime(counts)
        schedule = fee_schedule_at(venue=Venue.KALSHI, timestamp=datetime.now(timezone.utc))
        coefficient = fee_coefficient_for(schedule, FeeRole.TAKER)
        print(f"taker_fee quadratic schedule={schedule.name} coefficient={coefficient}")
        print("ask_rule yes=yes_ask.close no=100-yes_bid.close one contract")
        scored = score_markets(signing, load_markets(fetch_json), _candles_of)
    except Stop as exc:
        print(f"stop {exc}")
        return 2
    except (OSError, ValueError) as exc:
        print(f"fetch_failed {type(exc).__name__} {exc}")
        return 1
    for saturday, mean, var in scored["fits"]:
        print(
            f"fit {saturday.isoformat()} daily_mean={mean:.6f} daily_var={var:.6f} "
            f"week_mean={mean * 7:.6f}"
        )
    for name in ("sunday_open", "wednesday"):
        for line in summarize(name, scored["rows"][name]):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
