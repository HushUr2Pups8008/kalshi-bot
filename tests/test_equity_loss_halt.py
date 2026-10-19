"""Equity loss halt stays off unless the flag is on.

A buy that turns cash into a position at the same mark is not a loss.
A mark drop is. A bad read does not seed and does not trade.
"""

from __future__ import annotations

from pathlib import Path
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


def test_flag_off_cash_drop_still_halts_and_shuts_down(monkeypatch, tmp_path, caplog):
    # Current live behavior: cash $50 to $42 on a $50 bankroll trips the 10% cash line.
    _politics(monkeypatch, tmp_path)
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.side_effect = [50.0, 42.0]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("shadow read failed")

    rest._request.side_effect = _boom
    analysis = _desk_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._session_start_balance == pytest.approx(50.0)
    assert ex._equity_halt_seed is None

    with caplog.at_level("INFO"):
        reason = ex._validate(analysis)

    assert reason == _HALT
    assert ex._live_halted is True
    callback.assert_called_once()
    assert ex._equity_halt_seed is None
    assert not (tmp_path / "halt.json").exists()
    assert "shadow old=halt new=unreadable" in caplog.text


def test_flag_off_mark_drop_does_not_halt(monkeypatch, tmp_path, caplog):
    # Cash stays $50, so the live check passes. The shadow sees the mark drop.
    _politics(monkeypatch, tmp_path)
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.return_value = 50.0
    rest._request.side_effect = [
        {"balance": 5000, "portfolio_value": 0},
        {"balance": 4200, "portfolio_value": 0},
    ]
    analysis = _desk_analysis(capped_dollars=2.0)

    with caplog.at_level("INFO"):
        assert ex._validate(analysis) is None
        assert ex._validate(analysis) is None

    assert ex._live_halted is False
    callback.assert_not_called()
    assert "shadow old=pass new=seed" in caplog.text
    assert "shadow old=pass new=halt" in caplog.text
    assert not (tmp_path / "halt.json").exists()


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


def test_flag_on_outside_politics_keeps_the_cash_halt(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", "legacy")
    monkeypatch.setenv(EVENT_NEWS_EQUITY_LOSS_HALT, "true")
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    monkeypatch.setattr(halt, "HALT_LATCH_PATH", tmp_path / "halt.json")
    monkeypatch.setattr(halt, "CASH_CENTS_PATH", tmp_path / "cash.json")
    monkeypatch.setattr(halt, "OPERATOR_ALERT_PATH", tmp_path / "alert.json")
    ex, rest, _paper = _executor(monkeypatch)
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    rest.get_balance.side_effect = [50.0, 42.0]
    rest._request.side_effect = AssertionError("freeze path must not read equity")
    analysis = _make_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._validate(analysis) == _HALT
    callback.assert_called_once()
    assert ex._equity_halt_seed is None
    assert not (tmp_path / "halt.json").exists()
