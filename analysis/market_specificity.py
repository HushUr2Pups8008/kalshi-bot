"""
Market-specificity scoring (ROADMAP P3.2).

Purpose
-------
Distinguish *broadly-scoped* markets (e.g., "Will Iran agree to a peace
deal this month?") from *narrowly-scoped* markets (e.g., "Will the IAEA
issue a formal censure of Iran by 2026-05-15?"). The P0.3 verdict
identified market-scope ceiling as a driver of universal LLM anchoring;
P3.1 corroborated. A per-market specificity score gives downstream
reporting and (future) prioritization a signal to rank on.

Constraint
----------
**Pure function. No I/O, no side effects, no behavior change.**
P3.2 adds a diagnostic field only — it does *not* filter, prioritize,
or otherwise alter matching behavior. The score's post-hoc validation
path is to correlate it against LLM anchor rate (extension of
``scripts/flag_outcome_correlation.py``). If high-specificity markets
show a lower anchor rate than low-specificity markets with meaningful
n and non-overlapping CIs, the score is earning its keep.

Score definition
----------------
A float in ``[0.0, 1.0]`` (1.0 = most specific), computed as a weighted
sum of six pure features over the market's ``title``, ``subtitle``
(resolution criteria), ``close_time``, and ``ticker``:

1. **Resolution-criteria token count** — longer conditions ≈ more
   specific. Normalized to 30 tokens = 1.0. Weight 0.20.
2. **Specific-verb presence** — curated list of action verbs
   (``sign``, ``ratify``, ``certify``, ``issue``, …) signalling
   concrete named events. Any hit = 1.0, else 0.0. Weight 0.20.
3. **Numeric-threshold presence** — regex for percentages, dollar
   figures, explicit dates, ordinals, years, times. Any hit = 1.0.
   Weight 0.15.
4. **Named-entity density** — fraction of tokens matching
   ``_GEO_NAMED_ENTITIES`` (capped at 3 hits ⇒ 1.0). Weight 0.15.
5. **Days-to-close proximity** — ``1 - min(1, days/90)``. Past/unknown
   close handled explicitly. Weight 0.15.
6. **Ticker specificity** — regex for date-encoded ticker segments
   (e.g., ``-26MAY01``, ``-B260501``). Any hit = 1.0. Weight 0.15.

Weights sum to 1.0 and are a starting point — validate post-hoc and
retune from data.

Rationale for features NOT used
-------------------------------
- **Market price** (``yes_price``, ``yes_prob``) is deliberately
  excluded: price is downstream of specificity (specific events tend
  toward 0 or 100 cents over time), so using it here would be
  circular. We want to use specificity to *predict* behavior, not be
  derived from it.
- **Volume / open interest** are downstream of market age and
  popularity, not specificity. Excluded for the same reason.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from analysis.geo_entities import GEO_NAMED_ENTITIES

# Self-contained: market_matcher imports compute_specificity_score, so this
# module must not import from market_matcher (would create a cycle at import
# time). The shared named-entity roster now lives in analysis/geo_entities (a
# leaf module both consumers import — no cycle), replacing the old hand-copied
# "snapshot" that could silently drift from the matcher's set. The
# _days_to_close helper below remains a deliberate local copy.

# Canonical geo named-entity roster + specificity-only institutional tokens.
# The extras (iaea, un, scotus, ...) are kept OUT of the shared set because
# adding them to the matcher's gate set could change gate behavior; here they
# only narrow the named-entity-density sub-score.
_GEO_NAMED_ENTITIES = GEO_NAMED_ENTITIES | frozenset({
    "iaea", "un", "scotus", "fbi", "cia", "opec", "who",
})


# Local copy of analysis/market_matcher._days_to_close so this module
# is self-contained. Returns the number of days until close as a float,
# or None if the string cannot be parsed.
def _days_to_close(close_time_str: str) -> Optional[float]:
    if not close_time_str:
        return None
    try:
        from dateutil import parser as dp, tz as dtz
        _TZ = {
            "EST": dtz.tzoffset("EST", -5 * 3600),
            "EDT": dtz.tzoffset("EDT", -4 * 3600),
            "CST": dtz.tzoffset("CST", -6 * 3600),
            "CDT": dtz.tzoffset("CDT", -5 * 3600),
            "MST": dtz.tzoffset("MST", -7 * 3600),
            "MDT": dtz.tzoffset("MDT", -6 * 3600),
            "PST": dtz.tzoffset("PST", -8 * 3600),
            "PDT": dtz.tzoffset("PDT", -7 * 3600),
        }
        dt = dp.parse(close_time_str, tzinfos=_TZ)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86_400
    except Exception:
        return None


# Action verbs that signal a concrete, nameable event. Presence in the
# title/criteria is a strong specificity indicator. Non-exhaustive --
# curated from geopolitical/political market language observed in the
# Kalshi catalogue. Include morphological variants (tense, pluralization)
# since the tokenizer is simple.
_SPECIFIC_VERBS = frozenset({
    # Treaties / agreements
    "sign", "signs", "signed", "signing",
    "ratify", "ratifies", "ratified", "ratifying",
    # Formal announcements / issuances
    "announce", "announces", "announced", "announcing",
    "certify", "certifies", "certified",
    "issue", "issues", "issued", "issuing",
    "confirm", "confirms", "confirmed",
    # Appointments / removals
    "appoint", "appoints", "appointed",
    "nominate", "nominates", "nominated",
    "resign", "resigns", "resigned",
    "impeach", "impeaches", "impeached",
    # Military / physical actions
    "strike", "strikes", "struck", "striking",
    "launch", "launches", "launched", "launching",
    "detonate", "detonates", "detonated",
    "breach", "breaches", "breached",
    "invade", "invades", "invaded",
    # Diplomatic actions
    "recall", "recalls", "recalled",
    "expel", "expels", "expelled",
    "suspend", "suspends", "suspended",
    "withdraw", "withdraws", "withdrew", "withdrawn",
    # Sanctions / tariffs
    "impose", "imposes", "imposed",
    "lift", "lifts", "lifted",
    # Legislation / executive
    "pass", "passes", "passed", "passing",
    "veto", "vetoes", "vetoed",
    "invoke", "invokes", "invoked",
})

# Regex for numeric/temporal thresholds that concretize a resolution
# criterion. Case-insensitive.
_NUMERIC_THRESHOLD_PATTERN = re.compile(
    r"(?:"
    r"\b\d+\s*%"                                             # 5%, 50 %
    r"|\$[\d,]+(?:\.\d+)?"                                   # $100, $1,000.50
    r"|\b[\d,]+\s+(?:dollars|bps|basis\s+points)\b"          # 100 dollars
    r"|\b\d+(?:st|nd|rd|th)\b"                               # 15th
    r"|\b(?:19|20)\d{2}\b"                                   # year 2024-2099
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\s+\d{1,2}\b"                                    # May 15 / January 3
    r"|\b\d{1,2}\s*(?:am|pm)\b"                              # 3pm
    r"|\b\d+\s*(?:million|billion|trillion)\b"               # 5 billion
    r")",
    re.IGNORECASE,
)

# Regex for Kalshi ticker segments that encode a date (their convention
# signals specificity). Matches fragments like:
#   -26MAY01, -26-APR24, -B260501, -25-26-APR24
_TICKER_DATE_PATTERN = re.compile(
    r"(?:"
    r"-\d{2}[A-Z]{3}\d{2}"                                   # -26MAY01
    r"|-\d{2}-[A-Z]{3}\d{2,4}"                               # -26-APR24
    r"|-B\d{6}"                                              # -B260501
    r"|-\d{2}-\d{2}-[A-Z]{3}\d{2}"                           # -25-26-APR24
    r")",
    re.IGNORECASE,
)


_TOKEN_COUNT_CAP = 30  # token count at which sub-score saturates to 1.0
_NAMED_ENTITY_CAP = 3  # named-entity hits at which sub-score saturates
_DAYS_TO_CLOSE_CAP = 90  # days-out at which proximity sub-score hits 0

_WEIGHTS: dict[str, float] = {
    "token_count":           0.20,
    "specific_verb":         0.20,
    "numeric_threshold":     0.15,
    "named_entity_density":  0.15,
    "days_to_close":         0.15,
    "ticker_specificity":    0.15,
}


def _simple_tokens(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"\b\w+\b", text.lower())


def _token_count_score(resolution_text: str) -> float:
    tokens = _simple_tokens(resolution_text)
    return min(1.0, len(tokens) / _TOKEN_COUNT_CAP)


def _specific_verb_score(combined_text: str) -> float:
    tokens = set(_simple_tokens(combined_text))
    return 1.0 if tokens & _SPECIFIC_VERBS else 0.0


def _numeric_threshold_score(combined_text: str) -> float:
    if not combined_text:
        return 0.0
    return 1.0 if _NUMERIC_THRESHOLD_PATTERN.search(combined_text) else 0.0


def _named_entity_density_score(combined_text: str) -> float:
    tokens = _simple_tokens(combined_text)
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in _GEO_NAMED_ENTITIES)
    return min(1.0, hits / _NAMED_ENTITY_CAP)


def _days_to_close_score(close_time: str) -> float:
    days = _days_to_close(close_time or "")
    if days is None:
        return 0.5  # unparseable / missing: neutral
    if days <= 0:
        return 1.0  # already-resolved / past-close
    return max(0.0, 1.0 - min(1.0, days / _DAYS_TO_CLOSE_CAP))


def _ticker_specificity_score(ticker: str) -> float:
    if not ticker:
        return 0.0
    return 1.0 if _TICKER_DATE_PATTERN.search(ticker) else 0.0


def specificity_components(market: Any) -> dict[str, float]:
    """
    Return the per-feature sub-scores that feed ``compute_specificity_score``.

    Useful for debugging, report columns, and unit tests that want to
    assert on a single component in isolation. All sub-scores live in
    ``[0.0, 1.0]``.
    """
    title = str(getattr(market, "title", "") or "")
    subtitle = str(getattr(market, "subtitle", "") or "")
    ticker = str(getattr(market, "ticker", "") or "")
    close_time = str(getattr(market, "close_time", "") or "")

    resolution_text = (subtitle or title).strip()
    combined_text = f"{title} {subtitle}".strip()

    return {
        "token_count":           round(_token_count_score(resolution_text), 4),
        "specific_verb":         round(_specific_verb_score(combined_text), 4),
        "numeric_threshold":     round(_numeric_threshold_score(combined_text), 4),
        "named_entity_density":  round(_named_entity_density_score(combined_text), 4),
        "days_to_close":         round(_days_to_close_score(close_time), 4),
        "ticker_specificity":    round(_ticker_specificity_score(ticker), 4),
    }


def compute_specificity_score(market: Any) -> float:
    """
    Return a market-specificity score in ``[0.0, 1.0]`` (1.0 = most specific).

    ``market`` must expose the KalshiMarket attributes ``title``,
    ``subtitle``, ``ticker``, ``close_time``. Missing/empty values are
    tolerated (treated as no signal for that feature).
    """
    components = specificity_components(market)
    total = sum(components[k] * _WEIGHTS[k] for k in _WEIGHTS)
    # Sum of weights is 1.0 by construction, so the result is already
    # in [0, 1]; round for consistency with the component scale.
    return round(total, 4)
