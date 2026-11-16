"""Paper quotes for Kalshi's Liquidity Incentive Program. No orders.

This process reads public market data and writes logs/state/lip_paper/.
It does not sign requests, read credentials, or send anything but GET.

The reward formula is the one published by Kalshi, not an estimate:

- https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program
- https://help.kalshi.com/en/articles/16076644-liquidity-incentive-programs-where-to-find-them
- https://docs.kalshi.com/api-reference/incentive-programs/get-incentives
- https://docs.kalshi.com/api-reference/incentive-programs/get-incentive-schedules
- https://kalshi.com/incentives
- https://kalshi.com/regulatory/notices
- https://www.cftc.gov/filings/orgrules/rules10142633813.pdf

Help-center worked example: a 20% time-period score of a $100 reward with
8,000 of 10,000 snapshots qualifying pays 0.20 x 100 x 8,000/10,000 = $16.00.
A result below $1.00 is not paid. A result at or above $1.00 is rounded down
to the cent.

Fair value comes only from utils.countable_series_shadow. A series that is
not in that model is skipped. A short run scores only the seconds it saw.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    INITIAL_ORDER_FEE_ACCUMULATOR,
    FeeContext,
    FeeRole,
    fee_coefficient_for,
    fee_schedule_at,
    quote_fee,
)
from trading.venue import Venue
from utils.countable_series_shadow import COUNTABLE, record_countable_shadow

PUBLIC_HOST = "api.elections.kalshi.com"
OUTPUT_DIR = Path("logs/state/lip_paper")
DEFAULT_SERIES = ("KXTRUTHSOCIAL", "KXTRUMPACT")
HELP_CENTER_URL = "https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program"
CONTRACTS_MIN = 20
CONTRACTS_MAX = 50
BEHIND_MIN = 1
BEHIND_MAX = 2
CENT = Decimal("0.01")
DOLLAR = Decimal("1")
CENTI_CENTS_PER_DOLLAR = Decimal("10000")
TARGET_MIN = Decimal("100")
TARGET_MAX = Decimal("20000")
STATE_VERSION = 2

Opener = Callable[..., object]
Fetch = Callable[[str], str]


class PublicPathError(ValueError):
    """The URL is not one of the public reads this process is allowed to make."""


@dataclass(frozen=True)
class Incentive:
    program_id: str
    ticker: str
    series: str
    start: datetime
    end: datetime
    reward: Decimal
    target: Decimal
    discount: Decimal
    cap: Decimal | None = None


@dataclass(frozen=True)
class MarketView:
    ticker: str
    series: str
    status: str
    result: str
    price_level_structure: str
    rules_primary: str
    strike_type: str


@dataclass(frozen=True)
class PriceLevel:
    price_cents: int
    size: Decimal
    ours: Decimal


@dataclass(frozen=True)
class Book:
    yes: tuple[tuple[int, Decimal], ...]
    no: tuple[tuple[int, Decimal], ...]


@dataclass(frozen=True)
class TradePrint:
    trade_id: str
    ticker: str
    created: datetime
    yes_cents: int
    no_cents: int
    count: Decimal
    taker_book_side: str
    block: bool


@dataclass(frozen=True)
class SideScore:
    reference_cents: int
    our_share: Decimal
    our_size: Decimal


@dataclass(frozen=True)
class Snapshot:
    excluded: bool
    our_score: Decimal
    participant_score: Decimal
    yes_reference: int | None
    no_reference: int | None


@dataclass(frozen=True)
class Resting:
    ticker: str
    side: str
    price_cents: int
    remaining: Decimal
    queue_ahead: Decimal
    size_better: Decimal
    placed_at: datetime
    order_id: str
    fee_accumulator: Decimal
    program_id: str = ""


@dataclass(frozen=True)
class PaperFill:
    ticker: str
    side: str
    price_cents: int
    quantity: Decimal
    fee: Decimal
    filled_at: datetime


@dataclass(frozen=True)
class TrackedProgram:
    program_id: str
    ticker: str
    reward: Decimal
    cap: Decimal | None
    end: datetime


@dataclass(frozen=True)
class PaperState:
    quotes: tuple[Resting, ...]
    fills: tuple[PaperFill, ...]
    snapshots: tuple[tuple[str, str, Snapshot], ...]
    rewards: tuple[tuple[str, Decimal], ...]
    settled_pnl: Decimal
    fees: Decimal
    seen_trades: tuple[str, ...]
    tracked: tuple[TrackedProgram, ...] = ()
    trade_mark: tuple[tuple[str, str], ...] = ()
    trade_ids: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @staticmethod
    def empty() -> PaperState:
        return PaperState((), (), (), (), Decimal("0"), Decimal("0"), ())


def public_path_allowed(path: str) -> bool:
    """True only for public incentive, market, order-book, and trade reads."""
    if path in {"/trade-api/v2/incentive_programs", "/trade-api/v2/markets/trades"}:
        return True
    prefix = "/trade-api/v2/markets/"
    if not path.startswith(prefix):
        return False
    rest = path[len(prefix):]
    if not rest or "/" not in rest:
        return _ticker_ok(rest) and rest != "trades"
    ticker, leaf = rest.split("/", 1)
    return leaf == "orderbook" and _ticker_ok(ticker)


def _ticker_ok(ticker: str) -> bool:
    if not ticker or len(ticker) > 120:
        return False
    return all(char.isalnum() or char in "-_." for char in ticker)


def public_get(
    path: str,
    params: Mapping[str, str] | None = None,
    *,
    timeout: float = 20.0,
    opener: Opener | None = None,
) -> dict[str, object]:
    """GET one allowlisted public path. No body and no credential header."""
    if not public_path_allowed(path):
        raise PublicPathError(path)
    query = urllib.parse.urlencode(list((params or {}).items()))
    url = f"https://{PUBLIC_HOST}{path}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "kalshi-bot-lip-paper"},
    )
    open_fn = opener or _open_public
    try:
        with open_fn(request, timeout=timeout) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https" or final.hostname != PUBLIC_HOST:
                raise OSError("host")
            if not public_path_allowed(final.path):
                raise OSError("path")
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OSError(f"status {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        if isinstance(exc, OSError) and str(exc) in {"host", "path"}:
            raise
        raise OSError(type(exc).__name__) from None
    if not isinstance(payload, dict):
        raise ValueError("shape")
    return payload


def _open_public(request: urllib.request.Request, timeout: float = 20.0):
    opener = urllib.request.build_opener(_RefuseRedirect)
    return opener.open(request, timeout=timeout)


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise OSError("redirect")


def cents_from_dollars(raw: object) -> int:
    if not isinstance(raw, str):
        raise ValueError("price")
    cents = Decimal(raw) * 100
    if not cents.is_finite() or cents != cents.to_integral_value() or cents < 0 or cents > 100:
        raise ValueError("price")
    return int(cents)


def count_from_fp(raw: object) -> Decimal:
    if not isinstance(raw, str):
        raise ValueError("count")
    value = Decimal(raw)
    if not value.is_finite() or value < 0:
        raise ValueError("count")
    return value


def dollars_from_centi_cents(raw: object) -> Decimal:
    """Incentive money is an integer count of centi-cents. Do not round it yet."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("reward")
    if raw <= 0:
        raise ValueError("reward")
    return Decimal(raw) / CENTI_CENTS_PER_DOLLAR


def discount_from_bps(raw: object) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw > 10000:
        raise ValueError("discount")
    return Decimal(raw) / Decimal("10000")


def _when(raw: object) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise ValueError("time")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("time")
    return parsed.astimezone(timezone.utc)


def parse_incentive(row: Mapping[str, object]) -> Incentive:
    if row.get("incentive_type") != "liquidity":
        raise ValueError("type")
    target = count_from_fp(row.get("target_size_fp"))
    if target <= TARGET_MIN or target >= TARGET_MAX:
        raise ValueError("target")
    series = str(row.get("series_ticker") or "").strip().upper()
    ticker = str(row.get("market_ticker") or "").strip()
    if not series or not _ticker_ok(ticker):
        raise ValueError("ticker")
    start, end = _when(row.get("start_date")), _when(row.get("end_date"))
    if end <= start:
        raise ValueError("window")
    cap_raw = row.get("max_reward_per_account")
    cap = None if cap_raw is None else dollars_from_centi_cents(cap_raw)
    return Incentive(
        program_id=str(row.get("id") or ""),
        ticker=ticker,
        series=series,
        start=start,
        end=end,
        reward=dollars_from_centi_cents(row.get("period_reward")),
        target=target,
        discount=discount_from_bps(row.get("discount_factor_bps")),
        cap=cap,
    )


def liquidity_payout(
    time_period_score: Decimal,
    reward: Decimal,
    qualifying_snapshots: int,
    total_snapshots: int,
    cap: Decimal | None = None,
) -> Decimal:
    """Official payout. Below $1 pays nothing. Otherwise floor to the cent.

    The help-center example is liquidity_payout(0.20, 100, 8000, 10000) == 16.00.
    ``reward`` stays at centi-cent precision until this product is floored.
    A per-account cap, when the program publishes one, applies after that floor.
    """
    if not isinstance(qualifying_snapshots, int) or isinstance(qualifying_snapshots, bool):
        raise ValueError("snapshots")
    if not isinstance(total_snapshots, int) or isinstance(total_snapshots, bool):
        raise ValueError("snapshots")
    if total_snapshots <= 0 or qualifying_snapshots < 0 or qualifying_snapshots > total_snapshots:
        raise ValueError("snapshots")
    if time_period_score < 0 or reward < 0:
        raise ValueError("score")
    raw = time_period_score * reward * Decimal(qualifying_snapshots) / Decimal(total_snapshots)
    if raw < DOLLAR:
        return Decimal("0.00")
    floored = (raw * 100).to_integral_value(rounding=ROUND_FLOOR) / Decimal(100)
    if cap is not None:
        floored = min(floored, (cap * 100).to_integral_value(rounding=ROUND_FLOOR) / Decimal(100))
    if floored < DOLLAR:
        return Decimal("0.00")
    return floored.quantize(CENT)


def merge_levels(levels: Sequence[tuple[int, Decimal]]) -> tuple[tuple[int, Decimal], ...]:
    totals: dict[int, Decimal] = {}
    for price, size in levels:
        if price < 1 or price > 99 or size < 0:
            continue
        totals[price] = totals.get(price, Decimal(0)) + size
    return tuple(sorted(totals.items(), key=lambda item: item[0], reverse=True))


def with_our_bid(
    levels: Sequence[tuple[int, Decimal]],
    price_cents: int,
    contracts: int,
) -> tuple[PriceLevel, ...]:
    merged = merge_levels(levels)
    size = Decimal(contracts)
    found = False
    rows: list[PriceLevel] = []
    for price, resting in merged:
        if price == price_cents:
            rows.append(PriceLevel(price, resting + size, size))
            found = True
        else:
            rows.append(PriceLevel(price, resting, Decimal(0)))
    if not found and 1 <= price_cents <= 99:
        rows.append(PriceLevel(price_cents, size, size))
    return tuple(sorted(rows, key=lambda level: level.price_cents, reverse=True))


def qualifying_levels(
    levels: Sequence[PriceLevel],
    target: Decimal,
) -> tuple[tuple[PriceLevel, ...], int | None]:
    """Walk best bid down until cumulative size reaches the target.

    The reference price is the first level at which cumulative size reaches
    one fifth of the target. A side that never reaches the target qualifies
    nothing.
    """
    total = Decimal(0)
    fifth = target / Decimal(5)
    reference: int | None = None
    kept: list[PriceLevel] = []
    for level in sorted(levels, key=lambda item: item.price_cents, reverse=True):
        if level.size <= 0:
            continue
        total += level.size
        kept.append(level)
        if reference is None and total >= fifth:
            reference = level.price_cents
        if total >= target:
            return tuple(kept), reference
    return (), None


def score_side(levels: Sequence[PriceLevel], target: Decimal, discount: Decimal) -> SideScore | None:
    kept, reference = qualifying_levels(levels, target)
    if not kept or reference is None:
        return None
    raw_total = Decimal(0)
    our_raw = Decimal(0)
    our_size = Decimal(0)
    for level in kept:
        ticks = reference - level.price_cents
        power = 0 if ticks <= 0 else ticks
        multiplier = Decimal(1) if power == 0 else discount ** power
        raw = level.size * multiplier
        raw_total += raw
        our_raw += level.ours * multiplier
        our_size += level.ours
    if raw_total <= 0:
        return None
    return SideScore(reference_cents=reference, our_share=our_raw / raw_total, our_size=our_size)


def score_snapshot(
    yes: Sequence[PriceLevel],
    no: Sequence[PriceLevel],
    target: Decimal,
    discount: Decimal,
) -> Snapshot:
    yes_score = score_side(yes, target, discount)
    no_score = score_side(no, target, discount)
    if yes_score is None or no_score is None:
        return Snapshot(True, Decimal(0), Decimal(0), None, None)
    return Snapshot(
        excluded=False,
        our_score=yes_score.our_share + no_score.our_share,
        participant_score=Decimal(2),
        yes_reference=yes_score.reference_cents,
        no_reference=no_score.reference_cents,
    )


def sample_payout(snapshots: Sequence[Snapshot], reward: Decimal, cap: Decimal | None = None) -> Decimal:
    """Apply the published payout to this run's own snapshots.

    Each row is one observed second, not a stand-in for the unobserved
    seconds of the period. The exchange still scores every second.
    """
    if not snapshots:
        return Decimal("0.00")
    qualifying = [row for row in snapshots if not row.excluded]
    participant = sum((row.participant_score for row in qualifying), Decimal(0))
    score = Decimal(0) if participant == 0 else sum((row.our_score for row in qualifying), Decimal(0)) / participant
    return liquidity_payout(score, reward, len(qualifying), len(snapshots), cap)


def resting_prices(probability: Decimal, behind: int) -> tuple[int, int] | None:
    """YES and NO bids `behind` whole cents behind the count-model fair value."""
    if behind < BEHIND_MIN or behind > BEHIND_MAX:
        raise ValueError("behind")
    if not probability.is_finite() or probability <= 0 or probability >= 1:
        return None
    yes_bid = int((probability * 100).to_integral_value(rounding=ROUND_FLOOR)) - behind
    no_bid = int(((Decimal(1) - probability) * 100).to_integral_value(rounding=ROUND_FLOOR)) - behind
    if not 1 <= yes_bid <= 99 or not 1 <= no_bid <= 99:
        return None
    if yes_bid + no_bid >= 100:
        return None
    return yes_bid, no_bid


def queue_ahead(levels: Sequence[tuple[int, Decimal]], price_cents: int) -> tuple[Decimal, Decimal]:
    """Public size at our price, and public size at strictly better prices."""
    ahead = Decimal(0)
    better = Decimal(0)
    for price, size in merge_levels(levels):
        if price > price_cents:
            better += size
        elif price == price_cents:
            ahead += size
    return ahead, better


def hit_price(trade: TradePrint, side: str) -> int | None:
    """Price of a public print that hits this resting bid, in that bid's cents."""
    if trade.block or trade.count <= 0:
        return None
    if side == "yes" and trade.taker_book_side == "ask":
        return trade.yes_cents
    if side == "no" and trade.taker_book_side == "bid":
        return trade.no_cents
    return None


def consume(quote: Resting, trade_price: int, trade_count: Decimal) -> tuple[Resting, Decimal]:
    """Queue-adjusted fill. Size ahead of us trades before we do.

    A print strictly through our price also consumes better-priced size
    from the public book taken when the quote was recorded. A print at our
    price means those better bids are already gone, so later prints do not
    pay for them again.
    """
    better = quote.size_better
    ahead = quote.queue_ahead
    remaining = quote.remaining
    reaching = trade_count
    if trade_price > quote.price_cents:
        taken = min(better, reaching)
        return replace(quote, size_better=better - taken), Decimal(0)
    if trade_price == quote.price_cents:
        better = Decimal(0)
    elif trade_price < quote.price_cents:
        taken = min(better, reaching)
        better -= taken
        reaching -= taken
    taken_ahead = min(ahead, reaching)
    ahead -= taken_ahead
    reaching -= taken_ahead
    fill = min(remaining, reaching)
    return replace(quote, size_better=better, queue_ahead=ahead, remaining=remaining - fill), fill


def maker_fee(
    *,
    price_cents: int,
    quantity: Decimal,
    accumulator: Decimal,
    order_id: str,
    now: datetime,
) -> tuple[Decimal, Decimal]:
    """Maker fee from the pinned Kalshi schedule. The order id never leaves the process."""
    price = Decimal(price_cents) / Decimal(100)
    schedule = fee_schedule_at(venue=Venue.KALSHI, timestamp=now)
    quoted = quote_fee(
        FeeContext(
            schedule_id=schedule,
            role=FeeRole.MAKER,
            quantity=quantity,
            price=price,
            signed_revenue=-(quantity * price),
            order_id=order_id,
            accumulator=accumulator,
            multiplier=Decimal(1),
            coefficient=fee_coefficient_for(schedule, FeeRole.MAKER),
            account_precision=DIRECT_ACCOUNT_PRECISION,
            timestamp=now,
        )
    )
    return quoted.net_fee, quoted.next_accumulator


def apply_trades(
    quote: Resting,
    trades: Sequence[TradePrint],
    *,
    now: datetime,
) -> tuple[Resting, tuple[PaperFill, ...], Decimal, tuple[str, ...]]:
    """Fills one resting bid. Returned ids are prints inside this quote's window.

    Both sides of one ticker see the same prints. A print hits only one side.
    """
    current = quote
    fills: list[PaperFill] = []
    fee_total = Decimal(0)
    considered: list[str] = []
    ordered = sorted(trades, key=lambda trade: (trade.created, trade.trade_id))
    for trade in ordered:
        if trade.ticker != quote.ticker or not (quote.placed_at < trade.created <= now):
            continue
        considered.append(trade.trade_id)
        price = hit_price(trade, quote.side)
        if price is None or current.remaining <= 0:
            continue
        current, quantity = consume(current, price, trade.count)
        if quantity <= 0:
            continue
        fee, accumulator = maker_fee(
            price_cents=current.price_cents,
            quantity=quantity,
            accumulator=current.fee_accumulator,
            order_id=current.order_id,
            now=trade.created,
        )
        current = replace(current, fee_accumulator=accumulator)
        fills.append(
            PaperFill(
                ticker=quote.ticker,
                side=quote.side,
                price_cents=quote.price_cents,
                quantity=quantity,
                fee=fee,
                filled_at=trade.created,
            )
        )
        fee_total += fee
    return current, tuple(fills), fee_total, tuple(considered)


def settlement_pnl(fills: Sequence[PaperFill], result: str) -> Decimal:
    """Signed cash P&L of inventory marked to a public yes/no settlement. Fees stay separate."""
    if result not in {"yes", "no"}:
        raise ValueError("result")
    pnl = Decimal(0)
    for fill in fills:
        payoff = DOLLAR if fill.side == result else Decimal(0)
        pnl += fill.quantity * (payoff - Decimal(fill.price_cents) / Decimal(100))
    return pnl


def model_mark(fills: Sequence[PaperFill], probability: Decimal) -> Decimal:
    """Expected inventory P&L at the count-model probability. Not a settlement."""
    pnl = Decimal(0)
    for fill in fills:
        chance = probability if fill.side == "yes" else Decimal(1) - probability
        pnl += fill.quantity * (chance - Decimal(fill.price_cents) / Decimal(100))
    return pnl


def program_key(program: Incentive) -> str:
    if program.program_id:
        return program.program_id
    return f"{program.ticker}|{program.start.strftime('%Y-%m-%dT%H:%M:%SZ')}"


def crosses_book(book: Book, yes_bid: int, no_bid: int) -> bool:
    """A bid at or through the opposite touch would take, not rest."""
    best_yes = book.yes[0][0] if book.yes else None
    best_no = book.no[0][0] if book.no else None
    if best_no is not None and yes_bid + best_no >= 100:
        return True
    return best_yes is not None and no_bid + best_yes >= 100


def public_snapshot(book: Book, target: Decimal, discount: Decimal) -> Snapshot:
    """Score the public book with none of our size resting."""
    yes = tuple(PriceLevel(price, size, Decimal(0)) for price, size in book.yes)
    no = tuple(PriceLevel(price, size, Decimal(0)) for price, size in book.no)
    return score_snapshot(yes, no, target, discount)


def low_competition(snapshot: Snapshot, yes_levels: Sequence[PriceLevel], no_levels: Sequence[PriceLevel], target: Decimal) -> bool:
    """Our bid helps reach the target on a side, and both sides reach it.

    The incentive payload does not carry Kalshi's Low/Medium/High badge.
    A 20-50 contract quote that sits behind a book which already fills the
    target cannot score, so this process does not rest it.
    """
    if snapshot.excluded:
        return False
    yes_kept, _yes_ref = qualifying_levels(yes_levels, target)
    no_kept, _no_ref = qualifying_levels(no_levels, target)
    ours = sum((level.ours for level in yes_kept), Decimal(0)) + sum((level.ours for level in no_kept), Decimal(0))
    return ours > 0


def count_model_probabilities(
    markets: Sequence[Mapping[str, object]],
    *,
    now: datetime,
    path: Path,
    fetch: Fetch | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, tuple[Decimal | None, str | None]]:
    """Fair YES probabilities from the existing count model. No new fit."""
    found: dict[str, tuple[Decimal | None, str | None]] = {}
    countable: list[Mapping[str, object]] = []
    for market in markets:
        ticker = str(market.get("ticker") or "")
        series = str(market.get("series_ticker") or market.get("series") or "").strip().upper()
        if series not in COUNTABLE:
            found[ticker] = (None, "no_count_model")
            continue
        countable.append(market)
    if not countable:
        return found
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.stat().st_size if path.exists() else 0
    kwargs: dict[str, object] = {}
    if fetch is not None:
        kwargs["fetch"] = fetch
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    record_countable_shadow(countable, now=now, path=path, **kwargs)
    text = path.read_text(encoding="utf-8")
    fresh = text[before:] if before else text
    for line in fresh.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ticker = str(row.get("ticker") or "")
        reason = row.get("reason")
        raw = row.get("probability")
        if reason or not isinstance(raw, str):
            found[ticker] = (None, str(reason or "probability"))
            continue
        probability = Decimal(raw)
        if not probability.is_finite() or probability < 0 or probability > 1:
            found[ticker] = (None, "probability")
            continue
        found[ticker] = (probability, None)
    for market in countable:
        ticker = str(market.get("ticker") or "")
        found.setdefault(ticker, (None, "probability"))
    return found


@dataclass(frozen=True)
class CycleReport:
    lines: tuple[str, ...]
    state: PaperState


def run_cycle(
    *,
    now: datetime,
    programs: Sequence[Incentive],
    markets: Mapping[str, MarketView],
    books: Mapping[str, Book],
    trades: Mapping[str, Sequence[TradePrint]],
    probabilities: Mapping[str, tuple[Decimal | None, str | None]],
    state: PaperState,
    contracts: int,
    behind: int,
    max_markets: int,
    series: Sequence[str],
) -> CycleReport:
    if type(contracts) is not int or not CONTRACTS_MIN <= contracts <= CONTRACTS_MAX:
        raise ValueError("contracts")
    if type(behind) is not int or not BEHIND_MIN <= behind <= BEHIND_MAX:
        raise ValueError("behind")
    if type(max_markets) is not int or max_markets < 1:
        raise ValueError("max_markets")
    lines = [
        "LIP paper quoter. Public market data only. No orders.",
        f"formula {HELP_CENTER_URL}",
        "worked example 0.20 * 100 * 8000/10000 = 16.00",
        "count model " + ", ".join(
            f"{name}={'yes' if name in series else 'yes_not_in_this_run'}" for name in sorted(COUNTABLE)
        ),
        f"contracts {contracts} behind_ticks {behind}",
    ]
    active = [program for program in programs if program.start <= now < program.end and program.series in series]
    upcoming = [program for program in programs if program.start > now and program.series in series]
    by_ticker: dict[str, Incentive] = {}
    ambiguous: set[str] = set()
    for program in active:
        if program.ticker in by_ticker and by_ticker[program.ticker].program_id != program.program_id:
            ambiguous.add(program.ticker)
        by_ticker[program.ticker] = program
    for ticker in sorted(ambiguous):
        lines.append(f"skip {ticker} reason=overlapping_programs")
        by_ticker.pop(ticker, None)
    for program in upcoming:
        lines.append(
            f"skip {program.ticker} series={program.series} reason=window_not_started "
            f"start={program.start.strftime('%Y-%m-%dT%H:%M:%SZ')} reward={program.reward}"
        )

    tracked = {item.program_id: item for item in state.tracked}
    ends = {item.program_id: item.end for item in state.tracked}
    active_ids = set()
    for program in by_ticker.values():
        key = program_key(program)
        active_ids.add(key)
        tracked[key] = TrackedProgram(key, program.ticker, program.reward, program.cap, program.end)
        ends[key] = program.end
    marks = {ticker: _when(stamp) for ticker, stamp in state.trade_mark}
    boundaries = {ticker: set(ids) for ticker, ids in state.trade_ids}
    quotes = {(quote.ticker, quote.side): quote for quote in state.quotes}
    new_fills: list[PaperFill] = []
    fee_added = Decimal(0)
    touched: dict[str, list[TradePrint]] = {}
    for key, quote in list(quotes.items()):
        until = now
        if quote.program_id and quote.program_id not in active_ids:
            end = ends.get(quote.program_id)
            until = quote.placed_at if end is None else min(now, end)
        fresh = []
        for trade in trades.get(quote.ticker, ()):
            mark = marks.get(quote.ticker)
            if mark is not None and trade.created < mark:
                continue
            if mark is not None and trade.created == mark and trade.trade_id in boundaries.get(quote.ticker, set()):
                continue
            fresh.append(trade)
        updated, fills, fee, considered = apply_trades(quote, fresh, now=until)
        quotes[key] = updated
        considered_set = set(considered)
        touched.setdefault(quote.ticker, []).extend(trade for trade in fresh if trade.trade_id in considered_set)
        new_fills.extend(fills)
        fee_added += fee
        if fills:
            quantity = sum((fill.quantity for fill in fills), Decimal(0))
            lines.append(
                f"fill {quote.ticker} side={quote.side} qty={quantity} price_cents={quote.price_cents} fee={fee}"
            )
    for ticker, seen_trades in touched.items():
        if not seen_trades:
            continue
        latest = max(trade.created for trade in seen_trades)
        ids = {trade.trade_id for trade in seen_trades if trade.created == latest}
        previous = marks.get(ticker)
        if previous is not None and latest == previous:
            boundaries[ticker] = boundaries.get(ticker, set()) | ids
        elif previous is None or latest > previous:
            marks[ticker] = latest
            boundaries[ticker] = ids

    ranked: list[tuple[Decimal, str, Incentive, int, int, Snapshot, Snapshot]] = []
    observed: list[tuple[str, str, Snapshot]] = []
    for ticker in sorted(by_ticker):
        program = by_ticker[ticker]
        key = program_key(program)
        if program.series not in COUNTABLE:
            lines.append(f"skip {ticker} series={program.series} reason=no_count_model")
            continue
        market = markets.get(ticker)
        if market is None:
            lines.append(f"skip {ticker} reason=market_unread")
            continue
        if market.price_level_structure != "linear_cent":
            lines.append(f"skip {ticker} reason=tick_{market.price_level_structure or 'missing'}")
            continue
        if market.status != "active":
            observed.append((key, ticker, Snapshot(True, Decimal(0), Decimal(0), None, None)))
            lines.append(f"skip {ticker} reason=market_{market.status or 'unknown'}")
            continue
        book = books.get(ticker)
        if book is None:
            lines.append(f"skip {ticker} reason=book_unread")
            continue
        quiet = public_snapshot(book, program.target, program.discount)
        probability, reason = probabilities.get(ticker, (None, "probability"))
        if probability is None:
            observed.append((key, ticker, quiet))
            lines.append(f"skip {ticker} reason=count_model_{reason or 'missing'}")
            continue
        prices = resting_prices(probability, behind)
        if prices is None:
            observed.append((key, ticker, quiet))
            lines.append(f"skip {ticker} reason=quote_off_grid fair={probability}")
            continue
        yes_bid, no_bid = prices
        if crosses_book(book, yes_bid, no_bid):
            observed.append((key, ticker, quiet))
            lines.append(f"skip {ticker} reason=crosses fair={probability} yes_bid={yes_bid} no_bid={no_bid}")
            continue
        yes_levels = with_our_bid(book.yes, yes_bid, contracts)
        no_levels = with_our_bid(book.no, no_bid, contracts)
        snapshot = score_snapshot(yes_levels, no_levels, program.target, program.discount)
        if not low_competition(snapshot, yes_levels, no_levels, program.target):
            observed.append((key, ticker, quiet))
            lines.append(
                f"skip {ticker} reason=not_low_competition fair={probability} "
                f"yes_bid={yes_bid} no_bid={no_bid} target={program.target}"
            )
            continue
        ranked.append((snapshot.our_score, ticker, program, yes_bid, no_bid, snapshot, quiet))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen = ranked[:max_markets]
    chosen_tickers = {ticker for _score, ticker, _program, _yes, _no, _snap, _quiet in chosen}
    for _score, ticker, _program, _yes, _no, _snap, quiet in ranked[max_markets:]:
        observed.append((program_key(_program), ticker, quiet))
        lines.append(f"skip {ticker} reason=capped")

    # Fills above already used the queue from the previous pass. This pass joins the back.
    replaced: dict[tuple[str, str], Resting] = {}
    for our_score, ticker, program, yes_bid, no_bid, snapshot, _quiet in chosen:
        book = books[ticker]
        key = program_key(program)
        for side, price in (("yes", yes_bid), ("no", no_bid)):
            levels = book.yes if side == "yes" else book.no
            ahead, better = queue_ahead(levels, price)
            replaced[(ticker, side)] = Resting(
                ticker=ticker,
                side=side,
                price_cents=price,
                remaining=Decimal(contracts),
                queue_ahead=ahead,
                size_better=better,
                placed_at=now,
                order_id=f"lip-paper-{ticker}-{side}-{now.strftime('%Y%m%dT%H%M%SZ')}",
                fee_accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
                program_id=key,
            )
        observed.append((key, ticker, snapshot))
        probability = probabilities[ticker][0]
        lines.append(
            f"quote {ticker} fair={probability} yes_bid={yes_bid} no_bid={no_bid} "
            f"size={contracts} our_score={our_score.quantize(Decimal('0.000001'))} "
            f"yes_ref={snapshot.yes_reference} no_ref={snapshot.no_reference} "
            f"reward={program.reward} target={program.target} discount={program.discount}"
        )

    retained = [
        quote
        for quote in quotes.values()
        if quote.ticker not in chosen_tickers
        and quote.remaining > 0
        and quote.ticker in by_ticker
        and (quote.ticker not in markets or quote.ticker not in books)
    ]
    final_quotes = tuple(sorted((*replaced.values(), *retained), key=lambda quote: (quote.ticker, quote.side)))
    fills = state.fills + tuple(new_fills)
    settled = state.settled_pnl
    open_fills: list[PaperFill] = []
    by_market_result = {ticker: market.result for ticker, market in markets.items()}
    grouped: dict[str, list[PaperFill]] = {}
    for fill in fills:
        grouped.setdefault(fill.ticker, []).append(fill)
    for ticker, group in grouped.items():
        result = by_market_result.get(ticker, "")
        if result in {"yes", "no"}:
            settled += settlement_pnl(group, result)
            lines.append(f"settle {ticker} result={result} inventory_pnl={settlement_pnl(group, result)}")
        else:
            open_fills.extend(group)

    new_state = PaperState(
        quotes=final_quotes,
        fills=tuple(open_fills),
        snapshots=tuple(state.snapshots) + tuple(observed),
        rewards=(),
        settled_pnl=settled,
        fees=state.fees + fee_added,
        seen_trades=(),
        tracked=tuple(sorted(tracked.values(), key=lambda item: (item.ticker, item.program_id))),
        trade_mark=tuple(sorted((ticker, stamp.strftime("%Y-%m-%dT%H:%M:%SZ")) for ticker, stamp in marks.items())),
        trade_ids=tuple(sorted((ticker, tuple(sorted(ids))) for ticker, ids in boundaries.items())),
    )
    payouts = _payouts(new_state)
    reward_total = sum(payouts.values(), Decimal("0.00"))
    lines.append(
        f"totals lip_sample_payout={reward_total} settlement_pnl={new_state.settled_pnl} "
        f"fees={new_state.fees} unsettled_fills={len(new_state.fills)}"
    )
    lines.append(
        "sample_note payout uses only this run's snapshots. Kalshi scores every second; a short run is not the period payout."
    )
    if new_state.fills:
        lines.append("net pending_settlement = lip_sample_payout + settlement_pnl - fees")
        marked = Decimal(0)
        omitted = 0
        for fill in new_state.fills:
            probability = probabilities.get(fill.ticker, (None, None))[0]
            if probability is None:
                omitted += 1
                continue
            marked += model_mark((fill,), probability)
        lines.append(
            f"model_mark_pnl={marked.quantize(CENT)} omitted_fills={omitted} label=count_model_not_settlement"
        )
    else:
        net = reward_total + new_state.settled_pnl - new_state.fees
        lines.append(f"net {net} = lip_sample_payout + settlement_pnl - fees")
    snapshot_count: dict[str, int] = {}
    tickers: dict[str, str] = {}
    for program_id, ticker, _snapshot in new_state.snapshots:
        snapshot_count[program_id] = snapshot_count.get(program_id, 0) + 1
        tickers[program_id] = ticker
    for program_id, amount in sorted(payouts.items(), key=lambda item: tickers.get(item[0], item[0])):
        lines.append(
            f"payout {tickers.get(program_id, program_id)} sample={amount} "
            f"snapshots={snapshot_count.get(program_id, 0)}"
        )
    return CycleReport(tuple(lines), new_state)


def _payouts(state: PaperState) -> dict[str, Decimal]:
    known = {item.program_id: item for item in state.tracked}
    grouped: dict[str, list[Snapshot]] = {}
    for program_id, _ticker, snapshot in state.snapshots:
        grouped.setdefault(program_id, []).append(snapshot)
    found: dict[str, Decimal] = {}
    for program_id, rows in grouped.items():
        program = known.get(program_id)
        reward = Decimal(0) if program is None else program.reward
        cap = None if program is None else program.cap
        found[program_id] = sample_payout(rows, reward, cap)
    return found


def parse_market(payload: Mapping[str, object]) -> MarketView:
    market = payload.get("market") if isinstance(payload.get("market"), Mapping) else payload
    if not isinstance(market, Mapping):
        raise ValueError("market")
    ticker = str(market.get("ticker") or "")
    return MarketView(
        ticker=ticker,
        series=str(market.get("series_ticker") or ticker.split("-", 1)[0]).upper(),
        status=str(market.get("status") or ""),
        result=str(market.get("result") or "").lower(),
        price_level_structure=str(market.get("price_level_structure") or ""),
        rules_primary=str(market.get("rules_primary") or ""),
        strike_type=str(market.get("strike_type") or ""),
    )


def parse_book(payload: Mapping[str, object]) -> Book:
    raw = payload.get("orderbook_fp")
    if not isinstance(raw, Mapping):
        raise ValueError("book")

    def side(name: str) -> tuple[tuple[int, Decimal], ...]:
        rows = raw.get(name)
        if not isinstance(rows, list):
            raise ValueError("book")
        levels: list[tuple[int, Decimal]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise ValueError("book")
            levels.append((cents_from_dollars(row[0]), count_from_fp(row[1])))
        return merge_levels(levels)

    return Book(yes=side("yes_dollars"), no=side("no_dollars"))


def parse_trade(row: Mapping[str, object]) -> TradePrint | None:
    trade_id = str(row.get("trade_id") or "")
    ticker = str(row.get("ticker") or "")
    if not trade_id or not ticker:
        return None
    try:
        created = _when(row.get("created_time"))
        yes_cents = cents_from_dollars(row.get("yes_price_dollars"))
        no_cents = cents_from_dollars(row.get("no_price_dollars"))
        count = count_from_fp(row.get("count_fp"))
    except (ValueError, ArithmeticError):
        return None
    side = row.get("taker_book_side")
    if side not in {"bid", "ask"}:
        return None
    if not isinstance(row.get("is_block_trade"), bool):
        return None
    if not 1 <= yes_cents <= 99 or not 1 <= no_cents <= 99 or yes_cents + no_cents != 100:
        return None
    return TradePrint(trade_id, ticker, created, yes_cents, no_cents, count, str(side), bool(row.get("is_block_trade")))


def _dump(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if hasattr(value, "__dataclass_fields__"):
        return {item.name: _dump(getattr(value, item.name)) for item in fields(value)}
    raise TypeError(type(value).__name__)


def save_state(path: Path, state: PaperState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "version": STATE_VERSION,
        "quotes": [_dump(quote) for quote in state.quotes],
        "fills": [_dump(fill) for fill in state.fills],
        "snapshots": [
            [program_id, ticker, _dump(snapshot)] for program_id, ticker, snapshot in state.snapshots
        ],
        "rewards": [[ticker, format(reward, "f")] for ticker, reward in state.rewards],
        "settled_pnl": format(state.settled_pnl, "f"),
        "fees": format(state.fees, "f"),
        "seen_trades": list(state.seen_trades),
        "tracked": [_dump(item) for item in state.tracked],
        "trade_mark": [[ticker, stamp] for ticker, stamp in state.trade_mark],
        "trade_ids": [[ticker, list(ids)] for ticker, ids in state.trade_ids],
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _quote_from(row: Mapping[str, object]) -> Resting:
    return Resting(
        ticker=str(row["ticker"]),
        side=str(row["side"]),
        price_cents=int(row["price_cents"]),
        remaining=Decimal(str(row["remaining"])),
        queue_ahead=Decimal(str(row["queue_ahead"])),
        size_better=Decimal(str(row["size_better"])),
        placed_at=_when(row["placed_at"]),
        order_id=str(row["order_id"]),
        fee_accumulator=Decimal(str(row["fee_accumulator"])),
        program_id=str(row.get("program_id") or ""),
    )


def load_state(path: Path) -> PaperState:
    if not path.exists():
        return PaperState.empty()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        raise ValueError("state")
    quotes = tuple(_quote_from(row) for row in raw.get("quotes") or [])
    fills = tuple(
        PaperFill(
            ticker=str(row["ticker"]),
            side=str(row["side"]),
            price_cents=int(row["price_cents"]),
            quantity=Decimal(str(row["quantity"])),
            fee=Decimal(str(row["fee"])),
            filled_at=_when(row["filled_at"]),
        )
        for row in raw.get("fills") or []
    )
    snapshots = tuple(
        (
            str(program_id),
            str(ticker),
            Snapshot(
                excluded=bool(row["excluded"]),
                our_score=Decimal(str(row["our_score"])),
                participant_score=Decimal(str(row["participant_score"])),
                yes_reference=None if row.get("yes_reference") is None else int(row["yes_reference"]),
                no_reference=None if row.get("no_reference") is None else int(row["no_reference"]),
            ),
        )
        for program_id, ticker, row in raw.get("snapshots") or []
    )
    rewards = tuple((str(ticker), Decimal(str(reward))) for ticker, reward in raw.get("rewards") or [])
    seen = tuple(str(item) for item in raw.get("seen_trades") or [])
    tracked = tuple(
        TrackedProgram(
            program_id=str(row["program_id"]),
            ticker=str(row["ticker"]),
            reward=Decimal(str(row["reward"])),
            cap=None if row.get("cap") is None else Decimal(str(row["cap"])),
            end=_when(row["end"]),
        )
        for row in raw.get("tracked") or []
    )
    trade_mark = tuple((str(ticker), str(stamp)) for ticker, stamp in raw.get("trade_mark") or [])
    trade_ids = tuple((str(ticker), tuple(str(item) for item in ids)) for ticker, ids in raw.get("trade_ids") or [])
    return PaperState(
        quotes,
        fills,
        snapshots,
        rewards,
        Decimal(str(raw["settled_pnl"])),
        Decimal(str(raw["fees"])),
        seen,
        tracked,
        trade_mark,
        trade_ids,
    )


def _pages(get_json: Callable[[str, Mapping[str, str]], Mapping[str, object]], path: str, params: Mapping[str, str], key: str) -> list[object]:
    rows: list[object] = []
    cursor = ""
    for _page in range(20):
        query = dict(params)
        if cursor:
            query["cursor"] = cursor
        payload = get_json(path, query)
        batch = payload.get(key)
        if not isinstance(batch, list):
            raise ValueError(key)
        rows.extend(batch)
        cursor = str(payload.get("next_cursor") or payload.get("cursor") or "")
        if not cursor:
            return rows
    raise OSError("pages")


def fetch_programs(
    get_json: Callable[[str, Mapping[str, str]], Mapping[str, object]],
    series: Sequence[str],
) -> tuple[list[Incentive], list[str]]:
    found: list[Incentive] = []
    notes: list[str] = []
    for name in series:
        for status in ("active", "upcoming"):
            rows = _pages(
                get_json,
                "/trade-api/v2/incentive_programs",
                {"type": "liquidity", "status": status, "series_ticker": name, "limit": "100"},
                "incentive_programs",
            )
            for row in rows:
                if not isinstance(row, Mapping):
                    notes.append("skip incentive reason=shape")
                    continue
                try:
                    found.append(parse_incentive(row))
                except (ValueError, ArithmeticError) as exc:
                    ident = row.get("id") or row.get("market_ticker") or "?"
                    notes.append(f"skip incentive {ident} reason={exc}")
    return found, notes


def fetch_market(get_json: Callable[[str, Mapping[str, str]], Mapping[str, object]], ticker: str) -> MarketView:
    if not _ticker_ok(ticker):
        raise PublicPathError(ticker)
    return parse_market(get_json(f"/trade-api/v2/markets/{ticker}", {}))


def fetch_book(get_json: Callable[[str, Mapping[str, str]], Mapping[str, object]], ticker: str) -> Book:
    if not _ticker_ok(ticker):
        raise PublicPathError(ticker)
    return parse_book(get_json(f"/trade-api/v2/markets/{ticker}/orderbook", {"depth": "0"}))


def fetch_trades(
    get_json: Callable[[str, Mapping[str, str]], Mapping[str, object]],
    ticker: str,
    *,
    min_ts: int,
) -> tuple[list[TradePrint], int]:
    if not _ticker_ok(ticker):
        raise PublicPathError(ticker)
    rows = _pages(
        get_json,
        "/trade-api/v2/markets/trades",
        {"ticker": ticker, "limit": "1000", "min_ts": str(min_ts)},
        "trades",
    )
    trades: list[TradePrint] = []
    rejected = 0
    for row in rows:
        if not isinstance(row, Mapping):
            rejected += 1
            continue
        parsed = parse_trade(row)
        if parsed is None:
            rejected += 1
            continue
        trades.append(parsed)
    return trades, rejected


def shadow_market(market: MarketView) -> dict[str, object]:
    return {
        "ticker": market.ticker,
        "series_ticker": market.series,
        "rules_primary": market.rules_primary,
        "strike_type": market.strike_type,
    }


def execute(
    *,
    now: datetime | None = None,
    series: Sequence[str],
    contracts: int,
    behind: int,
    max_markets: int,
    output_dir: Path,
    get_json: Callable[[str, Mapping[str, str]], Mapping[str, object]],
    fetch: Fetch | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> CycleReport:
    for name in series:
        if name not in COUNTABLE:
            raise ValueError(f"no_count_model {name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    state = load_state(state_path)
    started = datetime.now(timezone.utc) if now is None else now
    programs, notes = fetch_programs(get_json, series)
    active = [program for program in programs if program.start <= started < program.end]
    markets: dict[str, MarketView] = {}
    books: dict[str, Book] = {}
    wanted = {program.ticker for program in active}
    wanted.update(fill.ticker for fill in state.fills)
    wanted.update(quote.ticker for quote in state.quotes)
    for ticker in sorted(wanted):
        try:
            markets[ticker] = fetch_market(get_json, ticker)
        except (OSError, ValueError, PublicPathError):
            continue
    priced = [
        shadow_market(market)
        for market in markets.values()
        if market.status == "active" and market.price_level_structure == "linear_cent"
    ]
    probabilities = count_model_probabilities(
        priced,
        now=started,
        path=output_dir / "count_model.jsonl",
        fetch=fetch,
        sleeper=sleeper,
    )
    for ticker, market in markets.items():
        if market.status == "active" and market.price_level_structure == "linear_cent":
            try:
                books[ticker] = fetch_book(get_json, ticker)
            except (OSError, ValueError, PublicPathError):
                continue
    trades: dict[str, list[TradePrint]] = {}
    for quote in state.quotes:
        if quote.ticker in trades:
            continue
        try:
            batch, rejected = fetch_trades(
                get_json,
                quote.ticker,
                min_ts=int(quote.placed_at.timestamp()),
            )
            trades[quote.ticker] = batch
            if rejected:
                notes.append(f"trades_ignored {quote.ticker} n={rejected}")
        except (OSError, ValueError, PublicPathError) as exc:
            raise OSError(f"trades {quote.ticker}") from exc
    # Stamp after the reads so a print already in the book is not applied again next pass.
    cycle_now = datetime.now(timezone.utc) if now is None else now
    report = run_cycle(
        now=cycle_now,
        programs=programs,
        markets=markets,
        books=books,
        trades=trades,
        probabilities=probabilities,
        state=state,
        contracts=contracts,
        behind=behind,
        max_markets=max_markets,
        series=series,
    )
    if notes:
        report = CycleReport(tuple(notes) + report.lines, report.state)
    save_state(state_path, report.state)
    with (output_dir / "cycles.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": cycle_now.strftime("%Y-%m-%dT%H:%M:%SZ"), "lines": list(report.lines)}) + "\n")
    return report


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper-quote Kalshi liquidity incentives. No orders.")
    parser.add_argument("--once", action="store_true", help="Run one pass and exit.")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between passes.")
    parser.add_argument("--contracts", type=int, default=CONTRACTS_MIN)
    parser.add_argument("--behind", type=int, default=BEHIND_MAX)
    parser.add_argument("--max-markets", type=int, default=10)
    parser.add_argument("--series", default=",".join(DEFAULT_SERIES))
    args = parser.parse_args(list(argv))
    if args.interval < 5:
        parser.error("interval must be at least 5 seconds")
    if type(args.contracts) is not int or not CONTRACTS_MIN <= args.contracts <= CONTRACTS_MAX:
        parser.error(f"contracts must be {CONTRACTS_MIN}-{CONTRACTS_MAX}")
    if type(args.behind) is not int or not BEHIND_MIN <= args.behind <= BEHIND_MAX:
        parser.error(f"behind must be {BEHIND_MIN} or {BEHIND_MAX}")
    if args.max_markets < 1:
        parser.error("max-markets must be at least 1")
    series = tuple(part.strip().upper() for part in str(args.series).split(",") if part.strip())
    if not series:
        parser.error("series is empty")
    unknown = [name for name in series if name not in COUNTABLE]
    if unknown:
        parser.error("no count model for " + ", ".join(unknown) + f"; model covers {', '.join(sorted(COUNTABLE))}")
    args.series_list = series
    return args


def main(argv: Sequence[str] | None = None) -> int:
    if os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("refusing to run while LIVE_TRADING_ENABLED is set", file=sys.stderr)
        return 2
    args = parse_args(sys.argv[1:] if argv is None else argv)
    passes = 1 if args.once else None
    done = 0
    while passes is None or done < passes:
        try:
            report = execute(
                series=args.series_list,
                contracts=args.contracts,
                behind=args.behind,
                max_markets=args.max_markets,
                output_dir=OUTPUT_DIR,
                get_json=public_get,
            )
        except (OSError, ValueError, PublicPathError) as exc:
            print(f"stop {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print("\n".join(report.lines))
        done += 1
        if passes is None:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
