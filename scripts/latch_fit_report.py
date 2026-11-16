"""Offline latch-fit report. Read a fill log, write a CSV, place no orders.

The session line is 10% of a $50 bankroll, $5. The halt module's only
$5 figure is ``CASH_DROP_CENTS`` (500 cents). Worst-case loss is
``size * price + fee(size)``. ``max_size_90`` is the largest whole size
whose loss is still within 90% of the line. ``would_8_trip`` is whether
8 contracts alone exceed the line.

Fees come from ``trading.fees.quote_fee`` (Kalshi taker, direct account).
A fill row that already records a fee uses that fee for its own size and
is marked ``logged``. Every other fee is marked ``formula``. Order ids
are never written.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pnl_report import parse_fill_log, series_of  # noqa: E402
from trading.fees import (  # noqa: E402
    DIRECT_ACCOUNT_PRECISION,
    INITIAL_ORDER_FEE_ACCUMULATOR,
    FeeContext,
    FeeRole,
    FeeUnscorableError,
    fee_coefficient_for,
    fee_schedule_at,
    quote_fee,
)
from trading.venue import Venue  # noqa: E402
from utils.event_news_session_halt import CASH_DROP_CENTS  # noqa: E402

DESK_BANKROLL = Decimal("50")
LOSS_LIMIT_FRACTION = Decimal("0.10")
HEADROOM_FRACTION = Decimal("0.9")
DESK_SIZE = 8
FEE_AS_OF = datetime(2026, 11, 15, tzinfo=timezone.utc)
_ORDER_ID_FIELD = "latch-fit"


def session_loss_line() -> Decimal:
    """$5. 10% of the $50 desk bankroll, equal to the halt module's $5."""
    line = DESK_BANKROLL * LOSS_LIMIT_FRACTION
    halt_dollars = Decimal(CASH_DROP_CENTS) / Decimal(100)
    if line != halt_dollars:
        raise RuntimeError("session loss line drifted from the halt module")
    return line


def _money(value: Decimal) -> str:
    return format(value.normalize(), "f")


def kalshi_taker_fee(
    size: int,
    price: Decimal,
    *,
    timestamp: datetime | None = None,
) -> Decimal:
    """Net taker fee from the pinned Kalshi schedule. Does not submit an order."""
    if isinstance(size, bool) or type(size) is not int or size < 1:
        raise FeeUnscorableError("size must be a positive integer")
    moment = timestamp or FEE_AS_OF
    schedule = fee_schedule_at(venue=Venue.KALSHI, timestamp=moment)
    quantity = Decimal(size)
    return quote_fee(
        FeeContext(
            schedule_id=schedule,
            role=FeeRole.TAKER,
            quantity=quantity,
            price=price,
            signed_revenue=-(quantity * price),
            order_id=_ORDER_ID_FIELD,
            accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
            multiplier=Decimal("1"),
            coefficient=fee_coefficient_for(schedule, FeeRole.TAKER),
            account_precision=DIRECT_ACCOUNT_PRECISION,
            timestamp=moment,
        )
    ).net_fee


def _loss(size: int, price: Decimal, fee: Decimal) -> Decimal:
    return (Decimal(size) * price) + fee


def max_size_90(price: Decimal, *, timestamp: datetime | None = None) -> int:
    """Largest n with n*price + fee(n) <= 90% of the session line."""
    budget = HEADROOM_FRACTION * session_loss_line()
    if price <= 0:
        return 0
    upper = int((budget / price).to_integral_value(rounding=ROUND_FLOOR))
    best = 0
    for size in range(1, upper + 1):
        fee = kalshi_taker_fee(size, price, timestamp=timestamp)
        if _loss(size, price, fee) <= budget:
            best = size
    return best


@dataclass(frozen=True)
class FitRow:
    origin: str
    ticker: str
    side: str
    series: str
    price_cents: Decimal
    size: int
    fee: Decimal
    fee_source: str
    worst_case_loss: Decimal
    max_size_90: int
    would_8_trip: bool

    def csv_row(self) -> list[str]:
        return [
            self.origin,
            self.ticker,
            self.side,
            self.series,
            _money(self.price_cents),
            str(self.size),
            _money(self.fee),
            self.fee_source,
            _money(self.worst_case_loss),
            str(self.max_size_90),
            "true" if self.would_8_trip else "false",
        ]


CSV_COLUMNS = (
    "origin",
    "ticker",
    "side",
    "series",
    "price_cents",
    "size",
    "fee",
    "fee_source",
    "worst_case_loss",
    "max_size_90",
    "would_8_trip",
)


def fit_row(
    *,
    origin: str,
    ticker: str,
    side: str,
    series: str,
    price_cents: Decimal,
    size: int,
    logged_fee: Decimal | None = None,
    timestamp: datetime | None = None,
) -> FitRow:
    price = price_cents / Decimal(100)
    if logged_fee is not None:
        fee = logged_fee
        fee_source = "logged"
    else:
        fee = kalshi_taker_fee(size, price, timestamp=timestamp)
        fee_source = "formula"
    if logged_fee is not None and size == DESK_SIZE:
        fee_at_8 = logged_fee
    else:
        fee_at_8 = kalshi_taker_fee(DESK_SIZE, price, timestamp=timestamp)
    return FitRow(
        origin=origin,
        ticker=ticker,
        side=side,
        series=series,
        price_cents=price_cents,
        size=size,
        fee=fee,
        fee_source=fee_source,
        worst_case_loss=_loss(size, price, fee),
        max_size_90=max_size_90(price, timestamp=timestamp),
        would_8_trip=_loss(DESK_SIZE, price, fee_at_8) > session_loss_line(),
    )


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(str(value))
    elif isinstance(value, str) and value.strip():
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            return None
    else:
        return None
    if not number.is_finite():
        return None
    return number


def _whole_size(value: object) -> int | None:
    number = _decimal(value)
    if number is None or number != number.to_integral_value():
        return None
    size = int(number)
    if size < 1:
        return None
    return size


def _series_name(ticker: str, explicit: str = "") -> str:
    named = explicit.strip().upper()
    if named:
        return named
    head = series_of(ticker).strip()
    if not head or head == "unknown":
        return "unknown"
    return head.upper()


def _stamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def rows_from_fill_log(text: str) -> tuple[list[FitRow], int]:
    """Parse fill-log lines. A logged fee is cents, matching the log column."""
    fitted: list[FitRow] = []
    skipped = 0
    for row in parse_fill_log(text):
        fields = row.fields
        price_cents = _decimal(fields[8]) if len(fields) >= 9 and fields[8] else None
        if price_cents is None:
            price_cents = _decimal(row.price_cents)
        size = _whole_size(fields[7]) if len(fields) >= 8 and fields[7] else None
        if size is None:
            size = _whole_size(row.qty)
        logged = _decimal(row.fee_cents)
        if price_cents is None or size is None or price_cents <= 0 or price_cents >= 100:
            skipped += 1
            continue
        fee = None if logged is None or logged < 0 else logged / Decimal(100)
        try:
            fitted.append(
                fit_row(
                    origin="fill",
                    ticker=row.ticker,
                    side=row.side,
                    series=_series_name(row.ticker),
                    price_cents=price_cents,
                    size=size,
                    logged_fee=fee,
                    timestamp=_stamp(fields[0]) if fields else None,
                )
            )
        except (FeeUnscorableError, ValueError):
            skipped += 1
    return fitted, skipped


def rows_from_attempts(text: str) -> tuple[list[FitRow], int]:
    fitted: list[FitRow] = []
    skipped = 0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(payload, dict):
            skipped += 1
            continue
        price_cents = _decimal(payload.get("price_cents"))
        size = _whole_size(payload.get("size", DESK_SIZE))
        ticker = str(payload.get("ticker") or "").strip()
        if (
            price_cents is None
            or size is None
            or not ticker
            or price_cents <= 0
            or price_cents >= 100
        ):
            skipped += 1
            continue
        try:
            fitted.append(
                fit_row(
                    origin="attempt",
                    ticker=ticker,
                    side=str(payload.get("side") or "").strip().lower(),
                    series=_series_name(ticker, str(payload.get("series") or "")),
                    price_cents=price_cents,
                    size=size,
                )
            )
        except (FeeUnscorableError, ValueError):
            skipped += 1
    return fitted, skipped


def median_max_size(rows: list[FitRow]) -> Decimal | None:
    if not rows:
        return None
    ordered = sorted(row.max_size_90 for row in rows)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[mid])
    return (Decimal(ordered[mid - 1]) + Decimal(ordered[mid])) / 2


def summary_lines(rows: list[FitRow], *, skipped: int) -> list[str]:
    line = session_loss_line()
    trips = sum(1 for row in rows if row.would_8_trip)
    median = median_max_size(rows)
    median_text = "n/a" if median is None else _money(median)
    logged = sum(1 for row in rows if row.fee_source == "logged")
    formula = sum(1 for row in rows if row.fee_source == "formula")
    return [
        f"line={_money(line)} headroom={_money(HEADROOM_FRACTION * line)}",
        f"rows={len(rows)} eight_trips={trips} median_max_size_90={median_text}",
        f"fee_logged={logged} fee_formula={formula} skipped={skipped}",
    ]


def write_csv(rows: list[FitRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row.csv_row())


def build_report(
    *,
    fill_text: str = "",
    attempt_text: str = "",
) -> tuple[list[FitRow], list[str]]:
    rows, skipped = rows_from_fill_log(fill_text)
    attempt_rows, attempt_skipped = rows_from_attempts(attempt_text)
    combined = rows + attempt_rows
    return combined, summary_lines(combined, skipped=skipped + attempt_skipped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline latch-fit sizing report. Reads files only. Places no orders."
    )
    parser.add_argument("--fill-log", type=Path, help="Path to a live fill log markdown file.")
    parser.add_argument("--attempts", type=Path, help="Optional JSONL of ticker, side, price_cents, series.")
    parser.add_argument("--csv", type=Path, required=True, help="Where to write the CSV.")
    args = parser.parse_args(argv)
    if args.fill_log is None and args.attempts is None:
        print("need --fill-log or --attempts", file=sys.stderr)
        return 2
    fill_text = ""
    attempt_text = ""
    if args.fill_log is not None:
        if not args.fill_log.is_file():
            print(f"fill log missing: {args.fill_log}", file=sys.stderr)
            return 1
        fill_text = args.fill_log.read_text(encoding="utf-8")
    if args.attempts is not None:
        if not args.attempts.is_file():
            print(f"attempts missing: {args.attempts}", file=sys.stderr)
            return 1
        attempt_text = args.attempts.read_text(encoding="utf-8")
    rows, lines = build_report(fill_text=fill_text, attempt_text=attempt_text)
    write_csv(rows, args.csv)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
