"""
Paper trading engine.

Stores simulated trades in a SQLite database (data/paper_trades.db).
Provides:
  - record_trade()         — log a would-be trade with full reasoning
  - resolve_market()       — mark a market as resolved and compute P&L
  - generate_report()      — print/return a detailed performance summary
  - daily_summary()        — called each morning to emit a digest log
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tabulate import tabulate

from analysis import SignalAnalysis
from config import cfg, DATA_DIR
from utils.logger import get_logger, trade_log

log = get_logger("paper_trader")

DB_PATH = DATA_DIR / "paper_trades.db"


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id            TEXT PRIMARY KEY,
    ts                  TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    market_title        TEXT NOT NULL,
    side                TEXT NOT NULL,
    contracts           INTEGER NOT NULL,
    price_cents         INTEGER NOT NULL,
    cost_dollars        REAL NOT NULL,
    estimated_prob      REAL NOT NULL,
    market_yes_price    REAL NOT NULL,
    edge                REAL NOT NULL,
    kelly_dollars       REAL NOT NULL,
    capped_dollars      REAL NOT NULL,
    signal_headline     TEXT NOT NULL,
    signal_source       TEXT NOT NULL,
    keywords_matched    TEXT NOT NULL,   -- JSON list
    reasoning           TEXT NOT NULL,
    resolved            INTEGER DEFAULT 0,
    resolved_yes        INTEGER,
    pnl_dollars         REAL
);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class PaperTrader:
    """Paper trading engine backed by SQLite."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._conn    = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()
        self._load_start_time()

    # ── State management ──────────────────────────────────────────────────────

    def _load_start_time(self) -> None:
        """Read paper trading start time from DB; write it if absent."""
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'paper_start_time'"
        ).fetchone()
        if row:
            dt = datetime.fromisoformat(row["value"])
            cfg.paper_start_time = dt
            log.info(
                "Paper trading started %s ago (%.1f days remaining)",
                row["value"],
                cfg.days_remaining_paper,
            )
        else:
            now = datetime.now(timezone.utc)
            cfg.paper_start_time = now
            self._conn.execute(
                "INSERT INTO bot_state (key, value) VALUES ('paper_start_time', ?)",
                (now.isoformat(),),
            )
            self._conn.commit()
            log.info("Paper trading phase started. Will run for %d days.", cfg.paper_trading_days)

    # ── Trade recording ───────────────────────────────────────────────────────

    def record_trade(self, analysis: SignalAnalysis) -> str:
        """
        Record a simulated trade from a SignalAnalysis result.

        Returns the generated trade_id.
        """
        from analysis.kelly import contracts_from_dollars

        trade_id     = str(uuid.uuid4())[:12]
        price_cents  = int(analysis.market.yes_price) if analysis.side == "yes" \
                       else int(100 - analysis.market.yes_price)
        price_cents  = max(1, min(99, price_cents))
        contracts    = contracts_from_dollars(analysis.capped_dollars, float(price_cents))
        cost_dollars = contracts * price_cents / 100.0

        self._conn.execute(
            """INSERT INTO paper_trades
               (trade_id, ts, ticker, market_title, side, contracts, price_cents,
                cost_dollars, estimated_prob, market_yes_price, edge, kelly_dollars,
                capped_dollars, signal_headline, signal_source, keywords_matched, reasoning)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            ),
        )
        self._conn.commit()

        # Also write to the structured trade log
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
            "[PAPER] %s: BUY %d %s @ %d¢ | cost=$%.2f | edge=%+.3f | %s",
            trade_id,
            contracts,
            analysis.side.upper(),
            price_cents,
            cost_dollars,
            analysis.edge,
            analysis.market.ticker,
        )
        log.info("        Reasoning: %s", analysis.reasoning[:120])

        return trade_id

    # ── Market resolution ─────────────────────────────────────────────────────

    def resolve_market(self, ticker: str, resolved_yes: bool) -> None:
        """
        Mark all open paper trades for a ticker as resolved and compute P&L.

        resolved_yes=True  → YES contracts pay $1, NO contracts pay $0
        resolved_yes=False → NO contracts pay $1, YES contracts pay $0
        """
        trades = self._conn.execute(
            "SELECT * FROM paper_trades WHERE ticker = ? AND resolved = 0", (ticker,)
        ).fetchall()

        if not trades:
            log.debug("No open paper trades for %s", ticker)
            return

        total_pnl = 0.0
        for t in trades:
            if t["side"] == "yes":
                payout   = t["contracts"] * 1.0 if resolved_yes else 0.0
            else:
                payout   = t["contracts"] * 1.0 if not resolved_yes else 0.0
            pnl = payout - t["cost_dollars"]
            total_pnl += pnl

            self._conn.execute(
                "UPDATE paper_trades SET resolved=1, resolved_yes=?, pnl_dollars=? "
                "WHERE trade_id=?",
                (int(resolved_yes), pnl, t["trade_id"]),
            )
            trade_log.log_paper_resolution(
                trade_id=t["trade_id"],
                ticker=ticker,
                resolved_yes=resolved_yes,
                pnl_dollars=pnl,
            )
            log.info(
                "[PAPER RESOLVED] %s %s → YES=%s | P&L: $%+.2f",
                t["trade_id"], ticker, resolved_yes, pnl,
            )

        self._conn.commit()
        log.info(
            "[PAPER RESOLVED] %s total: %d trades, net P&L $%+.2f",
            ticker, len(trades), total_pnl,
        )

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_all_trades(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM paper_trades ORDER BY ts DESC"
        ).fetchall()

    def generate_report(self) -> str:
        """
        Generate a detailed performance report as a formatted string.

        This is designed to be human-readable for evaluating the strategy
        before going live.
        """
        trades = self.get_all_trades()
        if not trades:
            return "No paper trades recorded yet."

        resolved   = [t for t in trades if t["resolved"]]
        open_trades = [t for t in trades if not t["resolved"]]

        # ── Summary stats ──────────────────────────────────────────────────────
        total_cost   = sum(t["cost_dollars"] for t in trades)
        total_pnl    = sum(t["pnl_dollars"] for t in resolved if t["pnl_dollars"] is not None)
        wins         = [t for t in resolved if (t["pnl_dollars"] or 0) > 0]
        losses       = [t for t in resolved if (t["pnl_dollars"] or 0) <= 0]

        avg_edge     = sum(t["edge"] for t in trades) / len(trades) if trades else 0
        avg_ev       = sum(
            t["edge"] * t["capped_dollars"]
            for t in trades
        ) / len(trades) if trades else 0

        win_rate     = len(wins) / len(resolved) if resolved else 0.0
        avg_win      = sum(t["pnl_dollars"] for t in wins) / len(wins) if wins else 0
        avg_loss     = sum(t["pnl_dollars"] for t in losses) / len(losses) if losses else 0

        lines = [
            "=" * 70,
            "PAPER TRADING PERFORMANCE REPORT",
            "=" * 70,
            f"  Paper trading since: {cfg.paper_start_time}",
            f"  Days remaining:      {cfg.days_remaining_paper:.1f}",
            f"  Report generated:    {datetime.now(timezone.utc).isoformat()}",
            "",
            "TRADE SUMMARY",
            f"  Total trades:        {len(trades)}",
            f"  Resolved:            {len(resolved)}",
            f"  Open (pending):      {len(open_trades)}",
            f"  Total capital deployed: ${total_cost:.2f}",
            "",
            "P&L (resolved trades only)",
            f"  Net P&L:             ${total_pnl:+.2f}",
            f"  Win rate:            {win_rate:.1%}  ({len(wins)}W / {len(losses)}L)",
            f"  Avg win:             ${avg_win:+.2f}",
            f"  Avg loss:            ${avg_loss:+.2f}",
            f"  Profit factor:       {abs(avg_win / avg_loss):.2f}x" if avg_loss else "  Profit factor: N/A",
            "",
            "SIGNAL QUALITY",
            f"  Average edge:        {avg_edge:+.4f}",
            f"  Average EV/trade:    ${avg_ev:+.3f}",
            "",
        ]

        # ── Resolved trades table ──────────────────────────────────────────────
        if resolved:
            lines.append("RESOLVED TRADES")
            table_data = []
            for t in resolved[-20:]:  # last 20
                table_data.append([
                    t["trade_id"],
                    t["ticker"][:20],
                    t["side"].upper(),
                    t["contracts"],
                    f"{t['price_cents']}¢",
                    f"${t['cost_dollars']:.2f}",
                    f"{t['estimated_prob']:.3f}",
                    f"{t['market_yes_price']:.1f}¢",
                    f"{t['edge']:+.3f}",
                    "YES" if t["resolved_yes"] else "NO",
                    f"${t['pnl_dollars']:+.2f}",
                ])
            lines.append(tabulate(
                table_data,
                headers=["ID", "Ticker", "Side", "Qty", "Price", "Cost",
                         "Est.P", "Mkt.P", "Edge", "Result", "P&L"],
                tablefmt="simple",
            ))
            lines.append("")

        # ── Open trades table ─────────────────────────────────────────────────
        if open_trades:
            lines.append("OPEN (UNRESOLVED) PAPER TRADES")
            table_data = []
            for t in open_trades:
                table_data.append([
                    t["trade_id"],
                    t["ticker"][:20],
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

        # ── Best/worst trades ──────────────────────────────────────────────────
        if resolved:
            best  = max(resolved, key=lambda t: t["pnl_dollars"] or 0)
            worst = min(resolved, key=lambda t: t["pnl_dollars"] or 0)
            lines.append("BEST TRADE")
            lines.append(f"  {best['trade_id']} | {best['ticker']} | "
                         f"{best['side'].upper()} | P&L: ${best['pnl_dollars']:+.2f}")
            lines.append(f"  Signal: \"{best['signal_headline'][:80]}\"")
            lines.append(f"  Reasoning: {best['reasoning'][:120]}")
            lines.append("")
            lines.append("WORST TRADE")
            lines.append(f"  {worst['trade_id']} | {worst['ticker']} | "
                         f"{worst['side'].upper()} | P&L: ${worst['pnl_dollars']:+.2f}")
            lines.append(f"  Signal: \"{worst['signal_headline'][:80]}\"")
            lines.append(f"  Reasoning: {worst['reasoning'][:120]}")
            lines.append("")

        # ── Go-live recommendation ─────────────────────────────────────────────
        lines.append("GO-LIVE ASSESSMENT")
        if len(resolved) < 5:
            lines.append("  INSUFFICIENT DATA — need at least 5 resolved trades before assessing.")
        elif win_rate >= 0.55 and total_pnl > 0 and avg_edge > 0.03:
            lines.append("  POSITIVE — Strategy shows edge. Consider going live.")
        elif total_pnl < -20:
            lines.append("  NEGATIVE — Net P&L is significantly negative. Review strategy before going live.")
        else:
            lines.append("  NEUTRAL — Mixed results. Continue paper trading for more data.")

        lines.append("=" * 70)

        report = "\n".join(lines)
        return report

    def daily_summary(self) -> None:
        """Print a short daily summary to the log."""
        trades = self.get_all_trades()
        resolved = [t for t in trades if t["resolved"]]
        pnl = sum(t["pnl_dollars"] for t in resolved if t["pnl_dollars"] is not None)
        open_count = len([t for t in trades if not t["resolved"]])

        log.info(
            "[DAILY SUMMARY] Trades total=%d resolved=%d open=%d | Net P&L=$%+.2f | Days left=%.1f",
            len(trades), len(resolved), open_count, pnl, cfg.days_remaining_paper,
        )
