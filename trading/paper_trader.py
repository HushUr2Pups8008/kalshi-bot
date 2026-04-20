"""
Paper trading engine.

Stores simulated trades in SQLite (data/paper_trades.db).

Key additions vs. original:
  - Notional bankroll tracked in bot_state, debited on entry, credited on resolution
  - Source credibility updated on every resolution
  - go_live_confirmed flag in bot_state controls paper vs. live mode
  - Weekly review reminder emitted in daily_summary()
"""

import dataclasses
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tabulate import tabulate

import config as config_module
from analysis import SignalAnalysis
from analysis.source_credibility import SourceCredibility
from config import cfg, DATA_DIR, PAPER_FLAT_CONTRACTS
from trading.portfolio import Portfolio, Position
from utils.logger import get_logger, trade_log, TRADE_LOG_FILE

log = get_logger("paper_trader")

DB_PATH = DATA_DIR / "paper_trades.db"

_DDL = """
CREATE TABLE IF NOT EXISTS keyword_outcomes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id      TEXT    NOT NULL,
    ticker        TEXT    NOT NULL,
    series_ticker TEXT    NOT NULL,
    keyword       TEXT    NOT NULL,
    direction     TEXT    NOT NULL,
    market_side   TEXT    NOT NULL,
    resolved_yes  INTEGER NOT NULL,
    correct       INTEGER NOT NULL,
    ts            TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id                TEXT PRIMARY KEY,
    ts                      TEXT NOT NULL,
    ticker                  TEXT NOT NULL,
    market_title            TEXT NOT NULL,
    side                    TEXT NOT NULL,
    contracts               INTEGER NOT NULL,
    price_cents             INTEGER NOT NULL,
    cost_dollars            REAL NOT NULL,
    estimated_prob          REAL NOT NULL,
    market_yes_price        REAL NOT NULL,
    edge                    REAL NOT NULL,
    kelly_dollars           REAL NOT NULL,
    capped_dollars          REAL NOT NULL,
    signal_headline         TEXT NOT NULL,
    signal_source           TEXT NOT NULL,
    keywords_matched        TEXT NOT NULL,
    reasoning               TEXT NOT NULL,
    source_multiplier       REAL DEFAULT 1.0,
    notional_bankroll_before REAL,
    notional_bankroll_after  REAL,
    resolved                INTEGER DEFAULT 0,
    resolved_yes            INTEGER,
    pnl_dollars             REAL,
    market_snapshot         TEXT
);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_credibility (
    source        TEXT PRIMARY KEY,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    total         INTEGER DEFAULT 0,
    accuracy      REAL    DEFAULT 0.5,
    multiplier    REAL    DEFAULT 1.0,
    last_updated  TEXT
);

CREATE TABLE IF NOT EXISTS source_stats (
    source        TEXT PRIMARY KEY,
    posts_seen    INTEGER DEFAULT 0,
    signals       INTEGER DEFAULT 0,
    opportunities INTEGER DEFAULT 0,
    trades        INTEGER DEFAULT 0,
    last_signal   TEXT,
    last_updated  TEXT
);

CREATE TABLE IF NOT EXISTS subreddit_candidates (
    sub            TEXT PRIMARY KEY,
    discovered_ts  TEXT NOT NULL,
    discovered_via TEXT NOT NULL,
    probe_count    INTEGER DEFAULT 0,
    last_probed    TEXT,
    status         TEXT DEFAULT 'candidate'
);
"""


@dataclasses.dataclass(frozen=True)
class _StartupStateSnapshot:
    existing_keys: set[str]
    paper_start_inserted: bool
    bankroll_inserted: bool


def _match_quality_report_section() -> list[str]:
    """
    Return a compact MATCH QUALITY section for the daily report.

    Reads MATCH_DIAGNOSTIC records from trades.jsonl (read-only).
    Diagnostic only -- no runtime behavior is affected by these metrics.
    """
    if not TRADE_LOG_FILE.exists():
        return ["MATCH QUALITY", "  No trades.jsonl found.", ""]

    from collections import Counter
    total = 0
    low = 0
    flag_counts: Counter[str] = Counter()
    ticker_low: Counter[str] = Counter()
    try:
        with TRADE_LOG_FILE.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "MATCH_DIAGNOSTIC":
                    continue
                total += 1
                if ev.get("low_match_quality"):
                    low += 1
                    ticker = str(ev.get("ticker") or "").strip()
                    if ticker:
                        ticker_low[ticker] += 1
                    for f in (ev.get("heuristic_flags") or []):
                        flag_counts[str(f)] += 1
    except OSError:
        return ["MATCH QUALITY", "  Could not read trades.jsonl.", ""]

    if total == 0:
        return ["MATCH QUALITY", "  No MATCH_DIAGNOSTIC records yet.", ""]

    low_pct = 100 * low / total
    lines = [
        "MATCH QUALITY  (approximate heuristics; diagnostic only -- not suppressed)",
        f"  Matched candidates:       {total}",
        f"  Low-quality flagged:      {low} ({low_pct:.0f}%)",
    ]
    if flag_counts:
        top_flags = ", ".join(f"{f}({c})" for f, c in flag_counts.most_common(3))
        lines.append(f"  Top flag reasons:         {top_flags}")
    if ticker_low:
        top_tickers = ", ".join(f"{t}({c})" for t, c in ticker_low.most_common(5))
        lines.append(f"  Top flagged tickers:      {top_tickers}")
    lines.append("  Full analysis: python scripts/match_quality_diagnostics.py --exclude-test")
    lines.append("")
    return lines


class PaperTrader:
    """Paper trading engine backed by SQLite."""

    _runtime_owner_pid: int | None = None

    def __init__(self, db_path: Path = DB_PATH, *, startup_context: str = "runtime"):
        self._db_path = db_path
        self._startup_context = startup_context
        self._initialized = False
        self._validate_startup_context()
        self._enforce_runtime_guards()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self.credibility: SourceCredibility
        self.portfolio: Portfolio
        self.initialize()

    def initialize(self) -> None:
        """Explicit bootstrap hook kept idempotent for repeated construction safety.

        The constructor still auto-calls this so existing runtime and CLI call
        sites keep working. That is the safer option here: the app assumes a
        ready-to-use PaperTrader in many places, and `initialize()` exists to
        make repeated construction/tests safe rather than to force a broad API
        change through startup code.
        """
        if self._initialized:
            return
        self._conn.executescript(_DDL)
        self._conn.commit()
        self._migrate_db()
        self.credibility = SourceCredibility(self._db_path)
        self.portfolio = Portfolio()
        self.portfolio.load_from_db(self._conn)
        startup_state = self._load_state()
        self._log_startup_diagnostics(startup_state)
        self._log_state_initialization(startup_state)
        if self._startup_context == "runtime":
            type(self)._runtime_owner_pid = os.getpid()
        self._initialized = True

    def _validate_startup_context(self) -> None:
        if self._startup_context not in {"runtime", "cli", "test"}:
            raise ValueError(f"Unsupported startup_context: {self._startup_context}")

    def _enforce_runtime_guards(self) -> None:
        if self._startup_context != "runtime":
            return
        if self._is_in_memory_db_path():
            log.error(
                "[PAPER_INIT_GUARD] runtime_in_memory_db_blocked=true pid=%s db=%s",
                os.getpid(),
                self._db_path_for_logging(),
            )
            raise ValueError("PaperTrader runtime context cannot use an in-memory SQLite database")
        if type(self)._runtime_owner_pid == os.getpid():
            log.error(
                "[PAPER_INIT_GUARD] duplicate_runtime_init_blocked=true pid=%s db=%s",
                os.getpid(),
                self._db_path_for_logging(),
            )
            raise RuntimeError("PaperTrader runtime initialization attempted more than once in the same process")

    # ── Schema migrations ─────────────────────────────────────────────────────

    def _migrate_db(self) -> None:
        """Add columns introduced after initial schema without dropping existing data."""
        cols = self._paper_trades_columns()
        added_cols: list[str] = []
        if "market_snapshot" not in cols and self._ensure_paper_trades_column("market_snapshot", "TEXT", cols):
            added_cols.append("market_snapshot")

        # v0.22.0: feedback-loop columns
        new_cols = [
            ("series_ticker",  "TEXT"),
            ("resolved_ts",    "TEXT"),
            ("signal_type",    "TEXT DEFAULT 'news'"),
            ("match_score",    "REAL"),
            ("llm_direction",  "TEXT"),
            ("llm_magnitude",  "TEXT"),
            ("llm_confidence", "REAL"),
            # v0.23.0: Kelly shadow sizing -- what Kelly would have done in paper mode
            # Lets analytics compare flat-5 vs Kelly trajectory before going live
            ("kelly_contracts", "INTEGER"),
        ]
        for col, col_type in new_cols:
            if col not in cols and self._ensure_paper_trades_column(col, col_type, cols):
                added_cols.append(col)

        # Backfill series_ticker from market_snapshot JSON for historical rows
        if "series_ticker" in added_cols:
            try:
                cur = self._conn.execute(
                    """UPDATE paper_trades
                       SET series_ticker = json_extract(market_snapshot, '$.series_ticker')
                       WHERE series_ticker IS NULL AND market_snapshot IS NOT NULL"""
                )
                self._conn.commit()
                if cur.rowcount:
                    log.info(
                        "DB backfilled series_ticker for %d historical trades", cur.rowcount
                    )
            except Exception as exc:
                log.warning("DB backfill of series_ticker failed: %s", exc)

        if added_cols:
            log.info("DB migration complete (added: %s)", ", ".join(added_cols))

    def _paper_trades_columns(self) -> set[str]:
        return {row[1] for row in self._conn.execute("PRAGMA table_info(paper_trades)")}

    def _ensure_paper_trades_column(self, col: str, col_type: str, cols: set[str]) -> bool:
        try:
            self._conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {col_type}")
            self._conn.commit()
            cols.add(col)
            return True
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                cols.update(self._paper_trades_columns())
                return False
            log.warning("DB migration failed for column %s: %s", col, exc)
            return False

    # ── State management ──────────────────────────────────────────────────────

    def _load_state(self) -> _StartupStateSnapshot:
        """Read persisted state; initialise defaults atomically on first run."""
        existing_keys = self._existing_state_keys()
        paper_start_inserted = False
        bankroll_inserted = False

        if "paper_start_time" not in existing_keys:
            paper_start_inserted = self._ensure_state_default(
                "paper_start_time",
                datetime.now(timezone.utc).isoformat(),
            )

        if "notional_bankroll" not in existing_keys:
            bankroll_inserted = self._ensure_state_default(
                "notional_bankroll",
                str(cfg.bankroll),
            )

        # Go-live flag -- sets cfg.is_paper_trading only if LIVE_TRADING_ENABLED=true.
        # The env var is a hard kill-switch: even if the DB says go_live_confirmed,
        # the bot stays in paper mode until the operator explicitly unlocks it.
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'go_live_confirmed'"
        ).fetchone()
        if row and row["value"] == "true":
            if cfg.live_trading_enabled:
                cfg.set_paper_mode(False)
                log.warning("GO-LIVE confirmed -- bot is in LIVE TRADING mode.")
            else:
                log.warning(
                    "LIVE TRADING BLOCKED -- go_live_confirmed=true in DB but "
                    "LIVE_TRADING_ENABLED is not set in .env. "
                    "Staying in paper mode. Set LIVE_TRADING_ENABLED=true to unlock."
                )
                cfg.set_paper_mode(True)
        else:
            cfg.set_paper_mode(True)

        return _StartupStateSnapshot(
            existing_keys=existing_keys,
            paper_start_inserted=paper_start_inserted,
            bankroll_inserted=bankroll_inserted,
        )

    def _existing_state_keys(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT key FROM bot_state WHERE key IN ('paper_start_time', 'notional_bankroll', 'go_live_confirmed')"
        ).fetchall()
        return {str(row["key"]) for row in rows}

    def _ensure_state_default(self, key: str, value: str) -> bool:
        before = self._conn.total_changes
        self._conn.execute(
            """
            INSERT INTO bot_state (key, value)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM bot_state WHERE key = ?
            )
            """,
            (key, value, key),
        )
        self._conn.commit()
        return self._conn.total_changes > before

    def _log_startup_diagnostics(self, startup_state: _StartupStateSnapshot) -> None:
        if self._startup_context == "test":
            return
        level = logging.INFO if self._startup_context == "runtime" else logging.DEBUG
        config_path = getattr(config_module, "__file__", "unknown")
        log.log(
            level,
            "[PAPER_INIT] pid=%s ctx=%s cwd=%s db=%s config=%s bankroll=%.2f "
            "paper_start_exists=%s bankroll_exists=%s go_live_exists=%s",
            os.getpid(),
            self._startup_context,
            os.getcwd(),
            self._db_path_for_logging(),
            Path(config_path).resolve() if config_path != "unknown" else "unknown",
            cfg.bankroll,
            "paper_start_time" in startup_state.existing_keys,
            "notional_bankroll" in startup_state.existing_keys,
            "go_live_confirmed" in startup_state.existing_keys,
        )

    def _log_state_initialization(self, startup_state: _StartupStateSnapshot) -> None:
        if self._startup_context == "test":
            return
        if startup_state.paper_start_inserted:
            log.info("Paper trading phase started -- runs until you confirm --go-live.")
        elif "paper_start_time" not in startup_state.existing_keys:
            log.warning("Paper trading phase state row was created concurrently during startup.")

        if startup_state.bankroll_inserted:
            log.info("Notional bankroll initialised at $%.2f", cfg.bankroll)
        elif "notional_bankroll" not in startup_state.existing_keys:
            log.warning(
                "Notional bankroll state row was created concurrently during startup; existing value preserved at $%.2f",
                self.get_notional_bankroll(),
            )

    def _db_path_for_logging(self) -> str:
        raw = str(self._db_path)
        if self._is_in_memory_db_path():
            return raw
        return str(Path(raw).resolve())

    def _is_in_memory_db_path(self) -> bool:
        raw = str(self._db_path)
        return raw == ":memory:" or (raw.startswith("file:") and "mode=memory" in raw)

    def _set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # ── Notional bankroll ─────────────────────────────────────────────────────

    def get_notional_bankroll(self) -> float:
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'notional_bankroll'"
        ).fetchone()
        return float(row["value"]) if row else cfg.bankroll

    def _debit_bankroll(self, amount: float) -> float:
        """Subtract amount from notional bankroll. Returns new balance."""
        current = self.get_notional_bankroll()
        new_val = current - amount
        self._set_state("notional_bankroll", str(round(new_val, 4)))
        return new_val

    def _credit_bankroll(self, amount: float) -> float:
        """Add amount to notional bankroll. Returns new balance."""
        current = self.get_notional_bankroll()
        new_val = current + amount
        self._set_state("notional_bankroll", str(round(new_val, 4)))
        return new_val

    # ── Go-live gate ──────────────────────────────────────────────────────────

    def confirm_go_live(self) -> None:
        """
        Flip the go_live_confirmed flag in the DB and update cfg.
        Called only from main.py --go-live after human confirmation.
        """
        self._set_state("go_live_confirmed", "true")
        cfg.set_paper_mode(False)
        log.warning(
            "GO-LIVE CONFIRMED. Bot will now place real orders. "
            "Notional bankroll at time of switch: $%.2f",
            self.get_notional_bankroll(),
        )

    # ── Trade recording ───────────────────────────────────────────────────────

    def record_trade(self, analysis: SignalAnalysis) -> str:
        from analysis.kelly import contracts_from_dollars

        trade_id    = str(uuid.uuid4())[:12]
        price_cents = int(analysis.market.yes_price) if analysis.side == "yes" \
                      else int(100 - analysis.market.yes_price)
        price_cents  = max(1, min(99, price_cents))
        # Kelly shadow: always compute what Kelly would size, even in flat paper mode.
        # Stored as kelly_contracts for post-hoc analytics comparing flat-5 vs Kelly P&L.
        kelly_contracts = contracts_from_dollars(analysis.capped_dollars, float(price_cents))
        # Paper training mode: flat contracts -- no bankroll gating so we
        # maximise trade volume and accumulate signal-quality data.
        if cfg.is_paper_trading:
            contracts    = PAPER_FLAT_CONTRACTS
        else:
            contracts    = kelly_contracts
        cost_dollars = contracts * price_cents / 100.0

        bankroll_before = self.get_notional_bankroll()
        bankroll_after  = self._debit_bankroll(cost_dollars)

        source_mult = self.credibility.get_multiplier(analysis.news_item.source)
        market_snapshot = json.dumps(dataclasses.asdict(analysis.market))

        self._conn.execute(
            """INSERT INTO paper_trades
               (trade_id, ts, ticker, market_title, side, contracts, price_cents,
                cost_dollars, estimated_prob, market_yes_price, edge, kelly_dollars,
                capped_dollars, signal_headline, signal_source, keywords_matched,
                reasoning, source_multiplier, notional_bankroll_before, notional_bankroll_after,
                market_snapshot, series_ticker, signal_type, match_score,
                llm_direction, llm_magnitude, llm_confidence, kelly_contracts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trade_id,
                datetime.now(timezone.utc).isoformat(),
                analysis.market.ticker,
                analysis.market.title,
                analysis.side,
                contracts,
                price_cents,
                cost_dollars,
                analysis.estimated_probability,
                analysis.market_yes_price,
                analysis.edge,
                analysis.kelly_dollars,
                analysis.capped_dollars,
                analysis.news_item.headline,
                analysis.news_item.source,
                json.dumps(analysis.keywords_matched),
                analysis.reasoning,
                source_mult,
                bankroll_before,
                bankroll_after,
                market_snapshot,
                analysis.market.series_ticker,
                getattr(analysis, "signal_type", "news"),
                getattr(analysis, "match_score", None),
                getattr(analysis, "llm_direction", None),
                getattr(analysis, "llm_magnitude", None),
                getattr(analysis, "llm_confidence", None),
                kelly_contracts,
            ),
        )
        self._conn.commit()

        # Keep portfolio in sync with DB
        self.portfolio.add(Position(
            trade_id=trade_id,
            ticker=analysis.market.ticker,
            side=analysis.side,
            contracts=contracts,
            cost_dollars=cost_dollars,
            price_cents=price_cents,
            estimated_prob=analysis.estimated_probability,
            market_yes_price=analysis.market_yes_price,
            ts=datetime.now(timezone.utc).isoformat(),
        ))

        paper_trade_kwargs = {
            "trade_id": trade_id,
            "ticker": analysis.market.ticker,
            "market_title": analysis.market.title,
            "side": analysis.side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": cost_dollars,
            "estimated_probability": analysis.estimated_probability,
            "market_yes_price": analysis.market_yes_price,
            "edge": analysis.edge,
            "kelly_dollars": analysis.kelly_dollars,
            "reasoning": analysis.reasoning,
            "signal_headline": analysis.news_item.headline,
            "signal_source": analysis.news_item.source,
        }
        signal_meta = vars(analysis).get("signal_meta")
        if signal_meta:
            paper_trade_kwargs["signal_meta"] = signal_meta
        trade_log.log_paper_trade(**paper_trade_kwargs)

        kelly_note = f" (kelly_shadow={kelly_contracts})" if cfg.is_paper_trading else ""
        log.info(
            "[PAPER] %s: BUY %d%s %s @ %dc | cost=$%.2f | edge=%+.3f | "
            "bankroll=$%.2f->$%.2f | src_mult=%.2fx | %s",
            trade_id, contracts, kelly_note, analysis.side.upper(), price_cents,
            cost_dollars, analysis.edge,
            bankroll_before, bankroll_after,
            source_mult, analysis.market.ticker,
        )
        log.info("        Reasoning: %s", analysis.reasoning[:120])

        return trade_id

    # ── Market resolution ─────────────────────────────────────────────────────

    def resolve_market(self, ticker: str, resolved_yes: bool) -> None:
        """
        Mark all open paper trades for a ticker as resolved, compute P&L,
        update notional bankroll, and record source credibility outcomes.
        """
        trades = self._conn.execute(
            "SELECT * FROM paper_trades WHERE ticker = ? AND resolved = 0", (ticker,)
        ).fetchall()

        if not trades:
            log.debug("No open paper trades for %s", ticker)
            return

        # Pre-calculate all outcomes before any DB writes
        outcomes: list[tuple] = []
        total_pnl    = 0.0
        total_payout = 0.0
        for t in trades:
            won    = (t["side"] == "yes" and resolved_yes) or \
                     (t["side"] == "no"  and not resolved_yes)
            payout = float(t["contracts"]) if won else 0.0
            pnl    = payout - t["cost_dollars"]
            outcomes.append((t, won, payout, pnl))
            total_pnl    += pnl
            total_payout += payout

        now_ts = datetime.now(timezone.utc).isoformat()
        series_ticker = ticker.split("-")[0]  # e.g. "KXTRUMPIRAN-26APR01" -> "KXTRUMPIRAN"

        # Atomically mark all trades resolved and credit bankroll in one transaction.
        # _credit_bankroll calls _set_state which commits -- both the UPDATE rows and
        # the bankroll write land in the same commit so a crash between them is impossible.
        with self._conn:
            for t, won, payout, pnl in outcomes:
                self._conn.execute(
                    "UPDATE paper_trades SET resolved=1, resolved_yes=?, pnl_dollars=?, "
                    "resolved_ts=? WHERE trade_id=?",
                    (int(resolved_yes), pnl, now_ts, t["trade_id"]),
                )
            # Credit bankroll once for total payout (cost was debited on entry per-trade)
            self._credit_bankroll(total_payout)

        # Keep portfolio in sync — all positions for this ticker are now closed
        self.portfolio.resolve(ticker)

        # Non-critical side effects: credibility + audit log + keyword outcomes
        # (DB already committed above)
        from config import GEOPOLITICAL_SIGNALS
        _kw_direction: dict[str, str] = {}
        for sig in GEOPOLITICAL_SIGNALS:
            for kw in sig["keywords"]:
                _kw_direction[kw] = sig["direction"]

        for t, won, payout, pnl in outcomes:
            self.credibility.record_outcome(t["signal_source"], was_correct=won)
            trade_log.log_paper_resolution(
                trade_id=t["trade_id"],
                ticker=ticker,
                resolved_yes=resolved_yes,
                pnl_dollars=pnl,
            )
            log.info(
                "[RESOLVED] %s %s YES=%s | pnl=$%+.2f | bankroll=$%.2f",
                t["trade_id"], ticker, resolved_yes, pnl,
                self.get_notional_bankroll(),
            )
            # Record per-keyword outcomes for feedback loop learning.
            # Each resolved trade writes one row per keyword that fired on it.
            # Accumulated over time this teaches us which keywords are predictive
            # on which series -- the foundation for Phase 3 dynamic weighting.
            try:
                keywords = json.loads(t["keywords_matched"] or "[]")
                for kw in keywords:
                    direction = _kw_direction.get(kw, t["side"])
                    correct = int(
                        (direction == "yes" and resolved_yes) or
                        (direction == "no"  and not resolved_yes)
                    )
                    self._conn.execute(
                        "INSERT INTO keyword_outcomes "
                        "(trade_id, ticker, series_ticker, keyword, direction, "
                        " market_side, resolved_yes, correct, ts) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (t["trade_id"], ticker, series_ticker, kw, direction,
                         t["side"], int(resolved_yes), correct, now_ts),
                    )
                if keywords:
                    self._conn.commit()
                    log.debug(
                        "[RESOLVED] %s: recorded %d keyword outcome(s)",
                        t["trade_id"], len(keywords),
                    )
            except Exception as exc:
                log.warning("Failed to record keyword outcomes for %s: %s", t["trade_id"], exc)

        log.info(
            "[RESOLVED] %s: %d trades | net P&L $%+.2f | notional bankroll $%.2f",
            ticker, len(trades), total_pnl, self.get_notional_bankroll(),
        )

    # ── Report ────────────────────────────────────────────────────────────────

    def get_all_open_trades(self, ticker: str) -> list[sqlite3.Row]:
        """Return all unresolved trades for ticker (oldest first)."""
        return self._conn.execute(
            "SELECT * FROM paper_trades WHERE ticker = ? AND resolved = 0 "
            "ORDER BY ts ASC",
            (ticker,),
        ).fetchall()

    def get_all_trades(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM paper_trades ORDER BY ts DESC"
        ).fetchall()

    def generate_report(self) -> str:
        trades    = self.get_all_trades()
        resolved  = [t for t in trades if t["resolved"]]
        open_t    = [t for t in trades if not t["resolved"]]
        notional  = self.get_notional_bankroll()
        start_row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'paper_start_time'"
        ).fetchone()
        start_ts  = start_row["value"] if start_row else "unknown"

        total_cost = sum(t["cost_dollars"] for t in trades)
        total_pnl  = sum(t["pnl_dollars"] for t in resolved if t["pnl_dollars"] is not None)
        wins       = [t for t in resolved if (t["pnl_dollars"] or 0) > 0]
        losses     = [t for t in resolved if (t["pnl_dollars"] or 0) <= 0]
        win_rate   = len(wins) / len(resolved) if resolved else 0.0
        avg_win    = sum(t["pnl_dollars"] for t in wins) / len(wins) if wins else 0
        avg_loss   = sum(t["pnl_dollars"] for t in losses) / len(losses) if losses else 0
        avg_edge   = sum(t["edge"] for t in trades) / len(trades) if trades else 0
        roi        = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        drawdown   = cfg.bankroll - notional

        lines = [
            "=" * 70,
            "PAPER TRADING PERFORMANCE REPORT",
            "=" * 70,
            f"  Paper trading since:    {start_ts}",
            f"  Report generated:       {datetime.now(timezone.utc).isoformat()}",
            f"  Mode:                   {'PAPER' if cfg.is_paper_trading else 'LIVE'}",
            "",
            "NOTIONAL BANKROLL",
            f"  Starting bankroll:      ${cfg.bankroll:.2f}",
            f"  Current notional:       ${notional:.2f}",
            f"  Total drawdown:         ${drawdown:+.2f}",
            f"  Capital deployed:       ${total_cost:.2f}",
            "",
            "TRADE SUMMARY",
            f"  Total trades:           {len(trades)}",
            f"  Resolved:               {len(resolved)}",
            f"  Open (pending):         {len(open_t)}",
            "",
            "P&L  (resolved trades)",
            f"  Net P&L:                ${total_pnl:+.2f}",
            f"  ROI on deployed capital:{roi:+.1f}%",
            f"  Win rate:               {win_rate:.1%}  ({len(wins)}W / {len(losses)}L)",
            f"  Avg win:                ${avg_win:+.2f}",
            f"  Avg loss:               ${avg_loss:+.2f}",
        ]

        if avg_loss:
            lines.append(f"  Profit factor:          {abs(avg_win / avg_loss):.2f}x")

        lines += [
            "",
            "SIGNAL QUALITY",
            f"  Average edge:           {avg_edge:+.4f}",
            "",
        ]

        lines += _match_quality_report_section()

        # Resolved trades table (last 20)
        if resolved:
            lines.append("RESOLVED TRADES  (most recent 20)")
            table_data = []
            for t in resolved[-20:]:
                table_data.append([
                    t["trade_id"],
                    t["ticker"][:18],
                    t["side"].upper(),
                    t["contracts"],
                    f"{t['price_cents']}c",
                    f"${t['cost_dollars']:.2f}",
                    f"{t['estimated_prob']:.3f}",
                    f"{t['edge']:+.3f}",
                    f"{t['source_multiplier']:.2f}x",
                    "YES" if t["resolved_yes"] else "NO",
                    f"${t['pnl_dollars']:+.2f}",
                ])
            lines.append(tabulate(
                table_data,
                headers=["ID", "Ticker", "Side", "Qty", "Price", "Cost",
                         "Est.P", "Edge", "SrcMult", "Result", "P&L"],
                tablefmt="simple",
            ))
            lines.append("")

        # Open trades table
        if open_t:
            lines.append("OPEN PAPER TRADES")
            table_data = []
            for t in open_t:
                table_data.append([
                    t["trade_id"],
                    t["ticker"][:18],
                    t["side"].upper(),
                    t["contracts"],
                    f"{t['price_cents']}c",
                    f"${t['cost_dollars']:.2f}",
                    f"{t['estimated_prob']:.3f}",
                    f"{t['edge']:+.3f}",
                    t["ts"][:10],
                ])
            lines.append(tabulate(
                table_data,
                headers=["ID", "Ticker", "Side", "Qty", "Price", "Cost", "Est.P", "Edge", "Date"],
                tablefmt="simple",
            ))
            lines.append("")

        # Best/worst
        if resolved:
            best  = max(resolved, key=lambda t: t["pnl_dollars"] or 0)
            worst = min(resolved, key=lambda t: t["pnl_dollars"] or 0)
            lines += [
                "BEST TRADE",
                f"  {best['trade_id']} | {best['ticker']} | {best['side'].upper()} | P&L: ${best['pnl_dollars']:+.2f}",
                f"  Signal: \"{best['signal_headline'][:80]}\"",
                f"  Reasoning: {best['reasoning'][:120]}",
                "",
                "WORST TRADE",
                f"  {worst['trade_id']} | {worst['ticker']} | {worst['side'].upper()} | P&L: ${worst['pnl_dollars']:+.2f}",
                f"  Signal: \"{worst['signal_headline'][:80]}\"",
                f"  Reasoning: {worst['reasoning'][:120]}",
                "",
            ]

        # Source credibility table
        lines.append("SOURCE CREDIBILITY")
        lines.append(self.credibility.format_table())
        lines.append("")

        # Go-live assessment
        lines.append("GO-LIVE ASSESSMENT")
        if len(resolved) < 10:
            lines.append(f"  INSUFFICIENT DATA --{len(resolved)}/10 resolved trades. Keep running.")
        elif win_rate >= 0.55 and total_pnl > 0 and avg_edge > 0.03:
            lines.append("  POSITIVE --Strategy shows edge. Run `python main.py --go-live` to confirm.")
        elif total_pnl < -50:
            lines.append("  NEGATIVE --Significant losses. Review strategy before considering live trading.")
        else:
            lines.append("  NEUTRAL --Mixed results. Continue paper trading and review next week.")

        lines += ["", "Run `python main.py --go-live` only after you are satisfied with these results.", "=" * 70]
        return "\n".join(lines)

    def daily_summary(self) -> None:
        trades   = self.get_all_trades()
        resolved = [t for t in trades if t["resolved"]]
        pnl      = sum(t["pnl_dollars"] for t in resolved if t["pnl_dollars"] is not None)
        open_cnt = len([t for t in trades if not t["resolved"]])
        notional = self.get_notional_bankroll()

        log.info(
            "[DAILY SUMMARY] trades=%d resolved=%d open=%d | "
            "net P&L=$%+.2f | notional=$%.2f",
            len(trades), len(resolved), open_cnt, pnl, notional,
        )
        log.info(
            "[WEEKLY REMINDER] Review with: python main.py --report  "
            "| Go live with: python main.py --go-live"
        )
