"""
Logging setup for the Kalshi bot.

Provides:
  - get_logger(name)  – colored console + rotating file logger
  - trade_log         – structured JSON logger for paper/live trade records
"""

import json
import logging
import logging.handlers
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import colorlog

from config import LOGS_DIR

# ── Log file paths ────────────────────────────────────────────────────────────
APP_LOG_FILE   = LOGS_DIR / "bot.log"
TRADE_LOG_FILE = LOGS_DIR / "trades.jsonl"   # newline-delimited JSON

# ── Formatter ─────────────────────────────────────────────────────────────────
_COLOR_FORMAT = (
    "%(log_color)s%(asctime)s %(levelname)-8s%(reset)s "
    "%(cyan)s%(name)-20s%(reset)s %(message)s"
)
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)-20s %(message)s"

_COLOR_MAP = {
    "DEBUG":    "white",
    "INFO":     "green",
    "WARNING":  "yellow",
    "ERROR":    "red",
    "CRITICAL": "bold_red",
}


class _WindowsSafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler using copy+truncate instead of rename for rotation.

    On Windows, os.rename() raises WinError 32 when any other process (VS Code,
    tail -f, etc.) holds the log file open. Rename failure leaves self.stream=None,
    causing every subsequent emit() to silently drop the record. Copy+truncate
    keeps the same path so open handles in other processes continue working.
    """

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        # Backup files are not held open -- rename is safe for those
        if self.backupCount > 0:
            for i in range(self.backupCount - 1, 0, -1):
                sfn = self.rotation_filename("%s.%d" % (self.baseFilename, i))
                dfn = self.rotation_filename("%s.%d" % (self.baseFilename, i + 1))
                if os.path.exists(sfn):
                    if os.path.exists(dfn):
                        os.remove(dfn)
                    os.rename(sfn, dfn)

            # Copy base log to .1, then truncate in place
            dfn = self.rotation_filename(self.baseFilename + ".1")
            if os.path.exists(dfn):
                os.remove(dfn)
            try:
                shutil.copy2(self.baseFilename, dfn)
            except Exception:
                pass

        # Truncate base file in place -- path and inode unchanged, so any
        # process holding it open continues writing to the same (now empty) file
        try:
            with open(self.baseFilename, "w", encoding=self.encoding or "utf-8"):
                pass
        except Exception:
            pass

        if not self.delay:
            self.stream = self._open()


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a logger with colored console output and rotating file output."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured
    logger.setLevel(level)

    # Console handler
    ch = colorlog.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(colorlog.ColoredFormatter(_COLOR_FORMAT, log_colors=_COLOR_MAP))

    # File handler (10 MB × 5 backups)
    fh = _WindowsSafeRotatingFileHandler(
        APP_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_FILE_FORMAT))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


# ── Structured trade logger ───────────────────────────────────────────────────

class TradeLogger:
    """Appends structured JSON records to trades.jsonl for easy analysis."""

    def __init__(self, path: Path = TRADE_LOG_FILE):
        self._path = path
        self._log  = get_logger("trade_logger")

    def _write(self, record: dict[str, Any]) -> None:
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def log_signal(
        self,
        *,
        source: str,
        headline: str,
        url: str,
        signal_strength: float,
        keywords_matched: list[str],
    ) -> None:
        self._write({
            "type": "SIGNAL",
            "source": source,
            "headline": headline,
            "url": url,
            "signal_strength": round(signal_strength, 4),
            "keywords_matched": keywords_matched,
        })

    def log_opportunity(
        self,
        *,
        ticker: str,
        market_title: str,
        market_yes_price: float,
        estimated_probability: float,
        edge: float,
        kelly_fraction: float,
        kelly_dollars: float,
        capped_dollars: float,
        side: str,           # "yes" or "no"
        reasoning: str,
    ) -> None:
        self._write({
            "type": "OPPORTUNITY",
            "ticker": ticker,
            "market_title": market_title,
            "market_yes_price": round(market_yes_price, 4),
            "estimated_probability": round(estimated_probability, 4),
            "edge": round(edge, 4),
            "kelly_fraction": round(kelly_fraction, 4),
            "kelly_dollars": round(kelly_dollars, 2),
            "capped_dollars": round(capped_dollars, 2),
            "side": side,
            "reasoning": reasoning,
        })

    def log_paper_trade(
        self,
        *,
        trade_id: str,
        ticker: str,
        market_title: str,
        side: str,
        contracts: int,
        price_cents: int,
        cost_dollars: float,
        estimated_probability: float,
        market_yes_price: float,
        edge: float,
        kelly_dollars: float,
        reasoning: str,
        signal_headline: str,
        signal_source: str,
    ) -> None:
        self._write({
            "type": "PAPER_TRADE",
            "trade_id": trade_id,
            "ticker": ticker,
            "market_title": market_title,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": round(cost_dollars, 2),
            "estimated_probability": round(estimated_probability, 4),
            "market_yes_price": round(market_yes_price, 4),
            "edge": round(edge, 4),
            "kelly_dollars": round(kelly_dollars, 2),
            "reasoning": reasoning,
            "signal_headline": signal_headline,
            "signal_source": signal_source,
        })

    def log_paper_resolution(
        self,
        *,
        trade_id: str,
        ticker: str,
        resolved_yes: bool,
        pnl_dollars: float,
    ) -> None:
        self._write({
            "type": "PAPER_RESOLUTION",
            "trade_id": trade_id,
            "ticker": ticker,
            "resolved_yes": resolved_yes,
            "pnl_dollars": round(pnl_dollars, 2),
        })

    def log_live_order(
        self,
        *,
        order_id: str,
        ticker: str,
        side: str,
        contracts: int,
        price_cents: int,
        cost_dollars: float,
        status: str,
    ) -> None:
        self._write({
            "type": "LIVE_ORDER",
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": round(cost_dollars, 2),
            "status": status,
        })

    def log_skipped(self, *, reason: str, ticker: str | None = None, headline: str | None = None) -> None:
        self._write({
            "type": "SKIPPED",
            "reason": reason,
            "ticker": ticker,
            "headline": headline,
        })


# Module-level singletons
trade_log = TradeLogger()
