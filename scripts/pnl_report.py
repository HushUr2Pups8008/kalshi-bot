"""Daily fee-net P&L from Kalshi fills and settlements.

Writes logs/reports/pnl-YYYYMMDD.md. Fills that share a ticker, side,
price, result, and order id are one display row. The row lists the series,
settled result, hold time, and fee-net P&L. Bucket means and totals under
15 are withheld and still count one sample per fill. Order ids are never
printed.

The fill log and the paper book are reconcile targets. Paper rows stay
out of the live total. A failed exchange read omits the running total.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

COHORT_START = datetime(2026, 8, 20, tzinfo=timezone.utc)
FEE_TOLERANCE = Decimal("0.0001")
COUNT_TOLERANCE = Decimal("0.0001")
MAX_PAGES = 50
PAPER_DB_RELATIVE = Path("data/paper_cohorts/kalshi-event-news-20260820/paper_trades.db")
FILL_LOG_RELATIVE = Path("logs/state/live-fill-log.md")


@dataclass(frozen=True)
class LiveFill:
    ticker: str
    side: str
    action: str
    qty: Decimal | None
    price: Decimal | None
    fee: Decimal | None
    order_id: str | None
    when: datetime | None


@dataclass(frozen=True)
class Settlement:
    ticker: str
    result: str
    yes_count: Decimal | None
    no_count: Decimal | None
    yes_cost: Decimal | None
    no_cost: Decimal | None
    revenue_cents: int | None
    fee: Decimal | None
    settled_at: datetime | None


@dataclass
class LogRow:
    index: int
    ending: str
    fields: list[str]
    ticker: str
    side: str
    qty: Decimal | None
    price_cents: int | None
    http_ok: bool
    fee_cents: str | None
    order_blank: bool


def money(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _dec(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str) and value.strip():
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None


def _time(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _cents(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            return None
        if number == number.to_integral_value():
            return int(number)
    return None


def series_of(ticker: str) -> str:
    head, _sep, _tail = ticker.partition("-")
    return head or "unknown"


def price_cents_of(price: Decimal) -> int | None:
    cents = price * Decimal("100")
    if abs(cents - cents.to_integral_value()) > Decimal("0.001"):
        return None
    return int(cents.to_integral_value())


_PRICE_BANDS = (
    (0, 20, "0.00-0.20"),
    (20, 40, "0.20-0.40"),
    (40, 60, "0.40-0.60"),
    (60, 80, "0.60-0.80"),
    (80, 101, "0.80-1.00"),
)
_EDGE_BANDS = ("below_8", "8_11", "12_19", "20_plus", "not stored")
_BOOK_PAIRS = (
    ("bid_cents", "ask_cents"),
    ("bid", "ask"),
    ("yes_bid", "yes_ask"),
    ("no_bid", "no_ask"),
)


def _shown(values: list[Decimal], *, allow_total: bool) -> str:
    """Under 15, withhold the fee-net total. A missing book never totals."""
    count = len(values)
    if count < 15:
        return f"not enough data (n={count})"
    if not allow_total:
        return f"n={count}"
    return f"n={count} fee_net={money(sum(values, Decimal(0)))}"


def _edge_band(side: str, cents: int, probability: Decimal | None) -> str:
    if probability is None:
        return "not stored"
    price = Decimal(cents) / Decimal(100)
    edge = probability - price if side == "yes" else (Decimal(1) - probability) - price
    rounded = int((edge * Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if rounded < 8:
        return "below_8"
    if rounded < 12:
        return "8_11"
    if rounded < 20:
        return "12_19"
    return "20_plus"


def _spread_cents(record: dict) -> int | None:
    sources = [record]
    meta = record.get("signal_meta")
    if isinstance(meta, dict):
        sources.append(meta)
    for source in sources:
        for bid_name, ask_name in _BOOK_PAIRS:
            bid, ask = source.get(bid_name), source.get(ask_name)
            if isinstance(bid, bool) or isinstance(ask, bool):
                continue
            if isinstance(bid, int) and isinstance(ask, int) and ask >= bid:
                return ask - bid
    return None


def live_order_entry_facts(
    root: Path,
) -> tuple[dict[tuple[str, str, int], Decimal | None], dict[tuple[str, str, int], int | None]]:
    """YES probability and spread at the logged order price. Conflicts stay unset."""
    from utils.trade_log_reader import iter_trade_records

    probabilities: dict[tuple[str, str, int], set[Decimal]] = defaultdict(set)
    spreads: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    if not root.is_dir():
        return {}, {}
    for record in iter_trade_records(root, event_types={"LIVE_ORDER"}):
        ticker = str(record.get("ticker") or "").strip()
        side = str(record.get("side") or "").strip().lower()
        price = record.get("price_cents")
        if not ticker or side not in {"yes", "no"} or isinstance(price, bool) or not isinstance(price, int):
            continue
        key = (ticker, side, price)
        raw = record.get("model_probability")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            probabilities[key].add(Decimal(str(raw)))
        spread = _spread_cents(record)
        if spread is not None:
            spreads[key].add(spread)
    yes = {
        key: (next(iter(values)) if len(values) == 1 else None)
        for key, values in probabilities.items()
    }
    book = {
        key: (next(iter(values)) if len(values) == 1 else None)
        for key, values in spreads.items()
    }
    return yes, book


def entry_quality_lines(
    samples: list[tuple[str, str, int, Decimal]],
    yes_probability: dict[tuple[str, str, int], Decimal | None],
    spread_cents: dict[tuple[str, str, int], int | None],
) -> list[str]:
    """Fee-net of settled fills by entry price, edge, and spread. No exit rule."""
    prices: dict[str, list[Decimal]] = {name: [] for _low, _high, name in _PRICE_BANDS}
    edges: dict[str, list[Decimal]] = {name: [] for name in _EDGE_BANDS}
    logged: dict[str, list[Decimal]] = defaultdict(list)
    missing_book: list[Decimal] = []
    for ticker, side, cents, fee_net in samples:
        band = "outside"
        for low, high, name in _PRICE_BANDS:
            if low <= cents < high:
                band = name
                break
        prices.setdefault(band, []).append(fee_net)
        probability = yes_probability.get((ticker, side, cents))
        edges[_edge_band(side, cents, probability)].append(fee_net)
        spread = spread_cents.get((ticker, side, cents))
        if spread is None:
            missing_book.append(fee_net)
        else:
            logged[f"{spread}c"].append(fee_net)
    lines = ["", "## Entry price", ""]
    for _low, _high, name in _PRICE_BANDS:
        lines.append(f"{name}: {_shown(prices[name], allow_total=True)}")
    if prices.get("outside"):
        lines.append(f"outside: {_shown(prices['outside'], allow_total=True)}")
    lines.extend(["", "## Entry edge", ""])
    for name in _EDGE_BANDS:
        lines.append(f"{name}: {_shown(edges[name], allow_total=True)}")
    lines.extend(["", "## Entry spread", ""])
    if not logged:
        lines.append(f"not logged: {_shown(missing_book, allow_total=False)}")
    else:
        for name in sorted(logged):
            lines.append(f"{name}: {_shown(logged[name], allow_total=True)}")
        if missing_book:
            lines.append(f"not logged: {_shown(missing_book, allow_total=False)}")
    return lines


def _hold(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None or end < start:
        return "unknown"
    return money(Decimal(str(round((end - start).total_seconds() / 3600, 2)))) + "h"


def _bucket_lines(samples: list[tuple[str, str, Decimal]]) -> list[str]:
    """Settled fee-net by series, side, and overall. One sample per fill."""
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    sides: dict[str, list[Decimal]] = {"yes": [], "no": []}
    for series, side, pnl in samples:
        grouped[series].append(pnl)
        if side in sides:
            sides[side].append(pnl)
    lines = [
        "", "## Buckets", "", "Mean and total are withheld under 15.", "",
        "| bucket | n | mean | fee_net |", "| --- | --- | --- | --- |",
    ]

    def add(label: str, values: list[Decimal]) -> None:
        count = len(values)
        if count < 15:
            shown = f"not enough data (n={count})"
            mean, total = shown, shown
        else:
            total_value = sum(values, Decimal(0))
            mean, total = money(total_value / Decimal(count)), money(total_value)
        lines.append(f"| {label} | {count} | {mean} | {total} |")

    for name in sorted(grouped):
        add(name, grouped[name])
    add("yes", sides["yes"])
    add("no", sides["no"])
    add("overall", [pnl for _series, _side, pnl in samples])
    return lines


def reconciliation_lines(
    *,
    ledger: Decimal,
    open_exposure: Decimal,
    open_fees: Decimal,
    cash: Decimal | None,
    portfolio: Decimal | None,
    starting: Decimal | None,
) -> list[str]:
    """cash + portfolio_mark - applied_net_funding = settled_fee_net + open adjustment.

    open adjustment is open_mark - open_cost - open_entry_fees. open_mark is
    the portfolio mark. The residual is the left side minus the right side.
    """
    lines = [
        "", "## Reconciliation", "",
        "identity: cash + portfolio_mark - applied_net_funding = "
        "settled_fee_net + (open_mark - open_cost - open_entry_fees)",
        f"settled_fee_net: {money(ledger)}",
        f"open_cost: {money(open_exposure)}",
        f"open_entry_fees: {money(open_fees)}",
    ]
    if cash is None or portfolio is None or starting is None:
        missing = []
        if cash is None or portfolio is None:
            missing.append("cash or portfolio value")
        if starting is None:
            missing.append("starting balance")
        lines.extend([
            "residual: unreadable",
            "reconciliation_gap: unreadable",
            "reason: " + " and ".join(missing) + " unread",
        ])
        return lines
    adjustment = portfolio - open_exposure - open_fees
    left = cash + portfolio - starting
    right = ledger + adjustment
    residual = left - right
    lines.extend([
        f"cash: {money(cash)}",
        f"portfolio_mark: {money(portfolio)}",
        f"applied_net_funding: {money(starting)}",
        "applied_net_funding is applied deposits net of their fees, minus applied withdrawals and their fees.",
        f"reconciliation_left: {money(left)}",
        f"open_mark: {money(portfolio)}",
        f"open_mark_minus_cost_and_fees: {money(adjustment)}",
        f"reconciliation_right: {money(right)}",
        f"residual: {money(residual)}",
        "closes_within_25_cents: " + ("yes" if abs(residual) <= Decimal("0.25") else "no"),
    ])
    if abs(residual) > Decimal("0.25"):
        lines.append("reason: deposits, fee rounding, or unsettled mark versus cost")
    return lines


def collect_pages(fetch, *, what: str, max_pages: int = MAX_PAGES) -> list:
    """Read cursor pages. A short or repeating cursor fails the whole read."""
    rows: list = []
    cursor = None
    for _page in range(max_pages):
        batch, cursor = fetch(cursor)
        if not isinstance(batch, list):
            raise ValueError(f"{what} page is not a list")
        rows.extend(batch)
        if not cursor:
            return rows
    raise ValueError(f"{what} pagination exceeded {max_pages} pages")


def fills_since_cohort(rows: list[object]) -> tuple[list[LiveFill], list[str]]:
    """Keep cohort fills. Unreadable rows become mismatches and stay out of the total."""
    kept: list[LiveFill] = []
    mismatches: list[str] = []
    for raw in rows:
        if not isinstance(raw, dict):
            mismatches.append("fill row is not an object")
            continue
        when = _time(raw.get("created_time"))
        if when is None:
            when = _time(raw.get("ts"))
        ticker = str(raw.get("ticker") or "").strip()
        if when is None:
            mismatches.append(f"{ticker or 'fill'}: fill time absent")
            continue
        if when < COHORT_START:
            continue
        side = str(raw.get("side") or "").strip().lower()
        action_raw = raw.get("action")
        action = str(action_raw).strip().lower() if isinstance(action_raw, str) else ""
        if action not in {"buy", "sell"}:
            action = "missing"
        price_key = "yes_price_dollars" if side == "yes" else "no_price_dollars"
        order_raw = raw.get("order_id")
        order_id = order_raw.strip() if isinstance(order_raw, str) and order_raw.strip() else None
        kept.append(
            LiveFill(
                ticker=ticker,
                side=side,
                action=action,
                qty=_dec(raw.get("count_fp")),
                price=_dec(raw.get(price_key)) if side in {"yes", "no"} else None,
                fee=_dec(raw.get("fee_cost")),
                order_id=order_id,
                when=when,
            )
        )
    return kept, mismatches


def settlements_by_ticker(rows: list[object]) -> tuple[dict[str, Settlement], list[str]]:
    found: dict[str, Settlement] = {}
    mismatches: list[str] = []
    for raw in rows:
        if not isinstance(raw, dict):
            mismatches.append("settlement row is not an object")
            continue
        ticker = str(raw.get("ticker") or "").strip()
        if not ticker:
            mismatches.append("settlement ticker absent")
            continue
        if ticker in found:
            mismatches.append(f"{ticker}: duplicate settlement")
            found.pop(ticker, None)
            continue
        result = str(raw.get("market_result") or "").strip().lower()
        found[ticker] = Settlement(
            ticker=ticker,
            result=result,
            yes_count=_dec(raw.get("yes_count_fp")),
            no_count=_dec(raw.get("no_count_fp")),
            yes_cost=_dec(raw.get("yes_total_cost_dollars")),
            no_cost=_dec(raw.get("no_total_cost_dollars")),
            revenue_cents=_cents(raw.get("revenue")),
            fee=_dec(raw.get("fee_cost")),
            settled_at=_time(raw.get("settled_time")),
        )
    return found, mismatches


def parse_fill_log(text: str) -> list[LogRow]:
    rows: list[LogRow] = []
    for index, raw in enumerate(text.splitlines(keepends=True)):
        ending = "\n" if raw.endswith("\n") else ""
        body = raw[:-1] if ending else raw
        if not body[:4].isdigit() or "T" not in body[:25]:
            continue
        fields = [part.strip() for part in body.split(",")]
        if len(fields) < 7:
            continue
        try:
            price_cents = int(fields[3])
        except ValueError:
            price_cents = None
        qty = _dec(fields[4])
        http = fields[6]
        http_ok = http.startswith("2") or http.lower() == "executed"
        fee_cents = fields[9] if len(fields) >= 11 else None
        order_blank = len(fields) >= 11 and fields[10] == ""
        rows.append(
            LogRow(
                index=index,
                ending=ending,
                fields=fields,
                ticker=fields[1],
                side=fields[2].lower(),
                qty=qty,
                price_cents=price_cents,
                http_ok=http_ok,
                fee_cents=fee_cents,
                order_blank=order_blank,
            )
        )
    return rows


def _match_key(ticker: str, side: str, qty: Decimal, price_cents: int) -> tuple[str, str, str, int]:
    return (ticker, side, format(qty.normalize(), "f"), price_cents)


def incomplete_report(reason: str, *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return (
        f"# P&L {stamp}\n\n"
        f"incomplete: {reason}\n\n"
        "running total omitted\n"
    )


def assemble_pnl(
    *,
    fill_rows: list[object],
    settlement_rows: list[object],
    fill_log_text: str,
    paper_rows: list[dict],
    open_tickers: set[str],
    paper_note: str | None = None,
    now: datetime | None = None,
    polymarket_rows: list[dict] | None = None,
    cash: Decimal | None = None,
    portfolio_value: Decimal | None = None,
    starting_balance: Decimal | None = None,
    entry_samples: list[tuple[str, str, int, Decimal]] | None = None,
) -> tuple[str, str, list[str]]:
    """Return the report, the possibly backfilled log, and the backfilled tickers."""
    fills, mismatches = fills_since_cohort(fill_rows)
    settlements, settlement_mismatches = settlements_by_ticker(settlement_rows)
    mismatches.extend(settlement_mismatches)
    duplicate_tickers = {
        item.split(":", 1)[0]
        for item in settlement_mismatches
        if item.endswith(": duplicate settlement")
    }
    log_rows = [row for row in parse_fill_log(fill_log_text) if row.http_ok]
    settled: list[tuple[str, str, Decimal]] = []
    running = Decimal("0")
    moment = now or datetime.now(timezone.utc)
    backfill_ids: dict[int, str] = {}

    buys_by_ticker: dict[str, list[LiveFill]] = defaultdict(list)
    sells_by_ticker: dict[str, list[LiveFill]] = defaultdict(list)
    for fill in fills:
        if fill.action not in {"buy", "sell"}:
            mismatches.append(f"{fill.ticker or 'fill'}: fill action absent")
            continue
        if (
            not fill.ticker
            or fill.side not in {"yes", "no"}
            or fill.qty is None
            or fill.qty <= 0
            or fill.price is None
            or fill.fee is None
            or price_cents_of(fill.price) is None
        ):
            mismatches.append(f"{fill.ticker or 'fill'}: fill economics unreadable")
            continue
        if fill.action == "sell":
            sells_by_ticker[fill.ticker].append(fill)
        else:
            buys_by_ticker[fill.ticker].append(fill)
    for ticker, group in sells_by_ticker.items():
        # A NO buy on the YES book is labeled sell. Count it when the
        # settlement cost matches, or when that ticker is still open.
        paid = _paid_for_side(group, settlements.get(ticker))
        still_open = ticker in open_tickers and settlements.get(ticker) is None
        if ticker not in buys_by_ticker and (paid or still_open):
            buys_by_ticker[ticker].extend(group)
            continue
        mismatches.append(f"{ticker}: sell fill; not allocated")

    allocated: list[tuple[LiveFill, str, str, str, str]] = []
    for ticker, group in buys_by_ticker.items():
        if ticker in duplicate_tickers:
            mismatches.append(f"{ticker}: duplicate settlement; not allocated")
            continue
        settlement = settlements.get(ticker)
        if settlement is None:
            if ticker in open_tickers:
                known = open_tickers.get(ticker) if isinstance(open_tickers, dict) else None
                qty = sum((fill.qty or Decimal("0")) for fill in group)
                if isinstance(known, Decimal) and abs(abs(known) - qty) > COUNT_TOLERANCE:
                    mismatches.append(f"{ticker}: open size differs from fills")
                for fill in group:
                    allocated.append((fill, "open", "open", "open", _hold(fill.when, moment)))
            else:
                mismatches.append(f"{ticker}: fill has no settlement and is not open")
            continue
        if settlement.result == "scalar":
            mismatches.append(f"{ticker}: scalar settlement; payout not allocated")
            continue
        if settlement.result not in {"yes", "no"}:
            mismatches.append(f"{ticker}: settlement result unreadable")
            continue
        yes_qty = sum((fill.qty or Decimal("0")) for fill in group if fill.side == "yes")
        no_qty = sum((fill.qty or Decimal("0")) for fill in group if fill.side == "no")
        yes_count = settlement.yes_count or Decimal("0")
        no_count = settlement.no_count or Decimal("0")
        if abs(yes_qty - yes_count) > COUNT_TOLERANCE or abs(no_qty - no_count) > COUNT_TOLERANCE:
            mismatches.append(f"{ticker}: settlement size differs from fills")
            continue
        fill_fee = sum((fill.fee or Decimal("0")) for fill in group)
        settlement_fee = settlement.fee or Decimal("0")
        if settlement_fee != 0 and abs(settlement_fee - fill_fee) > FEE_TOLERANCE:
            mismatches.append(
                f"{ticker}: settlement fee {money(settlement_fee)} differs from fill fees {money(fill_fee)}"
            )
        winning_qty = yes_qty if settlement.result == "yes" else no_qty
        expected_revenue = int((winning_qty * Decimal("100")).to_integral_value())
        if settlement.revenue_cents is None:
            mismatches.append(f"{ticker}: settlement revenue absent")
        elif settlement.revenue_cents != expected_revenue:
            mismatches.append(
                f"{ticker}: settlement revenue cents {settlement.revenue_cents} "
                f"differs from allocated {expected_revenue}"
            )
        for fill in group:
            assert fill.qty is not None and fill.price is not None and fill.fee is not None
            cost = fill.qty * fill.price
            payout = fill.qty if settlement.result == fill.side else Decimal("0")
            fee_net = payout - cost - fill.fee
            allocated.append(
                (fill, money(payout), money(fee_net), settlement.result, _hold(fill.when, settlement.settled_at))
            )
            running += fee_net
            settled.append((series_of(fill.ticker), fill.side, fee_net))
            if entry_samples is not None:
                cents = price_cents_of(fill.price)
                if cents is not None:
                    entry_samples.append((fill.ticker, fill.side, cents, fee_net))

    trades, open_premium, open_fees = _collapse_display(allocated)
    extra_ids, extra_tickers, extra_mismatches = reconcile_log(
        log_rows,
        buys_by_ticker_flat(buys_by_ticker),
    )
    backfill_ids.update(extra_ids)
    backfilled = extra_tickers
    mismatches.extend(extra_mismatches)

    new_log = _apply_backfill(fill_log_text, backfill_ids)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    report = _render(
        stamp=stamp,
        trades=trades,
        settled=settled,
        running=running,
        mismatches=mismatches,
        paper_rows=paper_rows,
        paper_note=paper_note,
        backfilled=backfilled,
        polymarket_rows=polymarket_rows or [],
        reconciliation=reconciliation_lines(
            ledger=running,
            open_exposure=open_premium,
            open_fees=open_fees,
            cash=cash,
            portfolio=portfolio_value,
            starting=starting_balance,
        ),
    )
    return report, new_log, backfilled


def _paid_for_side(group: list[LiveFill], settlement: Settlement | None) -> bool:
    """True when a sell-labeled fill paid the settlement cost on its side."""
    if settlement is None or not group:
        return False
    side = group[0].side
    if any(fill.side != side or fill.qty is None or fill.price is None for fill in group):
        return False
    premium = sum((fill.qty or Decimal("0")) * (fill.price or Decimal("0")) for fill in group)
    if side == "no":
        cost, other = settlement.no_cost, settlement.yes_cost
    elif side == "yes":
        cost, other = settlement.yes_cost, settlement.no_cost
    else:
        return False
    if cost is None:
        return False
    if other is not None and abs(other) > FEE_TOLERANCE:
        return False
    return abs(cost - premium) <= FEE_TOLERANCE


def _same_order_id(fills: list[LiveFill]) -> str | None:
    ids: list[str] = []
    for fill in fills:
        if not fill.order_id:
            return None
        ids.append(fill.order_id)
    if len(set(ids)) == 1:
        return ids[0]
    return None


def _fee_note(log_row: LogRow, fills: list[LiveFill], mismatches: list[str]) -> None:
    if log_row.fee_cents is None:
        mismatches.append(f"{log_row.ticker}: fee column absent")
        return
    log_fee = _dec(log_row.fee_cents)
    total = sum((fill.fee or Decimal("0")) * Decimal("100") for fill in fills)
    if log_fee is None or abs(log_fee - total) > Decimal("0.01"):
        mismatches.append(f"{log_row.ticker}: fill log fee differs")


def reconcile_log(
    log_rows: list[LogRow],
    fills: list[LiveFill],
) -> tuple[dict[int, str], list[str], list[str]]:
    """Match log rows to fills. Partials share one id only when the qty sums."""
    mismatches: list[str] = []
    backfill: dict[int, str] = {}
    backfilled: list[str] = []
    unused_fills = {
        id(fill): fill
        for fill in fills
        if fill.qty is not None and fill.price is not None and price_cents_of(fill.price) is not None
    }
    unused_logs: dict[int, LogRow] = {}
    for row in log_rows:
        if row.qty is None or row.price_cents is None:
            mismatches.append(f"{row.ticker}: fill log economics unreadable")
            continue
        unused_logs[row.index] = row

    fill_buckets: dict[tuple[str, str, str, int], list[LiveFill]] = defaultdict(list)
    log_buckets: dict[tuple[str, str, str, int], list[LogRow]] = defaultdict(list)
    for fill in unused_fills.values():
        cents = price_cents_of(fill.price)
        assert fill.qty is not None and cents is not None
        fill_buckets[_match_key(fill.ticker, fill.side, fill.qty, cents)].append(fill)
    for row in unused_logs.values():
        assert row.qty is not None and row.price_cents is not None
        log_buckets[_match_key(row.ticker, row.side, row.qty, row.price_cents)].append(row)
    for key in set(fill_buckets) | set(log_buckets):
        flist = fill_buckets.get(key, [])
        llist = log_buckets.get(key, [])
        pair_count = min(len(flist), len(llist))
        for index in range(pair_count):
            _fee_note(llist[index], [flist[index]], mismatches)
            unused_fills.pop(id(flist[index]), None)
            unused_logs.pop(llist[index].index, None)
        if len(flist) == 1 and len(llist) == 1:
            shared = _same_order_id(flist)
            if llist[0].order_blank and shared:
                backfill[llist[0].index] = shared.replace(",", "")
                backfilled.append(llist[0].ticker)

    logs_by_price: dict[tuple[str, str, int], list[LogRow]] = defaultdict(list)
    fills_by_price: dict[tuple[str, str, int], list[LiveFill]] = defaultdict(list)
    for row in unused_logs.values():
        assert row.price_cents is not None
        logs_by_price[(row.ticker, row.side, row.price_cents)].append(row)
    for fill in unused_fills.values():
        cents = price_cents_of(fill.price)
        assert cents is not None
        fills_by_price[(fill.ticker, fill.side, cents)].append(fill)
    for key, llist in logs_by_price.items():
        flist = fills_by_price.get(key, [])
        if len(llist) != 1 or len(flist) < 2 or llist[0].qty is None:
            continue
        qty_sum = sum((fill.qty or Decimal("0")) for fill in flist)
        shared = _same_order_id(flist)
        if qty_sum != llist[0].qty or not shared:
            continue
        _fee_note(llist[0], flist, mismatches)
        if llist[0].order_blank:
            backfill[llist[0].index] = shared.replace(",", "")
            backfilled.append(llist[0].ticker)
        for fill in flist:
            unused_fills.pop(id(fill), None)
        unused_logs.pop(llist[0].index, None)

    # One leftover log row and one leftover fill, same ticker, side, and
    # qty, within one cent. The report keeps the exchange price. A split
    # such as 1 at 66c plus 7 at 67c against one 8-at-67c log row does not
    # pair. This pass does not write the fill log.
    logs_by_qty: dict[tuple[str, str, str], list[LogRow]] = defaultdict(list)
    fills_by_qty: dict[tuple[str, str, str], list[LiveFill]] = defaultdict(list)
    for row in unused_logs.values():
        assert row.qty is not None
        logs_by_qty[(row.ticker, row.side, format(row.qty.normalize(), "f"))].append(row)
    for fill in unused_fills.values():
        assert fill.qty is not None
        fills_by_qty[(fill.ticker, fill.side, format(fill.qty.normalize(), "f"))].append(fill)
    for key, llist in logs_by_qty.items():
        flist = fills_by_qty.get(key, [])
        if len(llist) != 1 or len(flist) != 1 or llist[0].price_cents is None:
            continue
        fill_price = flist[0].price
        if fill_price is None:
            continue
        cents = price_cents_of(fill_price)
        if cents is None or abs(llist[0].price_cents - cents) > 1:
            continue
        _fee_note(llist[0], flist, mismatches)
        unused_fills.pop(id(flist[0]), None)
        unused_logs.pop(llist[0].index, None)

    for fill in unused_fills.values():
        mismatches.append(f"{fill.ticker}: fill not in fill log")
    for row in unused_logs.values():
        mismatches.append(f"{row.ticker}: fill log row has no live fill")
    return backfill, backfilled, mismatches


def _collapse_display(
    allocated: list[tuple[LiveFill, str, str, str, str]],
) -> tuple[list[tuple[str, ...]], Decimal, Decimal]:
    """One display row per ticker, side, price, result, and order id.

    Count and fee are summed. Hold starts at the earliest fill. Fills with
    no order id stay separate. Settled samples are not collapsed here.
    """
    groups: dict[tuple, list[tuple[LiveFill, str, str, str, str]]] = {}
    order: list[tuple] = []
    for item in allocated:
        fill, _payout_text, _fee_net_text, result_name, _hold_text = item
        assert fill.price is not None and fill.qty is not None and fill.fee is not None
        cents = price_cents_of(fill.price)
        assert cents is not None
        identity = fill.order_id if fill.order_id else f"row-{id(fill)}"
        key = (fill.ticker, fill.side, cents, result_name, identity)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    trades: list[tuple[str, ...]] = []
    open_premium = Decimal("0")
    open_fees = Decimal("0")
    for key in order:
        items = groups[key]
        ticker, side, cents, result_name, _identity = key
        qty = Decimal("0")
        fee = Decimal("0")
        payout = Decimal("0")
        fee_net = Decimal("0")
        earliest: datetime | None = None
        hold = "unknown"
        open_row = items[0][1] == "open"
        for fill, payout_text, fee_net_text, _result_name, hold_text in items:
            assert fill.qty is not None and fill.price is not None and fill.fee is not None
            qty += fill.qty
            fee += fill.fee
            if open_row:
                open_premium += fill.qty * fill.price
                open_fees += fill.fee
            else:
                payout += Decimal(payout_text)
                fee_net += Decimal(fee_net_text)
            if fill.when is not None and (earliest is None or fill.when < earliest):
                earliest = fill.when
                hold = hold_text
        if open_row:
            payout_text, fee_net_text = "open", "open"
        else:
            payout_text, fee_net_text = money(payout), money(fee_net)
        trades.append(
            (
                ticker,
                side,
                money(qty),
                str(cents),
                money(fee),
                payout_text,
                fee_net_text,
                result_name,
                hold,
                series_of(ticker),
            )
        )
    return trades, open_premium, open_fees


def buys_by_ticker_flat(groups: dict[str, list[LiveFill]]) -> list[LiveFill]:
    flat: list[LiveFill] = []
    for ticker in groups:
        flat.extend(groups[ticker])
    return flat


def _apply_backfill(text: str, ids_by_index: dict[int, str]) -> str:
    if not ids_by_index:
        return text
    lines = text.splitlines(keepends=True)
    for index, order_id in ids_by_index.items():
        raw = lines[index]
        ending = "\n" if raw.endswith("\n") else ""
        body = raw[:-1] if ending else raw
        fields = [part.strip() for part in body.split(",")]
        if len(fields) < 11 or fields[10] != "":
            continue
        fields[10] = order_id
        lines[index] = ", ".join(fields) + ending
    return "".join(lines)


def _render(
    *,
    stamp: str,
    trades: list[tuple[str, ...]],
    settled: list[tuple[str, str, Decimal]],
    running: Decimal,
    mismatches: list[str],
    paper_rows: list[dict],
    paper_note: str | None,
    backfilled: list[str],
    polymarket_rows: list[dict] | None = None,
    reconciliation: list[str] | None = None,
) -> str:
    lines = [
        f"# P&L {stamp}",
        "",
        "Cohort start: 2026-08-20T00:00:00Z.",
        "Source: Kalshi fills and settlements.",
        "The fill log and the paper book are reconcile targets.",
        "Paper rows are not in the running total.",
        "",
        "## Trades",
        "",
        "| venue | ticker | side | count | entry_price_cents | entry_fee_dollars | payout_dollars | fee_net_dollars | settled_result | hold_time | series |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted(trades):
        lines.append("| kalshi | " + " | ".join(row) + " |")
    lines.extend(_bucket_lines(settled))
    lines.extend(
        [
            "",
            "## Running total",
            "",
            f"running_total_dollars: {money(running)}",
            "",
            "Settled fee-net only. Open rows are excluded.",
        ]
    )
    lines.extend(reconciliation or reconciliation_lines(
        ledger=running, open_exposure=Decimal(0), open_fees=Decimal(0),
        cash=None, portfolio=None, starting=None,
    ))
    lines.extend(["", "## Mismatches", ""])
    if mismatches:
        for item in sorted(set(mismatches)):
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Paper book",
            "",
            "Not included in the live fee-net total.",
            "",
        ]
    )
    if paper_note:
        lines.append(paper_note)
        lines.append("")
    lines.extend(
        [
            "| venue | ticker | side | qty | price_cents | resolved | pnl_dollars | series | signal_type |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in sorted(paper_rows, key=lambda item: (str(item.get("ticker")), str(item.get("side")))):
        lines.append(
            "| {venue} | {ticker} | {side} | {contracts} | {price_cents} | {resolved} | {pnl_dollars} | {series_ticker} | {signal_type} |".format(
                venue=_paper_venue(row),
                ticker=row.get("ticker") or "",
                side=row.get("side") or "",
                contracts=row.get("contracts") if row.get("contracts") is not None else "",
                price_cents=row.get("price_cents") if row.get("price_cents") is not None else "",
                resolved=row.get("resolved") if row.get("resolved") is not None else "",
                pnl_dollars=row.get("pnl_dollars") if row.get("pnl_dollars") is not None else "",
                series_ticker=row.get("series_ticker") or "",
                signal_type=row.get("signal_type") or "",
            )
        )
    if polymarket_rows:
        lines.extend(
            [
                "",
                "## Polymarket paper rows",
                "",
                "Not included in the live fee-net total.",
                "",
                "| venue | series | ticker | queued | settlement |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in polymarket_rows:
            lines.append(
                "| {venue} | {series} | {ticker} | {queued} | {settlement} |".format(
                    venue=row.get("venue") or "",
                    series=row.get("series") or "",
                    ticker=row.get("ticker") or "",
                    queued=row.get("queued") or "0",
                    settlement=row.get("settlement") or "",
                )
            )
    backfill_text = ", ".join(sorted(set(backfilled))) if backfilled else "none"
    lines.extend(["", "## Backfill", "", f"backfilled_rows: {backfill_text}", ""])
    return "\n".join(lines)


def _paper_venue(row: dict) -> str:
    raw = str(row.get("venue") or "kalshi").strip().lower()
    if raw == "polymarket_us":
        return "polymarket_us"
    return "kalshi"


def read_paper(path: Path) -> tuple[list[dict], str | None]:
    if not path.is_file():
        return [], "paper db absent"
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return [], "paper db unreadable"
    try:
        conn.row_factory = sqlite3.Row
        names = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
        venue_sql = "venue" if "venue" in names else "'kalshi'"
        found = conn.execute(
            f"""
            SELECT ticker, side, contracts, price_cents, resolved,
                   pnl_dollars, series_ticker, signal_type, {venue_sql} AS venue
            FROM paper_trades
            """
        ).fetchall()
    except sqlite3.Error:
        return [], "paper db unreadable"
    finally:
        conn.close()
    return [dict(row) for row in found], None


def _open_tickers(positions: object) -> dict[str, Decimal | None]:
    if not isinstance(positions, list):
        raise ValueError("open positions response is not a list")
    sizes: dict[str, Decimal | None] = {}
    for row in positions:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip()
        if not ticker:
            continue
        size = row.get("position_fp")
        if size is None:
            size = row.get("position")
        number = _dec(size) if size is not None else None
        if number is not None and number == 0:
            continue
        sizes[ticker] = number
    return sizes


def _transfer_page(client: object, path: str, key: str) -> list:
    def fetch(cursor):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = client._request("GET", path, params=params)
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise ValueError(path)
        nxt = data.get("cursor") or None
        if nxt is not None and not isinstance(nxt, str):
            raise ValueError(path)
        return data[key], nxt
    return collect_pages(fetch, what=path)


def _applied_net(rows: list, *, withdraw: bool) -> Decimal:
    total = Decimal(0)
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "applied":
            continue
        amount, fee = row.get("amount_cents"), row.get("fee_cents")
        if type(amount) is not int or type(fee) is not int:
            raise ValueError("transfer amount absent")
        total += Decimal(amount + fee) if withdraw else Decimal(amount - fee)
    return total / Decimal(100)


def read_account(client: object) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Cash, portfolio mark, and net applied funding. A failed part stays None."""
    cash: Decimal | None = None
    portfolio: Decimal | None = None
    starting: Decimal | None = None
    try:
        data = client._request("GET", "/portfolio/balance")
        if not isinstance(data, dict):
            raise ValueError("balance")
        cash = _dec(data.get("balance_dollars"))
        if cash is None and type(data.get("balance")) is int:
            cash = Decimal(data["balance"]) / Decimal(100)
        if cash is None or type(data.get("portfolio_value")) is not int:
            raise ValueError("balance")
        portfolio = Decimal(data["portfolio_value"]) / Decimal(100)
    except Exception:
        cash = portfolio = None
    try:
        deposits = _applied_net(_transfer_page(client, "/portfolio/deposits", "deposits"), withdraw=False)
        withdrawals = _applied_net(
            _transfer_page(client, "/portfolio/withdrawals", "withdrawals"), withdraw=True,
        )
        starting = deposits - withdrawals
    except Exception:
        starting = None
    return cash, portfolio, starting


def load_exchange(client: object) -> tuple[list, list, dict[str, Decimal | None]]:
    min_ts = int(COHORT_START.timestamp())
    fills = collect_pages(
        lambda cursor: client.list_fills_page(min_ts=min_ts, limit=200, cursor=cursor),
        what="fills",
    )
    settlements = collect_pages(
        lambda cursor: client.list_settlements_page(limit=200, cursor=cursor),
        what="settlements",
    )
    positions = client.get_open_positions(strict=True)
    return fills, settlements, _open_tickers(positions)


def replace_if_unchanged(path: Path, original: str, updated: str) -> bool:
    """Write only when the file still matches the text that was edited."""
    if updated == original or not path.is_file():
        return False
    current = path.read_text(encoding="utf-8")
    if current != original:
        return False
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(path)
    return True


def run_report(
    client: object,
    *,
    repo: Path,
    write_fill_log: bool,
    now: datetime | None = None,
    entry_quality: bool = False,
) -> int:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    report_path = repo / "logs" / "reports" / f"pnl-{stamp}.md"
    if not entry_quality:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fill_rows, settlement_rows, open_tickers = load_exchange(client)
        log_path = repo / FILL_LOG_RELATIVE
        log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        paper_rows, paper_note = read_paper(repo / PAPER_DB_RELATIVE)
        cash, portfolio_value, starting_balance = read_account(client)
        grok_db = repo / "logs" / "state" / "grok_paper.db"
        polymarket_rows: list[dict] = []
        if grok_db.is_file() and not entry_quality:
            from utils.event_news_grok_edge import polymarket_observation_rows

            polymarket_rows = polymarket_observation_rows(grok_db)
        samples: list[tuple[str, str, int, Decimal]] = []
        report, new_log, tickers = assemble_pnl(
            fill_rows=fill_rows,
            settlement_rows=settlement_rows,
            fill_log_text=log_text,
            paper_rows=paper_rows,
            open_tickers=open_tickers,
            paper_note=paper_note,
            now=now,
            polymarket_rows=polymarket_rows,
            cash=cash,
            portfolio_value=portfolio_value,
            starting_balance=starting_balance,
            entry_samples=samples if entry_quality else None,
        )
        if entry_quality:
            yes_probability, book = live_order_entry_facts(repo / "logs" / "trades")
            for line in entry_quality_lines(samples, yes_probability, book):
                print(line)
            print(f"settled_fills={len(samples)}")
            return 0
        wrote = False
        if write_fill_log and new_log != log_text:
            wrote = replace_if_unchanged(log_path, log_text, new_log)
            if not wrote:
                report = report.replace(
                    "## Backfill\n",
                    "## Backfill\n\nfill log changed during backfill; order ids were not written\n",
                )
                tickers = []
        report_path.write_text(report, encoding="utf-8")
        print(f"report={report_path}")
        print("complete=yes")
        print("running_total_present=yes")
        print("backfilled=" + (", ".join(sorted(set(tickers))) if tickers else "none"))
        print("backfill_written=" + ("yes" if wrote else "no"))
        return 0
    except Exception:
        if entry_quality:
            print("complete=no")
            return 1
        report_path.write_text(incomplete_report("exchange read failed", now=now), encoding="utf-8")
        print(f"report={report_path}")
        print("complete=no")
        print("running total omitted")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the daily fee-net P&L report.")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--env-file", default="")
    parser.add_argument("--write-fill-log", action="store_true")
    parser.add_argument("--entry-quality", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    env_file = Path(args.env_file) if args.env_file else repo / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    from kalshi.rest_client import KalshiRestClient

    return run_report(
        KalshiRestClient(),
        repo=repo,
        write_fill_log=args.write_fill_log,
        entry_quality=args.entry_quality,
    )


if __name__ == "__main__":
    sys.exit(main())
