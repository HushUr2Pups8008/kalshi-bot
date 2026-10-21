"""Config-driven politics series universe.

Live admits come from config/series_universe.yaml. A missing or invalid
file admits nothing and logs one fail-closed line. This module does not
place orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

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
        "category",
        "max_contracts",
        "edge_min",
        "occupancy_family",
        "open_family_lock",
        "x_search",
        "venue",
    }
)
_VENUES = frozenset({"kalshi"})
_DEFAULT_MAX_CONTRACTS = 8
_DEFAULT_EDGE_MIN = "0.04"
_MENTION_SERIES = "KXTRUMPMENTION"

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
    category: str = ""
    max_contracts: int = _DEFAULT_MAX_CONTRACTS
    edge_min: str = _DEFAULT_EDGE_MIN
    occupancy_family: bool = False
    open_family_lock: bool = False
    x_search: bool = False
    venue: str = "kalshi"

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


def effective_mode(entry: SeriesEntry) -> str:
    """File mode. Mention stays live until the research flag is on.

    The yaml row records paper. A restart with the flag unset must still
    fetch mention and block POST with transcript_skip.
    """
    from utils.event_news_desk_flags import event_news_live_tradable_research_enabled

    if (
        entry.series == _MENTION_SERIES
        and entry.mode == "paper"
        and not event_news_live_tradable_research_enabled()
    ):
        return "live"
    return entry.mode


def live_series_tickers() -> tuple[str, ...]:
    universe = get_universe()
    if universe.failed:
        return ()
    return tuple(
        entry.series for entry in universe.entries if effective_mode(entry) == "live"
    )


def exact_series_in_universe(series: str) -> bool:
    """True when the ticker is a yaml series and its mode is not off.

    A blocklist prefix must not drop that series. A glued sibling does not match.
    """
    name = str(series or "").strip().upper()
    if not name:
        return False
    universe = get_universe()
    if universe.failed:
        return False
    return any(entry.series == name and entry.mode != "off" for entry in universe.entries)


def event_news_prefix_blocked(series: str, prefixes: Sequence[str]) -> bool:
    """True when a startswith block applies.

    An exact yaml series is exempt. Glued siblings stay blocked.
    A failed universe exempts nothing.
    """
    name = str(series or "").strip().upper()
    if not name:
        return False
    if exact_series_in_universe(name):

        return False
    return any(name.startswith(str(prefix)) for prefix in prefixes if prefix)


def occupancy_family_series() -> tuple[str, ...]:
    universe = get_universe()
    if universe.failed:
        return ()
    return tuple(entry.series for entry in universe.entries if entry.occupancy_family)


def open_family_lock_series() -> tuple[str, ...]:
    universe = get_universe()
    if universe.failed:
        return ()
    return tuple(entry.series for entry in universe.entries if entry.open_family_lock)


def x_search_series() -> tuple[str, ...]:
    universe = get_universe()
    if universe.failed:
        return ()
    return tuple(entry.series for entry in universe.entries if entry.x_search)


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


def market_edge_min(market: Any) -> Decimal | None:
    """Yaml edge_min. None when the file or the row cannot be read."""
    entry = match_market(market)
    if entry is None:
        return None
    try:
        number = Decimal(entry.edge_min)
    except Exception:
        return None
    if not number.is_finite() or number < 0 or number > 1:
        return None
    return number


def market_transcript_skip(market: Any) -> bool:
    entry = match_market(market)
    return bool(entry and entry.transcript_skip)


def event_news_live_post_block_reason(
    market: Any,
    *,
    config: Any = None,
) -> str | None:
    """None lets a live series continue. Paper mode returns paper_series_no_post."""
    universe = get_universe()
    if universe.failed:
        return "universe_unavailable"
    entry = match_market(market)
    if entry is None:
        return "not_live_series"
    mode = effective_mode(entry)
    if mode == "paper":
        return "paper_series_no_post"
    if mode != "live":
        return "series_off"
    if entry.transcript_skip:
        return "transcript_skip"
    return None


def _market_series(market: Any) -> str:
    raw = getattr(market, "series_ticker", "")
    if isinstance(raw, str):
        series = raw.strip().upper()
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
    effective_live = sum(1 for entry in entries if effective_mode(entry) == "live")
    log.info(
        "[EVENT_NEWS_UNIVERSE] loaded path=%s live=%d paper=%d effective_live=%d",
        path,
        live,
        paper,
        effective_live,
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
        category = item.get("category", "")
        if not isinstance(category, str):
            raise ValueError("bad_category")
        max_contracts = _max_contracts(item.get("max_contracts", _DEFAULT_MAX_CONTRACTS))
        edge_min = _edge_min(item.get("edge_min", _DEFAULT_EDGE_MIN))
        venue = _venue(item.get("venue", "kalshi"))
        entries.append(
            SeriesEntry(
                series=series,
                mode=mode,
                favorite_lo_cents=lo,
                favorite_hi_cents=hi,
                notes=notes.strip(),
                transcript_skip=transcript_skip,
                paper_score=paper_score,
                category=category.strip(),
                max_contracts=max_contracts,
                edge_min=edge_min,
                occupancy_family=_flag(item, "occupancy_family"),
                open_family_lock=_flag(item, "open_family_lock"),
                x_search=_flag(item, "x_search"),
                venue=venue,
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


def _max_contracts(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("bad_max_contracts")
    if value < 1 or value > _DEFAULT_MAX_CONTRACTS:
        raise ValueError("bad_max_contracts")
    return value


def _edge_min(value: Any) -> str:
    """A decimal string. Paper and the flag-on gate read it. Flag-off live does not."""
    if not isinstance(value, str):
        raise ValueError("bad_edge_min")
    text = value.strip()
    try:
        number = Decimal(text)
    except Exception as exc:
        raise ValueError("bad_edge_min") from exc
    if number < 0 or number > 1:
        raise ValueError("bad_edge_min")
    return format(number, "f")


def _venue(value: Any) -> str:
    """Missing means kalshi. Any other name fails the file closed."""
    if not isinstance(value, str):
        raise ValueError("bad_venue")
    venue = value.strip().lower()
    if venue not in _VENUES:
        raise ValueError("bad_venue")
    return venue
