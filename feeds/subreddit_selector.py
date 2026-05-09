"""
Adaptive subreddit selector.

Selects which subreddits to poll each Reddit cycle based on active Kalshi
market titles. Returns REDDIT_CORE_SUBREDDITS plus tier-2 topic subreddits
whose keywords overlap with currently open market titles, plus tier-3 candidate
probes that fill remaining capacity. Capped at REDDIT_MAX_SUBREDDITS.

Pure functions except for _load_probe_candidates / _update_probe_ts /
_mark_candidate_suppressed which do lightweight SQLite reads/writes.
"""

import logging
import re
import sqlite3
from pathlib import Path
from typing import Sequence

from config import (
    REDDIT_CORE_SUBREDDITS,
    REDDIT_MAX_SUBREDDITS,
    REDDIT_SUBREDDIT_TOPIC_MAP,
    REDDIT_TOPIC_KEYWORDS,
    SUBREDDIT_PROBE_COOLDOWN_SECS,
    SUBREDDIT_PROBE_SLOTS,
)
from utils.runtime_overrides import is_source_disabled
from kalshi import KalshiMarket


_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "will", "would",
    "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "and", "or", "but", "not", "this", "that", "it", "if", "as",
    "yes", "no", "over", "under", "win", "wins", "new", "more",
    "how", "what", "when", "who", "which", "its", "has", "have",
    "can", "get", "per", "any", "all",
})


def _is_disabled_reddit_source(subreddit: str) -> bool:
    """Defer to the runtime-overrides module-level helper."""
    return is_source_disabled(f"r/{subreddit}")


def filter_disabled_subreddits(subreddits: Sequence[str]) -> tuple[list[str], list[str]]:
    """Remove subreddits whose corresponding r/<name> source is globally disabled."""
    allowed: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for sub in subreddits:
        if sub in seen:
            continue
        seen.add(sub)
        if _is_disabled_reddit_source(sub):
            skipped.append(sub)
            continue
        allowed.append(sub)
    return allowed, skipped


def _tokenize(text: str) -> frozenset:
    """Lowercase, strip punctuation, remove stop words. Returns frozenset of tokens."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return frozenset(t for t in text.split() if t not in _STOP_WORDS and len(t) >= 3)


def _active_topics(markets: Sequence[KalshiMarket]) -> frozenset:
    """
    Return the set of topic tags that have at least one keyword match
    across all active market titles (and subtitles when present).
    """
    active: set[str] = set()
    for market in markets:
        tokens = _tokenize(market.title)
        subtitle = getattr(market, "subtitle", None)
        if subtitle:
            tokens = tokens | _tokenize(subtitle)
        for topic, keywords in REDDIT_TOPIC_KEYWORDS.items():
            if topic not in active and tokens & keywords:
                active.add(topic)
    return frozenset(active)


def _load_probe_candidates(
    db_path: Path,
    limit: int,
    cooldown_secs: int,
) -> list[str]:
    """
    Load candidate subreddits due for probing.
    Prioritises never-probed (probe_count ASC) then oldest discovery.
    Skips candidates probed within cooldown_secs.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT sub FROM subreddit_candidates
                WHERE status = 'candidate'
                  AND (
                      last_probed IS NULL
                      OR last_probed < datetime('now', ? || ' seconds')
                  )
                ORDER BY probe_count ASC, discovered_ts ASC
                LIMIT ?
                """,
                (f"-{cooldown_secs}", limit),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _update_probe_ts(db_path: Path, sub: str) -> None:
    """Increment probe_count and update last_probed timestamp."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                UPDATE subreddit_candidates
                SET probe_count = probe_count + 1,
                    last_probed = datetime('now')
                WHERE sub = ?
                """,
                (sub,),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Re-raise so the outer caller (select_subreddits) can fall back to core subs
        # and the failure surfaces in logs rather than silently corrupting probe state.
        logging.getLogger("subreddit_selector").error(
            "[SUBREDDIT] DB write failed (table=subreddit_candidates, sub=%s): %s",
            sub, exc,
        )
        raise


def _mark_candidate_suppressed(db_path: Path, sub: str) -> None:
    """Mark a candidate as suppressed so it is never probed again."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE subreddit_candidates SET status = 'suppressed' WHERE sub = ?",
                (sub,),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Re-raise so the outer caller (select_subreddits) can fall back to core subs
        # and suppression writes are never silently dropped.
        logging.getLogger("subreddit_selector").error(
            "[SUBREDDIT] DB write failed (table=subreddit_candidates, sub=%s): %s",
            sub, exc,
        )
        raise


def select_subreddits(
    markets: Sequence[KalshiMarket],
    source_stats=None,   # SourceStats instance or None
    db_path: Path = None,  # Path to paper_trades.db for Tier 3 candidate probing
) -> list[str]:
    """
    Return the subreddit list for the current poll cycle.

    Algorithm:
      Tier 1: REDDIT_CORE_SUBREDDITS (always included, never suppressed).
      Tier 2: Topic subs whose keywords match active market titles, sorted by
              signal rate (best first). Zero-signal subs are dropped.
      Tier 3: Candidate probes from subreddit_candidates DB (fills remaining
              capacity, up to SUBREDDIT_PROBE_SLOTS slots).

    Falls back to REDDIT_CORE_SUBREDDITS only when markets is empty.
    source_stats: optional SourceStats instance for quality-aware filtering.
    db_path: optional Path to paper_trades.db for Tier 3 candidate loading.
    """
    selected, skipped_disabled = filter_disabled_subreddits(REDDIT_CORE_SUBREDDITS)
    seen: set[str] = set(selected)

    if not markets:
        if skipped_disabled:
            logging.getLogger("subreddit_selector").debug(
                "[SUBREDDIT] Skipped disabled sources: %s",
                ", ".join(f"r/{sub}" for sub in skipped_disabled),
            )
        return selected

    active = _active_topics(markets)

    # Tier 2: topic-matched subreddits, quality-sorted and suppression-filtered
    suppressed_log = []
    for topic, subs in REDDIT_SUBREDDIT_TOPIC_MAP.items():
        if topic not in active:
            continue

        # Sort topic subs by signal rate: best quality first.
        # Subreddits with insufficient data (< MIN_POSTS) get ranking_score=1.0
        # and sort above known-bad ones (ranking_score < 0.5%).
        if source_stats is not None:
            ordered = sorted(
                subs,
                key=lambda s: source_stats.ranking_score("r/" + s),
                reverse=True,
            )
        else:
            ordered = subs

        for sub in ordered:
            if _is_disabled_reddit_source(sub):
                skipped_disabled.append(sub)
                continue
            if source_stats is not None and source_stats.is_suppressed("r/" + sub):
                suppressed_log.append(sub)
                continue
            if sub not in seen and len(selected) < REDDIT_MAX_SUBREDDITS:
                selected.append(sub)
                seen.add(sub)

    if suppressed_log:
        logging.getLogger("subreddit_selector").debug(
            "[SUBREDDIT] Suppressed (zero-signal): %s", ", ".join(suppressed_log)
        )

    # Tier 3: candidate probes fill remaining capacity
    if db_path is not None and len(selected) < REDDIT_MAX_SUBREDDITS:
        probes = _load_probe_candidates(
            db_path,
            limit=SUBREDDIT_PROBE_SLOTS,
            cooldown_secs=SUBREDDIT_PROBE_COOLDOWN_SECS,
        )
        probe_added = []
        for sub in probes:
            if _is_disabled_reddit_source(sub):
                skipped_disabled.append(sub)
                continue
            if source_stats is not None and source_stats.is_suppressed("r/" + sub):
                _mark_candidate_suppressed(db_path, sub)
                continue
            if sub not in seen and len(selected) < REDDIT_MAX_SUBREDDITS:
                selected.append(sub)
                seen.add(sub)
                probe_added.append(sub)
                _update_probe_ts(db_path, sub)

        if probe_added:
            logging.getLogger("subreddit_selector").debug(
                "[SUBREDDIT] Probing candidates: %s", ", ".join(probe_added)
            )

    if skipped_disabled:
        logging.getLogger("subreddit_selector").debug(
            "[SUBREDDIT] Skipped disabled sources: %s",
            ", ".join(f"r/{sub}" for sub in skipped_disabled),
        )

    return selected
