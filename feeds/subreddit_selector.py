"""
Adaptive subreddit selector.

Selects which subreddits to poll each Reddit cycle based on active Kalshi
market titles. Returns REDDIT_CORE_SUBREDDITS plus tier-2 topic subreddits
whose keywords overlap with currently open market titles, capped at
REDDIT_MAX_SUBREDDITS.

Pure functions -- no async, no state, no side effects.
"""

import re
from typing import Sequence

from config import (
    REDDIT_CORE_SUBREDDITS,
    REDDIT_MAX_SUBREDDITS,
    REDDIT_SUBREDDIT_TOPIC_MAP,
    REDDIT_TOPIC_KEYWORDS,
)
from kalshi import KalshiMarket


_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "will", "would",
    "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "and", "or", "but", "not", "this", "that", "it", "if", "as",
    "yes", "no", "over", "under", "win", "wins", "new", "more",
    "how", "what", "when", "who", "which", "its", "has", "have",
    "can", "get", "per", "any", "all",
})


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


def select_subreddits(markets: Sequence[KalshiMarket]) -> list[str]:
    """
    Return the subreddit list for the current poll cycle.

    Algorithm:
      1. Start with REDDIT_CORE_SUBREDDITS (always included, order preserved).
      2. Determine which topics are active via keyword match on market titles.
      3. For each active topic (in REDDIT_SUBREDDIT_TOPIC_MAP insertion order),
         append its subreddits, skipping duplicates already in the list.
      4. Stop when REDDIT_MAX_SUBREDDITS is reached.

    Falls back to REDDIT_CORE_SUBREDDITS only when markets is empty.
    """
    selected: list[str] = list(REDDIT_CORE_SUBREDDITS)
    seen: set[str] = set(selected)

    if not markets:
        return selected

    active = _active_topics(markets)

    for topic, subs in REDDIT_SUBREDDIT_TOPIC_MAP.items():
        if topic not in active:
            continue
        for sub in subs:
            if sub not in seen and len(selected) < REDDIT_MAX_SUBREDDITS:
                selected.append(sub)
                seen.add(sub)

    return selected
