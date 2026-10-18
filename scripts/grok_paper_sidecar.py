#!/usr/bin/env python3
"""Paper-only Grok sidecar.

Lists markets, reads positions, and writes logs/state/grok_edge_*.jsonl plus
logs/state/grok_paper.db. It does not import the executor and it does not
submit, cancel, or reprice an order. Kalshi access is GET only. POST and
DELETE to the order endpoints raise while this process is armed.
"""

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


def _run_scan(rest) -> dict[str, int]:
    from utils.event_news_grok_edge import PAPER_DB_PATH, run_paper_edge_scan
    from utils.event_news_research import event_news_matcher_reserve_series

    series_list = event_news_matcher_reserve_series()
    markets: list = []
    for series in series_list:
        page, _cursor = rest.get_markets(status="open", series_ticker=series, limit=20)
        markets.extend(page)
    counts = run_paper_edge_scan(
        markets,
        live=False,
        allowlist=series_list,
        db_path=PAPER_DB_PATH,
    )
    counts["fetched"] = len(markets)
    return counts


def _run_scores(rest) -> dict[str, int]:
    from utils.event_news_grok_edge import PAPER_DB_PATH, score_open_positions

    positions = rest.get_open_positions(strict=True)
    return score_open_positions(
        positions,
        live=False,
        market_lookup=rest.get_market,
        db_path=PAPER_DB_PATH,
    )


def _run_exit(rest) -> int:
    from utils.event_news_grok_edge import paper_position_rows, run_paper_exit_watch

    positions = rest.get_open_positions(strict=True)
    return run_paper_exit_watch(
        paper_position_rows(positions),
        live=False,
        market_lookup=rest.get_market,
    )


def _run_daily(rest) -> None:
    from utils.event_news_grok_edge import write_daily_edge_report

    write_daily_edge_report(market_lookup=rest.get_market)


def _print_once(scan: dict[str, int], scores: dict[str, int], before: str | None, after: str | None) -> None:
    from utils.event_news_grok_edge import CALL_LOG_PATH

    payload = {
        "fetched": scan.get("fetched", 0),
        "examined": scan.get("examined", 0),
        "queued": scan.get("queued", 0),
        "scan_skipped": scan.get("skipped", 0),
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
            scan = _run_scan(rest)
            try:
                scores = _run_scores(rest)
            except Exception as exc:
                print(
                    f"grok paper score failed error_type={type(exc).__name__}",
                    file=sys.stderr,
                )
                scores = {"scored": 0, "skipped": 0}
            _print_once(scan, scores, before, _spent(SPEND_PATH))
            return 0
        next_exit = time.monotonic() + 60
        report_day = ""
        while True:
            try:
                _run_scan(rest)
                _run_scores(rest)
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
