"""
Tests for trading/portfolio.py

Covers: position queries, exposure math, concentration checks, and resolution.
"""

import sqlite3

from trading.portfolio import Portfolio, Position


def _pos(
    trade_id: str,
    ticker: str,
    *,
    side: str = "yes",
    cost: float = 10.0,
    ts: str = "2026-01-01T00:00:00+00:00",
):
    return Position(
        trade_id=trade_id,
        ticker=ticker,
        side=side,
        contracts=5,
        cost_dollars=cost,
        price_cents=50,
        estimated_prob=0.60,
        entry_price_cents=50.0,
        ts=ts,
    )


class TestPortfolioQueries:
    def test_add_and_open_positions_returns_copy(self):
        p = Portfolio()
        pos = _pos("t1", "KXTEST")
        p.add(pos)

        positions = p.open_positions("KXTEST")
        assert positions == [pos]
        positions.clear()
        assert len(p.open_positions("KXTEST")) == 1

    def test_open_positions_without_ticker_returns_all(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE"))
        p.add(_pos("t2", "KXTWO"))
        assert len(p.open_positions()) == 2

    def test_tickers_and_position_count(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE"))
        p.add(_pos("t2", "KXONE"))
        p.add(_pos("t3", "KXTWO"))
        assert p.tickers() == {"KXONE", "KXTWO"}
        assert p.position_count() == 3

    def test_exposure_and_total_deployed(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE", cost=12.5))
        p.add(_pos("t2", "KXONE", cost=7.5))
        p.add(_pos("t3", "KXTWO", cost=5.0))
        assert p.exposure("KXONE") == 20.0
        assert p.total_deployed() == 25.0

    def test_latest_ts_returns_most_recent(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE", ts="2026-01-01T00:00:00+00:00"))
        p.add(_pos("t2", "KXONE", ts="2026-01-02T00:00:00+00:00"))
        assert p.latest_ts("KXONE") == "2026-01-02T00:00:00+00:00"
        assert p.latest_ts("KXMISS") is None


class TestPortfolioRisk:
    def test_concentration_ok_when_within_cap(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE", cost=20.0))
        assert p.is_concentration_ok("KXONE", additional_dollars=5.0, max_ticker_pct=0.25, bankroll=100.0)

    def test_concentration_fails_when_exceeding_cap(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE", cost=20.0))
        assert not p.is_concentration_ok("KXONE", additional_dollars=6.0, max_ticker_pct=0.25, bankroll=100.0)

    def test_non_positive_bankroll_or_cap_disables_concentration_check(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE", cost=20.0))
        assert p.is_concentration_ok("KXONE", additional_dollars=100.0, max_ticker_pct=0.25, bankroll=0.0)
        assert p.is_concentration_ok("KXONE", additional_dollars=100.0, max_ticker_pct=0.0, bankroll=100.0)

    def test_resolve_removes_all_positions_for_ticker(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE"))
        p.add(_pos("t2", "KXONE", side="no"))
        p.add(_pos("t3", "KXTWO"))
        p.resolve("KXONE")
        assert p.open_positions("KXONE") == []
        assert len(p.open_positions("KXTWO")) == 1


class TestPortfolioLoadFromDb:
    def test_load_from_db_reads_only_unresolved_positions(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT,
                ticker TEXT,
                side TEXT,
                contracts INTEGER,
                cost_dollars REAL,
                price_cents INTEGER,
                estimated_prob REAL,
                entry_price_cents REAL,
                ts TEXT,
                resolved INTEGER
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO paper_trades
            (trade_id, ticker, side, contracts, cost_dollars, price_cents,
             estimated_prob, entry_price_cents, ts, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("t1", "KXONE", "yes", 5, 10.0, 50, 0.6, 50.0, "2026-01-01T00:00:00+00:00", 0),
                ("t2", "KXONE", "no", 5, 8.0, 40, 0.4, 60.0, "2026-01-02T00:00:00+00:00", 1),
                ("t3", "KXTWO", "yes", 5, 12.0, 60, 0.7, 60.0, "2026-01-03T00:00:00+00:00", 0),
            ],
        )
        conn.commit()

        p = Portfolio()
        p.load_from_db(conn)

        assert {pos.trade_id for pos in p.open_positions()} == {"t1", "t3"}

    def test_load_from_db_tolerates_legacy_market_yes_price_column(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT,
                ticker TEXT,
                side TEXT,
                contracts INTEGER,
                cost_dollars REAL,
                price_cents INTEGER,
                estimated_prob REAL,
                market_yes_price REAL,
                ts TEXT,
                resolved INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades
            (trade_id, ticker, side, contracts, cost_dollars, price_cents,
             estimated_prob, market_yes_price, ts, resolved)
            VALUES ('legacy', 'KXLEG', 'yes', 5, 10.0, 50, 0.6, 51.0,
                    '2026-01-01T00:00:00+00:00', 0)
            """
        )
        conn.commit()

        p = Portfolio()
        p.load_from_db(conn)

        positions = p.open_positions("KXLEG")
        assert len(positions) == 1
        assert positions[0].entry_price_cents == 51.0


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-004 (2026-05-25) — open_positions_by_prefix
# ---------------------------------------------------------------------------

class TestOpenPositionsByPrefix:
    def test_returns_positions_matching_prefix(self):
        p = Portfolio()
        p.add(_pos("t1", "KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH"))
        p.add(_pos("t2", "KXTXRUNOFFENDORSE-26MAY26-DJT-KPAX"))
        p.add(_pos("t3", "KXTRUMPIRAN-26JUN01"))
        results = p.open_positions_by_prefix("KXTXRUNOFFENDORSE")
        assert len(results) == 2
        assert {r.ticker for r in results} == {
            "KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH",
            "KXTXRUNOFFENDORSE-26MAY26-DJT-KPAX",
        }

    def test_prefix_match_requires_word_boundary(self):
        """KXFOO must not match KXFOOBAR — prefix must be followed by '-' or end."""
        p = Portfolio()
        p.add(_pos("t1", "KXFOOBAR-26MAY26"))
        assert p.open_positions_by_prefix("KXFOO") == []
        assert len(p.open_positions_by_prefix("KXFOOBAR")) == 1

    def test_empty_prefix_returns_empty_list(self):
        p = Portfolio()
        p.add(_pos("t1", "KXTEST"))
        assert p.open_positions_by_prefix("") == []

    def test_exact_ticker_match_works(self):
        """Some tickers may be the prefix itself (no -suffix)."""
        p = Portfolio()
        p.add(_pos("t1", "KXTEST"))
        assert len(p.open_positions_by_prefix("KXTEST")) == 1

    def test_no_match_returns_empty(self):
        p = Portfolio()
        p.add(_pos("t1", "KXONE-A"))
        p.add(_pos("t2", "KXONE-B"))
        assert p.open_positions_by_prefix("KXTWO") == []
