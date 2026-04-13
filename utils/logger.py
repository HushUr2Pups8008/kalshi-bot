"""
Logging setup for the Kalshi bot.

Provides:
  - get_logger(name)          -- colored console + rotating file logger
  - emit_startup_banner(...)  -- write context line at startup and after each
                                 midnight rotation so every archived file is
                                 self-describing
  - trade_log                 -- structured JSON logger for paper/live records

File layout (logs/):
  app/bot.log                  -- active DEBUG+ log; rotated to bot.log.YYYY-MM-DD
  app/errors.log               -- active WARNING+ log; same rotation scheme
  app/bot.log.YYYY-MM-DD       -- 90-day archive
  app/errors.log.YYYY-MM-DD
  trades/trades.jsonl          -- structured trade records (monthly rotation)
  trades/archive/              -- trades-YYYYMM.jsonl.gz (12-month retention)
  reports/                     -- report_YYYYMMDD.txt, analysis_*.txt

Architecture:
  _app_fh and _err_fh are module-level singletons.  get_logger() creates one
  console handler per named logger and attaches the shared file handlers --
  this ensures exactly one write per record regardless of how many loggers are
  active, and exactly one rotation attempt at midnight.
"""

import json
import logging
import logging.handlers
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import colorlog

from config import LOGS_DIR

# ── Subdirectory layout ───────────────────────────────────────────────────────
_LOG_APP_DIR     = LOGS_DIR / "app"
_LOG_TRADES_DIR  = LOGS_DIR / "trades"
_LOG_REPORTS_DIR = LOGS_DIR / "reports"
_LOG_ARCHIVE_DIR = LOGS_DIR / "trades" / "archive"

for _d in (_LOG_APP_DIR, _LOG_TRADES_DIR, _LOG_REPORTS_DIR, _LOG_ARCHIVE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Log file paths ────────────────────────────────────────────────────────────
APP_LOG_FILE   = _LOG_APP_DIR    / "bot.log"
ERROR_LOG_FILE = _LOG_APP_DIR    / "errors.log"   # WARNING+ only -- quick triage
TRADE_LOG_FILE = _LOG_TRADES_DIR / "trades.jsonl" # newline-delimited JSON
LOG_REPORTS_DIR = _LOG_REPORTS_DIR                # for paper_trader report output

# ── Formatters ────────────────────────────────────────────────────────────────
_COLOR_FORMAT = (
    "%(log_color)s%(asctime)s UTC %(levelname)-8s%(reset)s "
    "%(cyan)s%(name)-20s%(reset)s %(message)s"
)
_FILE_FORMAT = "%(asctime)s UTC %(levelname)-8s %(name)-20s %(message)s"

_COLOR_MAP = {
    "DEBUG":    "white",
    "INFO":     "green",
    "WARNING":  "yellow",
    "ERROR":    "red",
    "CRITICAL": "bold_red",
}


def _utc_formatter(fmt: str) -> logging.Formatter:
    formatter = logging.Formatter(fmt)
    formatter.converter = time.gmtime
    return formatter


class _DailyRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """TimedRotatingFileHandler using copy+truncate instead of rename.

    Overrides rotate() so the base log path never moves. This lets any process
    (VS Code, tail -f) that has the file open continue writing without
    interruption across midnight rotation.

    On Windows this is required -- os.rename() raises WinError 32 when
    another process holds the file open. On Mac/Linux it is harmless: slightly
    less efficient than an atomic rename but functionally identical.

    Rotated backups: bot.log.YYYY-MM-DD, 90-day retention.

    Banner: a one-line context string (version/env/model) written at the top
    of every new file so any rotated archive is self-describing without
    needing to search back to the beginning of the bot run.

    Peer handlers: other _DailyRotatingFileHandler instances that should be
    rotated whenever this handler rotates. This ensures handlers with high
    log-level filters (e.g. WARNING-only) still rotate at midnight even if
    no qualifying messages arrive around that time.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._banner: str = ""
        self._peers: list["_DailyRotatingFileHandler"] = []

    def rotate(self, source: str, dest: str) -> None:
        try:
            shutil.copy2(source, dest)
        except Exception:
            pass
        try:
            with open(source, "w", encoding="utf-8"):
                pass  # truncate in place -- path and inode unchanged
        except Exception:
            pass

    def doRollover(self) -> None:
        super().doRollover()
        # Re-emit the banner at the top of the freshly-rotated (empty) file
        if self._banner and self.stream:
            try:
                self.stream.write(self._banner + "\n")
                self.stream.flush()
            except Exception:
                pass
        # Nudge peer handlers that may not have received any messages since
        # midnight (e.g. errors.log during a quiet period).
        for peer in self._peers:
            if peer.shouldRollover(logging.LogRecord(
                "", 0, "", 0, None, None, None
            )):
                peer.doRollover()


# ── Shared file handler singletons ────────────────────────────────────────────
# Created once on first get_logger() call; reused by every subsequent logger.
# This ensures exactly one rotation attempt at midnight and one write per record.
_app_fh: Optional[_DailyRotatingFileHandler] = None
_err_fh: Optional[_DailyRotatingFileHandler] = None


def _ensure_file_handlers() -> tuple[_DailyRotatingFileHandler, _DailyRotatingFileHandler]:
    global _app_fh, _err_fh
    if _app_fh is None:
        _app_fh = _DailyRotatingFileHandler(
            APP_LOG_FILE, when="midnight", backupCount=90, encoding="utf-8"
        )
        _app_fh.setLevel(logging.DEBUG)
        _app_fh.setFormatter(_utc_formatter(_FILE_FORMAT))
    if _err_fh is None:
        _err_fh = _DailyRotatingFileHandler(
            ERROR_LOG_FILE, when="midnight", backupCount=90, encoding="utf-8"
        )
        _err_fh.setLevel(logging.WARNING)
        _err_fh.setFormatter(_utc_formatter(_FILE_FORMAT))
        # bot.log rotates on every DEBUG+ message at midnight; nudge errors.log
        # along with it so it rotates even during quiet (no-WARNING) periods.
        _app_fh._peers.append(_err_fh)
    return _app_fh, _err_fh


def emit_startup_banner(version: str, model: str, env: str) -> None:
    """Write a one-line context banner to the active log files.

    Call once near the top of main.py run() after logging is set up.
    The banner is stored in each handler and re-emitted automatically
    after every midnight rotation, so every archived log file begins
    with a self-describing header line.

    Example output at top of bot.log (and errors.log):
        # ===== v0.6.7 | env=demo | model=qwen2.5:7b | py=3.14.0 =====
    """
    import sys
    banner = (
        f"# ===== v{version} | env={env} | model={model}"
        f" | py={sys.version.split()[0]} ====="
    )
    fh, eh = _ensure_file_handlers()
    for handler in (fh, eh):
        handler._banner = banner
        if handler.stream:
            try:
                handler.stream.write(banner + "\n")
                handler.stream.flush()
            except Exception:
                pass


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a named logger with console output and shared file output."""
    fh, eh = _ensure_file_handlers()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured
    logger.setLevel(level)

    # Console handler -- one per named logger (colored, INFO+)
    ch = colorlog.StreamHandler()
    ch.setLevel(logging.INFO)
    ch_formatter = colorlog.ColoredFormatter(_COLOR_FORMAT, log_colors=_COLOR_MAP)
    ch_formatter.converter = time.gmtime
    ch.setFormatter(ch_formatter)

    # Shared file handlers -- same objects attached to every logger
    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.addHandler(eh)
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
        source: str | None = None,
        headline: str | None = None,
        method: str | None = None,
        llm_direction: str | None = None,
        llm_magnitude: str | None = None,
    ) -> None:
        record = {
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
        }
        if source is not None:
            record["source"] = source
        if headline is not None:
            record["headline"] = headline
        if method is not None:
            record["method"] = method
        if llm_direction is not None:
            record["llm_direction"] = llm_direction
        if llm_magnitude is not None:
            record["llm_magnitude"] = llm_magnitude
        self._write(record)

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
        model_probability: float | None = None,
        market_price: float | None = None,
        edge: float | None = None,
        min_edge_threshold: float | None = None,
    ) -> None:
        record = {
            "type": "LIVE_ORDER",
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": round(cost_dollars, 2),
            "status": status,
        }
        if model_probability is not None:
            record["model_probability"] = round(model_probability, 4)
        if market_price is not None:
            record["market_price"] = round(market_price, 4)
        if edge is not None:
            record["edge"] = round(edge, 4)
        if min_edge_threshold is not None:
            record["min_edge_threshold"] = round(min_edge_threshold, 4)
        self._write(record)

    def log_skipped(
        self,
        *,
        reason: str,
        ticker: str | None = None,
        headline: str | None = None,
        source: str | None = None,
        method: str | None = None,
        llm_direction: str | None = None,
        llm_magnitude: str | None = None,
        model_probability: float | None = None,
        market_price: float | None = None,
        edge: float | None = None,
        min_edge_threshold: float | None = None,
    ) -> None:
        record = {
            "type": "SKIPPED",
            "reason": reason,
            "ticker": ticker,
            "headline": headline,
        }
        if source is not None:
            record["source"] = source
        if method is not None:
            record["method"] = method
        if llm_direction is not None:
            record["llm_direction"] = llm_direction
        if llm_magnitude is not None:
            record["llm_magnitude"] = llm_magnitude
        if model_probability is not None:
            record["model_probability"] = round(model_probability, 4)
        if market_price is not None:
            record["market_price"] = round(market_price, 4)
        if model_probability is not None and market_price is not None:
            signed_diff = model_probability - market_price
            record["signed_diff"] = round(signed_diff, 4)
            record["absolute_diff"] = round(abs(signed_diff), 4)
        if edge is not None:
            record["edge"] = round(edge, 4)
        if min_edge_threshold is not None:
            record["min_edge_threshold"] = round(min_edge_threshold, 4)
        self._write(record)

    def log_analysis_rejected(
        self,
        *,
        reason: str,
        ticker: str,
        source: str,
        headline: str,
        match_score: float,
        age_seconds: float | None = None,
    ) -> None:
        record = {
            "type": "ANALYSIS_REJECTED",
            "reason": reason,
            "ticker": ticker,
            "source": source,
            "headline": headline,
            "match_score": round(match_score, 4),
        }
        if age_seconds is not None:
            record["age_seconds"] = round(age_seconds, 2)
        self._write(record)

    def log_early_stale_drop(
        self,
        *,
        reason: str,
        source: str,
        headline: str,
        age_seconds: float | None = None,
        threshold_seconds: float | None = None,
    ) -> None:
        record = {
            "type": "EARLY_STALE_DROP",
            "reason": reason,
            "source": source,
            "headline": headline,
        }
        if age_seconds is not None:
            record["age_seconds"] = round(age_seconds, 2)
        if threshold_seconds is not None:
            record["threshold_seconds"] = round(threshold_seconds, 2)
        self._write(record)

    def log_early_fresh_pass(
        self,
        *,
        source: str,
        headline: str,
        age_seconds: float,
    ) -> None:
        self._write({
            "type": "EARLY_FRESH_PASS",
            "source": source,
            "headline": headline,
            "age_seconds": round(age_seconds, 2),
        })

    def log_signal_analysis_detail(
        self,
        *,
        ticker: str,
        source: str,
        headline: str,
        method: str,
        keywords: list[str],
        keyword_contributions: list[dict[str, Any]] | None,
        base_probability: float,
        final_probability: float,
        market_price: float,
        llm_direction: str | None = None,
        llm_magnitude: str | None = None,
        llm_confidence: float | None = None,
    ) -> None:
        record = {
            "type": "SIGNAL_ANALYSIS_DETAIL",
            "ticker": ticker,
            "source": source,
            "headline": headline,
            "method": method,
            "keywords": keywords,
            "base_probability": round(base_probability, 4),
            "final_probability": round(final_probability, 4),
            "market_price": round(market_price, 4),
        }
        if keyword_contributions:
            record["keyword_contributions"] = keyword_contributions
        if llm_direction is not None:
            record["llm_direction"] = llm_direction
        if llm_magnitude is not None:
            record["llm_magnitude"] = llm_magnitude
        if llm_confidence is not None:
            record["llm_confidence"] = round(llm_confidence, 4)
        self._write(record)

    def log_match_diagnostic(
        self,
        *,
        source: str,
        headline: str,
        ticker: str,
        market_title: str,
        match_score: float,
        matched_tokens: list[str],
        token_overlap_count: int,
        geo_overlap_count: int,
        generic_overlap_count: int,
        headline_token_count: int,
        market_title_token_count: int,
        overlap_ratio: float,
        low_match_quality: bool,
        heuristic_flags: list[str] | None = None,
    ) -> None:
        record = {
            "type": "MATCH_DIAGNOSTIC",
            "source": source,
            "headline": headline,
            "ticker": ticker,
            "market_title": market_title,
            "match_score": round(match_score, 4),
            "matched_tokens": matched_tokens,
            "token_overlap_count": token_overlap_count,
            "geo_overlap_count": geo_overlap_count,
            "generic_overlap_count": generic_overlap_count,
            "headline_token_count": headline_token_count,
            "market_title_token_count": market_title_token_count,
            "overlap_ratio": round(overlap_ratio, 4),
            "low_match_quality": low_match_quality,
        }
        if heuristic_flags:
            record["heuristic_flags"] = heuristic_flags
        self._write(record)

    def log_match_suppressed(
        self,
        *,
        source: str,
        headline: str,
        ticker: str,
        market_title: str,
        match_score: float,
        matched_tokens: list[str],
        heuristic_flags: list[str],
        reason: str,
    ) -> None:
        self._write({
            "type": "MATCH_SUPPRESSED",
            "source": source,
            "headline": headline,
            "ticker": ticker,
            "market_title": market_title,
            "match_score": round(match_score, 4),
            "matched_tokens": matched_tokens,
            "heuristic_flags": heuristic_flags,
            "reason": reason,
        })

    def log_match_suppression_candidate(
        self,
        *,
        source: str,
        headline: str,
        ticker: str,
        match_score: float,
        overlap_count: int,
        overlap_ratio: float,
        heuristic_flags: list[str],
        matched_tokens: list[str] | None = None,
    ) -> None:
        record: dict = {
            "type": "MATCH_SUPPRESSION_CANDIDATE",
            "source": source,
            "headline": headline[:200],
            "ticker": ticker,
            "match_score": round(match_score, 4),
            "overlap_count": overlap_count,
            "overlap_ratio": round(overlap_ratio, 4),
            "heuristic_flags": heuristic_flags,
        }
        if matched_tokens is not None:
            record["matched_tokens"] = matched_tokens
        self._write(record)

    def log_position_drift(
        self,
        *,
        ticker: str,
        entry_price: float,
        current_price: float,
        drift_cents: float,
        side: str,
        open_since: str,
    ) -> None:
        """Loop C: open position price has drifted significantly from entry."""
        self._write({
            "type": "POSITION_DRIFT",
            "ticker": ticker,
            "entry_price": round(entry_price, 2),
            "current_price": round(current_price, 2),
            "drift_cents": round(drift_cents, 2),
            "side": side,
            "open_since": open_since,
        })

    def log_new_market(
        self,
        *,
        ticker: str,
        title: str,
        series_ticker: str,
    ) -> None:
        """Loop D: a market not seen in the previous cache refresh has appeared."""
        self._write({
            "type": "NEW_MARKET",
            "ticker": ticker,
            "title": title,
            "series_ticker": series_ticker,
        })


# Module-level singletons
trade_log = TradeLogger()
