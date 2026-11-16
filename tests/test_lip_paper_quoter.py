"""Paper LIP quoter: public reads, queue-adjusted fills, and the published reward."""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import lip_paper_quoter as lip
from utils.countable_series_shadow import COUNTABLE

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc)
TICKER = "KXTRUTHSOCIAL-26NOV21-T40"
ORDER_MARKERS = (
    "/portfolio/orders",
    "/portfolio/events/orders",
    "place_limit_order",
    "cancel_order",
    "amend_order",
    "KalshiRestClient",
    "politics_loss_limit_halt.json",
)


def _project_imports(start: Path) -> list[Path]:
    pending = [start]
    seen: set[Path] = set()
    found: list[Path] = []
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        found.append(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        for name in modules:
            parts = name.split(".")
            file_path = ROOT.joinpath(*parts).with_suffix(".py")
            package = ROOT.joinpath(*parts) / "__init__.py"
            if file_path.is_file():
                pending.append(file_path)
            elif package.is_file():
                pending.append(package)
    return found


def test_quoter_cannot_reach_an_order_endpoint() -> None:
    start = ROOT / "scripts" / "lip_paper_quoter.py"
    files = _project_imports(start)
    names = {path.relative_to(ROOT).as_posix() for path in files}
    assert "scripts/lip_paper_quoter.py" in names
    assert "kalshi/rest_client.py" not in names
    assert "trading/executor.py" not in names
    blob = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for marker in ORDER_MARKERS:
        assert marker not in blob
    tree = ast.parse(start.read_text(encoding="utf-8"))
    methods = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Request"
    ]
    assert methods
    for call in methods:
        assert any(keyword.arg == "method" and getattr(keyword.value, "value", None) == "GET" for keyword in call.keywords)


def test_order_paths_never_open_a_socket() -> None:
    def opener(*_args, **_kwargs):
        raise AssertionError("network")

    for path in (
        "/trade-api/v2/portfolio/orders",
        "/trade-api/v2/portfolio/events/orders",
        "/trade-api/v2/portfolio/orders/abc",
        "/trade-api/v2/markets/KXTEST/order",
    ):
        with pytest.raises(lip.PublicPathError):
            lip.public_get(path, opener=opener)


def test_public_get_is_a_get_without_a_body() -> None:
    seen: dict[str, object] = {}

    class Response:
        def __init__(self, request):
            self.request = request

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return self.request.full_url

        def read(self):
            return b'{"incentive_programs":[]}'

    def opener(request, timeout=0):
        seen["method"] = request.get_method()
        seen["data"] = request.data
        seen["headers"] = {key.lower() for key, _value in request.header_items()}
        seen["timeout"] = timeout
        return Response(request)

    payload = lip.public_get("/trade-api/v2/incentive_programs", {"type": "liquidity"}, opener=opener)
    assert payload == {"incentive_programs": []}
    assert seen["method"] == "GET"
    assert seen["data"] is None
    assert "kalshi-access-key" not in seen["headers"]
    assert "authorization" not in seen["headers"]


def test_help_center_worked_example_and_the_dollar_floor() -> None:
    """20% of $100 with 8,000 of 10,000 snapshots qualifying is $16.00."""
    assert lip.liquidity_payout(Decimal("0.20"), Decimal("100"), 8000, 10000) == Decimal("16.00")
    assert lip.liquidity_payout(Decimal("0.009"), Decimal("100"), 1, 1) == Decimal("0.00")
    assert lip.liquidity_payout(Decimal("0.999"), Decimal("1"), 1, 1) == Decimal("0.00")
    assert lip.liquidity_payout(Decimal("1.009"), Decimal("1"), 1, 1) == Decimal("1.00")
    assert lip.liquidity_payout(Decimal("16.666"), Decimal("1"), 1, 1) == Decimal("16.66")


def test_reference_price_is_one_fifth_of_target_and_distance_uses_the_discount() -> None:
    levels = lip.with_our_bid(((50, Decimal(100)), (49, Decimal(150)), (48, Decimal(780))), 48, 20)
    scored = lip.score_side(levels, Decimal(1000), Decimal("0.5"))
    assert scored is not None
    assert scored.reference_cents == 49
    assert scored.our_size == Decimal(20)
    assert scored.our_share == Decimal(10) / Decimal(650)


def test_a_side_short_of_the_target_scores_nothing() -> None:
    levels = lip.with_our_bid(((50, Decimal(10)),), 49, 20)
    assert lip.score_side(levels, Decimal(1000), Decimal("0.5")) is None
    snapshot = lip.score_snapshot(levels, levels, Decimal(1000), Decimal("0.5"))
    assert snapshot.excluded is True
    assert snapshot.participant_score == 0


def test_quotes_one_or_two_ticks_behind_fair_value() -> None:
    assert lip.resting_prices(Decimal("0.40"), 1) == (39, 59)
    assert lip.resting_prices(Decimal("0.40"), 2) == (38, 58)
    assert lip.resting_prices(Decimal("0.995"), 1) is None


def _trade(trade_id: str, *, created: datetime, yes: int, book: str, count: str, block: bool = False) -> lip.TradePrint:
    return lip.TradePrint(
        trade_id=trade_id,
        ticker=TICKER,
        created=created,
        yes_cents=yes,
        no_cents=100 - yes,
        count=Decimal(count),
        taker_book_side=book,
        block=block,
    )


def _quote(
    side: str,
    price: int,
    *,
    ahead: str = "0",
    better: str = "0",
    placed: datetime | None = None,
    program_id: str = "",
) -> lip.Resting:
    return lip.Resting(
        ticker=TICKER,
        side=side,
        price_cents=price,
        remaining=Decimal(20),
        queue_ahead=Decimal(ahead),
        size_better=Decimal(better),
        placed_at=placed or NOW - timedelta(seconds=30),
        order_id=f"lip-paper-{TICKER}-{side}",
        fee_accumulator=Decimal("0"),
        program_id=program_id,
    )


def test_fills_are_queue_adjusted_and_ignore_block_prints() -> None:
    quote = _quote("yes", 40, ahead="10", better="5")
    placed = quote.placed_at
    trades = [
        _trade("better", created=placed + timedelta(seconds=1), yes=41, book="ask", count="4"),
        _trade("at", created=placed + timedelta(seconds=2), yes=40, book="ask", count="15"),
        _trade("block", created=placed + timedelta(seconds=3), yes=40, book="ask", count="20", block=True),
        _trade("other", created=placed + timedelta(seconds=4), yes=40, book="bid", count="20"),
        _trade("through", created=placed + timedelta(seconds=5), yes=39, book="ask", count="30"),
    ]
    updated, fills, _fee, considered = lip.apply_trades(quote, trades, now=NOW)
    assert [fill.quantity for fill in fills] == [Decimal(5), Decimal(15)]
    assert updated.remaining == 0
    assert updated.queue_ahead == 0
    assert updated.size_better == 0
    assert "block" in considered and "other" in considered
    fee, _accumulator = lip.maker_fee(
        price_cents=40,
        quantity=Decimal(5),
        accumulator=Decimal("0"),
        order_id=quote.order_id,
        now=placed + timedelta(seconds=2),
    )
    assert fills[0].fee == fee


def test_both_resting_sides_can_fill_from_the_same_public_tape() -> None:
    placed = NOW - timedelta(seconds=30)
    state = lip.PaperState(
        quotes=(_quote("yes", 40, placed=placed), _quote("no", 55, placed=placed)),
        fills=(),
        snapshots=(),
        rewards=(),
        settled_pnl=Decimal("0"),
        fees=Decimal("0"),
        seen_trades=(),
    )
    trades = [
        _trade("hit-yes", created=placed + timedelta(seconds=5), yes=40, book="ask", count="5"),
        _trade("hit-no", created=placed + timedelta(seconds=6), yes=45, book="bid", count="4"),
    ]
    report = lip.run_cycle(
        now=NOW,
        programs=(),
        markets={TICKER: _market()},
        books={TICKER: lip.Book(yes=((40, Decimal(1)),), no=((55, Decimal(1)),))},
        trades={TICKER: trades},
        probabilities={},
        state=state,
        contracts=20,
        behind=2,
        max_markets=10,
        series=lip.DEFAULT_SERIES,
    )
    text = "\n".join(report.lines)
    assert "side=yes qty=5" in text
    assert "side=no qty=4" in text
    assert "net pending_settlement = lip_sample_payout + settlement_pnl - fees" in text
    assert {fill.side: fill.quantity for fill in report.state.fills} == {"yes": Decimal(5), "no": Decimal(4)}


def _market(status: str = "active", result: str = "") -> lip.MarketView:
    return lip.MarketView(
        ticker=TICKER,
        series="KXTRUTHSOCIAL",
        status=status,
        result=result,
        price_level_structure="linear_cent",
        rules_primary=(
            "If the number of Truth Social posts by Donald Trump from Nov 15, 2026 "
            "to Nov 21, 2026 is below 40, then the market resolves to Yes."
        ),
        strike_type="less",
    )


def _program(ticker: str = TICKER, *, start: datetime | None = None, reward: str = "160.00") -> lip.Incentive:
    return lip.Incentive(
        program_id="program-1",
        ticker=ticker,
        series=ticker.split("-", 1)[0],
        start=start or NOW - timedelta(hours=1),
        end=NOW + timedelta(hours=12),
        reward=Decimal(reward),
        target=Decimal(1000),
        discount=Decimal("0.5"),
    )


def test_low_competition_quote_uses_the_published_payout() -> None:
    book = lip.Book(yes=((3, Decimal(1402)),), no=((86, Decimal(1045)),))
    report = lip.run_cycle(
        now=NOW,
        programs=(_program(),),
        markets={TICKER: _market()},
        books={TICKER: book},
        trades={},
        probabilities={TICKER: (Decimal("0.10"), None)},
        state=lip.PaperState.empty(),
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    text = "\n".join(report.lines)
    assert "quote KXTRUTHSOCIAL-26NOV21-T40 fair=0.10 yes_bid=8 no_bid=88 size=20" in text
    our = Decimal(20) / Decimal(1422) + Decimal(20) / Decimal(1065)
    expected = lip.liquidity_payout(our / Decimal(2), Decimal("160.00"), 1, 1)
    assert f"payout {TICKER} sample={expected}" in text
    assert f"net {expected} = lip_sample_payout + settlement_pnl - fees" in text


def test_depth_already_past_the_target_is_not_quoted() -> None:
    book = lip.Book(yes=((50, Decimal(1000)),), no=((50, Decimal(1000)),))
    report = lip.run_cycle(
        now=NOW,
        programs=(_program(),),
        markets={TICKER: _market()},
        books={TICKER: book},
        trades={},
        probabilities={TICKER: (Decimal("0.50"), None)},
        state=lip.PaperState.empty(),
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    assert any(line.startswith(f"skip {TICKER} reason=not_low_competition") for line in report.lines)
    assert report.state.quotes == ()


def test_a_series_without_the_count_model_is_skipped(tmp_path: Path) -> None:
    def fetch(_url: str) -> str:
        raise AssertionError("fetch")

    found = lip.count_model_probabilities(
        [{"ticker": "KXOTHER-1", "series_ticker": "KXOTHER", "rules_primary": "no model"}],
        now=NOW,
        path=tmp_path / "count.jsonl",
        fetch=fetch,
        sleeper=lambda _seconds: None,
    )
    assert found == {"KXOTHER-1": (None, "no_count_model")}
    report = lip.run_cycle(
        now=NOW,
        programs=(_program("KXOTHER-1"),),
        markets={},
        books={},
        trades={},
        probabilities=found,
        state=lip.PaperState.empty(),
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXOTHER",),
    )
    assert any("reason=no_count_model" in line for line in report.lines)


def test_fair_value_is_the_existing_count_model(tmp_path: Path) -> None:
    def one(stamp: str) -> dict[str, object]:
        return {
            "date": stamp,
            "handle": "realDonaldTrump",
            "platform": "Truth Social",
            "deleted_flag": False,
            "type": "Social",
        }

    counts = (("11", 1), ("12", 8), ("13", 1), ("14", 8), ("15", 1), ("16", 8), ("17", 1))
    rows = [one(f"2026-10-{day}T15:00:00+00:00") for day, count in counts for _ in range(count)]
    rows.append(one("2026-08-01T15:00:00+00:00"))

    def fetch(url: str) -> str:
        assert "rollcall.com" in url
        return json.dumps({"data": rows})

    market = {
        "ticker": "KXTRUTHSOCIAL-26OCT24-T240",
        "series_ticker": "KXTRUTHSOCIAL",
        "rules_primary": (
            "If the number of Truth Social posts by Donald Trump from Oct 18, 2026 "
            "to Oct 24, 2026 is above 240, then the market resolves to Yes."
        ),
        "strike_type": "greater",
    }
    path = tmp_path / "count.jsonl"
    found = lip.count_model_probabilities(
        [market],
        now=datetime(2026, 10, 20, 16, tzinfo=timezone.utc),
        path=path,
        fetch=fetch,
        sleeper=lambda _seconds: None,
    )
    probability, reason = found["KXTRUTHSOCIAL-26OCT24-T240"]
    assert reason is None
    row = json.loads(path.read_text(encoding="utf-8"))
    assert probability == Decimal(str(row["probability"]))
    assert row["model"] == "nb"


def test_default_series_are_covered_by_the_count_model() -> None:
    assert set(lip.DEFAULT_SERIES) <= set(COUNTABLE)
    assert "KXEOWEEK" in COUNTABLE
    assert "KXEOWEEK" not in lip.DEFAULT_SERIES
    assert lip.OUTPUT_DIR == Path("logs/state/lip_paper")


def test_incentive_reward_is_centi_cents() -> None:
    program = lip.parse_incentive(
        {
            "id": "abc",
            "incentive_type": "liquidity",
            "market_ticker": TICKER,
            "series_ticker": "KXTRUTHSOCIAL",
            "start_date": "2026-11-15T15:00:00Z",
            "end_date": "2026-11-17T05:00:00Z",
            "period_reward": 1600000,
            "discount_factor_bps": 5000,
            "target_size_fp": "1000.00",
        }
    )
    assert program.reward == Decimal("160.00")
    assert program.target == Decimal("1000.00")
    assert program.discount == Decimal("0.5")


def test_settlement_pnl_is_separate_from_fees() -> None:
    fills = (
        lip.PaperFill(TICKER, "yes", 40, Decimal(10), Decimal("0.25"), NOW),
        lip.PaperFill(TICKER, "no", 55, Decimal(4), Decimal("0.10"), NOW),
    )
    assert lip.settlement_pnl(fills, "yes") == Decimal(10) * (Decimal(1) - Decimal("0.40")) + Decimal(4) * (Decimal(0) - Decimal("0.55"))
    assert lip.settlement_pnl(fills, "no") == Decimal(10) * (Decimal(0) - Decimal("0.40")) + Decimal(4) * (Decimal(1) - Decimal("0.55"))


def test_state_round_trip(tmp_path: Path) -> None:
    quote = _quote("yes", 8)
    state = lip.PaperState(
        quotes=(quote,),
        fills=(),
        snapshots=(("program-1", TICKER, lip.Snapshot(False, Decimal("0.016422"), Decimal(2), 3, 86)),),
        rewards=((TICKER, Decimal("160.00")),),
        tracked=(lip.TrackedProgram("program-1", TICKER, Decimal("160.00"), None, NOW),),
        settled_pnl=Decimal("0"),
        fees=Decimal("0.01"),
        seen_trades=("trade-1",),
    )
    path = tmp_path / "state.json"
    lip.save_state(path, state)
    loaded = lip.load_state(path)
    assert loaded == state


def test_live_flag_refuses_before_any_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    assert lip.main([]) == 2


def test_cli_rejects_size_outside_the_paper_band() -> None:
    with pytest.raises(SystemExit):
        lip.parse_args(["--contracts", "8", "--once"])
    with pytest.raises(SystemExit):
        lip.parse_args(["--contracts", "51", "--once"])
    with pytest.raises(SystemExit):
        lip.parse_args(["--behind", "3", "--once"])
    with pytest.raises(SystemExit):
        lip.parse_args(["--series", "KXOTHER", "--once"])
    args = lip.parse_args(["--once", "--contracts", "20", "--behind", "2"])
    assert args.series_list == lip.DEFAULT_SERIES


def test_window_that_has_not_started_is_not_quoted() -> None:
    program = _program(start=NOW + timedelta(hours=1))
    report = lip.run_cycle(
        now=NOW,
        programs=(program,),
        markets={TICKER: _market(status="initialized")},
        books={},
        trades={},
        probabilities={},
        state=lip.PaperState.empty(),
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    assert any("reason=window_not_started" in line for line in report.lines)
    assert report.state.quotes == ()


def test_centi_cent_reward_is_not_rounded_before_the_dollar_rule() -> None:
    reward = lip.dollars_from_centi_cents(10090)
    assert lip.liquidity_payout(Decimal("0.9905"), reward, 1, 1) == Decimal("0.00")
    assert lip.liquidity_payout(Decimal(1), reward, 1, 1) == Decimal("1.00")


def test_an_at_price_print_clears_stale_size_ahead_of_a_later_through_print() -> None:
    quote = _quote("yes", 40, ahead="0", better="5")
    placed = quote.placed_at
    trades = [
        _trade("at", created=placed + timedelta(seconds=1), yes=40, book="ask", count="10"),
        _trade("through", created=placed + timedelta(seconds=2), yes=39, book="ask", count="4"),
    ]
    updated, fills, _fee, _considered = lip.apply_trades(quote, trades, now=NOW)
    assert [fill.quantity for fill in fills] == [Decimal(10), Decimal(4)]
    assert updated.size_better == 0
    assert updated.remaining == Decimal(6)


def test_a_quote_through_the_opposite_touch_is_not_rested() -> None:
    book = lip.Book(yes=((70, Decimal(1000)),), no=((30, Decimal(1000)),))
    report = lip.run_cycle(
        now=NOW,
        programs=(_program(),),
        markets={TICKER: _market()},
        books={TICKER: book},
        trades={},
        probabilities={TICKER: (Decimal("0.80"), None)},
        state=lip.PaperState.empty(),
        contracts=20,
        behind=1,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    assert any("reason=crosses" in line for line in report.lines)
    assert report.state.quotes == ()


def test_an_excluded_second_reduces_the_sample_payout() -> None:
    wide = lip.Book(yes=((3, Decimal(1402)),), no=((86, Decimal(1045)),))
    first = lip.run_cycle(
        now=NOW,
        programs=(_program(),),
        markets={TICKER: _market()},
        books={TICKER: wide},
        trades={},
        probabilities={TICKER: (Decimal("0.10"), None)},
        state=lip.PaperState.empty(),
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    our = Decimal(20) / Decimal(1422) + Decimal(20) / Decimal(1065)
    full = lip.liquidity_payout(our / Decimal(2), Decimal("160.00"), 1, 1)
    assert f"payout {TICKER} sample={full} snapshots=1" in "\n".join(first.lines)
    empty = lip.Book(yes=(), no=())
    second = lip.run_cycle(
        now=NOW + timedelta(seconds=60),
        programs=(_program(),),
        markets={TICKER: _market()},
        books={TICKER: empty},
        trades={},
        probabilities={TICKER: (Decimal("0.10"), None)},
        state=first.state,
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    halved = lip.liquidity_payout(our / Decimal(2), Decimal("160.00"), 1, 2)
    assert f"payout {TICKER} sample={halved} snapshots=2" in "\n".join(second.lines)


def test_a_later_program_does_not_reprice_the_earlier_window() -> None:
    wide = lip.Book(yes=((3, Decimal(1402)),), no=((86, Decimal(1045)),))
    first = lip.run_cycle(
        now=NOW,
        programs=(_program(),),
        markets={TICKER: _market()},
        books={TICKER: wide},
        trades={},
        probabilities={TICKER: (Decimal("0.10"), None)},
        state=lip.PaperState.empty(),
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    our = Decimal(20) / Decimal(1422) + Decimal(20) / Decimal(1065)
    original = lip.liquidity_payout(our / Decimal(2), Decimal("160"), 1, 1)
    later = lip.Incentive(
        program_id="program-2",
        ticker=TICKER,
        series="KXTRUTHSOCIAL",
        start=NOW + timedelta(days=3),
        end=NOW + timedelta(days=4),
        reward=Decimal("100000"),
        target=Decimal(1000),
        discount=Decimal("0.5"),
    )
    # The later window has not started, so it must not touch the open sample.
    second = lip.run_cycle(
        now=NOW + timedelta(seconds=60),
        programs=(_program(), later),
        markets={TICKER: _market()},
        books={TICKER: wide},
        trades={},
        probabilities={TICKER: (Decimal("0.10"), None)},
        state=first.state,
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    continued = lip.liquidity_payout(our / Decimal(2), Decimal("160"), 2, 2)
    assert f"payout {TICKER} sample={continued} snapshots=2" in "\n".join(second.lines)
    assert all("100000" not in line or "window_not_started" in line for line in second.lines)


def test_the_same_print_does_not_fill_a_retained_quote_twice() -> None:
    placed = NOW - timedelta(seconds=30)
    quote = _quote("yes", 40, placed=placed)
    state = lip.PaperState(
        quotes=(quote,),
        fills=(),
        snapshots=(),
        rewards=(),
        settled_pnl=Decimal("0"),
        fees=Decimal("0"),
        seen_trades=(),
    )
    trade = _trade("once", created=placed + timedelta(seconds=5), yes=40, book="ask", count="5")
    kwargs = dict(
        programs=(_program(),),
        markets={TICKER: _market()},
        books={},
        trades={TICKER: (trade,)},
        probabilities={TICKER: (Decimal("0.40"), None)},
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    first = lip.run_cycle(now=NOW, state=state, **kwargs)
    assert sum(fill.quantity for fill in first.state.fills) == Decimal(5)
    second = lip.run_cycle(now=NOW + timedelta(seconds=30), state=first.state, **kwargs)
    assert sum((fill.quantity for fill in second.state.fills), Decimal(0)) == Decimal(5)


def test_a_finished_program_drops_the_quote_and_settles() -> None:
    placed = NOW - timedelta(hours=2)
    quote = _quote("yes", 40, placed=placed, program_id="old")
    fill = lip.PaperFill(TICKER, "yes", 40, Decimal(5), Decimal("0.01"), placed + timedelta(minutes=1))
    state = lip.PaperState(
        quotes=(quote,),
        fills=(fill,),
        snapshots=(),
        rewards=(),
        settled_pnl=Decimal("0"),
        fees=Decimal("0.01"),
        seen_trades=(),
        tracked=(lip.TrackedProgram("old", TICKER, Decimal("160"), None, NOW - timedelta(hours=1)),),
    )
    late = _trade("late", created=NOW - timedelta(minutes=5), yes=40, book="ask", count="8")
    report = lip.run_cycle(
        now=NOW,
        programs=(),
        markets={TICKER: _market(result="yes")},
        books={},
        trades={TICKER: (late,)},
        probabilities={},
        state=state,
        contracts=20,
        behind=2,
        max_markets=10,
        series=("KXTRUTHSOCIAL",),
    )
    assert report.state.quotes == ()
    assert report.state.fills == ()
    assert any(line.startswith(f"settle {TICKER} result=yes") for line in report.lines)
    assert not any(line.startswith("fill ") for line in report.lines)


def test_a_bad_incentive_row_does_not_drop_the_rest() -> None:
    def get_json(path, params):
        assert path == "/trade-api/v2/incentive_programs"
        if params["status"] == "upcoming":
            return {"incentive_programs": [], "next_cursor": ""}
        return {
            "incentive_programs": [
                {"incentive_type": "liquidity", "market_ticker": "NOT A TICKER"},
                {
                    "id": "ok",
                    "incentive_type": "liquidity",
                    "market_ticker": TICKER,
                    "series_ticker": "KXTRUTHSOCIAL",
                    "start_date": "2026-11-15T15:00:00Z",
                    "end_date": "2026-11-17T05:00:00Z",
                    "period_reward": 1600000,
                    "discount_factor_bps": 5000,
                    "target_size_fp": "1000.00",
                },
            ],
            "next_cursor": "",
        }

    found, notes = lip.fetch_programs(get_json, ("KXTRUTHSOCIAL",))
    assert [item.ticker for item in found] == [TICKER]
    assert notes and "NOT A TICKER" in notes[0]
