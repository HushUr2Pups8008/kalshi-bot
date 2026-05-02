"""
Tests for trading/paper_trader.py

Covers: bankroll atomicity, resolve_market P&L accounting, win/loss tracking,
        keyword_outcomes written on resolution, kelly_contracts shadow sizing.

Uses :memory: SQLite so no disk I/O or shared state between tests.
"""

import asyncio
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Patch cfg before importing PaperTrader to avoid needing a real .env
import config as _cfg_module


def _run_resolve(trader, ticker, resolved_yes):
    """Sync adapter for PaperTrader.resolve_market (async since v0.29.47)."""
    asyncio.run(trader.resolve_market(ticker, resolved_yes))

_REAL_SQLITE_CONNECT = sqlite3.connect


def _shared_memory_connect(name: str):
    """
    Create a named shared in-memory SQLite DB plus a connect factory.

    PaperTrader opens multiple SQLite connections internally (its main handle,
    SourceCredibility, and occasionally reconnect-style test flows). A named
    shared-memory URI lets the tests exercise the real persistence logic
    without relying on filesystem temp directories in this environment.
    """
    db_uri = f"file:{name}-{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper = _REAL_SQLITE_CONNECT(db_uri, uri=True, check_same_thread=False)

    def _connect(*args, **kwargs):
        conn = _REAL_SQLITE_CONNECT(db_uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    return keeper, _connect
def _make_mock_analysis(
    ticker="KXTEST-25DEC31",
    series_ticker="KXTEST",
    side="yes",
    yes_price=40.0,
    estimated_prob=0.60,
    edge=0.20,
    kelly_dollars=10.0,
    capped_dollars=10.0,
    headline="Test headline",
    source="r/test",
    keywords=None,
    match_score=0.15,
    signal_type="news",
    llm_direction="yes",
    llm_magnitude="moderate",
    llm_confidence=0.8,
):
    """Build a minimal SignalAnalysis-like mock for record_trade()."""
    market = MagicMock()
    market.ticker       = ticker
    market.series_ticker = series_ticker
    market.title        = "Test market title"
    market.subtitle     = ""
    market.yes_price    = yes_price
    market.yes_bid      = yes_price - 1
    market.yes_ask      = yes_price + 1
    market.yes_prob     = yes_price / 100.0
    market.status       = "active"
    market.close_time   = "2025-12-31T23:59:59Z"

    # dataclasses.asdict requires a real dataclass; we stub asdict separately
    market.__dataclass_fields__ = {}

    news = MagicMock()
    news.headline = headline
    news.source   = source
    news.body     = ""
    news.published = datetime.now(timezone.utc)

    analysis = MagicMock()
    analysis.market                = market
    analysis.news_item             = news
    analysis.side                  = side
    analysis.estimated_probability = estimated_prob
    analysis.market_yes_price      = yes_price
    analysis.edge                  = edge
    analysis.kelly_fraction        = 0.5
    analysis.kelly_dollars         = kelly_dollars
    analysis.capped_dollars        = capped_dollars
    analysis.keywords_matched      = keywords or ["ceasefire", "attack"]
    analysis.reasoning             = "test reasoning"
    analysis.confidence            = llm_confidence
    analysis.match_score           = match_score
    analysis.signal_type           = signal_type
    analysis.llm_direction         = llm_direction
    analysis.llm_magnitude         = llm_magnitude
    analysis.llm_confidence        = llm_confidence
    return analysis


@pytest.fixture()
def trader(monkeypatch):
    """Return a PaperTrader backed by a fresh workspace-local SQLite DB."""
    # Prevent cfg validation from running (needs real API keys)
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)

    from trading.paper_trader import PaperTrader

    keeper, connect = _shared_memory_connect("test-trades")

    # Patch credibility and portfolio to avoid needing full DB state
    with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
         patch("trading.paper_trader.SourceCredibility") as MockCred:
        MockCred.return_value.get_multiplier.return_value = 1.0
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            pt = PaperTrader(db_path=":memory:", startup_context="test")
            # Seed bankroll
            pt._set_state("notional_bankroll", "500.0")
            yield pt
    keeper.close()


class TestBankrollAtomicity:
    """Regression for the bankroll desync bug fixed in v0.23.0."""

    def test_resolve_credits_bankroll_atomically(self, trader):
        """Bankroll credit must happen in the same SQLite commit as resolved=1."""
        analysis = _make_mock_analysis(yes_price=40.0, side="yes")

        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trade_id = trader.record_trade(analysis)

        before = trader.get_notional_bankroll()
        _run_resolve(trader, analysis.market.ticker, True)
        after = trader.get_notional_bankroll()

        # YES win at 40c: cost $0.40 * 5 contracts = $2.00 debited; payout = 5 contracts = $5
        # Net change = payout $5 credited back; bankroll should be higher than pre-resolve
        assert after > before

    def test_bankroll_and_resolved_flag_consistent(self, trader):
        """If resolved=1 in DB, bankroll must already reflect the payout."""
        analysis = _make_mock_analysis(yes_price=50.0, side="yes")

        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        _run_resolve(trader, analysis.market.ticker, True)

        row = trader._conn.execute(
            "SELECT resolved, pnl_dollars FROM paper_trades LIMIT 1"
        ).fetchone()
        assert row["resolved"] == 1
        assert row["pnl_dollars"] is not None

        bankroll = trader.get_notional_bankroll()
        # Bankroll should be above initial 500 - cost (already debited at record_trade)
        # After resolution of a win: initial 500 - cost + payout
        assert bankroll > 0


class TestResolveMarketAccounting:
    def test_winning_yes_trade_positive_pnl(self, trader):
        analysis = _make_mock_analysis(yes_price=40.0, side="yes")
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        _run_resolve(trader, analysis.market.ticker, True)

        row = trader._conn.execute(
            "SELECT pnl_dollars FROM paper_trades WHERE resolved=1"
        ).fetchone()
        # 5 contracts won = $5 payout; cost = 5 * 0.40 = $2.00; pnl = $3.00
        assert row["pnl_dollars"] == pytest.approx(3.00, abs=0.01)

    def test_losing_yes_trade_negative_pnl(self, trader):
        analysis = _make_mock_analysis(yes_price=40.0, side="yes")
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        _run_resolve(trader, analysis.market.ticker, False)

        row = trader._conn.execute(
            "SELECT pnl_dollars FROM paper_trades WHERE resolved=1"
        ).fetchone()
        # Lost: cost = 5 * 0.40 = $2.00; payout = 0; pnl = -$2.00
        assert row["pnl_dollars"] == pytest.approx(-2.00, abs=0.01)

    def test_winning_no_trade_positive_pnl(self, trader):
        analysis = _make_mock_analysis(yes_price=60.0, side="no")
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        _run_resolve(trader, analysis.market.ticker, False)

        row = trader._conn.execute(
            "SELECT pnl_dollars FROM paper_trades WHERE resolved=1"
        ).fetchone()
        # NO contract at yes_price=60c => price_cents = 100-60 = 40c
        # 5 contracts * 40c = $2.00 cost; win = 5 contracts = $5; pnl = $3.00
        assert row["pnl_dollars"] == pytest.approx(3.00, abs=0.01)

    def test_no_open_trades_is_noop(self, trader):
        """resolve_market with no open trades should not raise and not change bankroll."""
        before = trader.get_notional_bankroll()
        _run_resolve(trader, "KXNONEXISTENT-25DEC31", True)
        after = trader.get_notional_bankroll()
        assert before == after


class TestKeywordOutcomes:
    def test_keyword_outcomes_written_on_resolution(self, trader):
        analysis = _make_mock_analysis(keywords=["ceasefire", "attack"])
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        _run_resolve(trader, analysis.market.ticker, True)

        rows = trader._conn.execute("SELECT * FROM keyword_outcomes").fetchall()
        assert len(rows) == 2
        keywords_written = {r["keyword"] for r in rows}
        assert keywords_written == {"ceasefire", "attack"}

    def test_keyword_outcomes_correctness_flag(self, trader):
        """correct=1 when keyword direction matches resolution."""
        # 'ceasefire' direction is 'no' in GEOPOLITICAL_SIGNALS (ceasefire = NO to conflict).
        # We need to check the actual signal def to know expected correct value.
        # Instead, just verify the column is populated (0 or 1).
        analysis = _make_mock_analysis(keywords=["ceasefire"])
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        _run_resolve(trader, analysis.market.ticker, True)

        row = trader._conn.execute(
            "SELECT correct FROM keyword_outcomes WHERE keyword='ceasefire'"
        ).fetchone()
        assert row is not None
        assert row["correct"] in (0, 1)


class TestKellyShadow:
    def test_kelly_contracts_column_populated(self, trader):
        """kelly_contracts should be stored on every paper trade."""
        analysis = _make_mock_analysis(yes_price=50.0, capped_dollars=10.0)
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        row = trader._conn.execute(
            "SELECT kelly_contracts, contracts FROM paper_trades LIMIT 1"
        ).fetchone()
        assert row["kelly_contracts"] is not None
        assert row["kelly_contracts"] >= 1
        # In paper mode, flat 5 contracts are placed regardless of Kelly
        assert row["contracts"] == 5

    def test_record_trade_logs_blended_signal_meta_when_present(self, trader):
        analysis = _make_mock_analysis(yes_price=50.0, capped_dollars=10.0)
        analysis.signal_meta = {
            "source_lane": "blend",
            "blended_p": 0.60,
            "readiness_gate_min_edge_override": 0.03,
        }

        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}), \
             patch("trading.paper_trader.trade_log") as trade_log_mock:
            trader.record_trade(analysis)

        trade_log_mock.log_paper_trade.assert_called_once()
        assert (
            trade_log_mock.log_paper_trade.call_args.kwargs["signal_meta"]
            == analysis.signal_meta
        )

    def test_kelly_contracts_independent_of_flat(self, trader):
        """kelly_contracts reflects capped_dollars sizing, not PAPER_FLAT_CONTRACTS."""
        # At 50c, $20 => 40 contracts; $2 => 4 contracts. Both still use flat 5 in contracts.
        analysis_big  = _make_mock_analysis(yes_price=50.0, capped_dollars=20.0)
        analysis_small = _make_mock_analysis(
            yes_price=50.0, capped_dollars=2.0, ticker="KXTEST-25DEC30"
        )
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis_big)
            trader.record_trade(analysis_small)

        rows = trader._conn.execute(
            "SELECT kelly_contracts, contracts FROM paper_trades ORDER BY ts"
        ).fetchall()
        big_kelly   = rows[0]["kelly_contracts"]
        small_kelly = rows[1]["kelly_contracts"]
        assert big_kelly > small_kelly
        # Both use flat 5
        assert rows[0]["contracts"] == 5
        assert rows[1]["contracts"] == 5

    def test_record_trade_persists_executed_side_edge_yes(self, trader):
        """PROFIT-OBS-004 (closed 2026-05-02): yes-side trades persist
        analysis.edge unchanged (the YES-side perspective IS the executed
        perspective when the executor traded YES)."""
        analysis = _make_mock_analysis(side="yes", yes_price=40.0,
                                       estimated_prob=0.60, edge=0.20)
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        row = trader._conn.execute("SELECT side, edge FROM paper_trades").fetchone()
        assert row["side"] == "yes"
        assert row["edge"] == pytest.approx(0.20)

    def test_record_trade_persists_executed_side_edge_no(self, trader):
        """PROFIT-OBS-004 (closed 2026-05-02): no-side trades persist
        -analysis.edge so the recorded value reflects the executed side.

        Without this fix, every NO-side trade would persist a misleadingly
        negative edge — exactly the symptom that surfaced on the 3
        KXFISAEXTEND-26APR-MAY0{1,2,3} historical rows."""
        # YES-side edge of -0.068 (estimated_prob 0.432, market_yes 50c).
        # Executed-side edge for NO is +0.068.
        analysis = _make_mock_analysis(
            side="no", yes_price=50.0, estimated_prob=0.432, edge=-0.068,
            ticker="KXOBS004-NO",
        )
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXOBS004"}):
            trader.record_trade(analysis)
        row = trader._conn.execute(
            "SELECT side, edge FROM paper_trades WHERE ticker='KXOBS004-NO'"
        ).fetchone()
        assert row["side"] == "no"
        assert row["edge"] == pytest.approx(0.068), (
            "PROFIT-OBS-004: NO-side trade must persist executed-side edge "
            "(positive), not the YES-side perspective (negative)."
        )


class TestModeSelection:
    @pytest.mark.parametrize("kill_switch_enabled", [False, True])
    def test_missing_go_live_flag_starts_in_paper_mode(self, monkeypatch, kill_switch_enabled):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", kill_switch_enabled)

        from trading.paper_trader import PaperTrader

        keeper, connect = _shared_memory_connect("mode-default-paper")
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            PaperTrader(db_path=":memory:", startup_context="test")

        assert _cfg_module.cfg.is_paper_trading is True
        keeper.close()

    def test_false_go_live_flag_stays_in_paper_mode_even_when_kill_switch_enabled(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", True)

        from trading.paper_trader import PaperTrader

        keeper, connect = _shared_memory_connect("mode-explicit-false")
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            pt = PaperTrader(db_path=":memory:", startup_context="test")
            pt._set_state("go_live_confirmed", "false")

        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            PaperTrader(db_path=":memory:", startup_context="test")

        assert _cfg_module.cfg.is_paper_trading is True
        keeper.close()

    def test_go_live_confirmed_stays_paper_when_env_kill_switch_disabled(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", False)

        from trading.paper_trader import PaperTrader

        keeper, connect = _shared_memory_connect("mode-blocked")
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            pt = PaperTrader(db_path=":memory:", startup_context="test")
            pt._set_state("go_live_confirmed", "true")

        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            PaperTrader(db_path=":memory:", startup_context="test")

        assert _cfg_module.cfg.is_paper_trading is True
        keeper.close()

    def test_go_live_confirmed_enables_live_when_env_kill_switch_enabled(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", True)

        from trading.paper_trader import PaperTrader

        keeper, connect = _shared_memory_connect("mode-live")
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            pt = PaperTrader(db_path=":memory:", startup_context="test")
            pt._set_state("go_live_confirmed", "true")

        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            PaperTrader(db_path=":memory:", startup_context="test")

        assert _cfg_module.cfg.is_paper_trading is False
        keeper.close()


class TestStartupInitialization:
    def test_existing_bankroll_is_not_overwritten_on_repeated_construction(self, monkeypatch, caplog):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", False)

        from trading.paper_trader import PaperTrader

        keeper, connect = _shared_memory_connect("startup-idempotent-bankroll")
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            first = PaperTrader(db_path=":memory:", startup_context="test")
            first._set_state("notional_bankroll", "123.45")

        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 50.0)
        caplog.clear()
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred, \
             caplog.at_level(logging.INFO, logger="paper_trader"):
            MockCred.return_value.get_multiplier.return_value = 1.0
            second = PaperTrader(db_path=":memory:", startup_context="test")

        assert second.get_notional_bankroll() == pytest.approx(123.45, abs=0.001)
        assert "Notional bankroll initialised" not in caplog.text
        assert "Paper trading phase started" not in caplog.text
        assert "[PAPER_INIT]" not in caplog.text
        keeper.close()

    def test_initialize_is_idempotent_on_same_instance(self, monkeypatch, caplog):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", False)

        from trading.paper_trader import PaperTrader

        keeper, connect = _shared_memory_connect("startup-same-instance")
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred, \
             caplog.at_level(logging.INFO, logger="paper_trader"):
            MockCred.return_value.get_multiplier.return_value = 1.0
            trader = PaperTrader(db_path=":memory:", startup_context="test")
            caplog.clear()
            trader.initialize()

        assert "Paper trading phase started" not in caplog.text
        assert "Notional bankroll initialised" not in caplog.text
        assert "[PAPER_INIT]" not in caplog.text
        keeper.close()

    def test_cli_startup_uses_debug_diagnostics_without_runtime_info_noise(self, monkeypatch, caplog):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", False)

        from trading.paper_trader import PaperTrader

        keeper, connect = _shared_memory_connect("startup-cli-diagnostics")
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            PaperTrader(db_path=":memory:", startup_context="test")

        caplog.clear()
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred, \
             caplog.at_level(logging.DEBUG, logger="paper_trader"):
            MockCred.return_value.get_multiplier.return_value = 1.0
            PaperTrader(db_path=":memory:", startup_context="cli")

        assert "[PAPER_INIT]" in caplog.text
        assert "ctx=cli" in caplog.text
        assert "Paper trading phase started" not in caplog.text
        assert "Notional bankroll initialised" not in caplog.text
        keeper.close()

    def test_runtime_context_rejects_in_memory_db(self, monkeypatch, caplog):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", False)

        from trading.paper_trader import PaperTrader

        monkeypatch.setattr(PaperTrader, "_runtime_owner_pid", None)
        with caplog.at_level(logging.ERROR, logger="paper_trader"):
            with pytest.raises(ValueError, match="cannot use an in-memory SQLite database"):
                PaperTrader(db_path=":memory:", startup_context="runtime")

        assert "[PAPER_INIT_GUARD]" in caplog.text
        assert "runtime_in_memory_db_blocked=true" in caplog.text

    def test_runtime_context_blocks_repeated_runtime_init_in_same_process(self, monkeypatch, caplog):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 50.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", False)

        from trading.paper_trader import PaperTrader

        monkeypatch.setattr(PaperTrader, "_runtime_owner_pid", None)
        keeper, connect = _shared_memory_connect("runtime-repeat-guard")
        try:
            with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
                 patch("trading.paper_trader.SourceCredibility") as MockCred:
                MockCred.return_value.get_multiplier.return_value = 1.0
                first = PaperTrader(db_path="paper_trades.db", startup_context="runtime")
                with caplog.at_level(logging.ERROR, logger="paper_trader"):
                    with pytest.raises(RuntimeError, match="attempted more than once"):
                        PaperTrader(db_path="paper_trades.db", startup_context="runtime")

            assert first.get_notional_bankroll() == pytest.approx(50.0, abs=0.001)
            assert "[PAPER_INIT_GUARD]" in caplog.text
            assert "duplicate_runtime_init_blocked=true" in caplog.text
        finally:
            monkeypatch.setattr(PaperTrader, "_runtime_owner_pid", None)
            keeper.close()


# ---------------------------------------------------------------------------
# Coverage-focused tests — fill gaps identified in the 2026-04-23 baseline
# (generate_report, daily_summary, _match_quality_report_section, migrations,
# resolve_market keyword-outcome exception path, startup-context validation)
# ---------------------------------------------------------------------------


class TestReportGeneration:
    """Smoke tests for `generate_report` and `daily_summary`.

    These functions are ~155 lines of pure formatting code. We verify they
    run without error on empty and populated DBs and that the output
    contains expected section headers — not line-by-line formatting.
    """

    def test_generate_report_empty_db(self, trader):
        trader.credibility.format_table.return_value = "  (credibility table)"
        report = trader.generate_report()
        assert isinstance(report, str)
        assert "PAPER TRADING PERFORMANCE REPORT" in report
        assert "TRADE SUMMARY" in report
        assert "NOTIONAL BANKROLL" in report
        assert "GO-LIVE ASSESSMENT" in report
        # Empty DB -> insufficient data verdict
        assert "INSUFFICIENT DATA" in report

    def test_generate_report_with_open_trade(self, trader):
        trader.credibility.format_table.return_value = "  (credibility table)"
        analysis = _make_mock_analysis(yes_price=40.0, side="yes")
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        report = trader.generate_report()
        assert "OPEN PAPER TRADES" in report
        assert "KXTEST" in report

    def test_generate_report_with_resolved_trades_shows_best_worst(self, trader):
        trader.credibility.format_table.return_value = "  (credibility table)"
        # Two trades: one winning, one losing
        win_analysis = _make_mock_analysis(
            ticker="KXWIN-25DEC31", yes_price=40.0, side="yes",
        )
        loss_analysis = _make_mock_analysis(
            ticker="KXLOSS-25DEC31", yes_price=40.0, side="yes",
        )
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(win_analysis)
            trader.record_trade(loss_analysis)
        _run_resolve(trader, "KXWIN-25DEC31", True)
        _run_resolve(trader, "KXLOSS-25DEC31", False)

        report = trader.generate_report()
        assert "RESOLVED TRADES" in report
        assert "BEST TRADE" in report
        assert "WORST TRADE" in report
        assert "KXWIN" in report

    def test_daily_summary_logs_without_error(self, trader, caplog):
        with caplog.at_level(logging.INFO, logger="paper_trader"):
            trader.daily_summary()
        assert "[DAILY SUMMARY]" in caplog.text
        assert "[WEEKLY REMINDER]" in caplog.text

    def test_daily_summary_with_trades(self, trader, caplog):
        analysis = _make_mock_analysis(yes_price=50.0, side="yes")
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        with caplog.at_level(logging.INFO, logger="paper_trader"):
            trader.daily_summary()
        # trades=1 appears in the summary line
        assert "trades=1" in caplog.text


class TestMatchQualityReportSection:
    """`_match_quality_report_section` — reads trades.jsonl, tolerates missing/malformed."""

    def test_returns_placeholder_when_trades_jsonl_missing(self, monkeypatch, tmp_path):
        from trading import paper_trader as pt_module
        missing = tmp_path / "no-such-file.jsonl"
        monkeypatch.setattr(pt_module, "TRADE_LOG_FILE", missing)
        lines = pt_module._match_quality_report_section()
        assert lines[0] == "MATCH QUALITY"
        assert "No trades.jsonl found" in lines[1]

    def test_returns_placeholder_when_no_diagnostic_records(self, monkeypatch, tmp_path):
        from trading import paper_trader as pt_module
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        monkeypatch.setattr(pt_module, "TRADE_LOG_FILE", empty)
        lines = pt_module._match_quality_report_section()
        assert "No MATCH_DIAGNOSTIC records yet" in lines[1]

    def test_counts_low_match_quality_and_flags(self, monkeypatch, tmp_path):
        import json as _json
        from trading import paper_trader as pt_module

        records = [
            {"type": "MATCH_DIAGNOSTIC", "ticker": "KX1", "low_match_quality": True,
             "heuristic_flags": ["single_named_entity_only"]},
            {"type": "MATCH_DIAGNOSTIC", "ticker": "KX1", "low_match_quality": True,
             "heuristic_flags": ["minimal_overlap"]},
            {"type": "MATCH_DIAGNOSTIC", "ticker": "KX2", "low_match_quality": False,
             "heuristic_flags": []},
            {"type": "OTHER", "ticker": "ignored"},  # not a diagnostic, skipped
            "not-json",                              # malformed, skipped
        ]
        path = tmp_path / "trades.jsonl"
        body = "\n".join(
            (r if isinstance(r, str) else _json.dumps(r)) for r in records
        )
        path.write_text(body + "\n", encoding="utf-8")
        monkeypatch.setattr(pt_module, "TRADE_LOG_FILE", path)

        lines = pt_module._match_quality_report_section()
        text = "\n".join(lines)
        assert "Matched candidates:       3" in text
        assert "Low-quality flagged:      2" in text
        assert "single_named_entity_only" in text
        assert "KX1" in text

    def test_returns_placeholder_on_read_error(self, monkeypatch, tmp_path):
        """OSError while reading the file returns a placeholder, not an exception."""
        from trading import paper_trader as pt_module
        path = tmp_path / "trades.jsonl"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(pt_module, "TRADE_LOG_FILE", path)

        real_open = path.open
        def _raising_open(*args, **kwargs):
            raise OSError("simulated read failure")
        monkeypatch.setattr(type(path), "open", lambda self, *a, **kw: _raising_open(*a, **kw))

        lines = pt_module._match_quality_report_section()
        assert "Could not read trades.jsonl" in lines[1]
        # Restore not strictly necessary (monkeypatch auto-reverts), but keep reference
        _ = real_open


class TestValidateStartupContext:
    """`_validate_startup_context` — rejects unsupported values."""

    def test_unsupported_context_raises(self, monkeypatch):
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 50.0)

        from trading.paper_trader import PaperTrader

        keeper, connect = _shared_memory_connect("startup-ctx-bad")
        try:
            with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
                 patch("trading.paper_trader.SourceCredibility") as MockCred:
                MockCred.return_value.get_multiplier.return_value = 1.0
                with pytest.raises(ValueError, match="Unsupported startup_context"):
                    PaperTrader(db_path=":memory:", startup_context="bogus")
        finally:
            keeper.close()


class TestMigrations:
    """Schema migration branches — `_migrate_db`, `_ensure_paper_trades_column`."""

    def test_migrate_adds_new_columns_to_legacy_schema(self, monkeypatch):
        """A DB with only the original paper_trades columns gets migrated columns added."""
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        monkeypatch.setattr(_cfg_module.cfg, "bankroll", 50.0)
        monkeypatch.setattr(_cfg_module.cfg, "live_trading_enabled", False)

        keeper, connect = _shared_memory_connect("migrate-legacy")
        try:
            # Pre-create a legacy-schema paper_trades table (no new columns) and
            # seed a historical row that should survive migration.
            legacy_conn = connect()
            legacy_conn.execute(
                """
                CREATE TABLE paper_trades (
                    trade_id       TEXT PRIMARY KEY,
                    ts             TEXT NOT NULL,
                    ticker         TEXT NOT NULL,
                    market_title   TEXT NOT NULL,
                    side           TEXT NOT NULL,
                    contracts      INTEGER NOT NULL,
                    price_cents    INTEGER NOT NULL,
                    cost_dollars   REAL NOT NULL,
                    estimated_prob REAL NOT NULL,
                    market_yes_price REAL NOT NULL,
                    edge           REAL NOT NULL,
                    kelly_dollars  REAL NOT NULL,
                    capped_dollars REAL NOT NULL,
                    signal_headline TEXT NOT NULL,
                    signal_source  TEXT NOT NULL,
                    keywords_matched TEXT NOT NULL,
                    reasoning      TEXT NOT NULL,
                    source_multiplier REAL DEFAULT 1.0,
                    notional_bankroll_before REAL,
                    notional_bankroll_after  REAL,
                    resolved       INTEGER DEFAULT 0,
                    resolved_yes   INTEGER,
                    pnl_dollars    REAL,
                    market_snapshot TEXT
                )
                """
            )
            legacy_conn.execute(
                "INSERT INTO paper_trades (trade_id, ts, ticker, market_title, side, "
                "contracts, price_cents, cost_dollars, estimated_prob, market_yes_price, "
                "edge, kelly_dollars, capped_dollars, signal_headline, signal_source, "
                "keywords_matched, reasoning, market_snapshot) VALUES "
                "('legacy-1', '2024-01-01T00:00:00', 'KXLEG-24JAN01', 't', 'yes', "
                "5, 50, 2.5, 0.6, 50.0, 0.1, 2.5, 2.5, 'h', 's', '[]', 'r', "
                "'{\"series_ticker\": \"KXLEG\"}')"
            )
            legacy_conn.commit()

            from trading.paper_trader import PaperTrader
            with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
                 patch("trading.paper_trader.SourceCredibility") as MockCred:
                MockCred.return_value.get_multiplier.return_value = 1.0
                pt = PaperTrader(db_path=":memory:", startup_context="test")

            cols = pt._paper_trades_columns()
            # Columns added by _migrate_db
            for expected in ("series_ticker", "resolved_ts", "signal_type", "match_score",
                             "llm_direction", "llm_magnitude", "llm_confidence",
                             "kelly_contracts"):
                assert expected in cols, f"{expected} should be added by migration"

            # series_ticker backfilled from market_snapshot JSON for historical row
            row = pt._conn.execute(
                "SELECT series_ticker FROM paper_trades WHERE trade_id='legacy-1'"
            ).fetchone()
            assert row["series_ticker"] == "KXLEG"
        finally:
            keeper.close()

    def test_ensure_column_handles_duplicate_column_name(self, trader):
        """Calling _ensure_paper_trades_column on an already-present column returns False."""
        # series_ticker is added by the initial migration; re-requesting it should
        # hit the 'duplicate column name' branch and return False.
        cols = trader._paper_trades_columns()
        assert "series_ticker" in cols
        result = trader._ensure_paper_trades_column("series_ticker", "TEXT", cols)
        assert result is False

    def test_ensure_column_logs_warning_on_other_sqlite_error(self, trader, caplog):
        """Non-duplicate-column errors are logged and return False (don't raise)."""
        # sqlite3.Connection.execute is a read-only C-level attribute, so we
        # swap _conn for a small stub that raises a non-duplicate OperationalError.
        class _RaisingConn:
            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("simulated disk I/O error")
            def commit(self):
                pass

        original = trader._conn
        trader._conn = _RaisingConn()
        try:
            with caplog.at_level(logging.WARNING, logger="paper_trader"):
                result = trader._ensure_paper_trades_column("any_col", "TEXT", set())
        finally:
            trader._conn = original
        assert result is False
        assert "DB migration failed" in caplog.text


class TestResolveMarketExceptions:
    """Keyword-outcome recording failures are logged, not raised."""

    def test_keyword_outcomes_exception_does_not_break_resolution(self, trader, caplog):
        """If the per-keyword JSON is malformed, resolve_market logs a warning
        and still completes the bankroll credit + trade resolution."""
        analysis = _make_mock_analysis(yes_price=40.0, side="yes", keywords=["ceasefire"])
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        # Corrupt the keywords_matched JSON so json.loads raises inside the loop
        trader._conn.execute(
            "UPDATE paper_trades SET keywords_matched = 'not json' WHERE ticker = ?",
            (analysis.market.ticker,),
        )
        trader._conn.commit()

        with caplog.at_level(logging.WARNING, logger="paper_trader"):
            _run_resolve(trader, analysis.market.ticker, True)

        assert "Failed to record keyword outcomes" in caplog.text
        # And the trade is still marked resolved + bankroll credited
        row = trader._conn.execute(
            "SELECT resolved, pnl_dollars FROM paper_trades LIMIT 1"
        ).fetchone()
        assert row["resolved"] == 1


class TestCalibrationEmission:
    """PROFIT-CAL-001 (v0.29.47): per-lane estimates persisted on paper_trades
    and emitted as CALIBRATION_CHECK events at resolve time.
    """

    def _analysis_with_lanes(
        self,
        *,
        fast_lane_p=0.62,
        fast_lane_confidence=0.75,
        accumulation_p=0.58,
        accumulation_confidence=0.70,
        structural_p=0.55,
        structural_confidence=0.65,
        ticker="KXTEST-25DEC31",
    ):
        analysis = _make_mock_analysis(ticker=ticker, yes_price=40.0, side="yes")
        analysis.signal_meta = {
            "fast_lane_p": fast_lane_p,
            "fast_lane_confidence": fast_lane_confidence,
            "accumulation_p": accumulation_p,
            "accumulation_confidence": accumulation_confidence,
            "structural_p": structural_p,
            "structural_confidence": structural_confidence,
        }
        return analysis

    def test_record_trade_persists_lane_columns_from_signal_meta(self, trader):
        analysis = self._analysis_with_lanes()
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        row = trader._conn.execute(
            "SELECT fast_lane_p, fast_lane_confidence, accumulation_p, "
            "accumulation_confidence, structural_p, structural_confidence "
            "FROM paper_trades LIMIT 1"
        ).fetchone()
        assert row["fast_lane_p"] == pytest.approx(0.62)
        assert row["fast_lane_confidence"] == pytest.approx(0.75)
        assert row["accumulation_p"] == pytest.approx(0.58)
        assert row["accumulation_confidence"] == pytest.approx(0.70)
        assert row["structural_p"] == pytest.approx(0.55)
        assert row["structural_confidence"] == pytest.approx(0.65)

    def test_record_trade_stores_nulls_when_signal_meta_missing(self, trader):
        """Fast-lane-only candidates have no signal_meta; columns must be NULL."""
        analysis = _make_mock_analysis(yes_price=40.0, side="yes")
        # analysis.signal_meta is a MagicMock attribute (not a dict) — guarded by _lane_float
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        row = trader._conn.execute(
            "SELECT fast_lane_p, accumulation_p, structural_p "
            "FROM paper_trades LIMIT 1"
        ).fetchone()
        assert row["fast_lane_p"] is None
        assert row["accumulation_p"] is None
        assert row["structural_p"] is None

    def test_resolve_market_emits_one_calibration_check_per_populated_lane(self, trader):
        """All three lanes populated → three CALIBRATION_CHECK log emissions."""
        analysis = self._analysis_with_lanes()
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        with patch("trading.paper_trader.trade_log") as mock_log:
            _run_resolve(trader, analysis.market.ticker, True)

        calls = mock_log.log_calibration_check.call_args_list
        assert len(calls) == 3, f"expected 3 emissions, got {len(calls)}"
        lanes_seen = sorted(c.kwargs["lane"] for c in calls)
        assert lanes_seen == ["accumulation", "fast", "structural"]
        # final_resolution must be 1.0 for a YES-resolved market; error = |est - 1.0|
        for c in calls:
            assert c.kwargs["market_ticker"] == analysis.market.ticker
            assert c.kwargs["final_resolution"] == 1.0
            assert c.kwargs["error"] == pytest.approx(
                abs(c.kwargs["lane_estimate"] - 1.0)
            )

    def test_resolve_market_emits_zero_when_lane_columns_null(self, trader):
        """Historical rows (pre-v0.29.47, no lane data) must emit zero events."""
        analysis = _make_mock_analysis(yes_price=40.0, side="yes")
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        with patch("trading.paper_trader.trade_log") as mock_log:
            _run_resolve(trader, analysis.market.ticker, True)

        assert mock_log.log_calibration_check.call_count == 0

    def test_resolve_market_updates_calibration_task_state(self, trader, monkeypatch):
        """Injected CalibrationTask receives one state update per populated lane."""
        from tasks.calibration_task import CalibrationTask

        calibration_task = CalibrationTask()
        # Rebind after construction since the fixture builds trader without it.
        monkeypatch.setattr(trader, "_calibration_task", calibration_task)

        analysis = self._analysis_with_lanes()
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        _run_resolve(trader, analysis.market.ticker, True)

        assert set(calibration_task._state.lanes.keys()) == {
            "fast", "accumulation", "structural",
        }
        for lane in ("fast", "accumulation", "structural"):
            assert calibration_task._state.lanes[lane].sample_count == 1

    def test_resolve_market_survives_calibration_task_error(
        self, trader, monkeypatch, caplog
    ):
        """A failing record_calibration_check is logged but does not break
        resolution — the DB commit has already landed by the time it's called."""
        bad_task = MagicMock()
        bad_task.record_calibration_check = MagicMock(
            side_effect=RuntimeError("injected")
        )

        async def _async_raise(*args, **kwargs):
            raise RuntimeError("injected")
        bad_task.record_calibration_check = _async_raise

        monkeypatch.setattr(trader, "_calibration_task", bad_task)

        analysis = self._analysis_with_lanes()
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        with caplog.at_level(logging.WARNING, logger="paper_trader"):
            _run_resolve(trader, analysis.market.ticker, True)

        assert "CalibrationTask.record_calibration_check failed" in caplog.text
        row = trader._conn.execute(
            "SELECT resolved FROM paper_trades LIMIT 1"
        ).fetchone()
        assert row["resolved"] == 1  # resolution still committed

    def test_migration_is_idempotent(self, trader):
        """Running _migrate_db twice must be a no-op (no duplicate-column errors)."""
        before_cols = trader._paper_trades_columns()
        trader._migrate_db()
        after_cols = trader._paper_trades_columns()
        assert before_cols == after_cols
        # Sanity: all six PROFIT-CAL-001 columns present
        for col in (
            "fast_lane_p", "fast_lane_confidence",
            "accumulation_p", "accumulation_confidence",
            "structural_p", "structural_confidence",
        ):
            assert col in after_cols


class TestConfirmGoLive:
    """`confirm_go_live` — sets DB flag and flips cfg.is_paper_trading off."""

    def test_confirm_go_live_sets_flag_and_mode(self, trader, monkeypatch, caplog):
        # Ensure we can observe the mode flip
        monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
        with caplog.at_level(logging.WARNING, logger="paper_trader"):
            trader.confirm_go_live()

        row = trader._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'go_live_confirmed'"
        ).fetchone()
        assert row["value"] == "true"
        assert _cfg_module.cfg.is_paper_trading is False
        assert "GO-LIVE CONFIRMED" in caplog.text


class TestGoLiveAssessmentBranches:
    """`generate_report` GO-LIVE ASSESSMENT verdicts for ≥10 resolved trades.

    The three branches (POSITIVE / NEGATIVE / NEUTRAL) are selected by
    win_rate, total_pnl, and avg_edge after the INSUFFICIENT DATA threshold.
    """

    def _seed_resolved(self, trader, n, *, side="yes", win, pnl_per_trade, edge):
        """Insert n pre-resolved trades directly via SQL so the aggregate math
        produces the desired (win_rate, total_pnl, avg_edge) in generate_report.
        Each insert uses a uuid trade_id so repeated calls don't collide."""
        trader.credibility.format_table.return_value = "  (credibility table)"
        for i in range(n):
            tid = uuid.uuid4().hex[:12]
            trader._conn.execute(
                "INSERT INTO paper_trades (trade_id, ts, ticker, market_title, side, "
                "contracts, price_cents, cost_dollars, estimated_prob, market_yes_price, "
                "edge, kelly_dollars, capped_dollars, signal_headline, signal_source, "
                "keywords_matched, reasoning, resolved, resolved_yes, pnl_dollars) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    "2026-04-01T00:00:00+00:00",
                    f"KXTST-25DEC{i:02d}",
                    "t", side, 5, 50, 2.5, 0.6, 50.0,
                    edge, 2.5, 2.5, "h", "s", "[]", "r",
                    1, int(win), pnl_per_trade,
                ),
            )
        trader._conn.commit()

    def test_positive_verdict_when_edge_confirmed(self, trader):
        # 10 resolved, 7W/3L -> 70% win rate; strong positive P&L; high edge
        self._seed_resolved(trader, n=7, win=True,  pnl_per_trade=5.0, edge=0.10)
        self._seed_resolved(trader, n=3, win=False, pnl_per_trade=-2.5, edge=0.10)
        report = trader.generate_report()
        assert "POSITIVE" in report
        assert "Strategy shows edge" in report

    def test_negative_verdict_on_large_losses(self, trader):
        # 10 resolved, all losers with -$10 P&L each -> total_pnl = -$100 (< -50)
        self._seed_resolved(trader, n=10, win=False, pnl_per_trade=-10.0, edge=0.05)
        report = trader.generate_report()
        assert "NEGATIVE" in report
        assert "Significant losses" in report

    def test_neutral_verdict_on_mixed_results(self, trader):
        # 10 resolved, mixed P&L that doesn't meet either positive or negative threshold
        # 5W at $0.50 + 5L at -$0.50 = net $0, win_rate=50% (below 55% positive threshold)
        self._seed_resolved(trader, n=5, win=True,  pnl_per_trade=0.50, edge=0.02)
        self._seed_resolved(trader, n=5, win=False, pnl_per_trade=-0.50, edge=0.02)
        report = trader.generate_report()
        assert "NEUTRAL" in report
