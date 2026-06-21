import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import scripts.performance_analysis as pa
from scripts.throughput_operator_metrics import ThroughputOperatorSummary


SENTINEL = "2026-05-01T00:00:00+00:00"


def _create_db(path: Path, *, sentinel: str | None = SENTINEL) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT)")
        if sentinel is not None:
            conn.execute(
                "INSERT INTO bot_state (key, value) VALUES (?, ?)",
                ("p0_price_fix_deployed_ts", sentinel),
            )
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                ts TEXT,
                ticker TEXT,
                series_ticker TEXT,
                resolved INTEGER,
                pnl_dollars REAL,
                match_score REAL,
                contracts INTEGER,
                kelly_contracts INTEGER,
                price_cents REAL,
                cost_dollars REAL,
                resolved_yes INTEGER,
                side TEXT
            )
            """
        )
        rows = [
            ("pre-win", "2026-04-30T10:00:00+00:00", 1.0, 1),
            ("pre-win-2", "2026-04-30T11:00:00+00:00", 1.0, 1),
            ("post-loss", "2026-05-01T10:00:00+00:00", -1.0, 0),
            ("post-loss-2", "2026-05-01T11:00:00+00:00", -1.0, 0),
        ]
        conn.executemany(
            """
            INSERT INTO paper_trades (
                trade_id, ts, series_ticker, resolved, pnl_dollars, match_score,
                contracts, kelly_contracts, price_cents, cost_dollars,
                resolved_yes, side
            )
            VALUES (?, ?, 'KXGOV', 1, ?, 0.15, 5, 2, 50, 2.5, ?, 'yes')
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_lifetime_sections_split_pre_and_post_p0_cohorts(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path)
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    series = pa.section_per_series_win_rate()
    match_score = pa.section_match_score_calibration()
    kelly = pa.section_kelly_shadow()

    assert "P0 cohort boundary: 2026-05-01T00:00:00+00:00" in series
    assert "Pre-P0 (frozen) cohort:" in series
    assert "Post-P0 cohort:" in series
    assert "KXGOV" in series
    assert "100.0%" in series
    assert "$+2.00" in series
    assert "0.0%" in series
    assert "$-2.00" in series
    assert "KXGOV      4" not in series

    assert "Pre-P0 (frozen) cohort:" in match_score
    assert "Post-P0 cohort:" in match_score
    assert "0.10-0.20" in match_score
    assert "100.0%" in match_score
    assert "$+2.00" in match_score
    assert "0.0%" in match_score
    assert "$-2.00" in match_score
    assert "0.10-0.20        4" not in match_score

    assert "Pre-P0 (frozen) cohort:" in kelly
    assert "Post-P0 cohort:" in kelly
    assert "Flat-5 (actual) vs Kelly shadow sizing (2 resolved trades):" in kelly
    assert "Flat-5 (actual) vs Kelly shadow sizing (4 resolved trades):" not in kelly


def test_per_series_win_rate_normalizes_legacy_polymarket_series(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, series_ticker, resolved, pnl_dollars,
                match_score, contracts, kelly_contracts, price_cents, cost_dollars,
                resolved_yes, side
            )
            VALUES (?, ?, ?, 'polymarket_us', 1, ?, 0.15, 5, 2, 50, 2.5, ?, 'yes')
            """,
            [
                (
                    "pm-legacy-win",
                    "2026-05-02T10:00:00+00:00",
                    "ewc-usse-me-2026-11-03-dem",
                    1.0,
                    1,
                ),
                (
                    "pm-legacy-loss",
                    "2026-05-02T11:00:00+00:00",
                    "ewc-usse-me-2026-11-03-rep",
                    -1.0,
                    0,
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    output = pa.section_per_series_win_rate()

    post_section = output.split("Post-P0 cohort:", 1)[1]
    assert "polymarket_us:ewc-usse-me" in post_section
    assert "polymarket_us      " not in post_section


def test_lifetime_sections_fail_soft_when_p0_boundary_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path, sentinel=None)
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    outputs = [
        pa.section_per_series_win_rate(),
        pa.section_match_score_calibration(),
        pa.section_kelly_shadow(),
    ]

    for output in outputs:
        assert "P0 cohort boundary missing" in output
        assert "not reporting blended lifetime aggregates" in output
        assert "KXGOV" not in output
        assert "4 resolved trades" not in output


def test_load_db_state_tolerates_missing_bot_state_table(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY, ts TEXT)")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    assert pa.load_db_state() == {}


def test_lifetime_sections_preserve_schema_migration_messages(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY, ts TEXT)")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    assert "series_ticker column not found" in pa.section_per_series_win_rate()
    assert "match_score column not found" in pa.section_match_score_calibration()
    assert "kelly_contracts column not found" in pa.section_kelly_shadow()


def test_lifetime_sections_fail_soft_when_p0_boundary_malformed(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path, sentinel="not-a-timestamp")
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    output = pa.section_per_series_win_rate()

    assert "P0 cohort boundary missing" in output
    assert "not reporting blended lifetime aggregates" in output
    assert "KXGOV" not in output


def test_p0_boundary_normalizes_offset_to_utc_before_cohort_split(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path, sentinel="2026-05-01T00:00:00-06:00")
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO paper_trades (
                trade_id, ts, series_ticker, resolved, pnl_dollars, match_score,
                contracts, kelly_contracts, price_cents, cost_dollars,
                resolved_yes, side
            )
            VALUES (?, ?, 'KXOFFSET', 1, 1.0, 0.15, 5, 2, 50, 2.5, 1, 'yes')
            """,
            [
                ("offset-pre-1", "2026-05-01T05:30:00+00:00"),
                ("offset-pre-2", "2026-05-01T05:45:00+00:00"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    output = pa.section_per_series_win_rate()
    pre_section = output.split("Pre-P0 (frozen) cohort:", 1)[1].split("Post-P0 cohort:", 1)[0]
    post_section = output.split("Post-P0 cohort:", 1)[1]

    assert "P0 cohort boundary: 2026-05-01T06:00:00+00:00" in output
    assert "KXOFFSET" in pre_section
    assert "KXOFFSET" not in post_section
    assert "Post-P0 cohort:" in output


def test_p0_boundary_normalizes_z_suffix_to_stored_utc_format(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    _create_db(db_path, sentinel="2026-05-01T00:00:00Z")
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    output = pa.section_per_series_win_rate()

    assert "P0 cohort boundary: 2026-05-01T00:00:00+00:00" in output
    assert "$+2.00" in output
    assert "$-2.00" in output


def test_load_db_trades_filters_to_requested_window(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE paper_trades (trade_id TEXT PRIMARY KEY, ts TEXT, resolved INTEGER)"
        )
        conn.executemany(
            "INSERT INTO paper_trades (trade_id, ts, resolved) VALUES (?, ?, 1)",
            [
                ("old", "2026-05-06T23:59:59+00:00"),
                ("in-window", "2026-05-07T00:00:00+00:00"),
                ("late", "2026-06-07T00:00:00+00:00"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)

    rows = pa.load_db_trades(
        datetime(2026, 5, 7, tzinfo=timezone.utc),
        datetime(2026, 6, 6, 23, 59, 59, tzinfo=timezone.utc),
    )

    assert [row["trade_id"] for row in rows] == ["in-window"]
    assert len(pa.load_db_trades()) == 3


def test_golive_readiness_fallback_uses_persisted_start_and_measurable_drawdown():
    """Fallback path (no P0 boundary sentinel) gates on the LIFETIME cohort, reads
    the persisted starting bankroll for display, and computes a MEASURABLE
    peak-to-trough drawdown from the equity curve. Bankroll peaks at 50, dips to
    42 (16% peak-to-trough) then recovers — comfortably under the 20% cap."""
    db_trades = []
    for i in range(20):
        if i == 0:
            before = 50.0  # peak (also the persisted start)
        elif i == 1:
            before = 42.0  # trough -> 16% peak-to-trough
        else:
            before = 45.0
        db_trades.append(
            {
                "trade_id": f"t-{i}",
                "ts": f"2026-05-{1 + i:02d}T12:00:00+00:00",
                "resolved": 1,
                "pnl_dollars": 1.0 if i < 11 else -1.0,  # 55% win rate
                "notional_bankroll_before": before,
            }
        )

    output = pa.section_golive_readiness(
        db_trades,
        {"notional_bankroll": "45.25"},
    )

    # No P0 sentinel -> fallback to lifetime gating, labelled as such.
    assert "Cohort basis    : LIFETIME (post-P0 boundary missing" in output
    # Peak-to-trough is measurable (16%) and passes; start bankroll is displayed.
    assert "Drawdown        : 16.0% / 20% max  [PASS]" in output
    assert "(peak-to-trough)" in output
    assert "Notional bankroll : $45.25 (started $50.00)" in output
    # 20 resolved, 55% win, 16% drawdown -> READY; no failure reason emitted.
    assert "OVERALL: READY FOR LIVE TRADING" in output
    assert "drawdown" not in output.split("OVERALL:", 1)[1]


def _mtm_trades(n=20, start=50.0):
    """20 resolved lifetime trades, 55% win rate, flat 50.0 equity history."""
    return [
        {
            "trade_id": f"t-{i}",
            "ts": f"2026-05-{1 + i:02d}T12:00:00+00:00",
            "resolved": 1,
            "pnl_dollars": 1.0 if i < 11 else -1.0,
            "notional_bankroll_before": start,
        }
        for i in range(n)
    ]


def test_golive_drawdown_uses_mtm_equity_when_available():
    """PROFIT-DRAWDOWN-001c: with an MTM snapshot, the gate's current equity
    point is notional + marked open-position value. WHY: notional deducts each
    open trade's full cost at entry, so a bot holding many open positions shows
    a phantom drawdown (2026-06-12: notional said 45.7%, true equity said
    18.9%) and the go-live gate fails on a number that isn't a loss."""
    db_trades = _mtm_trades()
    state = {"notional_bankroll": "30.00"}  # notional alone: (50-30)/50 = 40% FAIL

    no_mtm = pa.section_golive_readiness(db_trades, state)
    assert "Drawdown        : 40.0% / 20% max  [FAIL]" in no_mtm
    assert "OVERALL: NOT READY" in no_mtm

    with_mtm = pa.section_golive_readiness(
        db_trades, state, mtm={"marked_value": 18.0, "unpriced_count": 0}
    )
    # Equity point = 30 + 18 = 48 -> peak-to-trough (50-48)/50 = 4% PASS.
    assert "Drawdown        : 4.0% / 20% max  [PASS]" in with_mtm
    assert "MTM equity        : $48.00 = notional + $18.00 open-position value" in with_mtm
    assert "OVERALL: READY FOR LIVE TRADING" in with_mtm


def test_golive_mtm_unavailable_labels_notional_fallback():
    """mtm=None (offline / fetch failure) gates on notional and SAYS so —
    silent fallback would leave the operator unable to tell a real drawdown
    from the open-cost artifact. Notional overstates drawdown, which is the
    conservative failure direction for a go-live gate."""
    output = pa.section_golive_readiness(
        _mtm_trades(), {"notional_bankroll": "30.00"}, mtm=None
    )
    assert "MTM equity        : unavailable" in output
    assert "overstates drawdown" in output
    assert "Drawdown        : 40.0% / 20% max  [FAIL]" in output


def test_golive_mtm_unpriced_positions_count_at_zero():
    """Unpriced positions contribute $0 to MTM equity (fail-closed): only
    marked_value is added; unknown_cost must NOT inflate equity. A position the
    tool could not price may genuinely be worthless."""
    output = pa.section_golive_readiness(
        _mtm_trades(),
        {"notional_bankroll": "30.00"},
        # unknown_cost present but must be ignored by the equity computation.
        mtm={"marked_value": 5.0, "unknown_cost": 13.0, "unpriced_count": 3},
    )
    # Equity = 30 + 5 = 35 (NOT 48): peak-to-trough (50-35)/50 = 30% -> still FAIL.
    assert "MTM equity        : $35.00 = notional + $5.00 open-position value" in output
    assert "(3 unpriced positions counted at $0)" in output
    assert "Drawdown        : 30.0% / 20% max  [FAIL]" in output


def test_golive_gates_on_post_p0_cohort_not_lifetime():
    """Operator decision 2026-06-11: the go-live gate uses the POST-P0 cohort,
    NOT lifetime. The pre-P0 cohort ran under the pre-fix pricing bug and is
    excluded as non-representative (matches 7b/7d/7e). A weak lifetime win rate
    must not gate go-live, and conversely a thin post-P0 sample must FAIL on
    resolved-count even when its win rate is perfect. WHY: gating on known-broken
    pre-P0 data was leveraging improper functionality as the gate."""
    boundary = "2026-05-12T23:50:04+00:00"
    db_trades = []
    # Pre-P0: 15 resolved, 4 wins (would drag a LIFETIME gate down to 45%).
    for i in range(15):
        db_trades.append(
            {
                "trade_id": f"pre-{i}",
                "ts": "2026-05-10T12:00:00+00:00",
                "resolved": 1,
                "pnl_dollars": 1.0 if i < 4 else -1.0,
                "notional_bankroll_before": 50.0 if i == 0 else None,
            }
        )
    # Post-P0: 5 resolved, ALL wins (100%) — the representative regime.
    for i in range(5):
        db_trades.append(
            {
                "trade_id": f"post-{i}",
                "ts": "2026-05-20T12:00:00+00:00",
                "resolved": 1,
                "pnl_dollars": 2.0,
                "notional_bankroll_before": None,
            }
        )
    state = {
        "notional_bankroll": "45.00",
        pa.P0_PRICE_FIX_SENTINEL_KEY: boundary,
    }
    output = pa.section_golive_readiness(db_trades, state)

    # Gate basis is POST-P0; the verdict uses post-P0 (100% win, 5 resolved),
    # NOT lifetime's 45%.
    assert "Cohort basis    : POST-P0" in output
    assert "Win rate        : 100% / 52% required  [PASS]" in output
    assert "Resolved trades : 5 / 20 required  [FAIL]" in output
    # A thin post-P0 sample fails on resolved-count even at 100% win rate.
    assert "OVERALL: NOT READY" in output
    assert "READY FOR LIVE TRADING" not in output
    assert "15 more resolved trades needed" in output
    # Lifetime is surfaced as informational only (45% win rate, 20 resolved).
    assert "INFORMATIONAL, not gating" in output
    assert "Resolved 20" in output


def test_golive_reports_peak_to_trough_and_cohort_label():
    """F1/F3 (revised for post-P0 gating): section 8 labels its cohort basis as
    POST-P0 and gates on the peak-to-trough drawdown. Here the bankroll dips to
    30 then recovers to 45: the gate uses the 40% peak-to-trough, while the
    lifetime informational view also reports the 10% start-vs-now figure."""
    db_trades = [
        {
            "trade_id": "a",
            "ts": "2026-05-20T12:00:00+00:00",
            "resolved": 1,
            "pnl_dollars": -1.0,
            "notional_bankroll_before": 50.0,
        },
        {
            "trade_id": "b",
            "ts": "2026-05-21T12:00:00+00:00",
            "resolved": 1,
            "pnl_dollars": -1.0,
            "notional_bankroll_before": 30.0,  # trough
        },
    ]
    state = {
        "notional_bankroll": "45.00",  # recovered
        pa.P0_PRICE_FIX_SENTINEL_KEY: "2026-05-12T23:50:04+00:00",
    }
    output = pa.section_golive_readiness(db_trades, state)
    assert "Cohort basis    : POST-P0" in output
    # Gate now uses peak-to-trough: peak 50 -> trough 30 = 40% (FAIL > 20% cap).
    assert "Drawdown        : 40.0% / 20% max  [FAIL]" in output
    assert "(peak-to-trough)" in output
    # Lifetime informational view still reports the 10% start-vs-now figure.
    assert "start-vs-now 10.0%" in output


def test_placed_performance_labels_in_window_cohort():
    """F2: Section 2 (placed trades) labels its cohort as IN-WINDOW and
    cross-references that go-live readiness (section 8) gates on the POST-P0
    cohort, so the in-window win rate is not misread as the gate's basis."""
    db_trades = [
        {"trade_id": "x", "resolved": 0, "cost_dollars": 1.0, "edge": 0.05}
    ]
    output = pa.section_placed_performance([], db_trades, {})
    assert "Cohort: IN-WINDOW" in output
    assert "POST-P0 cohort" in output


def test_kelly_shadow_payout_is_side_aware_no_win():
    """PROFIT-REPORT-002: a NO contract bought at 92c that resolves NO is a WIN
    (payout = contracts), not a total loss. The prior renderer assumed every
    position was YES and booked winning NO bets as losses, corrupting the Kelly
    delta (this is what produced the bogus post-P0 Kelly -48.9%)."""
    no_win = {
        "kelly_contracts": 2,
        "price_cents": 92,
        "resolved_yes": 0,  # market resolved NO -> a NO bet WINS
        "side": "no",
        "pnl_dollars": 0.40,
        "cost_dollars": 4.60,
    }
    out = pa._render_kelly_shadow_rows([no_win])
    assert "+0.16" in out  # kelly cost 1.84, payout 2.0 -> +0.16 (win booked)
    assert "-1.84" not in out  # the old YES-assumption total-loss value


def test_kelly_shadow_no_bet_resolving_yes_is_loss():
    """A NO contract loses when the market resolves YES: payout 0."""
    no_loss = {
        "kelly_contracts": 2,
        "price_cents": 92,
        "resolved_yes": 1,  # market resolved YES -> a NO bet LOSES
        "side": "no",
        "pnl_dollars": -4.60,
        "cost_dollars": 4.60,
    }
    out = pa._render_kelly_shadow_rows([no_loss])
    assert "-1.84" in out  # cost 1.84, payout 0 -> -1.84


def test_kelly_shadow_yes_bet_unchanged():
    """YES side keeps prior behaviour: wins when the market resolves YES."""
    yes_win = {
        "kelly_contracts": 2,
        "price_cents": 50,
        "resolved_yes": 1,
        "side": "yes",
        "pnl_dollars": 2.5,
        "cost_dollars": 2.5,
    }
    out = pa._render_kelly_shadow_rows([yes_win])
    assert "+1.00" in out  # cost 1.0, payout 2.0 -> +1.00


def test_single_vs_multi_source_breakout():
    """PROFIT-SOURCE-001: split resolved performance by source-class count read
    from PAPER_TRADE signal_meta. Single=1, multi>=2, missing=unknown."""
    entries = [
        {"type": "PAPER_TRADE", "trade_id": "s1", "signal_meta": {"evidence_source_class_count": 1}},
        {"type": "PAPER_TRADE", "trade_id": "m1", "signal_meta": {"evidence_source_class_count": 3}},
        {"type": "PAPER_TRADE", "trade_id": "u1", "signal_meta": {}},
    ]
    db_trades = [
        {"trade_id": "s1", "resolved": 1, "pnl_dollars": 2.0},
        {"trade_id": "m1", "resolved": 1, "pnl_dollars": -1.0},
        {"trade_id": "u1", "resolved": 1, "pnl_dollars": 0.5},
        {"trade_id": "open", "resolved": 0, "pnl_dollars": None},
    ]
    out = pa.section_single_vs_multi_source(entries, db_trades)
    assert "single (1 source)" in out
    assert "multi (>=2 sources)" in out
    # single: 1 resolved, 100% win, +2.00 ; multi: 1 resolved, 0% win, -1.00
    assert "100%" in out and "+2.00" in out
    assert "-1.00" in out


def test_single_vs_multi_source_no_data():
    out = pa.section_single_vs_multi_source([], [])
    assert "No resolved trades with source-class tracking yet" in out


def test_skip_breakdown_surfaces_raw_unclassified_reasons():
    entries = [
        {"type": "SKIPPED", "ticker": "KX1", "reason": "market closed: close_time_elapsed"},
        {"type": "SKIPPED", "ticker": "KX1", "reason": "market closed: close_time_elapsed"},
        {"type": "SKIPPED", "ticker": "KX2", "reason": "missing orderbook best ask"},
        {"type": "SKIPPED", "ticker": "KX3", "reason": "cooldown active for ticker"},
    ]

    output = pa.section_skip_breakdown(entries)

    assert "Unclassified raw skip reasons:" in output
    assert "2  market closed: close_time_elapsed" in output
    assert "1  missing orderbook best ask" in output


def test_skip_breakdown_classifies_admission_gate_reasons_as_non_controllable():
    entries = [
        {"type": "SKIPPED", "ticker": "KX1", "reason": "G1_blended_confidence"},
        {"type": "SKIPPED", "ticker": "KX2", "reason": "G6_recency_score"},
        {"type": "SKIPPED", "ticker": "KX3", "reason": "series_correlation_in_window"},
        {"type": "SKIPPED", "ticker": "KX4", "reason": "cooldown active for ticker"},
    ]

    output = pa.section_skip_breakdown(entries)

    assert "confidence gate" in output
    assert "recency gate" in output
    assert "series correlation gate" in output
    assert "KX1" not in output.split("Most-skipped tickers (controllable skips only):", 1)[1]
    assert "KX4" in output.split("Most-skipped tickers (controllable skips only):", 1)[1]


def test_operator_throughput_section_renders_trade_log_metrics():
    output = pa.section_operator_throughput(
        ThroughputOperatorSummary(
            opportunities=4,
            skipped=2,
            paper_trades=3,
            window_days=2.0,
            opportunities_per_day=2.0,
            skipped_per_opportunity=0.5,
            top_ticker_trades_per_day=[("KXHIGH", 1.0), ("KXLOW", 0.5)],
            opportunity_age_p50_seconds=60.0,
            opportunity_age_p90_seconds=180.0,
        ),
        trade_log_available=True,
    )

    assert "OPERATOR THROUGHPUT LEADING INDICATORS" in output
    assert "Opportunities/day              : 2.00" in output
    assert "Skipped/opportunity ratio      : 0.500" in output
    assert "Opportunity age p50/p90        : 1.0m / 3.0m" in output
    assert "KXHIGH: 1.00/day" in output


def test_operator_throughput_section_keeps_db_only_runs_functional():
    output = pa.section_operator_throughput(None, trade_log_available=False)

    assert "trade-log metrics unavailable" in output


def test_candidate_subreddits_summarizes_and_caps_large_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE subreddit_candidates (
                sub TEXT PRIMARY KEY,
                discovered_ts TEXT NOT NULL,
                discovered_via TEXT NOT NULL,
                probe_count INTEGER DEFAULT 0,
                last_probed TEXT,
                status TEXT DEFAULT 'candidate'
            )
            """
        )
        rows = [
            (
                f"sub{i:03d}",
                "2026-05-01T00:00:00+00:00",
                "query",
                i % 4,
                "2026-05-02T00:00:00+00:00",
                "candidate" if i < 8 else "suppressed",
            )
            for i in range(12)
        ]
        conn.executemany(
            """
            INSERT INTO subreddit_candidates
                (sub, discovered_ts, discovered_via, probe_count, last_probed, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pa, "DB_PATH", db_path)
    monkeypatch.setenv("SUBREDDIT_CANDIDATE_MAX_PROBES", "3")

    output = pa.section_candidate_subreddits(max_rows=5)

    assert "candidate: 8, suppressed: 4" in output
    assert "Max-probe no-signal eligible: 2" in output
    assert "Showing 5 of 12 rows" in output
    assert "r/sub003" in output
    assert "r/sub011" not in output
