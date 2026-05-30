"""Self-maintaining news-edge series set.

The bot only has a real edge on a small set of event-driven political /
geopolitical Kalshi series; ``feeds/search_news_monitor`` prioritizes those for
targeted news retrieval. A hand-frozen list (``config.NEWS_EDGE_SERIES``) rots:
the political moment shifts, an administration changes, or Kalshi retires a
series, and stale entries silently waste the query budget while new edge
clusters are missed — the same failure mode as the obsolete
``KALSHI_GEOPOLITICAL_SERIES`` allowlist.

This module derives the *active* edge set from a rolling window of OPPORTUNITY
history so series **auto-promote** when they start producing opportunities and
**auto-age-out** when they stop. It mirrors two existing durable patterns:

  * ``feeds/subreddit_discovery`` — candidate discovery + zero-signal suppression.
  * ``analysis/match_feedback`` — a recomputed JSON artifact under ``data/``
    loaded at runtime, with operator ``pinned`` overrides preserved.

Artifact schema (``data/news_edge_series.json``)::

    {
      "<SERIES_PREFIX>": {
        "opportunities": <int>,        # count within the window at last refresh
        "last_seen_utc": <iso8601>,    # most recent opportunity timestamp
        "pinned": <bool>               # operator override; never aged out
      }
    }

The static ``config.NEWS_EDGE_SERIES`` is only a **cold-start seed** — used when
no artifact exists yet. Once the aggregator has written the artifact, the seed
is ignored, so yesterday's edge series cannot be resurrected. Membership only
affects retrieval *priority* (never the trade gate), so staleness degrades
throughput silently rather than causing bad trades — which is why the loop can
age aggressively.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_WINDOW_DAYS = 45
DEFAULT_MIN_OPPS = 3
_PATH_DEFAULT = Path("data/news_edge_series.json")
_LOGS_DIR_DEFAULT = Path("logs/trades")


def _parse_iso(ts: Any) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _series_of(ticker: str) -> str:
    return (ticker or "").split("-")[0].upper()


def compute_edge_series(
    events: Iterable[tuple[str, str]],
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_opps: int = DEFAULT_MIN_OPPS,
) -> dict[str, dict[str, Any]]:
    """Build artifact entries from ``(series, iso_ts)`` opportunity events.

    A series is promoted when it has ``>= min_opps`` opportunities inside the
    trailing ``window_days``. Opportunities older than the window are ignored,
    which is what makes a series age out once it stops producing.
    """
    cutoff = now - timedelta(days=window_days)
    counts: dict[str, int] = {}
    last_seen: dict[str, datetime] = {}
    for series, ts in events:
        if not series:
            continue
        t = _parse_iso(ts)
        if t is None or t < cutoff:
            continue
        counts[series] = counts.get(series, 0) + 1
        if series not in last_seen or t > last_seen[series]:
            last_seen[series] = t
    return {
        series: {
            "opportunities": count,
            "last_seen_utc": last_seen[series].isoformat(),
        }
        for series, count in counts.items()
        if count >= min_opps
    }


def load_edge_series(path: Path = _PATH_DEFAULT) -> dict[str, dict[str, Any]]:
    """Load the artifact. Returns ``{}`` if absent or malformed."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def write_edge_series(
    entries: dict[str, dict[str, Any]],
    path: Path = _PATH_DEFAULT,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def active_edge_series(
    *,
    path: Path = _PATH_DEFAULT,
    seed: frozenset[str] | set[str] = frozenset(),
    now: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> frozenset[str]:
    """Return the currently-active edge series.

    Cold start (no artifact): return the static ``seed``. Once an artifact
    exists, the seed is ignored — entries are returned only if ``pinned`` or
    their ``last_seen_utc`` is within the window. An artifact whose entries have
    all aged out returns an empty set (search falls back to pure open-interest
    ranking — safe), NOT the seed, so dead series are never resurrected.
    """
    entries = load_edge_series(path)
    if not entries:
        return frozenset(seed)
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    active: set[str] = set()
    for series, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("pinned") is True:
            active.add(series)
            continue
        last_seen = _parse_iso(entry.get("last_seen_utc"))
        if last_seen is None or last_seen < cutoff:
            continue
        active.add(series)
    return frozenset(active)


def read_opportunity_events(
    logs_dir: Path = _LOGS_DIR_DEFAULT,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[tuple[str, str]]:
    """Scan trade-log JSONL for OPPORTUNITY records within the window.

    Returns ``(series, iso_ts)`` pairs. Tolerant of malformed lines. Used by the
    aggregator refresh; not on any hot path.
    """
    cutoff = now - timedelta(days=window_days)
    events: list[tuple[str, str]] = []
    for jsonl in sorted(logs_dir.rglob("*.jsonl")):
        try:
            with jsonl.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or '"OPPORTUNITY"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "OPPORTUNITY":
                        continue
                    ts = rec.get("ts")
                    t = _parse_iso(ts)
                    if t is None or t < cutoff:
                        continue
                    series = _series_of(rec.get("ticker", ""))
                    if series:
                        events.append((series, ts))
        except OSError:
            continue
    return events


def refresh(
    *,
    logs_dir: Path = _LOGS_DIR_DEFAULT,
    path: Path = _PATH_DEFAULT,
    now: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_opps: int = DEFAULT_MIN_OPPS,
) -> dict[str, dict[str, Any]]:
    """Recompute the artifact from recent OPPORTUNITY history.

    Preserves operator ``pinned`` entries from the existing artifact. Intended
    to run periodically from the match-feedback aggregator / a scheduled job —
    NOT from the runtime hot path.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    events = read_opportunity_events(logs_dir, now=now, window_days=window_days)
    entries = compute_edge_series(
        events, now=now, window_days=window_days, min_opps=min_opps
    )
    for series, entry in load_edge_series(path).items():
        if isinstance(entry, dict) and entry.get("pinned") is True:
            entries.setdefault(series, entry)
    write_edge_series(entries, path)
    return entries


if __name__ == "__main__":  # pragma: no cover
    result = refresh()
    print(f"news_edge_series refreshed: {len(result)} active series -> {sorted(result)}")
