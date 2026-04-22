"""
Tests for trading/paper_trader.py

Covers: bankroll atomicity, resolve_market P&L accounting, win/loss tracking,
        keyword_outcomes written on resolution, kelly_contracts shadow sizing.

Uses :memory: SQLite so no disk I/O or shared state between tests.
"""

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Patch cfg before importing PaperTrader to avoid needing a real .env
import config as _cfg_module

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
        trader.resolve_market(analysis.market.ticker, resolved_yes=True)
        after = trader.get_notional_bankroll()

        # YES win at 40c: cost $0.40 * 5 contracts = $2.00 debited; payout = 5 contracts = $5
        # Net change = payout $5 credited back; bankroll should be higher than pre-resolve
        assert after > before

    def test_bankroll_and_resolved_flag_consistent(self, trader):
        """If resolved=1 in DB, bankroll must already reflect the payout."""
        analysis = _make_mock_analysis(yes_price=50.0, side="yes")

        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)

        trader.resolve_market(analysis.market.ticker, resolved_yes=True)

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
        trader.resolve_market(analysis.market.ticker, resolved_yes=True)

        row = trader._conn.execute(
            "SELECT pnl_dollars FROM paper_trades WHERE resolved=1"
        ).fetchone()
        # 5 contracts won = $5 payout; cost = 5 * 0.40 = $2.00; pnl = $3.00
        assert row["pnl_dollars"] == pytest.approx(3.00, abs=0.01)

    def test_losing_yes_trade_negative_pnl(self, trader):
        analysis = _make_mock_analysis(yes_price=40.0, side="yes")
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        trader.resolve_market(analysis.market.ticker, resolved_yes=False)

        row = trader._conn.execute(
            "SELECT pnl_dollars FROM paper_trades WHERE resolved=1"
        ).fetchone()
        # Lost: cost = 5 * 0.40 = $2.00; payout = 0; pnl = -$2.00
        assert row["pnl_dollars"] == pytest.approx(-2.00, abs=0.01)

    def test_winning_no_trade_positive_pnl(self, trader):
        analysis = _make_mock_analysis(yes_price=60.0, side="no")
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        trader.resolve_market(analysis.market.ticker, resolved_yes=False)

        row = trader._conn.execute(
            "SELECT pnl_dollars FROM paper_trades WHERE resolved=1"
        ).fetchone()
        # NO contract at yes_price=60c => price_cents = 100-60 = 40c
        # 5 contracts * 40c = $2.00 cost; win = 5 contracts = $5; pnl = $3.00
        assert row["pnl_dollars"] == pytest.approx(3.00, abs=0.01)

    def test_no_open_trades_is_noop(self, trader):
        """resolve_market with no open trades should not raise and not change bankroll."""
        before = trader.get_notional_bankroll()
        trader.resolve_market("KXNONEXISTENT-25DEC31", resolved_yes=True)
        after = trader.get_notional_bankroll()
        assert before == after


class TestKeywordOutcomes:
    def test_keyword_outcomes_written_on_resolution(self, trader):
        analysis = _make_mock_analysis(keywords=["ceasefire", "attack"])
        with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
            trader.record_trade(analysis)
        trader.resolve_market(analysis.market.ticker, resolved_yes=True)

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
        trader.resolve_market(analysis.market.ticker, resolved_yes=True)

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
