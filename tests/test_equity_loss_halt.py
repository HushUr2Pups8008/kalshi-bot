"""The default session-loss check uses equity. The flag-on halt stays off.

A buy that turns cash into a position at the same bid is not a $5 loss.
A real mark drop is. A bad bid does not seed and does not trade.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config as cfg_module
from tests.test_executor import _make_analysis, _make_executor
from utils.event_news_desk_flags import (
    EVENT_NEWS_EQUITY_LOSS_HALT,
    _DESK_FLAG_NAMES,
    event_news_equity_loss_halt_enabled,
)
from utils.event_news_research import EVENT_NEWS_COHORT_ID

import utils.event_news_session_halt as halt

_HALT = "LIVE HALTED: session loss limit reached -- all trading suspended"


def _desk_analysis(**kwargs):
    """A live-series 60c book clears the chain, so the halt check still runs."""
    kwargs.setdefault("ticker", "KXAPRPOTUS-26OCT17-HALT")
    kwargs.setdefault("yes_price", 60.0)
    analysis = _make_analysis(**kwargs)
    analysis.market.series_ticker = "KXAPRPOTUS"
    ask = analysis.market.yes_ask_cents
    analysis.market.last_price_cents = ask
    analysis.market.last_price = ask
    analysis.executed_price_cents = int(ask)
    return analysis


def _politics(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    monkeypatch.delenv(EVENT_NEWS_EQUITY_LOSS_HALT, raising=False)
    monkeypatch.setattr(halt, "HALT_LATCH_PATH", tmp_path / "halt.json")
    monkeypatch.setattr(halt, "CASH_CENTS_PATH", tmp_path / "cash.json")
    monkeypatch.setattr(halt, "OPERATOR_ALERT_PATH", tmp_path / "alert.json")


def _executor(monkeypatch, *, bankroll: float = 50.0):
    ex, rest, paper = _make_executor(
        monkeypatch, bankroll=bankroll, loss_limit_pct=0.10
    )
    paper._starting_bankroll = bankroll
    rest.get_open_positions.return_value = []
    return ex, rest, paper


def test_flag_defaults_off_and_is_not_a_desk_flag(monkeypatch) -> None:
    monkeypatch.delenv(EVENT_NEWS_EQUITY_LOSS_HALT, raising=False)
    assert EVENT_NEWS_EQUITY_LOSS_HALT not in _DESK_FLAG_NAMES
    assert event_news_equity_loss_halt_enabled() is False
    for raw in ("false", "0", "2", "yes", ""):
        assert event_news_equity_loss_halt_enabled({EVENT_NEWS_EQUITY_LOSS_HALT: raw}) is False
    assert event_news_equity_loss_halt_enabled({EVENT_NEWS_EQUITY_LOSS_HALT: "true"}) is True
    assert event_news_equity_loss_halt_enabled({EVENT_NEWS_EQUITY_LOSS_HALT: "1"}) is True


def _book(price: str, *, side: str = "yes", qty: str = "100"):
    level = SimpleNamespace(price=Decimal(price), quantity=Decimal(qty))
    empty: tuple = ()
    return SimpleNamespace(
        yes_bids=(level,) if side == "yes" else empty,
        no_bids=(level,) if side == "no" else empty,
    )


def _open(count: str, *, side: str = "yes") -> dict[str, str]:
    signed = count if side == "yes" else f"-{count}"
    return {"ticker": "KXTRUTHSOCIAL-26OCT24-T240", "position_fp": signed}


def _exit_mark(price: str, qty: str = "8") -> Decimal:
    from trading.venue_gateway import KalshiGateway

    quantity = Decimal(qty)
    bid = Decimal(price)
    return quantity * bid - KalshiGateway(None).fee(bid, quantity)


def test_flag_off_cash_drop_with_no_position_still_halts(monkeypatch, tmp_path, caplog):
    # No open position, so equity is cash. $50 to $42 on a $50 book is an $8 loss.
    _politics(monkeypatch, tmp_path)
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.side_effect = [50.0, 42.0]
    analysis = _desk_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._session_start_balance == pytest.approx(50.0)

    with caplog.at_level("CRITICAL"):
        reason = ex._validate(analysis)

    assert reason == _HALT
    assert ex._live_halted is True
    assert ex._loss_limit_halted is True
    callback.assert_called_once()
    assert "loss_limit_halt" in caplog.text
    latch = json.loads(halt.LOSS_LIMIT_LATCH_PATH.read_text(encoding="utf-8"))
    assert latch["halted"] is True
    assert latch["line"] == pytest.approx(5.0)
    assert not (tmp_path / "halt.json").exists()


def test_flat_8_at_61c_buy_does_not_trip(monkeypatch, tmp_path):
    # Cash falls by the 8x61c purchase plus the entry fee. The bid is unchanged.
    # The old cash check called that a loss over $5. Equity only loses the fees.
    _politics(monkeypatch, tmp_path)
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    entry_fee = Decimal("8") * Decimal("0.61") - _exit_mark("0.61")
    cash_after = Decimal("50") - Decimal("8") * Decimal("0.61") - entry_fee
    assert Decimal("50") - cash_after >= Decimal("5.01")
    mark = _exit_mark("0.61")
    assert Decimal("50") - (cash_after + mark) < Decimal("5")
    rest.get_balance.side_effect = [50.0, float(cash_after)]
    rest.get_open_positions.side_effect = [[], [_open("8.00")]]
    rest.get_market_orderbook.return_value = _book("0.61", qty="8")
    analysis = _desk_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._session_start_balance == pytest.approx(50.0)
    assert ex._validate(analysis) is None

    assert ex._live_halted is False
    callback.assert_not_called()
    assert not halt.LOSS_LIMIT_LATCH_PATH.exists()


def test_no_position_uses_the_no_bid(monkeypatch, tmp_path):
    # An 8-NO buy at a flat 61c no-bid is the same equity shape as the YES buy.
    _politics(monkeypatch, tmp_path)
    ex, rest, _paper = _executor(monkeypatch)
    entry_fee = Decimal("8") * Decimal("0.61") - _exit_mark("0.61")
    cash_after = Decimal("50") - Decimal("8") * Decimal("0.61") - entry_fee
    rest.get_balance.side_effect = [50.0, float(cash_after)]
    rest.get_open_positions.side_effect = [[], [_open("8.00", side="no")]]
    rest.get_market_orderbook.return_value = _book("0.61", side="no", qty="8")
    analysis = _desk_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._validate(analysis) is None
    assert ex._live_halted is False


def test_mark_drop_of_5_01_trips_loss_limit(monkeypatch, tmp_path, caplog):
    # Cash stays $40. The bid falls. The limit is $5 of the $50 bankroll, not $10.
    _politics(monkeypatch, tmp_path)
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    high = _exit_mark("0.90")
    mid = _exit_mark("0.30")
    low = _exit_mark("0.03")
    assert high - mid < Decimal("5")
    assert high - low >= Decimal("5.01")
    rest.get_balance.return_value = 40.0
    rest.get_open_positions.return_value = [_open("8.00")]
    rest.get_market_orderbook.side_effect = [
        _book("0.90"),
        _book("0.30"),
        _book("0.03"),
    ]
    analysis = _desk_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._validate(analysis) is None
    with caplog.at_level("CRITICAL"):
        reason = ex._validate(analysis)

    assert reason == _HALT
    assert ex._loss_limit_halted is True
    callback.assert_called_once()
    assert "loss_limit_halt" in caplog.text
    assert "limit $5.00" in caplog.text
    latch = json.loads(halt.LOSS_LIMIT_LATCH_PATH.read_text(encoding="utf-8"))
    assert latch["halted"] is True
    assert latch["loss"] == pytest.approx(float(high - low))
    assert not (tmp_path / "halt.json").exists()


def test_unreadable_mark_fails_closed_and_does_not_seed(monkeypatch, tmp_path):
    _politics(monkeypatch, tmp_path)
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.return_value = 44.99
    rest.get_open_positions.return_value = [_open("8.00")]
    analysis = _desk_analysis(capped_dollars=2.0)

    rest.get_market_orderbook.side_effect = RuntimeError("book down")
    assert ex._validate(analysis) == "live equity unreadable"
    assert ex._session_start_balance is None
    assert ex._live_halted is False

    rest.get_market_orderbook.side_effect = None
    rest.get_market_orderbook.return_value = SimpleNamespace(yes_bids=(), no_bids=())
    assert ex._validate(analysis) == "live equity unreadable"
    assert ex._session_start_balance is None

    rest.get_open_positions.return_value = [
        {"ticker": "KXTRUTHSOCIAL-26OCT24-T240"},
    ]
    assert ex._validate(analysis) == "live equity unreadable"
    assert ex._session_start_balance is None
    callback.assert_not_called()
    assert not halt.LOSS_LIMIT_LATCH_PATH.exists()


def test_buy_that_keeps_equity_does_not_trip(monkeypatch, tmp_path):
    # $50 cash becomes $42 cash + $8 mark. Equity stays $50. Line is $5.
    _politics(monkeypatch, tmp_path)
    monkeypatch.setenv(EVENT_NEWS_EQUITY_LOSS_HALT, "true")
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.side_effect = [50.0, 42.0]
    rest._request.side_effect = [
        {"balance": 5000, "portfolio_value": 0},
        {"balance": 4200, "portfolio_value": 800},
    ]
    analysis = _desk_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._equity_halt_seed == pytest.approx(50.0)
    assert ex._session_start_balance is None
    assert ex._validate(analysis) is None

    assert ex._live_halted is False
    callback.assert_not_called()
    assert not (tmp_path / "halt.json").exists()


def test_mark_drop_trips_without_shutdown_or_latch(monkeypatch, tmp_path):
    # Seed $50. Mark falls from $8 to $0. Loss $8 is past the $5 line.
    _politics(monkeypatch, tmp_path)
    monkeypatch.setenv(EVENT_NEWS_EQUITY_LOSS_HALT, "true")
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.return_value = 42.0
    rest._request.side_effect = [
        {"balance": 4200, "portfolio_value": 800},
        {"balance": 4200, "portfolio_value": 0},
    ]
    analysis = _desk_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    reason = ex._validate(analysis)

    assert reason == _HALT
    assert ex._live_halted is True
    callback.assert_not_called()
    assert not (tmp_path / "halt.json").exists()
    assert ex._validate(analysis) == _HALT


def test_bad_equity_read_fails_closed_and_does_not_seed(monkeypatch, tmp_path):
    _politics(monkeypatch, tmp_path)
    monkeypatch.setenv(EVENT_NEWS_EQUITY_LOSS_HALT, "true")
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.return_value = 42.0
    analysis = _desk_analysis(capped_dollars=2.0)

    rest._request.side_effect = RuntimeError("balance down")
    assert ex._validate(analysis) == "live equity unreadable"
    assert ex._equity_halt_seed is None
    assert ex._live_halted is False

    rest._request.side_effect = None
    rest._request.return_value = {"balance": 4200}
    assert ex._validate(analysis) == "live equity unreadable"
    assert ex._equity_halt_seed is None

    rest._request.return_value = {"balance": True, "portfolio_value": 800}
    assert ex._validate(analysis) == "live equity unreadable"
    assert ex._equity_halt_seed is None

    rest._request.return_value = {"balance": 4200, "portfolio_value": 800}
    assert ex._validate(analysis) is None
    assert ex._equity_halt_seed == pytest.approx(50.0)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("later read failed")

    rest._request.side_effect = _boom
    assert ex._validate(analysis) == "live equity unreadable"
    assert ex._equity_halt_seed == pytest.approx(50.0)
    assert ex._live_halted is False
    callback.assert_not_called()
    assert not (tmp_path / "halt.json").exists()


def test_unreadable_sizing_bankroll_fails_closed(monkeypatch, tmp_path):
    _politics(monkeypatch, tmp_path)
    monkeypatch.setenv(EVENT_NEWS_EQUITY_LOSS_HALT, "true")
    ex, rest, paper = _executor(monkeypatch)
    paper._starting_bankroll = None
    rest.get_balance.return_value = 42.0
    rest._request.return_value = {"balance": 4200, "portfolio_value": 800}

    assert ex._validate(_desk_analysis(capped_dollars=2.0)) == "live equity unreadable"
    assert ex._equity_halt_seed is None
    assert ex._live_halted is False
    assert not (tmp_path / "halt.json").exists()


def test_loss_limit_shutdown_is_nonzero_and_not_a_clean_stop(monkeypatch, tmp_path, caplog):
    import main

    monkeypatch.setattr(halt, "OPERATOR_ALERT_PATH", tmp_path / "alert.json")
    bot = SimpleNamespace(executor=SimpleNamespace(_loss_limit_halted=True))

    try:
        with caplog.at_level("CRITICAL"):
            main._record_shutdown(bot)
        assert main._PROCESS_EXIT_CODE == 1
        assert "loss_limit_halt" in caplog.text
        assert not (tmp_path / "alert.json").exists()
    finally:
        main._PROCESS_EXIT_CODE = 0


def test_start_script_refuses_restart_after_loss_limit_halt() -> None:
    script = Path("scripts/start_politics_paper.sh").read_text(encoding="utf-8")
    check = script.split("exec /usr/bin/caffeinate", 1)[0]
    assert "LOSS_LIMIT_LATCH_PATH" in check
    assert 'echo "loss_limit_halt latched; not restarting"' in check
    assert "exit 0" in check


def test_flag_on_legacy_cohort_still_uses_the_equity_halt(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", "legacy")
    monkeypatch.setenv(EVENT_NEWS_EQUITY_LOSS_HALT, "true")
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    monkeypatch.setattr(halt, "HALT_LATCH_PATH", tmp_path / "halt.json")
    monkeypatch.setattr(halt, "CASH_CENTS_PATH", tmp_path / "cash.json")
    monkeypatch.setattr(halt, "OPERATOR_ALERT_PATH", tmp_path / "alert.json")
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.return_value = 42.0
    rest._request.side_effect = [
        {"balance": 4200, "portfolio_value": 800},
        {"balance": 4200, "portfolio_value": 0},
    ]
    analysis = _desk_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._validate(analysis) == _HALT
    callback.assert_not_called()
    assert not (tmp_path / "halt.json").exists()
    assert ex._equity_halt_seed == pytest.approx(50.0)
