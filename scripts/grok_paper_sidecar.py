#!/usr/bin/env python3
"""Paper-only Grok sidecar. GET only. The Polymarket order endpoint stays blocked."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

LOCK_NAME = "bot_runtime.kalshi-grok-paper.lock"
COHORT_ID = "kalshi-event-news-20260820"


def refuse_live_trading() -> None:
    """Stop when this process was started with live trading turned on."""
    live = str(os.environ.get("LIVE_TRADING_ENABLED") or "").strip().lower()
    if live == "true":
        raise SystemExit(2)


def arm_sidecar_environment() -> None:
    """Force paper-only flags before config or the Kalshi client is imported.

    An explicit cap, including the $0.50 dry-run, is left in place. The
    installed start script sets the cap to 3 before this process starts.
    """
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    os.environ["KALSHI_GROK_PAPER_SIDECAR"] = "true"
    os.environ["PAPER_COHORT_ID"] = COHORT_ID
    os.environ["EVENT_NEWS_GROK_EDGE_SCAN"] = "true"
    os.environ["EVENT_NEWS_GROK_EXIT_WATCH"] = "true"
    os.environ["EVENT_NEWS_GROK_GROUNDING"] = "true"
    os.environ.setdefault("GROK_DAILY_USD_CAP", "3")
    os.environ["POLYMARKET_US_ENABLED"] = "false"
    os.environ["POLYMARKET_US_LIVE_TRADING_ENABLED"] = "false"
    print(
        "[POLYMARKET_PAPER] active venue=polymarket_us order_endpoint=blocked",
        file=sys.stderr,
    )


def _acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print("grok paper lock held", file=sys.stderr)
        raise SystemExit(0)
    return handle


def _spent(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("spent_usd")
    try:
        number = Decimal(str(raw))
    except Exception:
        return None
    if not number.is_finite() or number < 0:
        return None
    return format(number, "f")


def _position_series(positions) -> tuple[str, ...]:
    """Series prefixes of open positions. Names outside the yaml set are dropped later."""
    from utils.event_news_grok_edge import paper_position_rows

    found: list[str] = []
    for row in paper_position_rows(positions):
        ticker = str(row.get("ticker") or "").strip().upper()
        series = ticker.split("-", 1)[0] if ticker else ""
        if series and series not in found:
            found.append(series)
    return tuple(found)


def _polymarket_entries(series_list: tuple[str, ...]):
    from utils.series_universe import get_universe

    universe = get_universe()
    if universe.failed:
        return []
    wanted = set(series_list)
    return [
        entry
        for entry in universe.entries
        if entry.series in wanted and entry.venue == "polymarket_us"
    ]


def _run_scan(
    rest,
    positions=None,
    *,
    now: datetime | None = None,
    polymarket_client=None,
    cross_venue_path: Path | None = None,
) -> dict[str, int]:
    """Fetch politics series only, rank them, and examine the top of that list.

    ``fetched`` is the Kalshi page length. Polymarket names are not sent to
    Kalshi. ``politics`` is how many Kalshi markets are open, close within
    30 days, and have an ask from 36c to 94c. KXDJI and the research prewarm
    fallback are not requested. A Polymarket read error does not drop the
    Kalshi scan. The order endpoint stays blocked.
    """
    from utils.event_news_grok_edge import (
        PAPER_CLOSE_WINDOW,
        PAPER_DB_PATH,
        SCAN_TOP_N,
        paper_position_rows,
        politics_scan_series,
        rank_politics_markets,
        run_paper_edge_scan,
        write_cross_venue,
    )

    moment = now or datetime.now(timezone.utc)
    series_list = politics_scan_series(_position_series(positions))
    entries = _polymarket_entries(series_list)
    poly_names = {entry.series for entry in entries}
    markets: list = []
    for series in series_list:
        if series in poly_names:
            continue
        page, _cursor = rest.get_markets(status="open", series_ticker=series, limit=20)
        markets.extend(page or [])
    kalshi_allow = tuple(series for series in series_list if series not in poly_names)
    ranked = rank_politics_markets(
        markets, now=moment, series=kalshi_allow, close_window=PAPER_CLOSE_WINDOW
    )
    open_tickers = tuple(row["ticker"] for row in paper_position_rows(positions))
    counts = run_paper_edge_scan(
        ranked,
        live=False,
        allowlist=kalshi_allow,
        db_path=PAPER_DB_PATH,
        max_markets=SCAN_TOP_N,
        now=moment,
        open_tickers=open_tickers,
    )
    counts["fetched"] = len(markets)
    counts["politics"] = len(ranked)
    report = polymarket_client is not None or os.environ.get("KALSHI_GROK_PAPER_SIDECAR") == "true"
    client = polymarket_client
    if client is None and report:
        try:
            from polymarket.public_client import PolymarketPublicClient

            client = PolymarketPublicClient()
        except Exception as exc:
            print(
                f"grok paper polymarket client failed error_type={type(exc).__name__}",
                file=sys.stderr,
            )
            client = None
    poly_views: list = []
    if client is not None and entries:
        from polymarket.paper_views import load_polymarket_paper_markets
        from polymarket.read_only import PolymarketOrderBlocked

        try:
            poly_views = load_polymarket_paper_markets(client, entries)
        except PolymarketOrderBlocked:
            raise
        except Exception as exc:
            print(
                f"grok paper polymarket fetch failed error_type={type(exc).__name__}",
                file=sys.stderr,
            )
            poly_views = []
        poly_ranked = rank_politics_markets(
            poly_views,
            now=moment,
            series=tuple(entry.series for entry in entries),
            close_window=PAPER_CLOSE_WINDOW,
        )
        if poly_ranked:
            poly_counts = run_paper_edge_scan(
                poly_ranked,
                live=False,
                allowlist=tuple(entry.series for entry in entries),
                db_path=PAPER_DB_PATH,
                max_markets=SCAN_TOP_N,
                now=moment,
                open_tickers=open_tickers,
            )
            for key in (
                "examined",
                "queued",
                "skipped",
                "screen_skipped",
                "cache_hits",
                "grounded_calls",
            ):
                counts[key] = counts.get(key, 0) + poly_counts.get(key, 0)
    if report:
        write_cross_venue([*markets, *poly_views], path=cross_venue_path, now=moment)
    return counts


def _run_scores(rest, positions=None) -> dict[str, int]:
    from utils.event_news_grok_edge import PAPER_DB_PATH, score_open_positions

    if positions is None:
        positions = rest.get_open_positions(strict=True)
    return score_open_positions(
        positions,
        live=False,
        market_lookup=rest.get_market,
        db_path=PAPER_DB_PATH,
    )


def run_score_then_scan(
    rest,
    *,
    now: datetime | None = None,
    polymarket_client=None,
    cross_venue_path: Path | None = None,
) -> tuple[dict[str, int], dict[str, int], BaseException | None]:
    """Score open positions, then scan the ranked politics book.

    A score or positions failure still scans. This does not arm the process
    and it does not import the executor. Callers print the score error.
    """
    positions: list = []
    scores = {"scored": 0, "skipped": 0}
    score_error: BaseException | None = None
    try:
        positions = list(rest.get_open_positions(strict=True) or [])
        scores = _run_scores(rest, positions)
    except Exception as exc:
        score_error = exc
        scores = {"scored": 0, "skipped": 0}
    scan = _run_scan(
        rest,
        positions,
        now=now,
        polymarket_client=polymarket_client,
        cross_venue_path=cross_venue_path,
    )
    return scores, scan, score_error


def _run_exit(rest) -> int:
    from utils.event_news_grok_edge import paper_position_rows, run_paper_exit_watch

    positions = rest.get_open_positions(strict=True)
    return run_paper_exit_watch(
        paper_position_rows(positions),
        live=False,
        market_lookup=rest.get_market,
    )


def _polymarket_settlement_lookup():
    """Official settlement GET. Construction failure fails closed with no HTTP."""
    try:
        from polymarket.public_client import PolymarketPublicClient

        client = PolymarketPublicClient()
    except Exception as exc:
        print(
            f"grok paper polymarket settlement client failed error_type={type(exc).__name__}",
            file=sys.stderr,
        )
        return None
    return client.get_market_settlement


def _run_daily(rest) -> None:
    from utils.event_news_grok_edge import PAPER_DB_PATH, write_daily_edge_report, write_paper_scorecard

    lookup = _polymarket_settlement_lookup()
    write_daily_edge_report(market_lookup=rest.get_market, polymarket_lookup=lookup)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    write_paper_scorecard(
        db_path=PAPER_DB_PATH,
        market_lookup=rest.get_market,
        report_path=Path(f"logs/reports/paper-scorecard-{day}.md"),
        polymarket_lookup=lookup,
    )


def _print_once(scan: dict[str, int], scores: dict[str, int], before: str | None, after: str | None) -> None:
    from utils.event_news_grok_edge import CALL_LOG_PATH

    payload = {
        "fetched": scan.get("fetched", 0),
        "politics": scan.get("politics", 0),
        "examined": scan.get("examined", 0),
        "queued": scan.get("queued", 0),
        "scan_skipped": scan.get("skipped", 0),
        "screen_skipped": scan.get("screen_skipped", 0),
        "cache_hits": scan.get("cache_hits", 0) + scores.get("cache_hits", 0),
        "grounded_calls": scan.get("grounded_calls", 0) + scores.get("grounded_calls", 0),
        "positions_scored": scores.get("scored", 0),
        "positions_not_scored": scores.get("skipped", 0),
        "spend_before_usd": before,
        "spend_after_usd": after,
        "call_log": str(CALL_LOG_PATH),
    }
    print(json.dumps(payload, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    refuse_live_trading()
    arm_sidecar_environment()
    args = list(sys.argv[1:] if argv is None else argv)
    once = "--once" in args
    from config import DATA_DIR
    from kalshi.rest_client import KalshiRestClient
    from utils.event_news_grok_edge import SPEND_PATH, edge_scan_interval_seconds

    lock = _acquire_lock(Path(DATA_DIR) / LOCK_NAME)
    try:
        rest = KalshiRestClient()
        if once:
            before = _spent(SPEND_PATH)
            scores, scan, score_error = run_score_then_scan(rest)
            if score_error is not None:
                print(
                    f"grok paper score failed error_type={type(score_error).__name__}",
                    file=sys.stderr,
                )
            _print_once(scan, scores, before, _spent(SPEND_PATH))
            return 0
        next_exit = time.monotonic() + 60
        report_day = ""
        while True:
            try:
                _scores, _scan, score_error = run_score_then_scan(rest)
                if score_error is not None:
                    print(
                        f"grok paper score failed error_type={type(score_error).__name__}",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"grok paper scan failed error_type={type(exc).__name__}", file=sys.stderr)
            now_mono = time.monotonic()
            if now_mono >= next_exit:
                try:
                    _run_exit(rest)
                except Exception as exc:
                    print(
                        f"grok paper exit failed error_type={type(exc).__name__}",
                        file=sys.stderr,
                    )
                next_exit = now_mono + 3600
            day = datetime.now(timezone.utc).date().isoformat()
            if day != report_day:
                try:
                    _run_daily(rest)
                except Exception as exc:
                    print(
                        f"grok paper report failed error_type={type(exc).__name__}",
                        file=sys.stderr,
                    )
                report_day = day
            time.sleep(edge_scan_interval_seconds())
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
