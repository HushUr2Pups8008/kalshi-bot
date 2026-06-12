"""
Performance analysis script for kalshi-bot.

Reads the preferred trade-log root at logs/trades/ and data/paper_trades.db to produce an end-to-end
report covering:
  1. Signal pipeline funnel (signal -> opportunity -> trade conversion rates)
  2. Placed trades performance (win rate, P&L, edge calibration)
  3. Skip reason breakdown
  4. Missed opportunities -- bets that should have been placed
  5. Per-source performance
  6. Edge calibration vs actual outcomes
  7. Go-live readiness gate status

Usage:
  python scripts/performance_analysis.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--enrich]

  --since   Start of analysis window (default: 30 days ago)
  --until   End of analysis window (default: today)
  --enrich  Query Kalshi REST API for resolutions of skipped opportunity tickers

Output: logs/analysis_YYYYMMDD_HHmm.txt  (also printed to stdout)

Run from the repo root (same directory as config.py).

From bash:
  cd /e/VS_Code/kalshi-bot
  .venv/Scripts/python.exe scripts/performance_analysis.py

The preferred source is logs/trades/. The legacy monolithic
logs/trades/trades.jsonl path remains readable during the cutover validation
window.
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tabulate import tabulate
from utils.trade_log_reader import TradeLogReadStats, iter_trade_records
from utils.output_paths import (
    PAPER_TRADES_DB,
    PERFORMANCE_REPORTS_DIR,
    RAW_TRADES_DIR,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
JSONL_PATH = RAW_TRADES_DIR
DB_PATH = PAPER_TRADES_DB
CACHE_PATH = REPO_ROOT / "logs" / "market_resolution_cache.json"
REPORTS_DIR = PERFORMANCE_REPORTS_DIR

PAPER_MIN_EDGE = 0.02
PAPER_FLAT_CONTRACTS = 5
CREDIBILITY_MIN_SAMPLE = 10
P0_PRICE_FIX_SENTINEL_KEY = "p0_price_fix_deployed_ts"

# Skip categories
_CONTROLLABLE_KEYWORDS = (
    "cooldown",
    "opposing position",
    "duplicate",
    "same-signal",
    "concentration",
    "status=active",
)
_BUG_SKIP_REASONS = ("market status=active",)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_ts(ts_str):
    """Parse ISO timestamp to UTC datetime."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _in_window(ts_str, since_dt, until_dt):
    dt = _parse_ts(ts_str)
    if dt is None:
        return False
    return since_dt <= dt <= until_dt


def _skip_category(reason):
    if not reason:
        return "unknown"
    r = reason.lower()
    if "g1_blended_confidence" in r:
        return "confidence gate"
    if "g2_evidence_source_class_diversity" in r:
        return "source diversity gate"
    if "g3_disagreement_score" in r:
        return "disagreement gate"
    if "g6_recency_score" in r:
        return "recency gate"
    if "series_correlation" in r:
        return "series correlation gate"
    if "status=active" in r:
        return "bug: market status=active (pre-v0.2.0)"
    if "cooldown" in r:
        return "cooldown"
    if "opposing" in r:
        return "opposing position"
    if "duplicate" in r or "same-signal" in r:
        return "duplicate / same-signal"
    if "concentration" in r:
        return "concentration limit"
    if "price" in r and "illiquid" in r:
        return "price illiquid"
    if "edge" in r and "below" in r:
        return "edge below threshold"
    if "balance" in r:
        return "insufficient balance"
    return "other"


def _is_controllable(reason):
    cat = _skip_category(reason)
    return cat not in (
        "edge below threshold",
        "price illiquid",
        "insufficient balance",
        "unknown",
        "confidence gate",
        "source diversity gate",
        "disagreement gate",
        "recency gate",
        "series correlation gate",
    )


def _counterfactual_pnl(side, price_cents, resolved_yes):
    """
    Compute hypothetical P&L for a skipped trade if it had been taken.
    Uses flat PAPER_FLAT_CONTRACTS (5) contracts.
    resolved_yes: True = market resolved YES, False = resolved NO
    """
    contracts = PAPER_FLAT_CONTRACTS
    p = price_cents / 100.0
    if side == "yes":
        if resolved_yes:
            return contracts * (1.0 - p)  # win: collect (1 - cost_per_contract)
        else:
            return -contracts * p  # loss: lose cost
    else:
        no_price = 1.0 - p
        if not resolved_yes:
            return contracts * p          # win: profit per NO contract = YES price fraction
        else:
            return -contracts * no_price  # loss: lose the NO price paid


def _pct(num, denom):
    if denom == 0:
        return "N/A"
    return "%.1f%%" % (100.0 * num / denom)


def _fmt_pnl(v):
    if v is None:
        return "--"
    sign = "+" if v >= 0 else ""
    return "%s$%.2f" % (sign, v)


def _venue_name(trade):
    venue = str(trade.get("venue") or "").strip()
    return venue or "unknown"


def _venue_performance_rows(db_trades):
    rows = []
    for venue in sorted({_venue_name(t) for t in db_trades}):
        venue_trades = [t for t in db_trades if _venue_name(t) == venue]
        resolved = [t for t in venue_trades if t.get("resolved")]
        wins = [t for t in resolved if (t.get("pnl_dollars") or 0) > 0]
        losses = [t for t in resolved if (t.get("pnl_dollars") or 0) <= 0]
        net_pnl = sum(t.get("pnl_dollars") or 0 for t in resolved)
        total_cost = sum(t.get("cost_dollars") or 0 for t in venue_trades)
        avg_edge = (
            sum(t.get("edge") or 0 for t in venue_trades) / len(venue_trades)
            if venue_trades
            else 0.0
        )
        rows.append(
            [
                venue,
                len(venue_trades),
                len(resolved),
                len(venue_trades) - len(resolved),
                _pct(len(wins), len(resolved)),
                "%d W / %d L" % (len(wins), len(losses)),
                _fmt_pnl(net_pnl),
                "$%.2f" % total_cost,
                "%+.4f" % avg_edge,
            ]
        )
    return rows


def _p0_cohort_boundary(conn):
    try:
        row = conn.execute(
            "SELECT value FROM bot_state WHERE key = ?",
            (P0_PRICE_FIX_SENTINEL_KEY,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    value = row[0]
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _p0_boundary_missing_message(section_name):
    return "\n".join(
        [
            section_name + ":",
            "",
            "P0 cohort boundary missing "
            "(bot_state.%s); not reporting blended lifetime aggregates."
            % P0_PRICE_FIX_SENTINEL_KEY,
        ]
    )


def _has_table(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _has_columns(conn, table_name, column_names):
    if not _has_table(conn, table_name):
        return False
    cols = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table_name)}
    return set(column_names).issubset(cols)


def _p0_cohort_specs(boundary):
    return (
        ("Pre-P0 (frozen)", "ts < ?", (boundary,)),
        ("Post-P0", "ts >= ?", (boundary,)),
    )


def _parse_p0_boundary(value):
    """Parse a P0 sentinel timestamp string into a tz-aware UTC datetime, or None.

    Mirrors the validation in ``_p0_cohort_boundary`` but operates on the value
    already loaded into the ``state`` dict (bot_state KV) so section 8 can split
    its cohort without re-opening the DB.
    """
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _equity_max_drawdown(points):
    """Running peak-to-trough max drawdown over an ordered equity series.

    ``points`` is a list of positive bankroll samples in chronological order.
    Returns the largest fractional decline from a running peak (0.0 if none).
    Unlike start-vs-now, this captures intra-history dips even when the series
    later recovers.
    """
    peak = None
    max_dd = 0.0
    for p in points:
        if p is None or p <= 0:
            continue
        if peak is None or p > peak:
            peak = p
        elif peak > 0:
            dd = (peak - p) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _bankroll_equity_points(trades, bankroll_now):
    """Chronological bankroll equity samples: each trade's pre-trade bankroll
    snapshot (``notional_bankroll_before``) in ts order, plus the current
    notional bankroll as the final point. Entry-sampled (coarse) but enough to
    detect a peak-to-trough dip the start-vs-now metric misses.

    Trades without a parseable ts are EXCLUDED — they cannot be placed on the
    time axis, and forcing them to a sentinel (min/max) would mis-order the
    curve and distort the peak-to-trough number.
    """
    dated = []
    for trade in trades:
        dt = _parse_ts(trade.get("ts"))
        if dt is None:
            continue
        before = trade.get("notional_bankroll_before")
        try:
            before_value = float(before)
        except (TypeError, ValueError):
            continue
        if before_value > 0:
            dated.append((dt, before_value))
    pts = [value for _, value in sorted(dated, key=lambda x: x[0])]
    try:
        now_value = float(bankroll_now)
    except (TypeError, ValueError):
        now_value = 0.0
    if now_value > 0:
        pts.append(now_value)
    return pts


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_jsonl(since_dt, until_dt):
    """Load and filter trade-log entries within the date window."""
    entries = []
    read_stats = TradeLogReadStats()
    for obj in iter_trade_records(JSONL_PATH, since=since_dt, until=until_dt, stats=read_stats):
        if "event" in obj and "type" not in obj:
            obj["type"] = obj["event"]
        entries.append(obj)
    if read_stats.lines_total == 0 and not JSONL_PATH.exists():
        print("WARNING: trade log not found at %s" % JSONL_PATH)
    return entries


def load_db_trades(since_dt=None, until_dt=None):
    """Load trades from paper_trades.db. Returns list of dicts.

    When a window is supplied, keep DB-derived report sections aligned with
    the JSONL window instead of mixing windowed funnel counts with lifetime DB
    performance.
    """
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM paper_trades ORDER BY ts").fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    if since_dt is None and until_dt is None:
        return trades

    filtered = []
    for trade in trades:
        dt = _parse_ts(trade.get("ts"))
        if dt is None:
            continue
        if since_dt is not None and dt < since_dt:
            continue
        if until_dt is not None and dt > until_dt:
            continue
        filtered.append(trade)
    return filtered


def load_db_state():
    """Load bot_state key-value pairs from DB."""
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("SELECT key, value FROM bot_state").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}


def load_resolution_cache():
    if CACHE_PATH.exists():
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_resolution_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# ---------------------------------------------------------------------------
# Resolution lookup
# ---------------------------------------------------------------------------


def build_resolution_map(db_trades, jsonl_entries, enrich):
    """
    Build ticker -> {"resolved_yes": bool, "result": "yes"/"no"/""}
    from DB trades, JSONL PAPER_RESOLUTION entries, and optionally Kalshi API.
    """
    resolution = {}

    # From DB
    for t in db_trades:
        if t.get("resolved") and t.get("resolved_yes") is not None:
            resolution[t["ticker"]] = {
                "resolved_yes": bool(t["resolved_yes"]),
                "result": "yes" if t["resolved_yes"] else "no",
                "source": "db",
            }

    # From trade-log PAPER_RESOLUTION entries
    for e in jsonl_entries:
        if e.get("type") == "PAPER_RESOLUTION":
            ticker = e.get("ticker", "")
            if ticker and ticker not in resolution:
                ry = e.get("resolved_yes")
                if ry is not None:
                    resolution[ticker] = {
                        "resolved_yes": bool(ry),
                        "result": "yes" if ry else "no",
                        "source": "jsonl",
                    }

    if enrich:
        cache = load_resolution_cache()
        tickers_to_check = set()

        # Collect tickers from SKIPPED entries not yet resolved
        for e in jsonl_entries:
            if e.get("type") == "SKIPPED":
                t = e.get("ticker", "")
                if t and t not in resolution and t not in cache:
                    tickers_to_check.add(t)

        if tickers_to_check:
            print(
                "\nFetching %d ticker resolutions from Kalshi API..."
                % len(tickers_to_check)
            )
            sys.path.insert(0, str(REPO_ROOT))
            try:
                from kalshi.rest_client import KalshiRestClient

                client = KalshiRestClient()
                for ticker in sorted(tickers_to_check):
                    market = client.get_market(ticker)
                    if market:
                        cache[ticker] = {
                            "status": market.status,
                            "result": market.result or "",
                        }
                    else:
                        cache[ticker] = {"status": "unknown", "result": ""}
                save_resolution_cache(cache)
                print("Resolution cache updated: %s" % CACHE_PATH)
            except Exception as exc:
                print("WARNING: Kalshi API enrichment failed: %s" % exc)

        # Merge cache into resolution map
        for ticker, info in cache.items():
            if ticker not in resolution and info.get("result") in ("yes", "no"):
                resolution[ticker] = {
                    "resolved_yes": info["result"] == "yes",
                    "result": info["result"],
                    "source": "api_cache",
                }

    return resolution


# ---------------------------------------------------------------------------
# Analysis sections
# ---------------------------------------------------------------------------


def section_header(title):
    line = "=" * 70
    return "\n%s\n  %s\n%s\n" % (line, title, line)


def section_pipeline_funnel(entries, db_trades):
    signals = [e for e in entries if e.get("type") == "SIGNAL"]
    opportunities = [e for e in entries if e.get("type") == "OPPORTUNITY"]
    # Exclude KXTEST trades generated by the test suite -- they never write to DB
    # and would inflate the PAPER_TRADE / PAPER_RESOLUTION counts.
    paper_trades = [
        e for e in entries
        if e.get("type") == "PAPER_TRADE" and "KXTEST" not in e.get("ticker", "")
    ]
    skipped = [e for e in entries if e.get("type") == "SKIPPED"]
    resolutions = [
        e for e in entries
        if e.get("type") == "PAPER_RESOLUTION" and "KXTEST" not in e.get("ticker", "")
    ]

    # Resolved from DB trades (joined by trade_id would be ideal, use count)
    db_resolved = sum(1 for t in db_trades if t.get("resolved"))
    db_open = sum(1 for t in db_trades if not t.get("resolved"))

    n_sig = len(signals)
    n_opp = len(opportunities)
    n_trd = len(paper_trades)
    n_skip = len(skipped)

    lines = []
    lines.append("Signal pipeline (events in window):")
    lines.append("")
    lines.append("  Signals detected           : %4d" % n_sig)
    lines.append(
        "  Opportunities identified    : %4d  (%s of signals)"
        % (n_opp, _pct(n_opp, n_sig))
    )
    n_executor = n_trd + n_skip   # opportunities that actually reached the executor
    silent_drops = max(0, n_opp - n_executor)
    lines.append(
        "  Trades placed (PAPER_TRADE) : %4d  (%s of opportunities)"
        % (n_trd, _pct(n_trd, n_opp))
    )
    lines.append(
        "  Skipped by executor        : %4d  (%s of opportunities)"
        % (n_skip, _pct(n_skip, n_opp))
    )
    if silent_drops > 0:
        lines.append(
            "  Silent drops (pre-executor): %4d  (%s of opportunities)"
            "  [Kelly gate bug, fixed v0.24.1]"
            % (silent_drops, _pct(silent_drops, n_opp))
        )
    lines.append("")
    lines.append("  DB resolved trades         : %4d" % db_resolved)
    lines.append("  DB open trades             : %4d" % db_open)
    lines.append("  PAPER_RESOLUTION events    : %4d" % len(resolutions))

    return "\n".join(lines)


def section_single_vs_multi_source(entries, db_trades):
    """PROFIT-SOURCE-001: split resolved-trade performance by distinct evidence
    source-class count (single-source vs multi-source), to evaluate whether
    allowing single-source trades (G2 relaxed to 1) helps or hurts. The count is
    captured per trade in the PAPER_TRADE event's signal_meta
    (evidence_source_class_count); fast-lane trades have no count (unknown)."""
    count_by_trade = {}
    for e in entries:
        if e.get("type") != "PAPER_TRADE":
            continue
        tid = e.get("trade_id")
        sm = e.get("signal_meta")
        if tid and isinstance(sm, dict):
            c = sm.get("evidence_source_class_count")
            if c is not None:
                try:
                    count_by_trade[tid] = int(c)
                except (TypeError, ValueError):
                    pass

    buckets = {"single (1 source)": [], "multi (>=2 sources)": [], "unknown/fast-lane": []}
    for t in db_trades:
        if not t.get("resolved"):
            continue
        c = count_by_trade.get(t.get("trade_id"))
        if c is None:
            buckets["unknown/fast-lane"].append(t)
        elif c <= 1:
            buckets["single (1 source)"].append(t)
        else:
            buckets["multi (>=2 sources)"].append(t)

    lines = [
        "Single-source vs multi-source performance (resolved; PROFIT-SOURCE-001):",
        "",
        "  G2 now allows single-source (G2_MIN_SOURCE_CLASSES=1); this tracks how",
        "  single- vs multi-source trades actually perform so the relaxation can be",
        "  kept or reverted on evidence. Source-class count from PAPER_TRADE signal_meta.",
        "",
        "%-22s %9s %9s %11s" % ("Cohort", "Resolved", "Win Rate", "Net P&L"),
        "-" * 54,
    ]
    any_data = False
    for label, rows in buckets.items():
        if not rows:
            continue
        any_data = True
        wins = sum(1 for r in rows if (r.get("pnl_dollars") or 0) > 0)
        net = sum(r.get("pnl_dollars") or 0 for r in rows)
        wr = "%.0f%%" % (wins / len(rows) * 100)
        lines.append("%-22s %9d %9s %+11.2f" % (label, len(rows), wr, net))
    if not any_data:
        lines.append("  No resolved trades with source-class tracking yet")
        lines.append("  (single-source trades only started after the G2 relaxation).")
    return "\n".join(lines)


def section_placed_performance(entries, db_trades, resolution_map):
    db_by_id = {t["trade_id"]: t for t in db_trades}

    # Use DB as primary source for resolved trades; JSONL PAPER_TRADE for context
    resolved = [t for t in db_trades if t.get("resolved")]
    unresolved = [t for t in db_trades if not t.get("resolved")]

    if not db_trades:
        return "No trades in database."

    wins = [t for t in resolved if (t.get("pnl_dollars") or 0) > 0]
    losses = [t for t in resolved if (t.get("pnl_dollars") or 0) <= 0]
    net_pnl = sum(t.get("pnl_dollars") or 0 for t in resolved)
    total_cost = sum(t.get("cost_dollars") or 0 for t in db_trades)
    roi = (net_pnl / total_cost * 100) if total_cost > 0 else 0.0
    avg_edge = (
        sum(t.get("edge") or 0 for t in db_trades) / len(db_trades) if db_trades else 0
    )

    lines = []
    lines.append("Placed trades (from paper_trades.db):")
    lines.append(
        "  Cohort: IN-WINDOW (report window only). Go-live readiness (section 8)"
    )
    lines.append(
        "  gates on the POST-P0 cohort, so its win rate / drawdown differ from"
    )
    lines.append("  the figures below.")
    lines.append("")
    lines.append("  Total trades  : %d" % len(db_trades))
    lines.append("  Resolved      : %d" % len(resolved))
    lines.append("  Open          : %d" % len(unresolved))
    lines.append(
        "  Win rate      : %s  (%d W / %d L)"
        % (_pct(len(wins), len(resolved)), len(wins), len(losses))
    )
    lines.append("  Net P&L       : %s" % _fmt_pnl(net_pnl))
    lines.append("  Total cost    : $%.2f" % total_cost)
    lines.append("  ROI on cost   : %.1f%%" % roi)
    lines.append("  Avg edge      : %+.4f" % avg_edge)

    venue_rows = _venue_performance_rows(db_trades)
    if venue_rows:
        lines.append("")
        lines.append("By venue:")
        lines.append(
            tabulate(
                venue_rows,
                headers=[
                    "Venue",
                    "Trades",
                    "Resolved",
                    "Open",
                    "Win Rate",
                    "W/L",
                    "Net P&L",
                    "Cost",
                    "Avg Edge",
                ],
                tablefmt="simple",
            )
        )

    if resolved:
        best = max(resolved, key=lambda t: t.get("pnl_dollars") or -999)
        worst = min(resolved, key=lambda t: t.get("pnl_dollars") or 999)
        lines.append("")
        lines.append(
            "  Best  trade: %s | %s %s | edge=%+.3f | P&L=%s"
            % (
                best["ticker"][:35],
                best["side"].upper(),
                best["ts"][:10],
                best.get("edge") or 0,
                _fmt_pnl(best.get("pnl_dollars")),
            )
        )
        lines.append(
            "  Worst trade: %s | %s %s | edge=%+.3f | P&L=%s"
            % (
                worst["ticker"][:35],
                worst["side"].upper(),
                worst["ts"][:10],
                worst.get("edge") or 0,
                _fmt_pnl(worst.get("pnl_dollars")),
            )
        )

    # Resolved trades table
    if resolved:
        lines.append("")
        lines.append("Resolved trades:")
        rows = []
        for t in sorted(resolved, key=lambda x: x["ts"]):
            result = "YES" if t.get("resolved_yes") else "NO"
            # v0.30.1/F-11 follow-up: render missing entry price as "n/a"
            # rather than `"%.0fc" % (... or 0)` which silently displayed a
            # missing price as "0c" (looks like a real 0c entry-price = audit
            # bug). Explicit 0 is preserved as "0c"; only None/missing renders
            # as "n/a". P-4 LD-2: no silent-50 (display-layer corollary: no
            # silent-0 either).
            myp = t.get("entry_price_cents")
            if myp is None:
                myp = t.get("market_yes_price")
            mkt_p_text = "n/a" if myp is None else "%.0fc" % myp
            rows.append(
                [
                    t["ts"][:10],
                    t["ticker"][:28],
                    t["side"].upper(),
                    "%+.3f" % (t.get("edge") or 0),
                    "%.2f" % (t.get("estimated_prob") or 0),
                    mkt_p_text,
                    "$%.2f" % (t.get("cost_dollars") or 0),
                    result,
                    _fmt_pnl(t.get("pnl_dollars")),
                    (t.get("signal_source") or "")[:22],
                ]
            )
        lines.append(
            tabulate(
                rows,
                headers=[
                    "Date",
                    "Ticker",
                    "Side",
                    "Edge",
                    "Est.P",
                    "Mkt.P",
                    "Cost",
                    "Result",
                    "P&L",
                    "Source",
                ],
                tablefmt="simple",
            )
        )

    return "\n".join(lines)


def section_skip_breakdown(entries):
    skipped = [e for e in entries if e.get("type") == "SKIPPED"]
    if not skipped:
        return "No skipped opportunities in window."

    by_cat = defaultdict(list)
    for s in skipped:
        cat = _skip_category(s.get("reason", ""))
        by_cat[cat].append(s)

    lines = []
    lines.append("Skip reason breakdown (%d total):" % len(skipped))
    lines.append("")
    rows = []
    for cat, items in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        controllable = (
            "yes" if _is_controllable(items[0].get("reason", "")) else "no (correct)"
        )
        rows.append([len(items), cat, controllable])
    lines.append(
        tabulate(
            rows, headers=["Count", "Category", "Controllable?"], tablefmt="simple"
        )
    )

    # Top tickers per controllable category
    lines.append("")
    lines.append("Most-skipped tickers (controllable skips only):")
    ticker_counts = defaultdict(int)
    for s in skipped:
        if _is_controllable(s.get("reason", "")):
            ticker_counts[s.get("ticker", "unknown")] += 1
    top = sorted(ticker_counts.items(), key=lambda x: -x[1])[:10]
    for ticker, count in top:
        lines.append("  %3d  %s" % (count, ticker))

    if by_cat.get("other"):
        raw_counts = Counter(
            (s.get("reason") or "unknown").strip() or "unknown"
            for s in by_cat["other"]
        )
        lines.append("")
        lines.append("Unclassified raw skip reasons:")
        raw_rows = [[count, reason[:120]] for reason, count in raw_counts.most_common(10)]
        lines.append(
            tabulate(raw_rows, headers=["Count", "Raw Reason"], tablefmt="simple")
        )

    return "\n".join(lines)


def section_missed_opportunities(entries, resolution_map):
    """
    Find SKIPPED entries paired with their OPPORTUNITY, compute counterfactual P&L
    for those where resolution is known and skip was controllable.
    """
    opportunities = [e for e in entries if e.get("type") == "OPPORTUNITY"]
    skipped = [e for e in entries if e.get("type") == "SKIPPED"]

    if not skipped:
        return "No skipped entries in window."

    # Build opportunity index: (ticker, ts[:13]) -> opp entry
    # Use minute-level matching to handle slight timestamp drift
    opp_index = {}
    for o in opportunities:
        ts = o.get("ts", "")
        ticker = o.get("ticker", "")
        key = (ticker, ts[:16])  # minute precision
        opp_index[key] = o

    # Match skips to opportunities
    paired = []
    unmatched = 0
    for s in skipped:
        if not _is_controllable(s.get("reason", "")):
            continue
        ts = s.get("ts", "")
        ticker = s.get("ticker", "")
        # Try exact minute, then +-1 minute
        opp = None
        for offset in (0, -1, 1, -2, 2):
            try:
                dt = _parse_ts(ts)
                if dt is None:
                    break
                candidate_ts = (dt + timedelta(minutes=offset)).strftime(
                    "%Y-%m-%dT%H:%M"
                )
                opp = opp_index.get((ticker, candidate_ts))
                if opp:
                    break
            except Exception:
                pass
        if opp:
            paired.append((s, opp))
        else:
            unmatched += 1

    lines = []
    lines.append("Missed opportunities (controllable skips with resolution known):")
    lines.append("")
    lines.append(
        "  Controllable skips in window : %d"
        % len([s for s in skipped if _is_controllable(s.get("reason", ""))])
    )
    lines.append("  Paired to an opportunity     : %d" % len(paired))
    if unmatched:
        lines.append("  Unmatched (no opportunity)   : %d" % unmatched)

    # For each paired, check if resolution is known
    resolvable = []
    skipped_no_price = 0
    for skip, opp in paired:
        ticker = opp.get("ticker", "")
        res = resolution_map.get(ticker)
        if res:
            # v0.30.1 follow-up: P-4 LD-2 no silent-50. Pre-fix code was
            # `int(opp.get("market_yes_price", 50))` — when the
            # OPPORTUNITY log row lacked `market_yes_price`, the entry-
            # price defaulted to 50¢ and silently fed the counterfactual
            # P&L computation, contaminating the missed-opportunities
            # table. Fail-closed: skip rows with no usable price and
            # surface the count in section output.
            # F-11 P1-C: accept either canonical key (post-bounce) or legacy alias (pre-bounce)
            myp = opp.get("entry_price_cents") if opp.get("entry_price_cents") is not None else opp.get("market_yes_price")
            if myp is None:
                skipped_no_price += 1
                continue
            price_cents = int(myp)
            side = opp.get("side", "yes")
            edge = opp.get("edge", 0)
            est_p = opp.get("estimated_probability", 0)
            resolved_yes = res["resolved_yes"]
            cf_pnl = _counterfactual_pnl(side, price_cents, resolved_yes)
            resolvable.append(
                {
                    "ts": skip.get("ts", "")[:10],
                    "ticker": ticker,
                    "reason": skip.get("reason", ""),
                    "edge": edge,
                    "side": side,
                    "est_p": est_p,
                    "mkt_p": price_cents,
                    "result": res["result"].upper(),
                    "cf_pnl": cf_pnl,
                }
            )

    lines.append("  With known resolution        : %d" % len(resolvable))
    if skipped_no_price:
        lines.append(
            "  Excluded — missing market price : %d "
            "(P-4 LD-2 fail-closed; no silent-50)" % skipped_no_price
        )

    if not resolvable:
        lines.append("")
        lines.append("  No missed opportunities with known resolution in this window.")
        lines.append("  Run with --enrich to fetch Kalshi API resolutions.")
        return "\n".join(lines)

    # Summary
    profitable = [r for r in resolvable if r["cf_pnl"] > 0]
    total_cf = sum(r["cf_pnl"] for r in resolvable)
    lines.append("")
    lines.append(
        "  Would have been profitable   : %d of %d" % (len(profitable), len(resolvable))
    )
    lines.append("  Total counterfactual P&L     : %s" % _fmt_pnl(total_cf))

    # Table
    lines.append("")
    rows = []
    for r in sorted(resolvable, key=lambda x: x["ts"]):
        cat = _skip_category(r["reason"])
        rows.append(
            [
                r["ts"],
                r["ticker"][:28],
                cat[:22],
                "%+.3f" % r["edge"],
                r["side"].upper(),
                "%.2f" % r["est_p"],
                "%dc" % r["mkt_p"],
                r["result"],
                _fmt_pnl(r["cf_pnl"]),
            ]
        )
    lines.append(
        tabulate(
            rows,
            headers=[
                "Date",
                "Ticker",
                "Skip Category",
                "Edge",
                "Side",
                "Est.P",
                "Mkt.P",
                "Resolution",
                "CF P&L",
            ],
            tablefmt="simple",
        )
    )

    # Key insight note
    bug_skips_wrong = [
        r for r in resolvable if "status=active" in r["reason"] and r["cf_pnl"] < 0
    ]
    if bug_skips_wrong:
        lines.append("")
        lines.append(
            "  NOTE: %d of the status=active bug skips would have LOST -- the bug"
            % len(bug_skips_wrong)
        )
        lines.append(
            "  accidentally prevented bad trades. Signal quality issue, not just a bug."
        )

    return "\n".join(lines)


def section_source_performance(entries, db_trades):
    """Per-source win rate and P&L from DB trades."""
    if not db_trades:
        return "No placed trades in database."

    by_source = defaultdict(
        lambda: {"trades": 0, "resolved": 0, "wins": 0, "pnl": 0.0, "cost": 0.0}
    )
    for t in db_trades:
        src = t.get("signal_source") or "unknown"
        by_source[src]["trades"] += 1
        by_source[src]["cost"] += t.get("cost_dollars") or 0
        if t.get("resolved"):
            by_source[src]["resolved"] += 1
            pnl = t.get("pnl_dollars") or 0
            by_source[src]["pnl"] += pnl
            if pnl > 0:
                by_source[src]["wins"] += 1

    rows = []
    for src, d in sorted(
        by_source.items(), key=lambda x: -(x[1]["wins"] / max(x[1]["resolved"], 1))
    ):
        n_res = d["resolved"]
        win_rate = ("%.0f%%" % (100.0 * d["wins"] / n_res)) if n_res else "N/A"
        note = " (low data)" if n_res < 3 else ""
        rows.append(
            [
                src[:35],
                d["trades"],
                d["resolved"],
                win_rate + note,
                _fmt_pnl(d["pnl"]) if n_res else "--",
            ]
        )

    lines = ["Per-source performance (from DB trades):", ""]
    lines.append(
        tabulate(
            rows,
            headers=["Source", "Trades", "Resolved", "Win Rate", "Net P&L"],
            tablefmt="simple",
        )
    )
    return "\n".join(lines)


def section_source_quality():
    """
    Source quality funnel from source_stats table.

    Shows posts_seen -> signals -> opportunities -> trades per source,
    with signal_rate and quality label.  Useful for identifying subreddits
    that consume poll slots without contributing actionable signals.
    """
    if not DB_PATH.exists():
        return "No database found."

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM source_stats ORDER BY posts_seen DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return "source_stats table not found -- bot has not run with v0.20.0+ yet."
    finally:
        conn.close()

    if not rows:
        return "No source stats recorded yet (table exists but is empty)."

    # Read thresholds from env (same defaults as SourceStats class)
    min_posts = int(os.getenv("SOURCE_STATS_MIN_POSTS", "100"))
    low_signal_rate = float(os.getenv("SOURCE_STATS_LOW_SIGNAL_RATE", "0.005"))
    zero_signal_posts = int(os.getenv("SOURCE_STATS_ZERO_SIGNAL_POSTS", "200"))

    table_rows = []
    for r in rows:
        posts = r["posts_seen"]
        sigs = r["signals"]
        opps = r["opportunities"]
        trades = r["trades"]

        sig_rate = sigs / posts if posts > 0 else 0.0
        opp_rate = opps / sigs if sigs > 0 else None

        suppressed = posts >= zero_signal_posts and sigs == 0
        low_quality = (
            posts >= min_posts and sig_rate < low_signal_rate and not suppressed
        )
        insufficient = posts < min_posts

        if suppressed:
            quality = "SUPPRESSED"
        elif low_quality:
            quality = "Low"
        elif insufficient:
            quality = "?"
        else:
            quality = "Good"

        table_rows.append(
            [
                (r["source"] or "")[:30],
                "{:,}".format(posts),
                "{:,}".format(sigs),
                "%.1f%%" % (sig_rate * 100) if posts > 0 else "N/A",
                "{:,}".format(opps),
                "%.0f%%" % (opp_rate * 100) if opp_rate is not None else "--",
                "{:,}".format(trades),
                quality,
            ]
        )

    # Sort by posts_seen desc (already sorted by query, but re-sort for stability)
    table_rows.sort(key=lambda r: -int(r[1].replace(",", "")))

    lines = ["Source quality funnel (from source_stats table):", ""]
    lines.append(
        tabulate(
            table_rows,
            headers=[
                "Source",
                "Posts",
                "Signals",
                "Sig%",
                "Opps",
                "Opp%",
                "Trades",
                "Quality",
            ],
            tablefmt="simple",
        )
    )

    # Quick summary
    suppressed_count = sum(1 for r in table_rows if r[7] == "SUPPRESSED")
    low_count = sum(1 for r in table_rows if r[7] == "Low")
    if suppressed_count or low_count:
        lines.append("")
        lines.append(
            "  %d suppressed (>=%d posts, 0 signals), %d low-quality (<%.1f%% signal rate)"
            % (suppressed_count, zero_signal_posts, low_count, low_signal_rate * 100)
        )

    return "\n".join(lines)


def section_candidate_subreddits(max_rows=None):
    """
    Show subreddits discovered via Reddit post search (v0.21.0 discovery loop).
    Displays discovered_ts, query that found it, probe_count, last_probed, status.
    """
    if not DB_PATH.exists():
        return "No database found."

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT sub, discovered_ts, discovered_via, probe_count, last_probed, status
            FROM subreddit_candidates
            ORDER BY status ASC, probe_count DESC, discovered_ts DESC
            """
        ).fetchall()
        max_probes = int(os.getenv("SUBREDDIT_CANDIDATE_MAX_PROBES", "3"))
        has_source_stats = (
            conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'source_stats'
                """
            ).fetchone()
            is not None
        )
        if has_source_stats:
            eligible_row = conn.execute(
                """
                SELECT count(*)
                FROM subreddit_candidates c
                LEFT JOIN source_stats s ON s.source = 'r/' || c.sub
                WHERE c.status = 'candidate'
                  AND c.probe_count >= ?
                  AND COALESCE(s.signals, 0) = 0
                  AND COALESCE(s.opportunities, 0) = 0
                  AND COALESCE(s.trades, 0) = 0
                """,
                (max_probes,),
            ).fetchone()
        else:
            eligible_row = conn.execute(
                """
                SELECT count(*)
                FROM subreddit_candidates
                WHERE status = 'candidate'
                  AND probe_count >= ?
                """,
                (max_probes,),
            ).fetchone()
        max_probe_no_signal = int(eligible_row[0] if eligible_row else 0)
    except sqlite3.OperationalError:
        return (
            "subreddit_candidates table not found -- bot has not run with v0.21.0+ yet."
        )
    finally:
        conn.close()

    if not rows:
        return "No candidate subreddits discovered yet."

    if max_rows is None:
        max_rows = int(os.getenv("PERFORMANCE_REPORT_MAX_SUBREDDIT_ROWS", "100"))
    max_rows = max(1, max_rows)

    status_counts = Counter(r["status"] for r in rows)
    visible_rows = rows[:max_rows]

    table_rows = []
    for r in visible_rows:
        discovered = (r["discovered_ts"] or "")[:10]
        via = (r["discovered_via"] or "")[:25]
        last = (r["last_probed"] or "never")[:16]
        table_rows.append(
            [
                "r/" + r["sub"],
                discovered,
                via,
                r["probe_count"],
                last,
                r["status"],
            ]
        )

    lines = ["Candidate subreddits (discovered via Reddit post search):", ""]
    summary = ", ".join(
        "%s: %d" % (status, count)
        for status, count in sorted(status_counts.items())
    )
    lines.append("  Status summary: %s" % summary)
    if max_probe_no_signal:
        lines.append(
            "  Max-probe no-signal eligible: %d (SUBREDDIT_CANDIDATE_MAX_PROBES=%d)"
            % (max_probe_no_signal, max_probes)
        )
    if len(rows) > len(visible_rows):
        lines.append(
            "  Showing %d of %d rows (set PERFORMANCE_REPORT_MAX_SUBREDDIT_ROWS to adjust)"
            % (len(visible_rows), len(rows))
        )
    lines.append("")
    lines.append(
        tabulate(
            table_rows,
            headers=[
                "Sub",
                "Discovered",
                "Via Query",
                "Probes",
                "Last Probed",
                "Status",
            ],
            tablefmt="simple",
        )
    )

    candidate_count = sum(1 for r in rows if r["status"] == "candidate")
    suppressed_count = sum(1 for r in rows if r["status"] == "suppressed")
    lines.append("")
    lines.append(
        "  %d active candidates, %d suppressed (zero-signal after probing)"
        % (candidate_count, suppressed_count)
    )

    return "\n".join(lines)


def _state_float(state, key):
    try:
        value = state.get(key)
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _starting_bankroll(db_trades, state):
    for key in ("paper_starting_bankroll", "starting_bankroll", "initial_bankroll"):
        value = _state_float(state, key)
        if value is not None and value > 0:
            return value

    first_trade = None
    for trade in db_trades:
        before = trade.get("notional_bankroll_before")
        if before is None:
            continue
        try:
            before_value = float(before)
        except (TypeError, ValueError):
            continue
        if before_value <= 0:
            continue
        dt = _parse_ts(trade.get("ts"))
        sort_key = dt or datetime.max.replace(tzinfo=timezone.utc)
        if first_trade is None or sort_key < first_trade[0]:
            first_trade = (sort_key, before_value)
    if first_trade is not None:
        return first_trade[1]

    env_value = os.getenv("BANKROLL")
    if env_value:
        try:
            parsed = float(env_value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return 500.0


def section_per_series_win_rate():
    """
    7b. Per-series win rate (resolved trades grouped by series_ticker).
    Requires series_ticker column added in v0.22.0.
    """
    if not DB_PATH.exists():
        return "No database found."

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if not _has_columns(conn, "paper_trades", ("series_ticker",)):
            return "series_ticker column not found -- run bot once with v0.22.0+ to migrate schema."
        boundary = _p0_cohort_boundary(conn)
        if boundary is None:
            return _p0_boundary_missing_message("Win rate by series")
        cohort_rows = []
        for label, ts_filter, params in _p0_cohort_specs(boundary):
            rows = conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(series_ticker, ''), '(unknown)') AS series,
                    count(*) AS total,
                    sum(CASE WHEN pnl_dollars > 0 THEN 1 ELSE 0 END) AS wins,
                    sum(pnl_dollars) AS net_pnl
                FROM paper_trades
                WHERE resolved = 1
                  AND pnl_dollars IS NOT NULL
                  AND """
                + ts_filter
                + """
                GROUP BY series
                HAVING count(*) >= 2
                ORDER BY total DESC
                """,
                params,
            ).fetchall()
            cohort_rows.append((label, rows))
    except sqlite3.OperationalError:
        return "series_ticker column not found -- run bot once with v0.22.0+ to migrate schema."
    finally:
        conn.close()

    if not any(rows for _label, rows in cohort_rows):
        return "No resolved trades with series_ticker yet."

    lines = [
        "Win rate by series (resolved, >= 2 trades; P0 cohorts):",
        "",
        "P0 cohort boundary: %s" % boundary,
    ]
    for label, rows in cohort_rows:
        lines.append("")
        lines.append("%s cohort:" % label)
        if not rows:
            lines.append("  No resolved trades with series_ticker in this cohort.")
            continue
        table_rows = []
        for r in rows:
            n = r["total"]
            wins = r["wins"]
            wr = wins / n if n else 0.0
            table_rows.append(
                [
                    r["series"],
                    n,
                    wins,
                    "%.1f%%" % (wr * 100),
                    "$%+.2f" % (r["net_pnl"] or 0),
                ]
            )
        lines.append(
            tabulate(
                table_rows,
                headers=["Series", "Trades", "Wins", "Win Rate", "Net P&L"],
                tablefmt="simple",
            )
        )
    return "\n".join(lines)


def section_keyword_accuracy():
    """
    7c. Keyword accuracy table -- top keywords by sample count with accuracy and multiplier.
    Uses keyword_outcomes table. Requires >= 10 resolved trades per (keyword, series_ticker).
    """
    if not DB_PATH.exists():
        return "No database found."

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT keyword, series_ticker, count(*) AS n,
                   sum(correct) AS wins,
                   round(1.0 * sum(correct) / count(*), 3) AS accuracy
            FROM keyword_outcomes
            GROUP BY keyword, series_ticker
            HAVING count(*) >= 10
            ORDER BY count(*) DESC
            LIMIT 30
            """
        ).fetchall()
        # Also pull aggregate per keyword (all series) for context
        agg_rows = conn.execute(
            """
            SELECT keyword, count(*) AS n, sum(correct) AS wins,
                   round(1.0 * sum(correct) / count(*), 3) AS accuracy
            FROM keyword_outcomes
            GROUP BY keyword
            ORDER BY count(*) DESC
            LIMIT 30
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return "keyword_outcomes table not found -- bot has not run with v0.19.0+ yet."
    finally:
        conn.close()

    lines = []

    if not rows and not agg_rows:
        return "No keyword outcome data yet (need >= 10 resolved trades with keyword matches)."

    # Per-(keyword, series) with >= 10 samples
    if rows:
        table_rows = []
        for r in rows:
            acc = r["accuracy"] or 0.0
            mult = max(0.5, min(1.5, 0.5 + acc))
            table_rows.append(
                [
                    r["keyword"],
                    r["series_ticker"] or "(all)",
                    r["n"],
                    r["wins"],
                    "%.1f%%" % (acc * 100),
                    "%.2fx" % mult,
                ]
            )
        lines.append(
            "Per-(keyword, series_ticker) accuracy (>= 10 samples, top 30 by count):"
        )
        lines.append("")
        lines.append(
            tabulate(
                table_rows,
                headers=["Keyword", "Series", "n", "Wins", "Accuracy", "Multiplier"],
                tablefmt="simple",
            )
        )
        lines.append("")

    # Aggregate per keyword (all series combined) for overview
    if agg_rows:
        agg_table = []
        for r in agg_rows:
            acc = r["accuracy"] or 0.0
            mult = max(0.5, min(1.5, 0.5 + acc))
            agg_table.append(
                [
                    r["keyword"],
                    r["n"],
                    r["wins"],
                    "%.1f%%" % (acc * 100),
                    "%.2fx" % mult,
                ]
            )
        lines.append("Aggregate per keyword (all series, top 30 by count):")
        lines.append("")
        lines.append(
            tabulate(
                agg_table,
                headers=["Keyword", "n", "Wins", "Accuracy", "Multiplier"],
                tablefmt="simple",
            )
        )

    return "\n".join(lines) if lines else "No keyword outcome data yet."


def section_match_score_calibration():
    """
    7d. Match score calibration -- win rate by match_score band.
    Uses match_score column added in v0.22.0.
    Advisory: suggests whether PAPER_MIN_MATCH_SCORE threshold should be adjusted.
    """
    if not DB_PATH.exists():
        return "No database found."

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if not _has_columns(conn, "paper_trades", ("match_score",)):
            return "match_score column not found -- run bot once with v0.22.0+ to migrate schema."
        boundary = _p0_cohort_boundary(conn)
        if boundary is None:
            return _p0_boundary_missing_message("Win rate by match_score band")
        cohort_rows = []
        for label, ts_filter, params in _p0_cohort_specs(boundary):
            rows = conn.execute(
                """
                SELECT match_score, pnl_dollars
                FROM paper_trades
                WHERE resolved = 1
                  AND pnl_dollars IS NOT NULL
                  AND match_score IS NOT NULL
                  AND """
                + ts_filter,
                params,
            ).fetchall()
            cohort_rows.append((label, rows))
    except sqlite3.OperationalError:
        return "match_score column not found -- run bot once with v0.22.0+ to migrate schema."
    finally:
        conn.close()

    if not any(rows for _label, rows in cohort_rows):
        return "No resolved trades with match_score yet (column added in v0.22.0)."

    # Bin into bands
    bands = [0.0, 0.10, 0.20, 0.30, 0.50, 1.01]
    band_labels = ["0.00-0.10", "0.10-0.20", "0.20-0.30", "0.30-0.50", "0.50+"]
    lines = [
        "Win rate by match_score band (P0 cohorts):",
        "",
        "P0 cohort boundary: %s" % boundary,
    ]
    any_table_rows = False

    # Advisory
    current_threshold = float(os.environ.get("PAPER_MIN_MATCH_SCORE", "0.06"))
    for cohort_label, rows in cohort_rows:
        lines.append("")
        lines.append("%s cohort:" % cohort_label)
        if not rows:
            lines.append("  No resolved trades with match_score in this cohort.")
            continue

        band_data: dict[str, list[float]] = {l: [] for l in band_labels}
        for r in rows:
            score = r["match_score"] or 0.0
            pnl = r["pnl_dollars"]
            for i in range(len(bands) - 1):
                if bands[i] <= score < bands[i + 1]:
                    band_data[band_labels[i]].append(pnl)
                    break

        table_rows = []
        best_band = None
        best_wr = 0.0
        for band_label in band_labels:
            pnls = band_data[band_label]
            if not pnls:
                continue
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / len(pnls)
            net = sum(pnls)
            table_rows.append(
                [band_label, len(pnls), wins, "%.1f%%" % (wr * 100), "$%+.2f" % net]
            )
            if wr > best_wr:
                best_wr = wr
                best_band = band_label

        if not table_rows:
            lines.append("  No resolved trades with match_score in range.")
            continue

        any_table_rows = True
        lines.append(
            tabulate(
                table_rows,
                headers=["Score Band", "Trades", "Wins", "Win Rate", "Net P&L"],
                tablefmt="simple",
            )
        )
        lines.append("")
        if best_band:
            low_bound = float(best_band.split("-")[0])
            lines.append(
                "  Advisory: highest win rate band is %s (%.1f%%). "
                "Current threshold: %.2f."
                % (best_band, best_wr * 100, current_threshold)
            )
            if low_bound > current_threshold + 0.01:
                lines.append(
                    "  Raising PAPER_MIN_MATCH_SCORE to %.2f may improve signal quality "
                    "-- verify with >= 30 samples before changing." % low_bound
                )
            else:
                lines.append("  Current threshold appears reasonable given available data.")

    if not any_table_rows:
        return "No resolved trades with match_score in range."

    return "\n".join(lines)


def _render_kelly_shadow_rows(rows):
    flat_pnl   = 0.0
    kelly_pnl  = 0.0
    flat_cost  = 0.0
    kelly_cost = 0.0

    for r in rows:
        k_contracts = r["kelly_contracts"] or 0
        k_cost      = k_contracts * r["price_cents"] / 100.0
        # Side-aware payout: a YES contract pays $1 when the market resolves
        # YES; a NO contract pays $1 when it resolves NO. The prior code assumed
        # every position was YES, so a winning NO bet (e.g. NO bought at 92c that
        # resolved NO) was booked as a total loss, corrupting the Kelly delta
        # (PROFIT-REPORT-002). For unknown/blank side we keep the YES assumption
        # (prior behaviour) rather than guess.
        side = str(r["side"] or "").strip().lower()
        if side == "no":
            won = not r["resolved_yes"]
        else:  # "yes" or unknown -> prior behaviour
            won = bool(r["resolved_yes"])
        k_payout    = float(k_contracts) if won else 0.0
        k_pnl       = k_payout - k_cost

        flat_pnl  += r["pnl_dollars"]
        kelly_pnl += k_pnl
        flat_cost += r["cost_dollars"]
        kelly_cost += k_cost

    n = len(rows)
    flat_roi  = (flat_pnl  / flat_cost  * 100) if flat_cost  > 0 else 0.0
    kelly_roi = (kelly_pnl / kelly_cost * 100) if kelly_cost > 0 else 0.0

    lines = [
        "Flat-5 (actual) vs Kelly shadow sizing (%d resolved trades):" % n,
        "",
        "  %-22s  %8s  %8s  %6s" % ("Metric", "Flat-5", "Kelly", "Delta"),
        "  %-22s  %8s  %8s  %6s" % ("-" * 22, "-" * 8, "-" * 8, "-" * 6),
        "  %-22s  %+8.2f  %+8.2f  %+6.2f" % (
            "Net P&L ($)", flat_pnl, kelly_pnl, kelly_pnl - flat_pnl),
        "  %-22s  %8.2f  %8.2f  %+6.2f" % (
            "Capital deployed ($)", flat_cost, kelly_cost, kelly_cost - flat_cost),
        "  %-22s  %7.1f%%  %7.1f%%  %+5.1f%%" % (
            "ROI on deployed (%)", flat_roi, kelly_roi, kelly_roi - flat_roi),
        "",
        "  NOTE: Kelly shadow is retrospective -- it uses the same capped_dollars",
        "  that the bot computed at trade time, converted to contracts at that price.",
        "  A positive Delta means Kelly would have outperformed flat-5 on these trades.",
    ]
    return "\n".join(lines)


def section_kelly_shadow():
    """Compare flat-5 actual P&L vs what Kelly shadow sizing would have returned."""
    if not DB_PATH.exists():
        return "No database found."

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if not _has_columns(conn, "paper_trades", ("kelly_contracts",)):
            return "kelly_contracts column not found -- run bot once with v0.23.0+ to migrate schema."
        boundary = _p0_cohort_boundary(conn)
        if boundary is None:
            return _p0_boundary_missing_message("Flat-5 vs Kelly shadow sizing")
        cohort_rows = []
        for label, ts_filter, params in _p0_cohort_specs(boundary):
            rows = conn.execute(
                """SELECT contracts, kelly_contracts, price_cents, pnl_dollars,
                          cost_dollars, resolved_yes, side
                   FROM paper_trades
                   WHERE resolved = 1
                     AND kelly_contracts IS NOT NULL
                     AND pnl_dollars IS NOT NULL
                     AND """
                + ts_filter,
                params,
            ).fetchall()
            cohort_rows.append((label, rows))
    except sqlite3.OperationalError:
        return "kelly_contracts column not found -- run bot once with v0.23.0+ to migrate schema."
    finally:
        conn.close()

    if not any(rows for _label, rows in cohort_rows):
        return "No resolved trades with kelly_contracts yet (column added in v0.23.0)."

    lines = [
        "Flat-5 (actual) vs Kelly shadow sizing (P0 cohorts):",
        "",
        "P0 cohort boundary: %s" % boundary,
    ]
    for label, rows in cohort_rows:
        lines.append("")
        lines.append("%s cohort:" % label)
        if not rows:
            lines.append("  No resolved trades with kelly_contracts in this cohort.")
            continue
        lines.append(_render_kelly_shadow_rows(rows))

    return "\n".join(lines)


def section_edge_calibration(db_trades):
    resolved = [
        t for t in db_trades if t.get("resolved") and t.get("resolved_yes") is not None
    ]
    if not resolved:
        return "No resolved trades for calibration."

    # Buckets span the full 0-1 range so NO-side trades (estimated_prob < 0.50)
    # are included.  Below 0.50 = bot took NO; above 0.50 = bot took YES.
    # "YES Resolved Rate" is the raw calibration metric regardless of side taken.
    # Calib Error = avg_est - YES_resolved_rate (+ = overestimates YES probability).
    buckets = [
        ("0.20-0.30", 0.20, 0.30),
        ("0.30-0.40", 0.30, 0.40),
        ("0.40-0.50", 0.40, 0.50),
        ("0.50-0.60", 0.50, 0.60),
        ("0.60-0.70", 0.60, 0.70),
        ("0.70-0.80", 0.70, 0.80),
        ("0.80+",     0.80, 1.01),
    ]

    lines = ["Edge calibration (estimated_prob vs actual outcome):", ""]
    lines.append(
        "  NOTE: Only %d resolved trades -- results are directional, not statistically robust."
        % len(resolved)
    )
    lines.append(
        "  Calib Error: positive = model overestimates YES probability; "
        "negative = underestimates."
    )
    lines.append("")

    rows = []
    for label, lo, hi in buckets:
        bucket_trades = [
            t for t in resolved if lo <= (t.get("estimated_prob") or 0) < hi
        ]
        if not bucket_trades:
            continue
        yes_resolved = sum(1 for t in bucket_trades if t.get("resolved_yes"))
        avg_est = sum(t.get("estimated_prob") or 0 for t in bucket_trades) / len(
            bucket_trades
        )
        yes_rate = yes_resolved / len(bucket_trades)
        calib_err = avg_est - yes_rate
        rows.append(
            [
                label,
                len(bucket_trades),
                "%.2f" % avg_est,
                "%.0f%%" % (yes_rate * 100),
                "%+.2f" % calib_err,
            ]
        )

    if rows:
        lines.append(
            tabulate(
                rows,
                headers=[
                    "Est.P Bucket",
                    "N",
                    "Avg Est.P",
                    "YES Resolved Rate",
                    "Calib Error",
                ],
                tablefmt="simple",
            )
        )
    else:
        lines.append("  No resolved trades match the buckets in this window.")

    return "\n".join(lines)


def section_golive_readiness(db_trades, state, mtm=None):
    resolved_all = [t for t in db_trades if t.get("resolved")]

    try:
        bankroll_now = float(state.get("notional_bankroll", 0))
    except (TypeError, ValueError):
        bankroll_now = 0.0

    # PROFIT-DRAWDOWN-001c: the notional bankroll deducts each open trade's
    # FULL entry cost at placement, so with many open positions the final
    # equity point understates true equity and the drawdown criterion fails on
    # a phantom number (2026-06-12: notional said 45.7%, true MTM equity said
    # 18.9%). When a mark-to-market snapshot is supplied (mtm dict from
    # scripts.mark_open_positions.compute_open_position_marks), the CURRENT
    # equity point becomes bankroll + marked value of open positions. Unpriced
    # positions count at $0 (fail-closed: unpriced == worthless). Marks are
    # conservative per venue: Kalshi held-side mid (bounded last fallback only
    # inside (0,100)); Polymarket min(held ask, 100 - opposite ask) -- residual
    # optimism is bounded by half the Kalshi spread. Historical
    # curve points remain entry-sampled notional snapshots — a past trough is
    # never erased. When mtm is None (offline / fetch failure), fall back to
    # the notional point and say so: notional overstates drawdown, which is the
    # conservative failure direction for a go-live gate.
    mtm_marked = None
    if isinstance(mtm, dict):
        try:
            mtm_marked = float(mtm.get("marked_value", 0) or 0)
        except (TypeError, ValueError):
            mtm_marked = None
        # NaN is truthy and float()-clean but would silently drop the final
        # equity point downstream (_bankroll_equity_points skips non-positive,
        # and NaN comparisons are all False) — gate would then see history
        # only. Reject non-finite values outright (review finding).
        if mtm_marked is not None and not math.isfinite(mtm_marked):
            mtm_marked = None
    equity_now = bankroll_now + mtm_marked if mtm_marked is not None else bankroll_now

    # Read config thresholds from env / defaults
    min_resolved = int(os.getenv("GO_LIVE_MIN_RESOLVED", "20"))
    min_win_rate = float(os.getenv("GO_LIVE_MIN_WIN_RATE", "0.52"))
    max_drawdown = float(os.getenv("GO_LIVE_MAX_DRAWDOWN_PCT", "0.20"))
    bankroll_start = _starting_bankroll(db_trades, state)

    def _cohort_stats(rows):
        wins = [t for t in rows if (t.get("pnl_dollars") or 0) > 0]
        wr = (len(wins) / len(rows)) if rows else 0.0
        pts = _bankroll_equity_points(rows, equity_now)
        ptt = _equity_max_drawdown(pts)
        # A 0% peak-to-trough from a <2-point curve is "bankroll history could
        # not be reconstructed", NOT "no drawdown". Flag it so the gate fails
        # closed instead of silently passing a cohort with missing equity data
        # (false-pass on a go-live safety gate).
        dd_measurable = len(pts) >= 2
        return len(rows), wr, ptt, dd_measurable

    # PROFIT-REPORT-001a (operator decision 2026-06-11): GATE ON POST-P0.
    # The pre-P0 cohort ran under the pre-fix Kalshi pricing bug and is excluded
    # everywhere else (7b/7d/7e) as non-representative — gating go-live on it was
    # leveraging known-broken functionality. The authoritative verdict now uses
    # the post-P0 (current-regime) cohort; lifetime is informational only. If the
    # P0 boundary sentinel is missing we fall back to lifetime (conservative) so
    # the gate always produces a verdict.
    boundary = _parse_p0_boundary(state.get(P0_PRICE_FIX_SENTINEL_KEY))
    if boundary is not None:
        gating = [
            t for t in resolved_all
            if (_parse_ts(t.get("ts")) is not None and _parse_ts(t.get("ts")) >= boundary)
        ]
        gate_basis = "POST-P0"
    else:
        gating = resolved_all
        gate_basis = "LIFETIME (post-P0 boundary missing -- fallback)"

    g_n, g_wr, g_ptt, g_dd_measurable = _cohort_stats(gating)
    life_n, life_wr, life_ptt, _ = _cohort_stats(resolved_all)
    life_dd_startnow = (
        (bankroll_start - bankroll_now) / bankroll_start if bankroll_start > 0 else 0.0
    )
    dd_ok = g_dd_measurable and g_ptt <= max_drawdown

    def gate(val, threshold, mode):
        ok = val >= threshold if mode == "min" else val <= threshold
        return "PASS" if ok else "FAIL"

    lines = ["Go-live readiness:", ""]
    lines.append("  Cohort basis    : %s (pre-P0 excluded -- ran under the" % gate_basis)
    lines.append("                    pre-fix pricing bug; matches 7b/7d/7e).")
    lines.append("")
    lines.append(
        "  Resolved trades : %d / %d required  [%s]"
        % (g_n, min_resolved, gate(g_n, min_resolved, "min"))
    )
    lines.append(
        "  Win rate        : %.0f%% / %.0f%% required  [%s]"
        % (g_wr * 100, min_win_rate * 100, gate(g_wr, min_win_rate, "min"))
    )
    if g_dd_measurable:
        lines.append(
            "  Drawdown        : %.1f%% / %.0f%% max  [%s]  (peak-to-trough)"
            % (g_ptt * 100, max_drawdown * 100, "PASS" if dd_ok else "FAIL")
        )
    else:
        lines.append(
            "  Drawdown        : n/a / %.0f%% max  [FAIL]  (peak-to-trough -- too"
            " few bankroll samples to measure; failing closed)"
            % (max_drawdown * 100)
        )
    lines.append(
        "  Notional bankroll : $%.2f (started $%.2f)" % (bankroll_now, bankroll_start)
    )
    if mtm_marked is not None:
        unpriced = 0
        if isinstance(mtm, dict):
            unpriced = int(mtm.get("unpriced_count") or 0)
        suffix = (
            "  (%d unpriced positions counted at $0)" % unpriced if unpriced else ""
        )
        lines.append(
            "  MTM equity        : $%.2f = notional + $%.2f open-position value;"
            " drawdown gates on this%s" % (equity_now, mtm_marked, suffix)
        )
    else:
        lines.append(
            "  MTM equity        : unavailable -- drawdown gates on notional,"
            " which counts open positions as worthless (overstates drawdown;"
            " conservative)"
        )
    lines.append("")
    lines.append(
        "  Lifetime view (incl. frozen pre-P0; INFORMATIONAL, not gating):"
    )
    lines.append(
        "    Resolved %d | Win rate %.0f%% | start-vs-now %.1f%% | peak-to-trough %.1f%%"
        % (life_n, life_wr * 100, life_dd_startnow * 100, life_ptt * 100)
    )
    lines.append("")

    all_pass = g_n >= min_resolved and g_wr >= min_win_rate and dd_ok
    if all_pass:
        lines.append("  OVERALL: READY FOR LIVE TRADING (%s cohort)" % gate_basis)
    else:
        missing = []
        if g_n < min_resolved:
            missing.append("%d more resolved trades needed" % (min_resolved - g_n))
        if g_wr < min_win_rate:
            missing.append(
                "win rate needs %.0f%% (currently %.0f%%)"
                % (min_win_rate * 100, g_wr * 100)
            )
        if not dd_ok:
            if not g_dd_measurable:
                missing.append(
                    "drawdown not measurable (too few bankroll samples)"
                )
            else:
                missing.append(
                    "drawdown %.1f%% exceeds %.0f%% cap"
                    % (g_ptt * 100, max_drawdown * 100)
                )
        lines.append("  OVERALL: NOT READY -- " + "; ".join(missing))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="kalshi-bot performance analysis")
    parser.add_argument(
        "--since",
        default=None,
        help="Start of window (YYYY-MM-DD). Default: 30 days ago.",
    )
    parser.add_argument(
        "--until", default=None, help="End of window (YYYY-MM-DD). Default: today."
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Fetch market resolutions from Kalshi API for unresolved tickers.",
    )
    parser.add_argument(
        "--no-mtm",
        action="store_true",
        help=(
            "Skip the open-position mark-to-market fetch; section 8's drawdown"
            " then gates on the notional bankroll (overstates drawdown when"
            " open positions retain value)."
        ),
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)

    if args.since:
        since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        since_dt = now - timedelta(days=30)

    if args.until:
        until_dt = datetime.fromisoformat(args.until).replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    else:
        until_dt = now

    # Load data
    entries = load_jsonl(since_dt, until_dt)
    db_trades_all = load_db_trades()
    db_trades = load_db_trades(since_dt, until_dt)
    state = load_db_state()
    resolution_map = build_resolution_map(db_trades_all, entries, args.enrich)

    # Build report
    report_lines = []

    # Header
    report_lines.append("=" * 70)
    report_lines.append("  KALSHI-BOT PERFORMANCE ANALYSIS")
    report_lines.append("  Generated : %s" % now.strftime("%Y-%m-%d %H:%M UTC"))
    report_lines.append(
        "  Window    : %s -> %s"
        % (since_dt.strftime("%Y-%m-%d"), until_dt.strftime("%Y-%m-%d"))
    )
    report_lines.append("  JSONL     : %d entries in window" % len(entries))
    if len(db_trades_all) == len(db_trades):
        report_lines.append("  DB trades : %d in window" % len(db_trades))
    else:
        report_lines.append(
            "  DB trades : %d in window (%d lifetime)"
            % (len(db_trades), len(db_trades_all))
        )
    report_lines.append("=" * 70)

    # Caveats
    caveats = []
    if since_dt < datetime(2026, 3, 15, tzinfo=timezone.utc):
        caveats.append(
            "Window includes pre-DB-wipe data (before 2026-03-15). DB has 7 trades only."
        )
    if caveats:
        report_lines.append("")
        report_lines.append("CAVEATS:")
        for c in caveats:
            report_lines.append("  * " + c)

    report_lines.append(section_header("1. SIGNAL PIPELINE FUNNEL"))
    report_lines.append(section_pipeline_funnel(entries, db_trades))

    report_lines.append(section_header("2. PLACED TRADES PERFORMANCE"))
    report_lines.append(section_placed_performance(entries, db_trades, resolution_map))

    report_lines.append(section_header("3. SKIP REASON BREAKDOWN"))
    report_lines.append(section_skip_breakdown(entries))

    report_lines.append(section_header("4. MISSED OPPORTUNITIES"))
    report_lines.append(section_missed_opportunities(entries, resolution_map))

    report_lines.append(section_header("5. PER-SOURCE PERFORMANCE"))
    report_lines.append(section_source_performance(entries, db_trades))

    report_lines.append(section_header("5b. SINGLE vs MULTI-SOURCE PERFORMANCE"))
    report_lines.append(section_single_vs_multi_source(entries, db_trades))

    report_lines.append(section_header("6. SOURCE QUALITY FUNNEL"))
    report_lines.append(section_source_quality())

    report_lines.append(section_header("6b. CANDIDATE SUBREDDITS"))
    report_lines.append(section_candidate_subreddits())

    report_lines.append(section_header("7. EDGE CALIBRATION"))
    report_lines.append(section_edge_calibration(db_trades))

    report_lines.append(section_header("7b. PER-SERIES WIN RATE"))
    report_lines.append(section_per_series_win_rate())

    report_lines.append(section_header("7c. KEYWORD ACCURACY"))
    report_lines.append(section_keyword_accuracy())

    report_lines.append(section_header("7d. MATCH SCORE CALIBRATION"))
    report_lines.append(section_match_score_calibration())

    report_lines.append(section_header("7e. KELLY SHADOW SIZING"))
    report_lines.append(section_kelly_shadow())

    # PROFIT-DRAWDOWN-001c: mark open positions to market so section 8's
    # drawdown criterion reflects true paper equity, not the notional bankroll
    # (which counts every open position as worthless). Fail-soft: any failure
    # (offline, API error, missing creds) falls back to the notional point,
    # which OVERSTATES drawdown -- the conservative direction for this gate.
    # Default ON despite the --enrich opt-in convention because the gate's
    # correctness depends on it and the fetch is bounded by open-position
    # count; --no-mtm opts out.
    mtm = None
    if not args.no_mtm:
        try:
            from scripts.mark_open_positions import compute_open_position_marks

            mtm = compute_open_position_marks()
        except SystemExit as exc:
            # config.BotConfig.__post_init__ sys.exit(1)s on missing/invalid
            # creds, and the lazy KalshiRestClient import inside the pricing
            # loop imports config. SystemExit is BaseException -- without this
            # clause it would escape and kill the whole daily report instead
            # of falling back to the notional drawdown (review finding).
            print("WARNING: open-position mark-to-market failed: SystemExit(%s)" % exc)
            mtm = None
        except Exception as exc:
            print("WARNING: open-position mark-to-market failed: %s" % exc)
            mtm = None

    report_lines.append(section_header("8. GO-LIVE READINESS"))
    report_lines.append(section_golive_readiness(db_trades_all, state, mtm=mtm))

    report_lines.append("")
    report_lines.append("=" * 70)
    report_lines.append("  END OF REPORT")
    report_lines.append("=" * 70)

    report_text = "\n".join(report_lines)

    # Write output file
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_name = "analysis_%s.txt" % now.strftime("%Y%m%d_%H%M")
    out_path = REPORTS_DIR / out_name
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(report_text)
    print("\nReport saved to: %s" % out_path)


if __name__ == "__main__":
    main()
