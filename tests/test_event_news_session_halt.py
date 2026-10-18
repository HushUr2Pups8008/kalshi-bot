"""Session halt uses the first successful equity read and ignores a bad balance."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import config as cfg_module
from kalshi.rest_client import KalshiRestClient
from tests.test_executor import _make_analysis, _make_executor
from utils.event_news_research import EVENT_NEWS_COHORT_ID
from utils.event_news_session_halt import session_halt_loss_line

import utils.event_news_session_halt as halt


def _enable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setenv("EVENT_NEWS_SESSION_HALT", "true")
    monkeypatch.setattr(halt, "HALT_LATCH_PATH", tmp_path / "halt.json")
    monkeypatch.setattr(halt, "CASH_CENTS_PATH", tmp_path / "cash.json")
    monkeypatch.setattr(halt, "OPERATOR_ALERT_PATH", tmp_path / "alert.json")


def test_halt_line_is_percent_of_seeded_equity_capped_at_ten_dollars() -> None:
    # 10% of $29.20 is $2.92. A $200 book caps at $10, not 10% of cfg.bankroll.
    assert session_halt_loss_line(29.20, 0.10) == pytest.approx(2.92)
    assert session_halt_loss_line(200.0, 0.10) == pytest.approx(10.0)


def test_get_balance_failure_returns_none_not_zero() -> None:
    client = KalshiRestClient.__new__(KalshiRestClient)
    client._request = MagicMock(side_effect=RuntimeError("balance down"))
    assert client.get_balance() is None


def test_get_balance_uses_integer_cents() -> None:
    client = KalshiRestClient.__new__(KalshiRestClient)
    client._request = MagicMock(
        return_value={"balance": 2920, "balance_dollars": "29.2049"}
    )
    assert client.get_balance() == pytest.approx(29.20)


def test_seed_of_29_20_falling_to_19_halts_without_shutdown(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_open_positions.return_value = []
    rest.get_balance.side_effect = [29.20, 19.0]
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    analysis = _make_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._session_start_balance == pytest.approx(29.20)
    reason = ex._validate(analysis)

    assert reason == "LIVE HALTED: session loss limit reached -- all trading suspended"
    assert ex._live_halted is True
    callback.assert_not_called()
    rest.place_limit_order.assert_not_called()
    latch = json.loads((tmp_path / "halt.json").read_text(encoding="utf-8"))
    assert latch["halted"] is True
    assert latch["line"] == pytest.approx(2.92)


def test_open_cost_is_part_of_session_equity(monkeypatch, tmp_path):
    # Cash stays $10. The loss is the drop in position cost, 19.20 to 9.00.
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_balance.return_value = 10.0
    rest.get_open_positions.side_effect = [
        [{"ticker": "KXTRUMPSAY-26OCT19-GOLF", "position_fp": "8.00", "market_exposure_dollars": "19.20"}],
        [{"ticker": "KXTRUMPSAY-26OCT19-GOLF", "position_fp": "8.00", "market_exposure_dollars": "9.00"}],
    ]
    analysis = _make_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._session_start_balance == pytest.approx(29.20)
    reason = ex._validate(analysis)

    assert reason is not None
    assert "HALTED" in reason


def test_small_drop_under_the_equity_line_does_not_halt(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_open_positions.return_value = []
    rest.get_balance.side_effect = [29.20, 27.0]
    analysis = _make_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    assert ex._validate(analysis) is None
    assert ex._live_halted is False


def test_zero_first_read_never_arms_the_baseline(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_balance.return_value = 0.0
    callback = MagicMock()
    ex.set_shutdown_callback(callback)

    reason = ex._validate(_make_analysis())

    assert reason == "live balance unreadable"
    assert ex._session_start_balance is None
    assert ex._live_halted is False
    callback.assert_not_called()
    assert not (tmp_path / "halt.json").exists()


def test_zero_later_read_skips_and_is_not_a_loss(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_open_positions.return_value = []
    rest.get_balance.side_effect = [29.20, 0.0, 29.20]
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    analysis = _make_analysis(capped_dollars=2.0)

    assert ex._validate(analysis) is None
    reason = ex._validate(analysis)

    assert reason == "live balance unreadable"
    assert ex._session_start_balance == pytest.approx(29.20)
    assert ex._live_halted is False
    callback.assert_not_called()
    assert ex._validate(analysis) is None
    assert not (tmp_path / "halt.json").exists()


def test_none_balance_does_not_seed(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_balance.return_value = None

    reason = ex._validate(_make_analysis())

    assert reason == "live balance unreadable"
    assert ex._session_start_balance is None


def test_positions_get_failure_does_not_seed(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_balance.return_value = 29.20
    rest.get_open_positions.side_effect = RuntimeError("positions down")

    reason = ex._validate(_make_analysis(capped_dollars=2.0))

    assert reason == "live equity unreadable"
    assert ex._session_start_balance is None
    assert ex._live_halted is False
    assert not (tmp_path / "halt.json").exists()


def test_nan_open_cost_does_not_seed(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_balance.side_effect = [29.20, 29.20]
    rest.get_open_positions.side_effect = [
        [{
            "ticker": "KXTRUMPSAY-26OCT19-GOLF",
            "position_fp": "8.00",
            "market_exposure_dollars": "nan",
        }],
        [],
    ]
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    analysis = _make_analysis(capped_dollars=2.0)

    reason = ex._validate(analysis)

    assert reason == "live equity unreadable"
    assert ex._session_start_balance is None
    assert ex._live_halted is False
    callback.assert_not_called()
    assert ex._validate(analysis) is None
    assert ex._session_start_balance == pytest.approx(29.20)


def test_latch_blocks_a_new_executor_without_a_new_baseline(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_open_positions.return_value = []
    rest.get_balance.side_effect = [29.20, 19.0]
    analysis = _make_analysis(capped_dollars=2.0)
    ex._validate(analysis)
    assert "HALTED" in (ex._validate(analysis) or "")

    restarted, restarted_rest, _ = _make_executor(
        monkeypatch, bankroll=500.0, loss_limit_pct=0.10
    )

    assert restarted._live_halted is True
    reason = restarted._validate(_make_analysis(capped_dollars=2.0))
    assert reason is not None
    assert "HALTED" in reason
    restarted_rest.get_balance.assert_not_called()
    restarted_rest.place_limit_order.assert_not_called()


def test_flag_off_keeps_the_bankroll_line_and_still_rejects_zero(monkeypatch, tmp_path):
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", "kalshi-macro-20260820")
    monkeypatch.setattr(halt, "CASH_CENTS_PATH", tmp_path / "cash.json")
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_balance.side_effect = [0.0, 500.0, 440.0]
    callback = MagicMock()
    ex.set_shutdown_callback(callback)
    analysis = _make_analysis()

    assert ex._validate(analysis) == "live balance unreadable"
    assert ex._session_start_balance is None
    assert ex._validate(analysis) is None
    reason = ex._validate(analysis)

    assert reason is not None
    assert "HALTED" in reason
    callback.assert_called_once()
    assert not (tmp_path / "cash.json").exists()


def test_handled_stop_alert_is_politics_only(monkeypatch, tmp_path):
    from main import _record_politics_handled_stop

    alert = tmp_path / "alert.json"
    monkeypatch.setattr(halt, "OPERATOR_ALERT_PATH", alert)
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", "kalshi-macro-20260820")
    _record_politics_handled_stop()
    assert not alert.exists()

    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    _record_politics_handled_stop()
    payload = json.loads(alert.read_text(encoding="utf-8"))
    assert payload["reason"] == "handled_stop"


def test_cash_drop_from_3995_to_3470_is_recorded_and_a_rise_updates_last(tmp_path):
    """3995c then 3470c is a same-day drop. A later rise still rewrites last."""
    from datetime import datetime, timezone

    path = tmp_path / "cash.json"
    assert halt.record_cash_cents(
        39.95,
        path,
        now=datetime(2026, 10, 17, 3, 9, 46, tzinfo=timezone.utc),
    )
    assert halt.record_cash_cents(
        34.70,
        path,
        now=datetime(2026, 10, 17, 23, 24, 48, tzinfo=timezone.utc),
    )
    dropped = json.loads(path.read_text(encoding="utf-8"))
    assert dropped["date"] == "2026-10-17"
    assert dropped["start_cents"] == 3995
    assert dropped["prev_cents"] == 3995
    assert dropped["last_cents"] == 3470
    assert dropped["ts"] == "2026-10-17T23:24:48Z"
    assert 3995 - 3470 > halt.CASH_DROP_CENTS

    assert halt.record_cash_cents(
        36.00,
        path,
        now=datetime(2026, 10, 17, 23, 30, tzinfo=timezone.utc),
    )
    risen = json.loads(path.read_text(encoding="utf-8"))
    assert risen["start_cents"] == 3995
    assert risen["prev_cents"] == 3470
    assert risen["last_cents"] == 3600
    assert risen["ts"] == "2026-10-17T23:30:00Z"


def test_invalid_cash_read_keeps_the_prior_file(tmp_path, caplog):
    """None, 0, NaN, and inf must not replace a valid cash file."""
    import logging
    from datetime import datetime, timezone

    path = tmp_path / "cash.json"
    assert halt.record_cash_cents(
        39.95,
        path,
        now=datetime(2026, 10, 17, 3, 9, 46, tzinfo=timezone.utc),
    )
    prior = path.read_bytes()
    caplog.set_level(logging.WARNING, logger="utils.event_news_session_halt")
    for bad in (None, 0, 0.0, float("nan"), float("inf"), float("-inf"), False):
        assert halt.record_cash_cents(bad, path) is False
        assert path.read_bytes() == prior
    assert "cash=not_measured" in caplog.text


def test_failed_balance_get_does_not_overwrite_cash(monkeypatch, tmp_path, caplog):
    """An HTTP failure and a zero read leave the previous cash file in place."""
    import logging

    _enable(monkeypatch, tmp_path)
    path = tmp_path / "cash.json"
    from datetime import datetime, timezone

    assert halt.record_cash_cents(
        39.95,
        path,
        now=datetime(2026, 10, 17, 3, 9, 46, tzinfo=timezone.utc),
    )
    prior = path.read_bytes()
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    rest.get_balance.return_value = None
    caplog.set_level(logging.WARNING)
    reason = ex._validate(_make_analysis())
    assert reason == "live balance unreadable"
    assert "cash=not_measured" in caplog.text
    assert path.read_bytes() == prior

    rest.get_balance.return_value = 0.0
    assert ex._validate(_make_analysis()) == "live balance unreadable"
    assert path.read_bytes() == prior


def test_cash_integer_resets_on_a_new_utc_date(tmp_path):
    from datetime import datetime, timezone

    path = tmp_path / "cash.json"
    halt.record_cash_cents(
        29.20,
        path,
        now=datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc),
    )
    halt.record_cash_cents(
        24.00,
        path,
        now=datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc),
    )
    same_day = json.loads(path.read_text(encoding="utf-8"))
    assert same_day["start_cents"] == 2920
    assert same_day["prev_cents"] == 2920
    assert same_day["last_cents"] == 2400

    halt.record_cash_cents(
        24.00,
        path,
        now=datetime(2026, 10, 16, 0, 5, tzinfo=timezone.utc),
    )
    next_day = json.loads(path.read_text(encoding="utf-8"))
    assert next_day["date"] == "2026-10-16"
    assert next_day["start_cents"] == 2400
    assert next_day["last_cents"] == 2400
    assert next_day["prev_cents"] == 2400


def test_cross_midnight_drop_keeps_the_previous_valid_cents(tmp_path):
    """3995c on Oct 17 then 3470c on Oct 18 still names the prior read."""
    from datetime import datetime, timezone

    path = tmp_path / "cash.json"
    assert halt.record_cash_cents(
        39.95,
        path,
        now=datetime(2026, 10, 17, 3, 9, 46, tzinfo=timezone.utc),
    )
    assert halt.record_cash_cents(
        34.70,
        path,
        now=datetime(2026, 10, 18, 0, 8, 26, tzinfo=timezone.utc),
    )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["date"] == "2026-10-18"
    assert stored["start_cents"] == 3470
    assert stored["last_cents"] == 3470
    assert stored["prev_cents"] == 3995
    assert stored["ts"] == "2026-10-18T00:08:26Z"
    assert 3995 - 3470 > halt.CASH_DROP_CENTS


def test_positions_failure_still_records_a_valid_balance(monkeypatch, tmp_path, caplog):
    """A positions GET failure must not discard a balance that already parsed."""
    import logging

    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    path = tmp_path / "cash.json"
    calls: list[str] = []

    def _request(method, endpoint, *args, **kwargs):
        assert method == "GET"
        calls.append(endpoint)
        if endpoint == "/portfolio/balance":
            return {"balance": 3470, "portfolio_value": 1864}
        if endpoint == "/portfolio/positions":
            assert path.is_file()
            raise RuntimeError("positions down")
        raise AssertionError(endpoint)

    rest._request.side_effect = _request
    caplog.set_level(logging.WARNING)
    assert ex._event_news_equity_and_open_cost_cents() == (None, None)
    assert calls == ["/portfolio/balance", "/portfolio/positions"]
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["last_cents"] == 3470
    assert "cash=not_measured" not in caplog.text
    assert "positions read failed" in caplog.text


def test_non_dict_balance_logs_not_measured_and_skips_positions(
    monkeypatch, tmp_path, caplog
):
    """A non-dict balance is not a cash read and must not ask for positions."""
    import logging

    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    calls: list[str] = []

    def _request(method, endpoint, *args, **kwargs):
        assert method == "GET"
        calls.append(endpoint)
        return None

    rest._request.side_effect = _request
    caplog.set_level(logging.WARNING)
    assert ex._event_news_equity_and_open_cost_cents() == (None, None)
    assert calls == ["/portfolio/balance"]
    assert "cash=not_measured" in caplog.text
    assert not (tmp_path / "cash.json").exists()


def test_valid_balance_and_positions_record_cash_and_return_equity(
    monkeypatch, tmp_path
):
    """The cash file updates before the positions GET, and equity still returns."""
    _enable(monkeypatch, tmp_path)
    ex, rest, _ = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    path = tmp_path / "cash.json"

    def _request(method, endpoint, *args, **kwargs):
        assert method == "GET"
        if endpoint == "/portfolio/balance":
            return {"balance": 3470, "portfolio_value": 1864}
        if endpoint == "/portfolio/positions":
            assert json.loads(path.read_text(encoding="utf-8"))["last_cents"] == 3470
            return {"market_positions": []}
        raise AssertionError(endpoint)

    rest._request.side_effect = _request
    assert ex._event_news_equity_and_open_cost_cents() == (3470 + 1864, 0)


def test_politics_plist_throttles_and_does_not_arm_the_halt() -> None:
    text = Path("deploy/com.jake.kalshi-bot-politics.plist").read_text(encoding="utf-8")
    assert "<key>ThrottleInterval</key>" in text
    assert "<integer>30</integer>" in text
    assert "EVENT_NEWS_SESSION_HALT" not in text
    assert "<string>55</string>" in text


def test_start_script_runs_the_guard_before_exec_and_stays_down_on_cap() -> None:
    text = Path("scripts/start_politics_paper.sh").read_text(encoding="utf-8")
    guard_at = text.index("politics_restart_guard.py")
    exec_at = text.index("exec /usr/bin/caffeinate")
    assert guard_at < exec_at
    assert 'guard_status" -eq 75' in text
    assert "exit 0" in text
    assert "export EVENT_NEWS_SESSION_HALT" not in text


def _paper_executor(monkeypatch, bankrolls):
    """Paper event-news executor. The halt flag stays unset, so live is off."""
    from trading.executor import TradeExecutor

    monkeypatch.setattr(cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setattr(cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(cfg_module.cfg, "live_loss_limit_pct", 0.10)
    monkeypatch.setattr(cfg_module.cfg, "live_ticker_cooldown", 0)
    monkeypatch.setattr(cfg_module.cfg, "paper_ticker_cooldown", 0)
    monkeypatch.setattr(cfg_module.cfg, "min_edge", 0.04)
    monkeypatch.setattr(cfg_module.cfg, "max_ticker_exposure_pct", 1.0)
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    rest = MagicMock()
    rest.get_market.return_value = None
    paper = MagicMock()
    paper.get_effective_sizing_bankroll.return_value = 500.0
    paper.get_notional_bankroll.side_effect = list(bankrolls)
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.open_positions_by_prefix.return_value = []
    paper.portfolio.is_concentration_ok.return_value = True
    paper.portfolio.exposure.return_value = 0.0
    paper.portfolio.tickers.return_value = []
    executor = TradeExecutor(rest, paper)
    return executor, rest, paper


def test_unset_session_halt_is_paper_on_and_live_off(monkeypatch, tmp_path):
    from utils.event_news_desk_flags import (
        EVENT_NEWS_SESSION_HALT,
        event_news_desk_flag_enabled,
    )

    _enable(monkeypatch, tmp_path)
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    paper, _rest, _paper_book = _paper_executor(monkeypatch, [50.0])
    live, _live_rest, _live_paper = _make_executor(monkeypatch, bankroll=500.0, loss_limit_pct=0.10)
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)

    assert event_news_desk_flag_enabled(EVENT_NEWS_SESSION_HALT, live=False) is True
    assert event_news_desk_flag_enabled(EVENT_NEWS_SESSION_HALT, live=True) is False
    assert paper._session_halt_enabled() is True
    assert live._session_halt_enabled() is False


def _paper_analysis():
    """Ask and last agree. An unset MagicMock last price floats to 1.0 on 3.14."""
    analysis = _make_analysis(capped_dollars=2.0)
    ask = analysis.market.yes_ask_cents
    analysis.market.last_price_cents = ask
    analysis.market.last_price = ask
    return analysis


def test_paper_bankroll_drop_trips_at_the_equity_line(monkeypatch, tmp_path):
    # 10% of a $50 paper book is $5. The $10 cap is not the binding line here.
    _enable(monkeypatch, tmp_path)
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    executor, rest, paper = _paper_executor(monkeypatch, [50.0, 46.0, 50.0, 45.0])
    callback = MagicMock()
    executor.set_shutdown_callback(callback)
    analysis = _paper_analysis()

    assert executor._validate(analysis) is None
    assert executor._session_start_balance == pytest.approx(50.0)
    assert executor._validate(analysis) is None
    assert executor._live_halted is False

    fresh, fresh_rest, _fresh_paper = _paper_executor(monkeypatch, [50.0, 45.0])
    fresh.set_shutdown_callback(callback)
    assert fresh._validate(analysis) is None
    reason = fresh._validate(analysis)

    assert reason == "PAPER HALTED: session loss limit reached -- all trading suspended"
    assert fresh._live_halted is True
    assert session_halt_loss_line(50.0, 0.10) == pytest.approx(5.0)
    assert session_halt_loss_line(200.0, 0.10) == pytest.approx(10.0)
    callback.assert_not_called()
    rest.place_limit_order.assert_not_called()
    fresh_rest.place_limit_order.assert_not_called()
    rest.get_balance.assert_not_called()
    fresh_rest.get_balance.assert_not_called()
    paper.get_notional_bankroll.assert_called()
    latch = json.loads((tmp_path / "halt.json").read_text(encoding="utf-8"))
    assert latch["halted"] is True
    assert latch["line"] == pytest.approx(5.0)
    assert latch["seed_equity"] == pytest.approx(50.0)


def test_paper_infinite_bankroll_does_not_seed(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    executor, rest, _paper = _paper_executor(monkeypatch, [float("inf"), float("nan")])
    analysis = _paper_analysis()

    assert executor._validate(analysis) == "paper bankroll unreadable"
    assert executor._validate(analysis) == "paper bankroll unreadable"
    assert executor._session_start_balance is None
    assert executor._live_halted is False
    assert not (tmp_path / "halt.json").exists()
    rest.place_limit_order.assert_not_called()
    rest.get_balance.assert_not_called()


def test_paper_bankroll_read_failure_admits_nothing(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    executor, rest, _paper = _paper_executor(
        monkeypatch,
        [RuntimeError("bankroll down"), None, 0.0, 50.0],
    )
    callback = MagicMock()
    executor.set_shutdown_callback(callback)
    analysis = _paper_analysis()

    assert executor._validate(analysis) == "paper bankroll unreadable"
    assert executor._validate(analysis) == "paper bankroll unreadable"
    assert executor._validate(analysis) == "paper bankroll unreadable"
    assert executor._session_start_balance is None
    assert executor._live_halted is False
    assert not (tmp_path / "halt.json").exists()
    callback.assert_not_called()
    rest.place_limit_order.assert_not_called()
    rest.get_balance.assert_not_called()

    assert executor._validate(analysis) is None
    assert executor._session_start_balance == pytest.approx(50.0)


def test_paper_halt_clears_only_when_the_latch_file_is_removed(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    _enable(monkeypatch, tmp_path)
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    executor, _rest, _paper = _paper_executor(monkeypatch, [50.0, 40.0])
    analysis = _paper_analysis()
    assert executor._validate(analysis) is None
    assert "HALTED" in (executor._validate(analysis) or "")

    restarted, restarted_rest, restarted_paper = _paper_executor(monkeypatch, [80.0])
    assert restarted._live_halted is True
    reason = restarted._validate(analysis)
    assert reason == "PAPER HALTED: session loss limit reached -- all trading suspended"
    restarted_paper.get_notional_bankroll.assert_not_called()
    restarted_rest.get_balance.assert_not_called()
    restarted_rest.place_limit_order.assert_not_called()

    halt.record_cash_cents(
        40.0,
        tmp_path / "cash.json",
        now=datetime(2026, 10, 18, 0, 5, tzinfo=timezone.utc),
    )
    still_latched, _still_rest, still_paper = _paper_executor(monkeypatch, [80.0])
    assert still_latched._live_halted is True
    assert "HALTED" in (still_latched._validate(analysis) or "")
    still_paper.get_notional_bankroll.assert_not_called()
    assert json.loads((tmp_path / "halt.json").read_text(encoding="utf-8"))["halted"] is True

    (tmp_path / "halt.json").unlink()
    cleared, cleared_rest, _cleared_paper = _paper_executor(monkeypatch, [50.0])
    assert cleared._live_halted is False
    assert cleared._validate(analysis) is None
    assert cleared._session_start_balance == pytest.approx(50.0)
    assert cleared._live_halted is False
    cleared_rest.place_limit_order.assert_not_called()
    assert not (tmp_path / "halt.json").exists()
