"""Config-driven politics series universe.

Live admits come from config/series_universe.yaml. A missing or invalid
file admits nothing and logs one fail-closed line. This module does not
place orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from utils.logger import get_logger

log = get_logger("series_universe")

_MODES = frozenset({"live", "paper", "off"})
_ALLOWED_KEYS = frozenset(
    {
        "series",
        "mode",
        "favorite_lo_cents",
        "favorite_hi_cents",
        "notes",
        "transcript_skip",
        "paper_score",
    }
)
_POLITICS_COHORT_ID = "kalshi-event-news-20260820"

_cached: SeriesUniverse | None = None
_cached_path: Path | None = None
_path_override: Path | None = None


@dataclass(frozen=True)
class SeriesEntry:
    series: str
    mode: str
    favorite_lo_cents: int
    favorite_hi_cents: int
    notes: str
    transcript_skip: bool = False
    paper_score: bool = False

    @property
    def favorite_band(self) -> tuple[float, float]:
        return (self.favorite_lo_cents / 100.0, self.favorite_hi_cents / 100.0)


@dataclass(frozen=True)
class SeriesUniverse:
    path: str
    entries: tuple[SeriesEntry, ...]
    failed: bool
    reason: str


def default_universe_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "series_universe.yaml"


def reset_series_universe(path: Path | None = None) -> None:
    """Drop the cache. ``path`` overrides the repo file until the next reset."""
    global _cached, _cached_path, _path_override
    _cached = None
    _cached_path = None
    _path_override = path


def series_ticker_matches(candidate: str, prefix: str) -> bool:
    """Exact series ticker, or that ticker plus a hyphen. Glued names do not match."""
    left = str(candidate or "").strip().upper()
    right = str(prefix or "").strip().upper()
    if not left or not right:
        return False
    return left == right or left.startswith(right + "-")


def get_universe() -> SeriesUniverse:
    global _cached, _cached_path
    path = _path_override if _path_override is not None else default_universe_path()
    if _cached is not None and _cached_path == path:
        return _cached
    loaded = _load_universe(path)
    _cached = loaded
    _cached_path = path
    return loaded


def universe_failed() -> bool:
    return get_universe().failed


def live_series_tickers() -> tuple[str, ...]:
    universe = get_universe()
    if universe.failed:
        return ()
    return tuple(entry.series for entry in universe.entries if entry.mode == "live")


def paper_score_entries() -> tuple[SeriesEntry, ...]:
    """Mode paper, plus live rows that opted into paper scoring."""
    universe = get_universe()
    if universe.failed:
        return ()
    return tuple(
        entry
        for entry in universe.entries
        if entry.mode == "paper" or entry.paper_score
    )


def match_market(market: Any) -> SeriesEntry | None:
    universe = get_universe()
    if universe.failed:
        return None
    series = _market_series(market)
    ticker = _market_ticker(market)
    for entry in universe.entries:
        if series_ticker_matches(series, entry.series) or series_ticker_matches(
            ticker, entry.series
        ):
            return entry
    return None


def market_transcript_skip(market: Any) -> bool:
    entry = match_market(market)
    return bool(entry and entry.transcript_skip)


def event_news_live_post_block_reason(
    market: Any,
    *,
    config: Any = None,
) -> str | None:
    """None lets a politics live series continue. Every other case blocks POST.

    Non-politics cohorts return None so freeze orders are unchanged.
    """
    if not _is_politics_cohort(config):
        return None
    universe = get_universe()
    if universe.failed:
        return "universe_unavailable"
    entry = match_market(market)
    if entry is None:
        return "not_live_series"
    if entry.mode == "paper":
        return "paper_series_no_post"
    if entry.mode != "live":
        return "series_off"
    if entry.transcript_skip:
        return "transcript_skip"
    return None


def _is_politics_cohort(config: Any) -> bool:
    if config is None:
        from config import cfg

        config = cfg
    return (
        str(getattr(config, "paper_cohort_id", "")).strip().lower()
        == _POLITICS_COHORT_ID
    )


def _market_series(market: Any) -> str:
    series = str(getattr(market, "series_ticker", "") or "").strip().upper()
    if series:
        return series
    return _market_ticker(market).split("-", 1)[0]


def _market_ticker(market: Any) -> str:
    return str(getattr(market, "ticker", "") or "").strip().upper()


def _load_universe(path: Path) -> SeriesUniverse:
    if not path.is_file():
        return _fail(path, "missing")
    try:
        text = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(text)
    except (OSError, UnicodeError, yaml.YAMLError):
        return _fail(path, "unreadable")
    try:
        entries = _parse_entries(payload)
    except ValueError as exc:
        return _fail(path, str(exc))
    live = sum(1 for entry in entries if entry.mode == "live")
    paper = sum(1 for entry in entries if entry.mode == "paper")
    log.info(
        "[EVENT_NEWS_UNIVERSE] loaded path=%s live=%d paper=%d",
        path,
        live,
        paper,
    )
    return SeriesUniverse(
        path=str(path),
        entries=entries,
        failed=False,
        reason="",
    )


def _fail(path: Path, reason: str) -> SeriesUniverse:
    log.error(
        "[EVENT_NEWS_UNIVERSE] fail_closed path=%s reason=%s",
        path,
        reason,
    )
    return SeriesUniverse(path=str(path), entries=(), failed=True, reason=reason)


def _parse_entries(payload: Any) -> tuple[SeriesEntry, ...]:
    if not isinstance(payload, list):
        raise ValueError("not_a_list")
    entries: list[SeriesEntry] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("entry_not_a_map")
        unknown = set(item) - _ALLOWED_KEYS
        if unknown:
            raise ValueError("unknown_key")
        series = str(item.get("series") or "").strip().upper()
        if not series or not series.isalnum():
            raise ValueError("missing_series")
        if series in seen:
            raise ValueError("duplicate")
        seen.add(series)
        mode = item.get("mode")
        if mode not in _MODES:
            raise ValueError("bad_mode")
        lo = _cents(item.get("favorite_lo_cents"))
        hi = _cents(item.get("favorite_hi_cents"))
        if lo is None or hi is None or lo >= hi:
            raise ValueError("bad_band")
        notes = item.get("notes")
        if not isinstance(notes, str) or not notes.strip():
            raise ValueError("missing_notes")
        transcript_skip = _flag(item, "transcript_skip")
        paper_score = _flag(item, "paper_score")
        entries.append(
            SeriesEntry(
                series=series,
                mode=mode,
                favorite_lo_cents=lo,
                favorite_hi_cents=hi,
                notes=notes.strip(),
                transcript_skip=transcript_skip,
                paper_score=paper_score,
            )
        )
    if not entries:
        raise ValueError("empty")
    return tuple(entries)


def _cents(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 100:
        return None
    return value


def _flag(item: dict[str, Any], name: str) -> bool:
    if name not in item:
        return False
    value = item[name]
    if not isinstance(value, bool):
        raise ValueError("bad_flag")
    return value
