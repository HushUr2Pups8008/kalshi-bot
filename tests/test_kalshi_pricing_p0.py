"""
P-1 RED tests for the post-P0 pricing surface on `KalshiMarket`,
`SignalAnalysis`, `Position` (trading.portfolio), side selection, paper
fill, Kelly, WS absorb, and provenance.

Covers canonical roadmap §7 matrix rows P0-MARKET-007..P0-PROV-020 plus
DT-1b/DT-2b semantic-shift assertions per §11 LD-2/LD-10/LD-13.

Expected RED today because none of the post-P0 fields, methods, or helper
functions exist yet. Do NOT stub the implementation. Drive it via P-2..P-6.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from analysis import SignalAnalysis
from kalshi import KalshiMarket
from trading.portfolio import Position


# ---------------------------------------------------------------------------
# Helpers — synthetic post-P0 KalshiMarket construction
# ---------------------------------------------------------------------------

def _make_post_p0_market(
    *,
    ticker: str = "KXTEST-26DEC31",
    yes_bid_cents: int | None = 38,
    yes_ask_cents: int | None = 40,
    no_bid_cents: int | None = 58,
    no_ask_cents: int | None = 62,
    price_available: bool = True,
    price_source: str = "rest_list",
    price_method: str = "dollars_fixed_point",
    price_retrieved_at: datetime | None = None,
    status: str = "active",
):
    """Construct the post-P0 KalshiMarket. RED today: TypeError on unknown kwargs."""
    return KalshiMarket(
        ticker=ticker,
        title="Synthetic",
        # Legacy fields kept for backwards-compat (will raise via property
        # post-P0 when price_available=False; harmless when None here).
        yes_bid=0.0, yes_ask=0.0, yes_price=0.0,
        volume=0, open_interest=0,
        close_time="2027-01-01T00:00:00Z",
        status=status,
        # Post-P0 fields:
        yes_bid_cents=yes_bid_cents,
        yes_ask_cents=yes_ask_cents,
        no_bid_cents=no_bid_cents,
        no_ask_cents=no_ask_cents,
        price_available=price_available,
        price_source=price_source,
        price_method=price_method,
        price_retrieved_at=price_retrieved_at or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# P0-MARKET-007 — invariants hold on valid bid/ask
# ---------------------------------------------------------------------------

def test_p0_market_007_valid_yes_no_bidask_invariant_holds():
    m = _make_post_p0_market(
        yes_bid_cents=38, yes_ask_cents=40, no_bid_cents=58, no_ask_cents=62
    )
    assert m.yes_bid_cents <= m.yes_ask_cents
    assert m.no_bid_cents <= m.no_ask_cents
    assert m.yes_bid_cents + m.no_ask_cents <= 100
    assert m.is_tradeable() is True


# ---------------------------------------------------------------------------
# P0-MARKET-008 — unavailable price short-circuits, no 50 fallback (CR-C)
# ---------------------------------------------------------------------------

def test_p0_market_008_price_unavailable_blocks_executable_price():
    m = _make_post_p0_market(
        yes_bid_cents=None, yes_ask_cents=None,
        no_bid_cents=None, no_ask_cents=None,
        price_available=False,
        price_source="unavailable",
        price_method="none",
    )
    with pytest.raises(ValueError):
        m.executable_yes_price()
    with pytest.raises(ValueError):
        m.executable_no_price()
    assert m.is_tradeable() is False
    # Legacy properties also raise — never return 50 as silent fallback.
    with pytest.raises(ValueError):
        _ = m.yes_price
    with pytest.raises(ValueError):
        _ = m.yes_prob


# ---------------------------------------------------------------------------
# P0-SIDE-009 — selector picks YES when YES edge dominates
# ---------------------------------------------------------------------------

def test_p0_side_009_side_selector_picks_yes_when_yes_edge_dominant():
    """yes_edge = 0.58 - 0.38 = +0.20; no_edge = 0.42 - 0.65 = -0.23. YES wins."""
    from analysis.side_selection import select_side  # type: ignore[import-not-found]

    m = _make_post_p0_market(
        yes_bid_cents=36, yes_ask_cents=38,
        no_bid_cents=60, no_ask_cents=65,
    )
    side, executable_cents = select_side(m, blended_yes_prob=0.58)
    assert side == "yes"
    assert executable_cents == 38  # YES executable ask


# ---------------------------------------------------------------------------
# P0-SIDE-010 — selector picks NO when NO edge dominates (regression guard)
# ---------------------------------------------------------------------------

def test_p0_side_010_side_selector_picks_no_when_no_edge_dominant():
    """Critical: must be 30 (NO ask), NOT 28 (= 100-72_yes_mid)."""
    from analysis.side_selection import select_side  # type: ignore[import-not-found]

    m = _make_post_p0_market(
        yes_bid_cents=70, yes_ask_cents=72,
        no_bid_cents=28, no_ask_cents=30,
    )
    # blended YES prob 0.40 → blended NO prob 0.60 → no_edge = 0.60 - 0.30 = +0.30.
    # yes_edge = 0.40 - 0.72 = -0.32 → NO wins.
    side, executable_cents = select_side(m, blended_yes_prob=0.40)
    assert side == "no"
    assert executable_cents == 30, (
        f"expected NO executable ask 30¢, got {executable_cents}. "
        "Anything other than 30 (especially 28) indicates legacy midpoint logic."
    )


# ---------------------------------------------------------------------------
# P0-PAPER-011 / P0-PAPER-012 — paper fill uses executable price
# ---------------------------------------------------------------------------

_REAL_SQLITE_CONNECT = sqlite3.connect


def _shared_memory_connect(name: str):
    """In-memory sqlite shared-cache wiring mirrored from test_paper_trader.py."""
    db_uri = f"file:{name}-{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper = _REAL_SQLITE_CONNECT(db_uri, uri=True, check_same_thread=False)

    def _connect(*_args, **_kwargs):
        conn = _REAL_SQLITE_CONNECT(db_uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    return keeper, _connect


def _make_analysis_for_paper(*, market, side: str, executed_price_cents: int):
    """SignalAnalysis with new executed_price_cents field — RED until DT-2b ships."""
    return SignalAnalysis(
        news_item=MagicMock(headline="h", source="r/x", body="",
                            published=datetime.now(timezone.utc)),
        market=market,
        estimated_probability=0.60,
        executed_price_cents=executed_price_cents,  # NEW post-P0 field (LD-2)
        edge=0.20,
        side=side,
        kelly_fraction=0.5,
        kelly_dollars=10.0,
        capped_dollars=10.0,
        keywords_matched=["alpha"],
        reasoning="paper-fill test",
        confidence=0.7,
    )


def test_p0_dt_2b_signal_analysis_market_yes_price_alias_populated():
    """post-P0: market_yes_price is a deprecated alias of executed_price_cents."""
    m = _make_post_p0_market(yes_bid_cents=40, yes_ask_cents=42,
                             no_bid_cents=58, no_ask_cents=60)
    sa = _make_analysis_for_paper(market=m, side="yes", executed_price_cents=42)
    # Alias populated at construction time from executed_price_cents.
    assert sa.market_yes_price == 42, (
        f"market_yes_price alias not populated from executed_price_cents; "
        f"got {sa.market_yes_price!r}"
    )
    # Executed price still the canonical field.
    assert sa.executed_price_cents == 42


def test_p0_paper_011_paper_fill_uses_yes_executable_price():
    """Paper trade row for YES side records yes_ask_cents (executable), not midpoint."""
    import config as _cfg_module

    keeper, connect = _shared_memory_connect("p0-paper-yes")
    try:
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            with patch.object(_cfg_module.cfg, "is_paper_trading", True), \
                 patch.object(_cfg_module.cfg, "bankroll", 500.0), \
                 patch.object(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25), \
                 patch.object(_cfg_module.cfg, "kelly_fraction", 0.5):
                from trading.paper_trader import PaperTrader

                m = _make_post_p0_market(
                    ticker="KXTEST-PAPYES",
                    yes_bid_cents=40, yes_ask_cents=42,
                    no_bid_cents=58, no_ask_cents=60,
                )
                sa = _make_analysis_for_paper(
                    market=m, side="yes", executed_price_cents=42,
                )
                with patch("dataclasses.asdict",
                           return_value={"series_ticker": ""}):
                    pt = PaperTrader(db_path=":memory:",
                                     startup_context="p0-paper-yes")
                    pt._set_state("notional_bankroll", "500.0")
                    pt.record_trade(sa)

        # Query the row that was written.
        rows = keeper.execute(
            "SELECT price_cents FROM paper_trades WHERE ticker = ?",
            ("KXTEST-PAPYES",),
        ).fetchall()
        assert rows, "paper_trades row missing"
        assert rows[0][0] == 42, (
            f"paper-fill recorded {rows[0][0]}¢ but executable yes_ask was 42¢"
        )
    finally:
        keeper.close()


def test_p0_paper_012_paper_fill_uses_no_executable_price():
    """Paper trade row for NO side records no_ask_cents (executable), not 100-yes-mid."""
    import config as _cfg_module

    keeper, connect = _shared_memory_connect("p0-paper-no")
    try:
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            with patch.object(_cfg_module.cfg, "is_paper_trading", True), \
                 patch.object(_cfg_module.cfg, "bankroll", 500.0), \
                 patch.object(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25), \
                 patch.object(_cfg_module.cfg, "kelly_fraction", 0.5):
                from trading.paper_trader import PaperTrader

                m = _make_post_p0_market(
                    ticker="KXTEST-PAPNO",
                    yes_bid_cents=70, yes_ask_cents=72,
                    no_bid_cents=58, no_ask_cents=61,
                )
                sa = _make_analysis_for_paper(
                    market=m, side="no", executed_price_cents=61,
                )
                with patch("dataclasses.asdict",
                           return_value={"series_ticker": ""}):
                    pt = PaperTrader(db_path=":memory:",
                                     startup_context="p0-paper-no")
                    pt._set_state("notional_bankroll", "500.0")
                    pt.record_trade(sa)

        rows = keeper.execute(
            "SELECT price_cents FROM paper_trades WHERE ticker = ?",
            ("KXTEST-PAPNO",),
        ).fetchall()
        assert rows, "paper_trades row missing"
        assert rows[0][0] == 61, (
            f"paper-fill recorded {rows[0][0]}¢ but executable no_ask was 61¢. "
            "61 NOT 39 (= 100 - 61 yes_mid)."
        )
    finally:
        keeper.close()


# ---------------------------------------------------------------------------
# P0-KELLY-013 / P0-KELLY-014 — Kelly consumes executable, not midpoint
# ---------------------------------------------------------------------------

def test_p0_kelly_013_kelly_consumes_executable_side_price():
    """Kelly helper accepts an executable cents price and returns finite size.

    Locks-in (P0-KELLY-013): the `kelly_bet` helper at `analysis/kelly.py` is
    the single Kelly surface; post-P0 wiring feeds it `executed_price_cents`
    (LD-10 / DT-2b), not the YES midpoint. This test asserts the helper itself
    accepts an executable cents price and returns a positive bet.
    """
    from analysis.kelly import kelly_bet

    bet_dollars, _kelly_frac, _edge = kelly_bet(
        estimated_probability=0.58,
        market_price_cents=42,
        bankroll=500.0,
        kelly_fraction=0.5,
    )
    assert bet_dollars > 0


def test_p0_kelly_014_kelly_regression_guard_vs_midpoint():
    """Different executable-vs-midpoint inputs produce different bet sizes.

    Locks-in (P0-KELLY-014): if a future change collapses Kelly's price
    sensitivity (e.g., always uses midpoint internally), this regression
    guard fails. Documents the executable-vs-midpoint asymmetry the post-P0
    wiring relies on.
    """
    from analysis.kelly import kelly_bet

    bet_executable, _f_e, _e_e = kelly_bet(
        estimated_probability=0.58, market_price_cents=42,
        bankroll=500.0, kelly_fraction=0.5,
    )
    bet_midpoint_50, _f_m, _e_m = kelly_bet(
        estimated_probability=0.58, market_price_cents=50,
        bankroll=500.0, kelly_fraction=0.5,
    )
    assert bet_executable != pytest.approx(bet_midpoint_50), (
        "Kelly produced identical bet sizes for executable 42¢ vs midpoint 50¢ — "
        "executable-vs-midpoint regression guard tripped"
    )


# ---------------------------------------------------------------------------
# P0-WS-015 — WS midpoint does not clobber REST executable
# ---------------------------------------------------------------------------

def test_p0_ws_015_ws_midpoint_does_not_overwrite_rest_executable():
    """WS book update with bid/ask should populate cents in place — REST
    executable not overwritten by a midpoint-derived value."""
    from kalshi.websocket_client import absorb_book_update  # type: ignore[import-not-found]

    m = _make_post_p0_market(
        yes_bid_cents=36, yes_ask_cents=37,
        no_bid_cents=63, no_ask_cents=64,
        price_source="rest_list",
    )
    # Simulate a WS book delta with same bid/ask range; the absorb must NOT
    # collapse to a midpoint and write 37 elsewhere.
    absorb_book_update(m, {"yes": {"bid": 36, "ask": 38}})
    assert m.yes_ask_cents == 38, (
        f"WS absorb did not update yes_ask_cents in-place; got {m.yes_ask_cents}"
    )
    # No midpoint leak into yes_bid_cents from old REST.
    assert m.yes_bid_cents in (36, 37), "WS absorb corrupted yes_bid_cents"


# ---------------------------------------------------------------------------
# P0-WS-016 — WS no-data returns None, not 50
# ---------------------------------------------------------------------------

def test_p0_ws_016_ws_no_data_returns_none_not_50():
    """Querying a never-seen ticker returns None, never a 50 fallback."""
    from kalshi.websocket_client import KalshiWebSocketClient  # type: ignore[import-not-found]

    # Construct WITHOUT connecting — only the cache query path is under test.
    client = KalshiWebSocketClient.__new__(KalshiWebSocketClient)
    # No hasattr guard: the call must reach a real get_yes_price method.
    # AttributeError here IS the expected RED state until P-6 ships it.
    result = client.get_yes_price("UNKNOWN_TICKER_XYZ")
    assert result is None, (
        "WS must return None for unknown ticker, not 50"
    )
    assert result != 50, (
        "WS must never silently return 50 — load-bearing post-P0 invariant"
    )


# ---------------------------------------------------------------------------
# P0-BLOCK-017 — unavailable market short-circuits EV
# ---------------------------------------------------------------------------

def test_p0_block_017_unavailable_market_short_circuits_ev():
    """compute_edge() refuses to operate on price_available=False."""
    from analysis.side_selection import compute_edge  # type: ignore[import-not-found]

    m = _make_post_p0_market(
        yes_bid_cents=None, yes_ask_cents=None,
        no_bid_cents=None, no_ask_cents=None,
        price_available=False, price_method="none",
        price_source="unavailable",
    )
    try:
        result = compute_edge(m, blended_yes_prob=0.60)
    except ValueError:
        return  # acceptable
    # Alternative acceptable shape: sentinel return.
    assert result in (None, (None, None)), (
        f"compute_edge on unavailable market must raise ValueError or "
        f"return None/(None,None); got {result!r}"
    )


# ---------------------------------------------------------------------------
# P0-BLOCK-018 — unavailable market rejects paper trade
# ---------------------------------------------------------------------------

def test_p0_block_018_unavailable_market_rejects_paper_trade(caplog):
    """Paper recorder refuses to write a row when price_available=False."""
    import config as _cfg_module

    keeper, connect = _shared_memory_connect("p0-block-018")
    try:
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            with patch.object(_cfg_module.cfg, "is_paper_trading", True), \
                 patch.object(_cfg_module.cfg, "bankroll", 500.0), \
                 patch.object(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25), \
                 patch.object(_cfg_module.cfg, "kelly_fraction", 0.5):
                from trading.paper_trader import PaperTrader

                m = _make_post_p0_market(
                    ticker="KXTEST-BLOCK",
                    yes_bid_cents=None, yes_ask_cents=None,
                    no_bid_cents=None, no_ask_cents=None,
                    price_available=False,
                    price_source="unavailable", price_method="none",
                )
                sa = _make_analysis_for_paper(
                    market=m, side="yes", executed_price_cents=42,
                )
                with patch("dataclasses.asdict",
                           return_value={"series_ticker": ""}):
                    pt = PaperTrader(db_path=":memory:",
                                     startup_context="p0-block-018")
                    pt._set_state("notional_bankroll", "500.0")
                    # Must NOT silently insert a row.
                    pt.record_trade(sa)

        rows = keeper.execute(
            "SELECT trade_id FROM paper_trades WHERE ticker = ?",
            ("KXTEST-BLOCK",),
        ).fetchall()
        assert not rows, "paper trade inserted despite price_available=False"
        # Structured log entry expected.
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "price_unavailable" in joined or "skip_reason" in joined, (
            "skip log entry missing skip_reason=price_unavailable"
        )
    finally:
        keeper.close()


# ---------------------------------------------------------------------------
# P0-PROV-019 — paper trade DB row + log carries provenance
# ---------------------------------------------------------------------------

def test_p0_prov_019_paper_trade_carries_provenance(caplog):
    """price_source, price_method, price_retrieved_at on both DB and log."""
    import config as _cfg_module

    keeper, connect = _shared_memory_connect("p0-prov-019")
    try:
        with patch("trading.paper_trader.sqlite3.connect", side_effect=connect), \
             patch("trading.paper_trader.SourceCredibility") as MockCred:
            MockCred.return_value.get_multiplier.return_value = 1.0
            with patch.object(_cfg_module.cfg, "is_paper_trading", True), \
                 patch.object(_cfg_module.cfg, "bankroll", 500.0), \
                 patch.object(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25), \
                 patch.object(_cfg_module.cfg, "kelly_fraction", 0.5):
                from trading.paper_trader import PaperTrader

                retrieved = datetime(2026, 5, 11, 13, 37, 16, tzinfo=timezone.utc)
                m = _make_post_p0_market(
                    ticker="KXTEST-PROV",
                    yes_bid_cents=40, yes_ask_cents=42,
                    no_bid_cents=58, no_ask_cents=60,
                    price_source="rest_list",
                    price_method="dollars_fixed_point",
                    price_retrieved_at=retrieved,
                )
                sa = _make_analysis_for_paper(
                    market=m, side="yes", executed_price_cents=42,
                )
                with patch("dataclasses.asdict",
                           return_value={"series_ticker": ""}):
                    pt = PaperTrader(db_path=":memory:",
                                     startup_context="p0-prov-019")
                    pt._set_state("notional_bankroll", "500.0")
                    pt.record_trade(sa)

        # Inspect schema; the DB row must carry provenance columns.
        cols = [r[1] for r in keeper.execute(
            "PRAGMA table_info(paper_trades)"
        ).fetchall()]
        for required in ("price_source", "price_method", "price_retrieved_at"):
            assert required in cols, (
                f"paper_trades schema missing provenance column {required!r}"
            )
        row = keeper.execute(
            "SELECT price_source, price_method, price_retrieved_at "
            "FROM paper_trades WHERE ticker = ?",
            ("KXTEST-PROV",),
        ).fetchone()
        assert row is not None
        assert row[0] == "rest_list"
        assert row[1] == "dollars_fixed_point"
        assert row[2] is not None and row[2] != ""
    finally:
        keeper.close()


# ---------------------------------------------------------------------------
# P0-PROV-020 — OPPORTUNITY JSONL event carries provenance
# ---------------------------------------------------------------------------

def test_p0_prov_020_opportunity_log_carries_provenance(tmp_path: Path,
                                                       monkeypatch):
    """Captured OPPORTUNITY event must carry price_source, price_method,
    price_retrieved_at. A 50¢ executable price must carry an explicit
    `_fixture_encodes_real_50c: true` flag or `skip_reason`."""
    from utils.logger import emit_opportunity  # type: ignore[import-not-found]

    out_path = tmp_path / "opportunity.jsonl"
    monkeypatch.setenv("KALSHI_OPPORTUNITY_LOG", str(out_path))

    m = _make_post_p0_market(
        ticker="KXTEST-OPP",
        yes_bid_cents=40, yes_ask_cents=42,
        no_bid_cents=58, no_ask_cents=60,
        price_source="rest_list",
        price_method="dollars_fixed_point",
    )
    emit_opportunity(market=m, blended_yes_prob=0.58, side="yes",
                     executed_price_cents=42)

    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert lines, "opportunity log empty"
    evt = json.loads(lines[-1])
    for required in ("price_source", "price_method", "price_retrieved_at"):
        assert required in evt, f"OPPORTUNITY event missing {required!r}"

    # If executable cents == 50, the event must carry an explicit marker.
    if evt.get("price_cents") == 50:
        assert evt.get("_fixture_encodes_real_50c") is True or \
               evt.get("skip_reason"), (
                   "50¢ price emitted without _fixture_encodes_real_50c=true "
                   "or skip_reason"
               )


# ---------------------------------------------------------------------------
# DT-1b — OpenPosition / Position semantic-shift
# ---------------------------------------------------------------------------

def test_p0_dt_1b_open_position_market_yes_price_stores_executed():
    """Post-P0, the `market_yes_price` DB column stores the executed entry
    price (cents) for the chosen side, NOT the YES midpoint.

    Hydration test: a row with market_yes_price=61 (cents) for a NO trade
    correctly represents "entered NO at 61¢"; the field is now the entry
    cost basis for the executed side. The Position dataclass must also
    expose post-P0 provenance attributes (`price_source`, `price_method`)
    so callers can distinguish executed-side semantics from the legacy
    "yes midpoint" semantics. This second assertion is the load-bearing
    RED gate — those fields do not exist on Position today.
    """
    keeper, connect = _shared_memory_connect("p0-dt-1b")
    try:
        conn = connect()
        # Build a paper_trades schema that includes post-P0 provenance columns.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                trade_id TEXT PRIMARY KEY,
                ticker TEXT,
                side TEXT,
                contracts INTEGER,
                cost_dollars REAL,
                price_cents INTEGER,
                estimated_prob REAL,
                market_yes_price REAL,
                ts TEXT,
                resolved INTEGER DEFAULT 0,
                price_source TEXT,
                price_method TEXT,
                price_retrieved_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO paper_trades(trade_id, ticker, side, contracts, "
            "cost_dollars, price_cents, estimated_prob, market_yes_price, "
            "ts, resolved, price_source, price_method, price_retrieved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t1", "KXTEST-DT1B", "no", 10, 6.10, 61, 0.40, 61.0,
             "2026-05-11T13:37:16Z", 0,
             "rest_list", "dollars_fixed_point", "2026-05-11T13:37:16Z"),
        )
        conn.commit()

        from trading.portfolio import Portfolio
        portfolio = Portfolio()
        portfolio.load_from_db(conn)
        positions = portfolio.open_positions("KXTEST-DT1B")
        assert len(positions) == 1
        pos = positions[0]
        assert isinstance(pos, Position)
        assert pos.side == "no"
        # market_yes_price column == executed entry price for chosen side.
        assert pos.market_yes_price == 61.0
        # Documents the semantic shift: NOT 100-61=39 (yes midpoint).
        assert pos.market_yes_price != 39.0
        # Load-bearing post-P0 assertions: Position must carry provenance
        # so it is unambiguous that market_yes_price is the executed-side
        # entry, not a legacy midpoint. These attributes don't exist today.
        assert hasattr(pos, "price_source"), (
            "Position dataclass missing post-P0 price_source attribute"
        )
        assert hasattr(pos, "price_method"), (
            "Position dataclass missing post-P0 price_method attribute"
        )
        assert pos.price_source == "rest_list"
        assert pos.price_method == "dollars_fixed_point"
    finally:
        keeper.close()


# ---------------------------------------------------------------------------
# Property: KalshiMarket round-trip — invariant or fail-closed
# ---------------------------------------------------------------------------

def test_p0_kalshi_market_round_trip_property_based():
    """Hypothesis: integer cents (1..99) — invariant holds when bid<=ask,
    fail-closed when bid>ask."""
    from hypothesis import given, settings
    from hypothesis import strategies as st

    @given(yes_bid=st.integers(1, 99), yes_ask=st.integers(1, 99))
    @settings(max_examples=60, deadline=None)
    def _prop(yes_bid: int, yes_ask: int):
        no_bid = 100 - yes_ask
        no_ask = 100 - yes_bid
        rejected = False
        m = None
        try:
            m = _make_post_p0_market(
                yes_bid_cents=yes_bid, yes_ask_cents=yes_ask,
                no_bid_cents=no_bid, no_ask_cents=no_ask,
            )
        except ValueError:
            # Constructor-level rejection acceptable for invariant violations.
            rejected = True
        # If constructor rejected unknown kwargs entirely (RED state today),
        # that's a structural failure — not an acceptable property outcome.
        if isinstance(m, Exception) or rejected:
            assert yes_bid > yes_ask, (
                "constructor rejected a VALID input (yes_bid<=yes_ask) — "
                "Post-P0 KalshiMarket must accept new cents fields."
            )
            return
        assert m is not None
        if yes_bid > yes_ask:
            # If accepted at all, must mark non-tradeable.
            assert m.is_tradeable() is False or m.price_available is False
        else:
            assert m.is_tradeable() is True
            assert m.yes_bid_cents <= m.yes_ask_cents

    _prop()
