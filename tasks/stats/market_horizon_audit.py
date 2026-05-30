"""Market-horizon observability (rot surfaces #11/#12).

Three static constants encode assumptions about Kalshi's market-time
distribution:
  * ``MAX_MARKET_DAYS_TO_EXPIRY`` (30) — markets closing further out are excluded
    from the universe.
  * ``_DAYS_TO_CLOSE_CAP`` (90) — markets closing further out get a zero
    proximity sub-score in specificity.
  * ``MARKET_CACHE_TTL_SECONDS`` — refresh cadence (reported for context).

If Kalshi drifts toward longer-dated markets these silently misalign (the
universe filter excludes too much; the proximity score saturates) with no
alarm. This audit fetches the live open-market set, reports the close-time
distribution, and flags drift. The distribution math is a pure, tested function;
the live fetch is a best-effort integration adapter.

Run standalone (needs Kalshi creds in ``.env``)::

    python -m tasks.stats.market_horizon_audit
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.market_specificity import _DAYS_TO_CLOSE_CAP
from config import MARKET_CACHE_TTL_SECONDS, MAX_MARKET_DAYS_TO_EXPIRY
from tasks.stats.edge_series import _parse_iso
from utils.output_paths import DERIVED_STATE_DIR

_PATH_DEFAULT = DERIVED_STATE_DIR / "market_horizon_audit.json"


def summarize_market_horizon(
    markets: Any,
    *,
    max_days_expiry: int,
    days_close_cap: int,
    now: datetime,
) -> dict[str, Any]:
    """Close-time distribution + drift flags over a market set. Pure."""
    days: list[float] = []
    total = 0
    unparseable = 0
    for m in markets:
        total += 1
        ct = m.get("close_time") if isinstance(m, dict) else getattr(m, "close_time", None)
        dt = _parse_iso(ct)
        if dt is None:
            unparseable += 1
            continue
        days.append((dt - now).total_seconds() / 86400.0)
    dated = len(days)
    days.sort()

    def _q(q: float) -> float | None:
        if not days:
            return None
        return round(days[min(len(days) - 1, int(q * len(days)))], 1)

    beyond_expiry = sum(1 for d in days if d > max_days_expiry)
    beyond_cap = sum(1 for d in days if d > days_close_cap)

    flags: list[str] = []
    p75 = _q(0.75)
    if p75 is not None and p75 > days_close_cap:
        flags.append(
            f"p75 days-to-close {p75} > _DAYS_TO_CLOSE_CAP {days_close_cap}: "
            "proximity sub-score saturates (~0) for most markets"
        )
    if dated and beyond_expiry / dated > 0.5:
        flags.append(
            f"{beyond_expiry}/{dated} markets close beyond MAX_MARKET_DAYS_TO_EXPIRY "
            f"{max_days_expiry}d: a majority of the universe is excluded"
        )

    return {
        "count": total,
        "count_dated": dated,
        "count_unparseable": unparseable,
        "days_quantiles": {"p50": _q(0.5), "p75": p75, "p90": _q(0.9), "p99": _q(0.99)},
        "pct_beyond_max_days_expiry": (beyond_expiry / dated) if dated else 0.0,
        "pct_beyond_days_close_cap": (beyond_cap / dated) if dated else 0.0,
        "flags": flags,
    }


def fetch_open_markets(client: Any = None, *, max_pages: int = 250) -> list:
    """Paginate the live open-market set. Best-effort; needs Kalshi creds."""
    if client is None:
        from kalshi.rest_client import KalshiRestClient

        client = KalshiRestClient()
    markets: list = []
    cursor = None
    for _ in range(max_pages):
        page, cursor = client.get_markets(status="open", cursor=cursor)
        markets.extend(page)
        if not cursor:
            break
    return markets


def audit(
    *,
    now: datetime | None = None,
    write: bool = True,
    max_pages: int = 250,
    path: Path = _PATH_DEFAULT,
) -> dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc)
    markets = fetch_open_markets(max_pages=max_pages)
    report = summarize_market_horizon(
        markets,
        max_days_expiry=MAX_MARKET_DAYS_TO_EXPIRY,
        days_close_cap=_DAYS_TO_CLOSE_CAP,
        now=now,
    )
    report["generated_utc"] = now.isoformat()
    report["assumptions"] = {
        "max_days_expiry": MAX_MARKET_DAYS_TO_EXPIRY,
        "days_close_cap": _DAYS_TO_CLOSE_CAP,
        "cache_ttl_seconds": MARKET_CACHE_TTL_SECONDS,
    }
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":  # pragma: no cover
    try:
        r = audit()
    except Exception as exc:
        print(f"market_horizon_audit: live fetch failed ({type(exc).__name__}: {exc}); "
              "run where Kalshi creds are available.")
        raise SystemExit(0)
    print(
        f"market_horizon: {r['count_dated']}/{r['count']} dated; "
        f"days p50={r['days_quantiles']['p50']} p75={r['days_quantiles']['p75']} "
        f"p90={r['days_quantiles']['p90']}; "
        f"{r['pct_beyond_max_days_expiry']:.0%} beyond expiry-filter, "
        f"{r['pct_beyond_days_close_cap']:.0%} beyond proximity-cap"
    )
    for f in r["flags"]:
        print(f"  ⚠️ {f}")
