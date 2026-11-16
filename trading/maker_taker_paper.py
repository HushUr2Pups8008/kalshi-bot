"""Maker-versus-taker paper log. No order is placed and none is changed.

A veto-allowed live attempt appends one JSONL row. The offline scorer
reads that file, or a candidate file, and grades it against the public
trades tape. Politics must be running again before rows accumulate.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    INITIAL_ORDER_FEE_ACCUMULATOR,
    KALSHI_GENERAL_2026_07_07,
    FeeContext,
    FeeRole,
    fee_coefficient_for,
    quote_fee,
)
from trading.orderbook import BinaryMarketBook
from utils.event_news_desk_flags import DEFAULT_MAKER_WAIT_S
from utils.event_news_grok_veto import _append_jsonl, _parse_utc_stamp
from utils.event_news_research import event_news_series_ticker

# Coefficients: Kalshi fee schedule effective 2026-07-07.
# https://kalshi.com/docs/kalshi-fee-schedule.pdf
# SHA-256 815e2d5127d02d2fb90773d1a3844dc15a987696171eddc4e58de87b59c6124c
# Taker 0.07 and maker 0.0175 are pinned on KALSHI_GENERAL_2026_07_07.
# Per series, fee_type and fee_multiplier are the fields published by
# GET https://api.elections.kalshi.com/trade-api/v2/series/{series_ticker}
# (https://docs.kalshi.com/api-reference/market/get-series).
# quadratic_with_maker_fees scales the 0.0175 maker coefficient by that
# multiplier. quadratic charges takers only, so its maker multiplier is 0.
# A series with no published fee_type is left unpriced, not stored as 0.
# The names below were read from that series endpoint on 2026-11-15.
_QUADRATIC_ONE = frozenset(
    (
        "KXAPRPOTUS KXBABELMANDEBWEEKLY KXCANUSDEAL KXEOWEEK KXEFFTARIFF "
        "KXFEDMENTION KXFISAEXTEND KXHORMUZMAX KXHORMUZPEAK KXHORMUZWEEKLY "
        "KXLEAVECONGRESS KXMOCTRUMP25 KXPANAMAWEEKLY KXSECPRESSMENTION "
        "KXSBUDGETRES KXSUEZWEEKLY KXTRUMPACT KXTRUMPAPPROVE KXTRUMPMENTION "
        "KXTRUMPNOMNUM KXTRUMPSAY KXTRUMPUFC KXTRUTHSOCIAL KXUSAIRANAGREEMENT "
        "KXVOTESAVEAMERICA"
    ).split()
)
_MAKER_FEE_SERIES = {"KXFED": Decimal("1")}
_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_PAPER_PATH = _REPO_ROOT / "logs" / "state" / "maker_taker_paper.jsonl"
MAKER_TAKER_PAPER_PATH = _RUNTIME_PAPER_PATH
PUBLIC_TRADES_URL = "https://api.elections.kalshi.com/trade-api/v2/markets/trades"
_WINDOW = timedelta(seconds=DEFAULT_MAKER_WAIT_S)


def record_veto_allowed_attempt(
    analysis: Any,
    *,
    contracts: int,
    path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Append one row. Any failure stays inside this function."""
    try:
        _record(analysis, contracts=contracts, path=path, now=now)
    except Exception:
        return


def score_candidate_file(
    path: Path,
    trades: Sequence[Mapping[str, Any]] | None = None,
    *,
    window_s: int = DEFAULT_MAKER_WAIT_S,
    scout_no_bid: bool = False,
    get: Callable[[str], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Score a JSONL file of decision rows or Scout mention candidates."""
    return score_rows(
        _read_jsonl(path),
        trades,
        window_s=window_s,
        scout_no_bid=scout_no_bid,
        get=get,
    )


def score_rows(
    rows: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]] | None = None,
    *,
    window_s: int = DEFAULT_MAKER_WAIT_S,
    scout_no_bid: bool = False,
    get: Callable[[str], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Grade ask-1c against the public tape.

    ``trades`` is the already loaded public tape. When it is omitted, each
    ticker is read from ``GET /markets/trades`` on the public host. A print
    counts when its timestamp is after the row and at most ``window_s``
    later. The instant ``window_s`` seconds after the row counts. The next
    microsecond does not. A print at the row timestamp does not count.

    Scout's rule, when ``scout_no_bid`` is set, applies to a resting NO bid:
    a public print fills it only when the NO price is strictly below the
    bid. A print at the bid does not count.
    """
    tape = list(trades) if trades is not None else None
    scored: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        scored.append(_score_one(row, tape, window_s=window_s, scout_no_bid=scout_no_bid, get=get))
    return scored


def fetch_public_trades(
    ticker: str,
    start: datetime,
    end: datetime,
    *,
    get: Callable[[str], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Read the public trades tape. No auth header and no order call."""
    found: list[dict[str, Any]] = []
    cursor = ""
    # min_ts/max_ts are whole seconds. The local timestamp filter is exact.
    params_base = {
        "ticker": ticker,
        "limit": "1000",
        "min_ts": str(int(start.timestamp()) - 1),
        "max_ts": str(int(end.timestamp()) + 1),
    }
    for _ in range(20):
        params = dict(params_base)
        if cursor:
            params["cursor"] = cursor
        url = PUBLIC_TRADES_URL + "?" + urllib.parse.urlencode(params)
        payload = _public_get(url) if get is None else get(url)
        page = payload.get("trades") if isinstance(payload, Mapping) else None
        if not isinstance(page, list):
            break
        found.extend(item for item in page if isinstance(item, dict))
        cursor = str(payload.get("cursor") or "") if isinstance(payload, Mapping) else ""
        if not cursor:
            break
    return found


def _record(analysis: Any, *, contracts: int, path: Path | None, now: datetime | None) -> None:
    market = getattr(analysis, "market", None)
    venue = getattr(market, "venue", "kalshi")
    if isinstance(venue, str) and venue.strip().lower() not in {"", "kalshi"}:
        return
    ticker = str(getattr(market, "ticker", "") or "").strip()
    side = str(getattr(analysis, "side", "") or "").strip().lower()
    if not ticker or side not in {"yes", "no"}:
        return
    series = event_news_series_ticker(market)
    book = getattr(analysis, "_maker_taker_orderbook", None)
    queue = _queue(book, side) if isinstance(book, BinaryMarketBook) else {}
    ask = queue.get("best_ask_cents")
    maker = queue.get("ask_minus_1c")
    fee_type, multiplier = _series_terms(
        series,
        getattr(market, "fee_type", None),
        getattr(market, "fee_multiplier", None),
    )
    moment = now or datetime.now(timezone.utc)
    size = contracts if type(contracts) is int and contracts > 0 else None
    taker_fee = _fee(FeeRole.TAKER, ask, size, fee_type, multiplier, moment)
    maker_fee = _fee(FeeRole.MAKER, maker, size, fee_type, multiplier, moment)
    target = MAKER_TAKER_PAPER_PATH if path is None else path
    if _pytest_blocks(target):
        return
    _append_jsonl(
        target,
        {
            "ts": _stamp(moment),
            "ticker": ticker,
            "series": series,
            "side": side,
            "best_ask_cents": ask,
            "ask_minus_1c": maker,
            "resting_size_at_ask_minus_1c": queue.get("resting_size"),
            "queue_ahead": queue.get("queue_ahead"),
            "taker_fee": taker_fee,
            "maker_fee": maker_fee,
            "contracts": size,
            "fee_type": fee_type,
            "fee_multiplier": None if multiplier is None else format(multiplier, "f"),
        },
    )


def _queue(book: BinaryMarketBook, side: str) -> dict[str, Any]:
    """Ask, ask-1c, and the size already resting at ask-1c.

    Kalshi publishes bids. The ask is one dollar minus the best opposing
    bid. A new bid at ask-1c stands behind that resting size, so the
    resting size is the queue ahead. Both numbers come from this book.
    """
    opposing = book.no_bids if side == "yes" else book.yes_bids
    own = book.yes_bids if side == "yes" else book.no_bids
    if not opposing:
        return {}
    ask = _cents(Decimal("1") - opposing[0].price)
    if ask is None:
        return {}
    maker = ask - 1
    if not 1 <= maker <= 99:
        return {"best_ask_cents": ask, "ask_minus_1c": None, "resting_size": None, "queue_ahead": None}
    resting = Decimal("0")
    for level in own:
        if _cents(level.price) == maker:
            resting += level.quantity
    text = format(resting, "f")
    return {
        "best_ask_cents": ask,
        "ask_minus_1c": maker,
        "resting_size": text,
        "queue_ahead": text,
    }


def _series_terms(
    series: str,
    fee_type: object,
    fee_multiplier: object,
) -> tuple[str | None, Decimal | None]:
    explicit = _typed_terms(fee_type, fee_multiplier)
    if explicit is not None:
        return explicit
    name = str(series or "").strip().upper()
    if name in _QUADRATIC_ONE:
        return "quadratic", Decimal("1")
    pinned = _MAKER_FEE_SERIES.get(name)
    if pinned is not None:
        return "quadratic_with_maker_fees", pinned
    return None, None


def _typed_terms(fee_type: object, fee_multiplier: object) -> tuple[str, Decimal] | None:
    if not isinstance(fee_type, str) or not fee_type.strip():
        return None
    kind = fee_type.strip()
    if kind not in {"quadratic", "quadratic_with_maker_fees"}:
        return None
    multiplier = Decimal("1") if fee_multiplier is None else _decimal(fee_multiplier)
    if multiplier is None or multiplier < 0:
        return None
    return kind, multiplier


def _fee(
    role: FeeRole,
    price_cents: object,
    quantity: Decimal | int | None,
    fee_type: str | None,
    multiplier: Decimal | None,
    moment: datetime,
) -> str | None:
    amount = quantity if isinstance(quantity, Decimal) else _decimal(quantity)
    if type(price_cents) is not int or amount is None or amount <= 0:
        return None
    if fee_type is None or multiplier is None or not 1 <= price_cents <= 99:
        return None
    # quadratic_with_maker_fees keeps the published maker multiplier.
    # quadratic's published maker multiplier is 0. The coefficient stays 0.0175.
    if role is FeeRole.MAKER and fee_type == "quadratic":
        role_multiplier = Decimal("0")
    elif fee_type in {"quadratic", "quadratic_with_maker_fees"}:
        role_multiplier = multiplier
    else:
        return None
    when = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    price = Decimal(price_cents) / Decimal("100")
    quantity = amount
    try:
        quote = quote_fee(
            FeeContext(
                schedule_id=KALSHI_GENERAL_2026_07_07,
                role=role,
                quantity=quantity,
                price=price,
                signed_revenue=-(quantity * price),
                order_id="maker-taker-paper",
                accumulator=INITIAL_ORDER_FEE_ACCUMULATOR,
                multiplier=role_multiplier,
                coefficient=fee_coefficient_for(KALSHI_GENERAL_2026_07_07, role),
                account_precision=DIRECT_ACCOUNT_PRECISION,
                timestamp=when.astimezone(timezone.utc),
            )
        )
    except Exception:
        return None
    return format(quote.net_fee, "f")


def _score_one(
    row: Mapping[str, Any],
    trades: list[Mapping[str, Any]] | None,
    *,
    window_s: int,
    scout_no_bid: bool,
    get: Callable[[str], Mapping[str, Any]] | None,
) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "")
    side = str(row.get("side") or "").strip().lower()
    start = _parse_utc_stamp(row.get("ts"))
    maker = row.get("ask_minus_1c")
    ask = row.get("best_ask_cents")
    queue = _decimal(row.get("queue_ahead"))
    contracts = _contracts(row.get("contracts"))
    scout = bool(scout_no_bid) and side == "no"
    tape = trades
    if tape is None and start is not None and ticker:
        tape = fetch_public_trades(
            ticker,
            start,
            start + timedelta(seconds=window_s),
            get=get,
        )
    at_volume, through_volume = _volumes(
        tape or [],
        ticker=ticker,
        side=side,
        start=start,
        maker=maker if type(maker) is int else None,
        window_s=window_s,
        scout=scout,
    )
    qualifying = through_volume if scout else at_volume + through_volume
    filled = _filled(at_volume, through_volume, queue, contracts, scout=scout)
    fee_type, multiplier = _series_terms(
        str(row.get("series") or ""),
        row.get("fee_type"),
        row.get("fee_multiplier"),
    )
    delta = None
    if filled is not None and filled > 0 and start is not None and type(ask) is int and type(maker) is int:
        delta = _delta(ask, maker, filled, fee_type, multiplier, start)
    return {
        "ticker": ticker,
        "series": str(row.get("series") or ""),
        "side": side,
        "ts": row.get("ts"),
        "traded_at_or_through": qualifying > 0,
        "volume": format(qualifying, "f"),
        "queue_ahead": None if queue is None else format(queue, "f"),
        "volume_minus_queue": None if queue is None else format(qualifying - queue, "f"),
        "maker_filled": filled is None or filled > 0,
        "filled_contracts": None if filled is None else format(filled, "f"),
        "fee_net_delta": delta,
        "fill_rule": "scout_no_bid" if scout else "at_or_through",
    }


def _volumes(
    trades: Sequence[Mapping[str, Any]],
    *,
    ticker: str,
    side: str,
    start: datetime | None,
    maker: int | None,
    window_s: int,
    scout: bool,
) -> tuple[Decimal, Decimal]:
    at_volume, through_volume = Decimal("0"), Decimal("0")
    if start is None or maker is None or side not in {"yes", "no"}:
        return at_volume, through_volume
    window = timedelta(seconds=window_s)
    for trade in trades:
        if str(trade.get("ticker") or "") != ticker:
            continue
        moment = _parse_utc_stamp(trade.get("created_time") or trade.get("ts"))
        if moment is None or not (start < moment <= start + window):
            continue
        cents = _trade_cents(trade, side)
        count = _decimal(trade.get("count_fp", trade.get("count")))
        if cents is None or count is None or count <= 0:
            continue
        if cents < maker:
            through_volume += count
        elif cents == maker and not scout:
            at_volume += count
    return at_volume, through_volume


def _filled(
    at_volume: Decimal,
    through_volume: Decimal,
    queue: Decimal | None,
    contracts: Decimal | None,
    *,
    scout: bool,
) -> Decimal | None:
    """Contracts a new bid would have been filled for.

    A print strictly through the bid clears the level, queue included.
    A print at the bid fills only the size behind the queue. Scout ignores
    prints at the bid.
    """
    if through_volume > 0:
        return contracts
    if scout or queue is None or at_volume <= queue:
        return Decimal("0")
    room = at_volume - queue
    if contracts is None:
        return room
    return room if room < contracts else contracts


def _delta(
    ask: int,
    maker: int,
    filled: Decimal,
    fee_type: str | None,
    multiplier: Decimal | None,
    moment: datetime,
) -> str | None:
    """Taker all-in minus maker all-in for the same contracts.

    Positive means the maker fill would have cost less than taking the ask.
    """
    taker = _fee(FeeRole.TAKER, ask, filled, fee_type, multiplier, moment)
    maker_fee = _fee(FeeRole.MAKER, maker, filled, fee_type, multiplier, moment)
    if taker is None or maker_fee is None:
        return None
    taker_all_in = filled * Decimal(ask) / Decimal("100") + Decimal(taker)
    maker_all_in = filled * Decimal(maker) / Decimal("100") + Decimal(maker_fee)
    return format(taker_all_in - maker_all_in, "f")


def _trade_cents(trade: Mapping[str, Any], side: str) -> int | None:
    key = "yes_price_dollars" if side == "yes" else "no_price_dollars"
    cents = _cents(_decimal(trade.get(key)))
    if cents is not None:
        return cents
    other = _decimal(trade.get("yes_price_dollars" if side == "no" else "no_price_dollars"))
    if other is None:
        return None
    return _cents(Decimal("1") - other)


def _cents(price: Decimal | None) -> int | None:
    if price is None or not price.is_finite():
        return None
    scaled = price * Decimal("100")
    cents = int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
    if abs(scaled - cents) > Decimal("0.001") or not 1 <= cents <= 99:
        return None
    return cents


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    if not number.is_finite():
        return None
    return number


def _contracts(value: object) -> Decimal | None:
    number = _decimal(value)
    if number is None or number <= 0:
        return None
    return number


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _pytest_blocks(path: Path) -> bool:
    if "pytest" not in sys.modules:
        return False
    try:
        return path.resolve() == _RUNTIME_PAPER_PATH.resolve()
    except OSError:
        return False


def _public_get(url: str) -> Mapping[str, Any]:
    # Public trades. No Authorization header and no signed Kalshi request.
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("public trades response must be an object")
    return payload
