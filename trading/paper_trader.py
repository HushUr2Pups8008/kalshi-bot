"""
Paper trading engine.

Stores simulated trades in SQLite (data/paper_trades.db).

Key additions vs. original:
  - Notional bankroll tracked in bot_state, debited on entry, credited on resolution
  - Source credibility updated on every resolution
  - go_live_confirmed flag in bot_state controls paper vs. live mode
  - Weekly review reminder emitted in daily_summary()
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tabulate import tabulate

from analysis import SignalAnalysis
from analysis.source_credibility import SourceCredibility
from config import cfg, DATA_DIR, PAPER_FLAT_CONTRACTS
from utils.logger import get_logger, trade_log

log = get_logger("paper_trader")

DB_PATH = DATA_DIR / "paper_trades.db"

_DDL = """
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
    pnl_dollars             REAL
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
"""


class PaperTrader:
    """Paper trading engine backed by SQLite."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path    = db_path
        self._conn       = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()
        self.credibility = SourceCredibility(db_path)
        self._load_state()

    # ── State management ──────────────────────────────────────────────────────

    def _load_state(self) -> None:
        """Read persisted state; initialise defaults on first run."""
        # Paper trading start time
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'paper_start_time'"
        ).fetchone()
        if not row:
            now = datetime.now(timezone.utc).isoformat()
            self._set_state("paper_start_time", now)
            log.info("Paper trading phase started — runs until you confirm --go-live.")
        else:
            log.info("Paper trading resumed (started %s).", row["value"])

        # Notional bankroll
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'notional_bankroll'"
        ).fetchone()
        if not row:
            self._set_state("notional_bankroll", str(cfg.bankroll))
            log.info("Notional bankroll initialised at $%.2f", cfg.bankroll)

        # Go-live flag — sets cfg.is_paper_trading
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'go_live_confirmed'"
        ).fetchone()
        if row and row["value"] == "true":
            cfg.is_paper_trading = False
            log.warning("GO-LIVE confirmed — bot is in LIVE TRADING mode.")
        else:
            cfg.is_paper_trading = True

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
        cfg.is_paper_trading = False
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
        # Paper training mode: flat contracts -- no bankroll gating so we
        # maximise trade volume and accumulate signal-quality data.
        if cfg.is_paper_trading:
            contracts    = PAPER_FLAT_CONTRACTS
        else:
            contracts    = contracts_from_dollars(analysis.capped_dollars, float(price_cents))
        cost_dollars = contracts * price_cents / 100.0

        bankroll_before = self.get_notional_bankroll()
        bankroll_after  = self._debit_bankroll(cost_dollars)

        source_mult = self.credibility.get_multiplier(analysis.news_item.source)

        self._conn.execute(
            """INSERT INTO paper_trades
               (trade_id, ts, ticker, market_title, side, contracts, price_cents,
                cost_dollars, estimated_prob, market_yes_price, edge, kelly_dollars,
                capped_dollars, signal_headline, signal_source, keywords_matched,
                reasoning, source_multiplier, notional_bankroll_before, notional_bankroll_after)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            ),
        )
        self._conn.commit()

        trade_log.log_paper_trade(
            trade_id=trade_id,
            ticker=analysis.market.ticker,
            market_title=analysis.market.title,
            side=analysis.side,
            contracts=contracts,
            price_cents=price_cents,
            cost_dollars=cost_dollars,
            estimated_probability=analysis.estimated_probability,
            market_yes_price=analysis.market_yes_price,
            edge=analysis.edge,
            kelly_dollars=analysis.kelly_dollars,
            reasoning=analysis.reasoning,
            signal_headline=analysis.news_item.headline,
            signal_source=analysis.news_item.source,
        )

        log.info(
            "[PAPER] %s: BUY %d %s @ %d¢ | cost=$%.2f | edge=%+.3f | "
            "bankroll=$%.2f→$%.2f | src_mult=%.2fx | %s",
            trade_id, contracts, analysis.side.upper(), price_cents,
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

        total_pnl = 0.0
        for t in trades:
            won   = (t["side"] == "yes" and resolved_yes) or \
                    (t["side"] == "no"  and not resolved_yes)
            payout    = t["contracts"] * 1.0 if won else 0.0
            pnl       = payout - t["cost_dollars"]
            total_pnl += pnl

            # Credit bankroll with payout (cost was already debited on entry)
            self._credit_bankroll(payout)

            self._conn.execute(
                "UPDATE paper_trades SET resolved=1, resolved_yes=?, pnl_dollars=? "
                "WHERE trade_id=?",
                (int(resolved_yes), pnl, t["trade_id"]),
            )

            # Update source credibility
            # Signal was "correct" if we won the trade
            self.credibility.record_outcome(t["signal_source"], was_correct=won)

            trade_log.log_paper_resolution(
                trade_id=t["trade_id"],
                ticker=ticker,
                resolved_yes=resolved_yes,
                pnl_dollars=pnl,
            )
            log.info(
                "[RESOLVED] %s %s → YES=%s | pnl=$%+.2f | bankroll=$%.2f",
                t["trade_id"], ticker, resolved_yes, pnl,
                self.get_notional_bankroll(),
            )

        self._conn.commit()
        log.info(
            "[RESOLVED] %s: %d trades | net P&L $%+.2f | notional bankroll $%.2f",
            ticker, len(trades), total_pnl, self.get_notional_bankroll(),
        )

    # ── Report ────────────────────────────────────────────────────────────────

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
                    f"{t['price_cents']}¢",
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
                    f"{t['price_cents']}¢",
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
            lines.append(f"  INSUFFICIENT DATA — {len(resolved)}/10 resolved trades. Keep running.")
        elif win_rate >= 0.55 and total_pnl > 0 and avg_edge > 0.03:
            lines.append("  POSITIVE — Strategy shows edge. Run `python main.py --go-live` to confirm.")
        elif total_pnl < -50:
            lines.append("  NEGATIVE — Significant losses. Review strategy before considering live trading.")
        else:
            lines.append("  NEUTRAL — Mixed results. Continue paper trading and review next week.")

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
