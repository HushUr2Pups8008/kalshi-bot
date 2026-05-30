"""RSS feed-health observability (Medium-tier rot surface #6).

``config.RSS_FEEDS`` is a hand-maintained list of feed URLs. Feeds rot silently:
a domain changes, an endpoint is deprecated, a CDN reshuffles — and the bot just
ingests less news with no alarm. This probes each feed and classifies it
live / stale / empty / unreachable, writing ``data/feed_health.json`` and
surfacing the unhealthy ones.

Network-probing, so run it on its OWN cadence (e.g. weekly) rather than coupling
it to the daily aggregator::

    python -m tasks.stats.feed_health

The classification is a pure function (unit-tested); the probe is an integration
adapter that isolates per-feed errors so one bad feed can't break the audit.
"""

from __future__ import annotations

import calendar
import json
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from config import RSS_FEEDS
from tasks.stats.edge_series import _parse_iso
from utils.output_paths import DERIVED_STATE_DIR

_STALE_AFTER_DEFAULT = 7 * 86400  # a feed silent for a week is likely broken
_AGENT = "kalshi-bot-feed-health/1.0 (feed liveness monitor)"
_PATH_DEFAULT = DERIVED_STATE_DIR / "feed_health.json"


def classify_feed_health(
    *,
    status: int | None,
    entry_count: int,
    newest_age_seconds: float | None,
    stale_after_seconds: int = _STALE_AFTER_DEFAULT,
) -> str:
    """Classify a feed from its probe result. Pure."""
    if status is None or status >= 400:
        return "unreachable"
    if entry_count == 0:
        return "empty"
    if newest_age_seconds is not None and newest_age_seconds > stale_after_seconds:
        return "stale"
    return "live"


def _entry_dt(entry: Any) -> datetime | None:
    def _get(key):
        if isinstance(entry, dict):
            return entry.get(key)
        return getattr(entry, key, None)

    for key in ("published_parsed", "updated_parsed"):
        v = _get(key)
        if v:
            try:
                return datetime.fromtimestamp(calendar.timegm(v), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
    for key in ("published", "updated"):
        v = _get(key)
        if isinstance(v, str) and v.strip():
            dt = _parse_iso(v)
            if dt is not None:
                return dt
            try:
                dt = parsedate_to_datetime(v)
            except (TypeError, ValueError):
                dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
    return None


def _newest_entry_age_seconds(entries: Any, now: datetime) -> float | None:
    """Age (seconds) of the most recent dated entry, or None if none are dated."""
    ages = [
        (now - dt).total_seconds()
        for dt in (_entry_dt(e) for e in entries)
        if dt is not None
    ]
    return min(ages) if ages else None


def probe_feed(
    url: str,
    *,
    now: datetime,
    timeout: float = 10.0,
    stale_after_seconds: int = _STALE_AFTER_DEFAULT,
    agent: str = _AGENT,
) -> dict[str, Any]:
    """Fetch + parse a feed and classify its health. Never raises."""
    import socket

    try:
        import feedparser  # local import: heavy, only needed when probing
    except ImportError as exc:  # pragma: no cover
        return {"url": url, "status": None, "entry_count": 0,
                "newest_age_seconds": None, "health": "error", "error": f"feedparser: {exc}"}

    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        d = feedparser.parse(url, agent=agent)
    except Exception as exc:  # network/parse blowups isolated per feed
        return {"url": url, "status": None, "entry_count": 0,
                "newest_age_seconds": None, "health": "error", "error": str(exc)[:160]}
    finally:
        socket.setdefaulttimeout(old_timeout)

    status = getattr(d, "status", None)
    entries = getattr(d, "entries", []) or []
    newest = _newest_entry_age_seconds(entries, now)
    feed_meta = getattr(d, "feed", None)
    title = feed_meta.get("title") if hasattr(feed_meta, "get") else None
    return {
        "url": url,
        "title": title,
        "status": status,
        "entry_count": len(entries),
        "newest_age_seconds": newest,
        "health": classify_feed_health(
            status=status,
            entry_count=len(entries),
            newest_age_seconds=newest,
            stale_after_seconds=stale_after_seconds,
        ),
    }


def audit_feeds(
    urls: "list[str] | None" = None,
    *,
    now: datetime | None = None,
    timeout: float = 10.0,
    stale_after_seconds: int = _STALE_AFTER_DEFAULT,
    path: Path = _PATH_DEFAULT,
    write: bool = True,
) -> dict[str, Any]:
    if urls is None:
        urls = list(RSS_FEEDS)
    if now is None:
        now = datetime.now(timezone.utc)
    results = [
        probe_feed(u, now=now, timeout=timeout, stale_after_seconds=stale_after_seconds)
        for u in urls
    ]
    by_health = Counter(r["health"] for r in results)
    unhealthy = [r for r in results if r["health"] != "live"]
    report = {
        "generated_utc": now.isoformat(),
        "feed_count": len(urls),
        "by_health": dict(by_health),
        "unhealthy": sorted(unhealthy, key=lambda r: r["url"]),
    }
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":  # pragma: no cover
    r = audit_feeds()
    print(f"feed_health: {r['feed_count']} feeds -> {r['by_health']}")
    for item in r["unhealthy"]:
        print(f"  [{item['health']}] {item['url']} "
              f"(status={item['status']}, entries={item['entry_count']})")
