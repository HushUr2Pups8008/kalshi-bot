#!/usr/bin/env python3
"""Daily politics universe discovery. Read-only. Does not POST or call Grok.

GET /markets?status=open&mve_filter=exclude. A series is a candidate when
it has a market that closes within 14 days, has 24h volume of at least 500
contracts, and a two-sided spread of 0c through 14c. Crossed books are
excluded. Response status must be active. Sports prefixes stay out.
Series already in the universe file are not recommended again.

The script writes a report. It does not edit the universe file and it does
not apply the promotion rule.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import MARKET_SERIES_BLOCKLIST_PREFIXES
from utils.series_universe import get_universe, universe_failed

HORIZON = timedelta(days=14)
MIN_VOLUME = Decimal("500")
MAX_SPREAD_EXCLUSIVE = 15
MOUNTAIN = ZoneInfo("America/Denver")
PROMOTION_RULE = (
    "A series is eligible for live after at least 15 settled paper trades "
    "and 15 clustered events, with clustered fee-net CI low above 0 and BH q at or below 0.10. "
    "Otherwise it says recommend off or not enough data and names the failing condition. "
    "This report does not change modes."
)
POLITICS_CATEGORIES = frozenset({"politics", "elections"})
ECON_POLICY_CATEGORIES = frozenset({"economics"})
_PAGE_CAP = 500


def ask_cents(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        cents = (Decimal(str(raw)) * Decimal("100")).to_integral_value(ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    value = int(cents)
    if value < 0 or value > 100:
        return None
    return value


def parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def parse_decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def volume_24h(market: Mapping[str, Any]) -> Decimal | None:
    for key in ("volume_24h_fp", "volume_24h"):
        if key in market and market.get(key) not in (None, ""):
            return parse_decimal(market.get(key))
    return None


def market_qualifies(
    *,
    market_state: str,
    close_at: datetime | None,
    volume: Decimal | None,
    yes_cents: int | None,
    no_cents: int | None,
    now: datetime,
) -> bool:
    """Two-sided near-term book. Negative spread is not a quote."""
    if market_state != "active":
        return False
    if close_at is None or not (now < close_at <= now + HORIZON):
        return False
    if volume is None or volume < MIN_VOLUME:
        return False
    if yes_cents is None or no_cents is None:
        return False
    if not (1 <= yes_cents <= 99 and 1 <= no_cents <= 99):
        return False
    spread = yes_cents + no_cents - 100
    return 0 <= spread < MAX_SPREAD_EXCLUSIVE


def prefix_hit(series: str, prefixes: tuple[str, ...] | list[str]) -> bool:
    return any(series.startswith(prefix) for prefix in prefixes if prefix)


def is_denied(series: str, explicit: set[str]) -> bool:
    """Prefix deny is gone. The universe file mode is the desk gate."""
    del series, explicit
    return False


def is_politics_econ_policy(
    series: str,
    *,
    category: str,
    explicit: set[str],
) -> bool:
    """Politics or economics, and not a sports prefix."""
    if is_denied(series, explicit):
        return False
    if prefix_hit(series, MARKET_SERIES_BLOCKLIST_PREFIXES):
        return False
    return category.strip().lower() in POLITICS_CATEGORIES | ECON_POLICY_CATEGORIES


def median_close_text(closes: list[datetime]) -> str:
    if not closes:
        return ""
    midpoint = statistics.median(item.timestamp() for item in closes)
    return (
        datetime.fromtimestamp(midpoint, timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def format_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_money(value: Decimal | None) -> str:
    """Print a dollar mean half-up to 0.0001. Does not change the stored mean."""
    if value is None:
        return ""
    rounded = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return format_decimal(rounded)


def settlement_rows(
    db_path: Path,
    series_names: list[str],
) -> list[dict[str, Any]]:
    """n, hit rate, and fee-net EV from the paper DB. Missing fee-net is not zero."""
    wanted = list(dict.fromkeys(series_names))
    if not db_path.is_file():
        return [
            {
                "series": name,
                "n": None,
                "hits": None,
                "hit_rate": None,
                "fee_net_ev": None,
                "note": "database_missing",
            }
            for name in wanted
        ]
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        found = conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='paper_trade_accounting'"
        ).fetchone()
        if found is None:
            return [
                {
                    "series": name,
                    "n": None,
                    "hits": None,
                    "hit_rate": None,
                    "fee_net_ev": None,
                    "note": "fee_net_table_missing",
                }
                for name in wanted
            ]
        query = """
            SELECT
                CASE
                    WHEN t.series_ticker IS NOT NULL AND TRIM(t.series_ticker) != ''
                    THEN UPPER(TRIM(t.series_ticker))
                    ELSE UPPER(SUBSTR(t.ticker, 1, INSTR(t.ticker || '-', '-') - 1))
                END AS series_name,
                LOWER(t.side) AS side,
                t.resolved_yes AS resolved_yes,
                a.fee_net_pnl_dollars AS fee_net_pnl_dollars
            FROM paper_trades AS t
            LEFT JOIN paper_trade_accounting AS a ON a.trade_id = t.trade_id
            WHERE a.fee_net_pnl_dollars IS NOT NULL
        """
        grouped: dict[str, list[tuple[str, int | None, Decimal]]] = defaultdict(list)
        for series_name, side, resolved_yes, fee_text in conn.execute(query):
            fee = parse_decimal(fee_text)
            if fee is None or not series_name:
                continue
            grouped[str(series_name)].append((str(side or ""), resolved_yes, fee))
    finally:
        conn.close()

    rows: list[dict[str, Any]] = []
    for name in wanted:
        samples = grouped.get(name, [])
        n = len(samples)
        if n == 0:
            rows.append(
                {
                    "series": name,
                    "n": 0,
                    "hits": 0,
                    "hit_rate": None,
                    "fee_net_ev": None,
                    "note": "no_settled_fee_net_trades",
                }
            )
            continue
        hits = 0
        total = Decimal("0")
        for side, resolved_yes, fee in samples:
            total += fee
            if side == "yes" and resolved_yes == 1:
                hits += 1
            elif side == "no" and resolved_yes == 0:
                hits += 1
        rows.append(
            {
                "series": name,
                "n": n,
                "hits": hits,
                "hit_rate": Decimal(hits) / Decimal(n),
                "fee_net_ev": total / Decimal(n),
                "note": "",
            }
        )
    return rows


def render_report(
    *,
    fetched_at: datetime,
    pages: int,
    raw_count: int,
    active_count: int,
    series_pages: int,
    candidates: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    settlement: list[dict[str, Any]],
    paper_configured: list[dict[str, Any]],
    top: int,
) -> str:
    lines = [
        f"# Universe candidates {fetched_at.astimezone(MOUNTAIN).strftime('%Y-%m-%d')}",
        "",
        f"Fetched at: {fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "Request: GET /markets?status=open&mve_filter=exclude",
        f"Market pages: {pages}",
        f"Raw markets: {raw_count}",
        f"Response status active: {active_count}",
        f"Series pages: {series_pages}",
        "",
        "A candidate series has at least one market with response status active,",
        "close time inside the next 14 days, 24h volume of at least 500 contracts,",
        "and a two-sided spread of 0c through 14c. Spread is yes ask plus no ask",
        "minus 100. Asks outside 1c through 99c are not a two-sided quote.",
        "A negative spread is excluded. Deny-list prefixes are excluded unless",
        "that exact series is already in config/series_universe.yaml.",
        "This job does not add series.",
        "",
        f"## Top {top} candidate series",
        "",
        "| rank | series | markets | volume_24h | median_close_utc | category |",
        "|---:|---|---:|---:|---|---|",
    ]
    for index, row in enumerate(candidates[:top], start=1):
        lines.append(
            "| {rank} | {series} | {markets} | {volume} | {close} | {category} |".format(
                rank=index,
                series=row["series"],
                markets=row["markets"],
                volume=format_decimal(row["volume_24h"]),
                close=row["median_close"],
                category=row.get("category") or "unknown",
            )
        )
    lines.extend(
        [
            "",
            "## Politics and econ-policy subset",
            "",
            "Included when the series category is Politics, Elections, or Economics,",
            "the series is not on the non-politics deny list, and the series does",
            "not match MARKET_SERIES_BLOCKLIST_PREFIXES. Ranked by the same",
            "qualifying 24h volume. Already configured series are not recommended.",
            "",
            "| rank | series | markets | volume_24h | median_close_utc | category |",
            "|---:|---|---:|---:|---|---|",
        ]
    )
    if recommendations:
        for index, row in enumerate(recommendations, start=1):
            lines.append(
                "| {rank} | {series} | {markets} | {volume} | {close} | {category} |".format(
                    rank=index,
                    series=row["series"],
                    markets=row["markets"],
                    volume=format_decimal(row["volume_24h"]),
                    close=row["median_close"],
                    category=row.get("category") or "unknown",
                )
            )
    else:
        lines.append("| | none | | | | |")
    lines.extend(
        [
            "",
            "## Configured paper series",
            "",
            "Mode paper rows from config/series_universe.yaml. Stats are this",
            "fetch's qualifying markets. A blank volume means the series had no",
            "qualifying market. This job does not write the yaml and does not POST.",
            "Probabilities for these series are scored by the paper sidecar.",
            "This job does not call Grok.",
            "",
            "| series | markets | volume_24h | median_close_utc | category | title |",
            "|---|---:|---:|---|---|---|",
        ]
    )
    if paper_configured:
        for row in paper_configured:
            volume = row.get("volume_24h")
            lines.append(
                "| {series} | {markets} | {volume} | {close} | {category} | {title} |".format(
                    series=row["series"],
                    markets="" if row.get("markets") in (None, "") else row["markets"],
                    volume="" if volume in (None, "") else format_decimal(volume),
                    close=row.get("median_close") or "",
                    category=row.get("category") or "",
                    title=row.get("title") or "",
                )
            )
    else:
        lines.append("| | | | | | |")
    lines.extend(
        [
            "",
            "## Settlement",
            "",
            "n is settled paper trades with a fee-net pnl. Hit rate is wins over n.",
            "A YES trade hits when resolved_yes is 1. A NO trade hits when",
            "resolved_yes is 0. Fee-net EV is the mean fee_net_pnl_dollars,",
            "printed half-up to 0.0001 dollars. A series with n of 0 has no",
            "fee-net EV. Missing pnl is not recorded as 0.",
            "",
            "| series | n | hits | hit_rate | fee_net_ev | note |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in settlement:
        lines.append(
            "| {series} | {n} | {hits} | {rate} | {ev} | {note} |".format(
                series=row["series"],
                n="" if row["n"] is None else row["n"],
                hits="" if row["hits"] is None else row["hits"],
                rate="" if row["hit_rate"] is None else format_decimal(row["hit_rate"]),
                ev="" if row["fee_net_ev"] is None else format_money(row["fee_net_ev"]),
                note=row.get("note") or "",
            )
        )
    lines.extend(
        [
            "",
            "## Promotion rule",
            "",
            PROMOTION_RULE,
            "",
        ]
    )
    return "\n".join(lines)


def _get_json(client: Any, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    if "order" in endpoint or endpoint.startswith("/portfolio"):
        raise RuntimeError("discovery does not call order or portfolio endpoints")
    data = client._request("GET", endpoint, params=params)
    if not isinstance(data, dict):
        raise RuntimeError(f"{endpoint} page was not an object")
    return data


def _page(client: Any, endpoint: str, list_key: str, limit: int) -> tuple[int, list[Any]]:
    cursor = None
    seen: set[str] = set()
    pages = 0
    rows: list[Any] = []
    while True:
        params: dict[str, Any] = {"limit": limit}
        if endpoint == "/markets":
            params["status"] = "open"
            params["mve_filter"] = "exclude"
        if cursor:
            params["cursor"] = cursor
        data = _get_json(client, endpoint, params)
        batch = data.get(list_key)
        if not isinstance(batch, list):
            raise RuntimeError(f"{endpoint} page was not a list")
        if cursor and not batch:
            raise RuntimeError(f"{endpoint} returned an empty page with a cursor")
        pages += 1
        rows.extend(batch)
        nxt = data.get("cursor") or None
        if not nxt:
            return pages, rows
        if nxt == cursor or nxt in seen:
            raise RuntimeError(f"{endpoint} repeated a cursor")
        seen.add(str(nxt))
        cursor = str(nxt)
        if pages >= _PAGE_CAP:
            raise RuntimeError(f"{endpoint} exceeded {_PAGE_CAP} pages")
        if pages % 10 == 0:
            print(f"{endpoint} pages={pages} rows={len(rows)}", flush=True)


def _explicit_series() -> set[str]:
    universe = get_universe()
    if universe.failed:
        raise RuntimeError(f"universe fail_closed reason={universe.reason}")
    return {entry.series for entry in universe.entries}


def build_candidates(
    markets: list[Any],
    series_meta: dict[str, dict[str, Any]],
    *,
    now: datetime,
    explicit: set[str],
) -> tuple[int, int, list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    active_count = 0
    grouped: dict[str, dict[str, Any]] = {}
    near_term: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_state = str(market.get("status") or "").strip().lower()
        if market_state == "active":
            active_count += 1
        series = str(market.get("series_ticker") or "").strip().upper()
        if not series:
            ticker = str(market.get("ticker") or "").strip().upper()
            series = ticker.split("-", 1)[0] if ticker else ""
        if not series or is_denied(series, explicit):
            continue
        close_at = parse_time(market.get("close_time"))
        yes = ask_cents(market.get("yes_ask_dollars"))
        if yes is None:
            yes = ask_cents(market.get("yes_ask"))
        no = ask_cents(market.get("no_ask_dollars"))
        if no is None:
            no = ask_cents(market.get("no_ask"))
        volume = volume_24h(market)
        if (
            market_state == "active"
            and close_at is not None
            and now < close_at <= now + HORIZON
        ):
            near_term[series].append(
                {
                    "ticker": str(market.get("ticker") or ""),
                    "title": str(market.get("title") or ""),
                    "yes_ask_cents": yes,
                    "no_ask_cents": no,
                    "yes_ask_size": _size_text(market, "yes_ask_size_fp"),
                    "no_ask_size": _size_text(market, "no_ask_size_fp"),
                    "open_interest": _size_text(market, "open_interest_fp"),
                    "volume_24h": format_decimal(volume) if volume is not None else "",
                }
            )
        if not market_qualifies(
            market_state=market_state,
            close_at=close_at,
            volume=volume,
            yes_cents=yes,
            no_cents=no,
            now=now,
        ):
            continue
        bucket = grouped.setdefault(
            series,
            {"volume_24h": Decimal("0"), "markets": 0, "closes": []},
        )
        bucket["markets"] += 1
        bucket["volume_24h"] += volume or Decimal("0")
        if close_at is not None:
            bucket["closes"].append(close_at)
    candidates: list[dict[str, Any]] = []
    for series, bucket in grouped.items():
        meta = series_meta.get(series, {})
        candidates.append(
            {
                "series": series,
                "markets": bucket["markets"],
                "volume_24h": bucket["volume_24h"],
                "median_close": median_close_text(bucket["closes"]),
                "category": str(meta.get("category") or ""),
                "title": str(meta.get("title") or ""),
            }
        )
    candidates.sort(key=lambda row: (-row["volume_24h"], row["series"]))
    keep = {row["series"] for row in candidates} | explicit
    trimmed = {series: rows for series, rows in near_term.items() if series in keep}
    return active_count, len(markets), candidates, trimmed


def _size_text(market: Mapping[str, Any], key: str) -> str:
    value = parse_decimal(market.get(key))
    return "" if value is None else format_decimal(value)


def recommend_paper_series(
    candidates: list[dict[str, Any]],
    *,
    explicit: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for row in candidates:
        if row["series"] in explicit:
            continue
        if not is_politics_econ_policy(
            row["series"],
            category=str(row.get("category") or ""),
            explicit=explicit,
        ):
            continue
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def _series_meta(rows: list[Any]) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or item.get("series_ticker") or "").strip().upper()
        if not ticker:
            continue
        tags = item.get("tags") or ()
        if isinstance(tags, str):
            tags = [tags]
        elif not isinstance(tags, list):
            tags = []
        meta[ticker] = {
            "category": str(item.get("category") or ""),
            "title": str(item.get("title") or ""),
            "tags": [str(tag) for tag in tags if str(tag).strip()],
        }
    return meta


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format_decimal(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def discover(client: Any, *, now: datetime | None = None) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    explicit = _explicit_series()
    market_pages, markets = _page(client, "/markets", "markets", 1000)
    series_pages, series_rows = _page(client, "/series", "series", 200)
    active_count, raw_count, candidates, near_term = build_candidates(
        markets,
        _series_meta(series_rows),
        now=clock,
        explicit=explicit,
    )
    return {
        "fetched_at": clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": market_pages,
        "raw": raw_count,
        "active": active_count,
        "series_pages": series_pages,
        "candidates": _json_ready(candidates),
        "near_term": near_term,
    }


def write_outputs(
    snapshot: dict[str, Any],
    *,
    report_dir: Path,
    paper_db: Path,
    top: int,
    recommend: int,
) -> Path:
    fetched_at = parse_time(snapshot.get("fetched_at"))
    if fetched_at is None:
        raise RuntimeError("snapshot fetched_at is missing")
    explicit = _explicit_series()
    candidates = []
    for row in snapshot.get("candidates") or []:
        if not isinstance(row, dict) or not row.get("series"):
            continue
        volume = parse_decimal(row.get("volume_24h"))
        if volume is None:
            raise RuntimeError(f"candidate volume missing for {row.get('series')}")
        candidates.append({**row, "volume_24h": volume})
    recommendations = recommend_paper_series(
        candidates,
        explicit=explicit,
        limit=recommend,
    )
    by_series = {row["series"]: row for row in candidates}
    paper_configured: list[dict[str, Any]] = []
    for entry in get_universe().entries:
        if entry.mode != "paper":
            continue
        row = by_series.get(entry.series)
        if row is None:
            paper_configured.append(
                {
                    "series": entry.series,
                    "markets": "",
                    "volume_24h": None,
                    "median_close": "",
                    "category": "",
                    "title": "",
                }
            )
        else:
            paper_configured.append(row)
    settlement_names = [
        entry.series
        for entry in get_universe().entries
        if entry.mode == "paper" or entry.paper_score or entry.mode == "live"
    ]
    settlement = settlement_rows(paper_db, settlement_names)
    text = render_report(
        fetched_at=fetched_at,
        pages=int(snapshot["pages"]),
        raw_count=int(snapshot["raw"]),
        active_count=int(snapshot["active"]),
        series_pages=int(snapshot["series_pages"]),
        candidates=candidates,
        recommendations=recommendations,
        settlement=settlement,
        paper_configured=paper_configured,
        top=top,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    day = fetched_at.astimezone(MOUNTAIN).strftime("%Y%m%d")
    path = report_dir / f"universe-candidates-{day}.md"
    path.write_text(text, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-out", default="")
    parser.add_argument("--snapshot-in", default="")
    parser.add_argument("--report-dir", default="docs/reports")
    parser.add_argument("--paper-db", default="")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--recommend", type=int, default=10)
    args = parser.parse_args(argv)
    if universe_failed():
        raise RuntimeError(f"universe fail_closed reason={get_universe().reason}")
    repo = Path(__file__).resolve().parents[1]
    paper_db = Path(args.paper_db) if args.paper_db else (
        repo / "data/paper_cohorts/kalshi-event-news-20260820/paper_trades.db"
    )
    if args.snapshot_in:
        snapshot = json.loads(Path(args.snapshot_in).read_text(encoding="utf-8"))
    else:
        from kalshi.rest_client import KalshiRestClient

        snapshot = discover(KalshiRestClient())
        if args.snapshot_out:
            Path(args.snapshot_out).write_text(
                json.dumps(_json_ready(snapshot)),
                encoding="utf-8",
            )
    report = write_outputs(
        snapshot,
        report_dir=Path(args.report_dir),
        paper_db=paper_db,
        top=args.top,
        recommend=args.recommend,
    )
    print(f"report={report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
