"""
Market matcher: finds Kalshi markets relevant to a news item.

Scoring strategy:
  1. Tokenize both the news headline+body and the market title+subtitle.
  2. Compute weighted Jaccard overlap between the token sets.
  3. Boost score for markets closing soon (more liquid / time-sensitive).
  4. Require a minimum similarity score (lower during paper trading).

The market list is cached and refreshed every MARKET_CACHE_TTL_SECONDS.
"""

import asyncio
import math
import os
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, SimpleQueue
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from config import cfg, MARKET_CACHE_TTL_SECONDS, MAX_MARKET_DAYS_TO_EXPIRY
from config import PAPER_MIN_MATCH_SCORE, PAPER_MAX_CANDIDATES, MARKET_SERIES_BLOCKLIST_PREFIXES
from config import ENABLE_LOW_QUALITY_MATCH_SUPPRESSION, ENABLE_MATCH_SUPPRESSION_DEBUG
from analysis.geo_entities import GEO_NAMED_ENTITIES
from analysis.market_specificity import compute_specificity_score
from analysis.match_feedback import is_market_defining_token
from analysis.regime_classifier import compute_regime_weights
from feeds import NewsItem
from kalshi import KalshiMarket
from kalshi.rest_client import KalshiRestClient
from kalshi.series_metadata import KalshiSeriesMetadata, normalize_series_list
from utils.logger import get_logger, trade_log, write_trade_log_async
from utils.lifecycle import build_lifecycle_id, settlement_source_match
from utils.market_horizon import evaluate_market_horizon

log = get_logger("market_matcher")


async def _write_match_telemetry(
    enabled: bool,
    writer: Any,
    **payload: Any,
) -> None:
    if enabled:
        await write_trade_log_async(writer, **payload)


def _diagnostic_venue_for_market(market: KalshiMarket, market_prefix: str) -> str:
    raw_venue = getattr(market, "venue", None)
    if hasattr(raw_venue, "value"):
        raw_venue = raw_venue.value
    if raw_venue is not None:
        normalized = str(raw_venue).strip().lower()
        if normalized:
            return normalized
    if market_prefix.startswith("polymarket_") or market_prefix.startswith("polymarket:"):
        return "polymarket"
    return "kalshi"


def _contract_context_tokens(market: Any) -> set[str]:
    chunks: list[str] = []
    for attr in ("rules_primary", "rules_secondary", "resolution_source", "contract_terms_url"):
        value = getattr(market, attr, "")
        if value:
            chunks.append(str(value))
    for source in getattr(market, "settlement_sources", ()) or ():
        if isinstance(source, dict):
            values = (
                source.get("label"),
                source.get("name"),
                source.get("source"),
                source.get("title"),
                source.get("domain"),
                source.get("url"),
            )
        else:
            values = (
                getattr(source, "label", ""),
                getattr(source, "domain", ""),
                getattr(source, "url", ""),
            )
        chunks.extend(str(value) for value in values if value)
    return _tokenize(" ".join(chunks))


# Live thresholds (tighter — only trade high-confidence matches)
LIVE_MIN_MATCH_SCORE = 0.06
LIVE_MAX_CANDIDATES  = 5


# ── Text tokeniser ────────────────────────────────────────────────────────────

_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "on",
    "at", "by", "for", "with", "from", "and", "or", "but", "not", "no",
    "this", "that", "it", "its", "their", "there", "if", "as", "about",
    "after", "before", "than", "when", "who", "which", "what", "how",
    # PROFIT-MATCH-DYNAMIC (2026-05-24): universal quantifier escaped previous
    # filtering. Market titles like "Will ANY member of Trump's Cabinet leave"
    # tokenized "any" as content, then matched the headline's "any" — bridging
    # KXCABLEAVE to any Trump-mentioning headline via [any, trump]. Filter
    # both forms.
    "any", "anyone", "anything",
    # Sports betting noise words — prevent "Over 224.5" matching "war is over"
    "over", "under", "yes", "no", "scored", "points", "goals", "rebounds",
    "assists", "win", "wins", "loss", "losses", "vs", "per", "total",
}

_GEOPOLITICAL_BOOST = {
    # Countries / regions
    "ukraine", "russia", "china", "taiwan", "iran", "israel", "gaza",
    "korea", "north korea", "nato", "europe", "usa", "united states",
    "pakistan", "india", "afghanistan", "syria", "iraq", "saudi",
    # Political roles / actions
    "president", "prime minister", "election", "military", "war", "ceasefire",
    "nuclear", "sanctions", "coup", "treaty", "summit", "un", "troops",
    "attack", "invasion", "strike", "withdraw", "deploy", "negotiate",
    # Trade & economic policy
    "tariff", "tariffs", "trade", "import", "export", "embargo", "customs",
    "commerce", "reciprocal", "duty", "duties", "liberation",
    # Foreign policy
    "diplomatic", "ambassador", "embassy", "alliance", "pact", "foreign",
    "bilateral", "multilateral", "sovereignty", "extradition",
    # Domestic policy (US-focused)
    "executive order", "shutdown", "impeach", "impeachment", "cabinet",
    "congress", "senate", "legislation", "regulation", "nomination",
    "confirmation", "indictment", "pardon", "veto",
}

# Named geo entities specific enough that a single token overlap is meaningful.
# Used in the tiered headline gate and geo-coherence edge checks.
# Generic conflict words (war, attack, bank, people) are NOT included here --
# those require 2+ overlaps to pass the gate.
# Single source of truth — analysis/geo_entities. Kept as an alias so the
# figure roster cannot drift against market_specificity's copy (rot-surface fix).
_GEO_NAMED_ENTITIES = GEO_NAMED_ENTITIES

_PRE_LLM_GATE_STOPWORDS = frozenset(
    {
        "during",
        "say",
        "says",
        "said",
        "amid",
        "after",
        "before",
        "latest",
        "live",
        "update",
        "updates",
        "report",
        "reports",
    }
)

_PRE_LLM_GATE_GENERIC_TOKENS = frozenset(
    {
        "official",
        "officials",
        "leader",
        "leaders",
        "president",
        "government",
        "minister",
        "ministers",
    }
)


def _tokenize(text: str) -> set[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text)
    tokens = set(text.split())
    return tokens - _STOP_WORDS


def _meaningful_tokens(tokens: set[str]) -> set[str]:
    return {t for t in tokens if len(t) >= 3 and not t.isdigit()}


def _similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union        = tokens_a | tokens_b
    if not union:
        return 0.0
    boost = sum(1.5 for t in intersection if t in _GEOPOLITICAL_BOOST)
    return min(1.0, len(intersection) / len(union) + boost * 0.03)


def _structure_quality_flags(
    *,
    overlap: set[str],
    geo_overlap: set[str],
    generic_overlap: set[str],
    headline_meaningful: set[str],
    market_title_meaningful: set[str],
) -> list[str]:
    flags: list[str] = []
    overlap_ratio = len(overlap) / max(1, min(len(headline_meaningful), len(market_title_meaningful)))
    if overlap_ratio < 0.2:
        flags.append("low_token_overlap")
    if len(geo_overlap) == 1 and len(generic_overlap) == 0:
        flags.append("single_named_entity_only")
    if len(overlap) <= 1:
        flags.append("minimal_overlap")
    return flags


def _weak_match_penalty_multiplier(flags: set[str]) -> float:
    """Apply a lightweight penalty to structurally weak matches before thresholding."""
    if "single_named_entity_only" in flags and "minimal_overlap" in flags:
        return 0.75
    if "single_named_entity_only" in flags:
        return 0.9
    return 1.0


def _combined_token_downweight(
    overlap: set[str],
    ticker_prefix: str,
    weights: dict[str, dict[str, Any]],
) -> float:
    """Return composition-aware matcher-feedback multiplier.

    One generic downweighted bridge token should not dominate a larger overlap
    that also contains full-weight supporting tokens. Missing or malformed
    entries count as 1.0.
    """
    if not overlap or not weights:
        return 1.0
    values: list[float] = []
    for token in overlap:
        # A market's own ticker-defining token is never a noise bridge; keep it
        # at full weight so the self-poisoning feedback loop cannot floor the
        # correct market out of the candidate funnel.
        if is_market_defining_token(token, ticker_prefix):
            values.append(1.0)
            continue
        raw = weights.get(f"{ticker_prefix}:{token}", {}).get("weight", 1.0)
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            values.append(1.0)
    return sum(values) / len(values) if values else 1.0


def _token_downweight_details(
    overlap: set[str],
    ticker_prefix: str,
    weights: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return the composed downweight plus per-token audit details."""
    token_weights: dict[str, dict[str, Any]] = {}
    for token in sorted(overlap):
        # Mirror the guard in _combined_token_downweight: a market's own
        # ticker-defining token is reported at full weight, never downweighted.
        if is_market_defining_token(token, ticker_prefix):
            token_weights[token] = {"weight": 1.0, "status": "defining"}
            continue
        key = f"{ticker_prefix}:{token}"
        entry = weights.get(key, {}) if weights else {}
        raw = entry.get("weight", 1.0) if isinstance(entry, dict) else 1.0
        try:
            weight = float(raw)
        except (TypeError, ValueError):
            weight = 1.0

        if isinstance(entry, dict) and entry.get("pinned") is True:
            status = "pinned"
        elif isinstance(entry, dict) and entry.get("_seed_status") == "provisional":
            status = "provisional"
        elif isinstance(entry, dict) and entry:
            status = "automatic"
        else:
            status = "default"

        token_weights[token] = {
            "weight": weight,
            "status": status,
        }

    return {
        "tokens": sorted(overlap),
        "token_weights": token_weights,
        "composition_rule": "mean",
        "final_multiplier": _combined_token_downweight(overlap, ticker_prefix, weights),
    }


def _match_quality_flags(
    *,
    overlap: set[str],
    geo_overlap: set[str],
    generic_overlap: set[str],
    headline_meaningful: set[str],
    market_title_meaningful: set[str],
    score: float,
    min_score: float,
) -> list[str]:
    flags = _structure_quality_flags(
        overlap=overlap,
        geo_overlap=geo_overlap,
        generic_overlap=generic_overlap,
        headline_meaningful=headline_meaningful,
        market_title_meaningful=market_title_meaningful,
    )
    if score <= min_score + 0.02:
        flags.append("near_threshold_score")
    return flags


def _normalize_pre_llm_gate_tokens(tokens: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tokens:
        token = str(raw or "").strip().lower()
        if not token or token in seen:
            continue
        normalized.append(token)
        seen.add(token)
    return normalized


def _pre_llm_gate_reason(
    *,
    semantic_overlap_count: int,
    semantic_overlap_ratio: float,
    filtered_stopword_count: int,
    filtered_generic_count: int,
) -> str | None:
    if semantic_overlap_count >= 2 and semantic_overlap_ratio > 0.25:
        return None
    if semantic_overlap_count == 0:
        if filtered_stopword_count and filtered_generic_count:
            return "low_information_overlap"
        if filtered_stopword_count:
            return "stopword_only_overlap"
        if filtered_generic_count:
            return "generic_only_overlap"
    if semantic_overlap_count < 2:
        return "insufficient_semantic_overlap"
    return "low_semantic_overlap_ratio"


def _compute_pre_llm_match_meta(headline: str, market_title: str, matched_tokens: list[str]) -> dict[str, Any]:
    normalized_tokens = _normalize_pre_llm_gate_tokens(matched_tokens)
    filtered_stopword_count = sum(1 for token in normalized_tokens if token in _PRE_LLM_GATE_STOPWORDS)
    candidate_tokens = [token for token in normalized_tokens if token not in _PRE_LLM_GATE_STOPWORDS]
    filtered_generic_count = sum(1 for token in candidate_tokens if token in _PRE_LLM_GATE_GENERIC_TOKENS)
    semantic_tokens = [token for token in candidate_tokens if token not in _PRE_LLM_GATE_GENERIC_TOKENS]
    headline_token_count = len(_meaningful_tokens(_tokenize(headline)))
    market_token_count = len(_meaningful_tokens(_tokenize(market_title)))
    semantic_overlap_count = len(semantic_tokens)
    semantic_overlap_ratio = semantic_overlap_count / max(1, min(headline_token_count, market_token_count))
    pre_llm_quality_pass = (
        semantic_overlap_count >= 2
        and semantic_overlap_ratio > 0.25
    )
    semantic_named_entity_count = sum(1 for token in semantic_tokens if token in _GEO_NAMED_ENTITIES)
    gate_reason = _pre_llm_gate_reason(
        semantic_overlap_count=semantic_overlap_count,
        semantic_overlap_ratio=semantic_overlap_ratio,
        filtered_stopword_count=filtered_stopword_count,
        filtered_generic_count=filtered_generic_count,
    )
    return {
        "pre_llm_semantic_overlap_tokens": semantic_tokens,
        "pre_llm_semantic_overlap_count": semantic_overlap_count,
        "pre_llm_semantic_overlap_ratio": semantic_overlap_ratio,
        "pre_llm_quality_pass": pre_llm_quality_pass,
        "pre_llm_gate_reason": gate_reason,
        "pre_llm_headline_token_count": headline_token_count,
        "pre_llm_market_token_count": market_token_count,
        "pre_llm_filtered_stopword_count": filtered_stopword_count,
        "pre_llm_filtered_generic_count": filtered_generic_count,
        "pre_llm_semantic_token_types": {
            "named_entity": semantic_named_entity_count,
            "generic": semantic_overlap_count - semantic_named_entity_count,
        },
    }


def _days_to_close(
    close_time_str: str,
    *,
    now: datetime | None = None,
) -> Optional[float]:
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
        reference_time = now or datetime.now(timezone.utc)
        return (dt - reference_time).total_seconds() / 86_400
    except Exception:
        return None


# ── Series discovery ──────────────────────────────────────────────────────────
#
# Kalshi's market catalogue has shifted away from organised geo series
# (e.g. KXUKR, KXINTL) toward thousands of one-off series per topic.
# We discover political/geo series by keyword-matching their titles, then
# fetch markets only for matched series — avoiding 10k+ sports parlays.
#
# The keywords here cast a wide net; the _GEOPOLITICAL_BOOST check inside
# find_candidates() acts as the final precision gate at match time.

_GEO_SERIES_KEYWORDS = frozenset({
    # Countries / regions
    "russia", "ukraine", "china", "taiwan", "iran", "israel", "gaza",
    "korea", "nato", "pakistan", "india", "japan", "turkey", "saudi",
    "europe", "european", "syria", "iraq", "afghanistan", "venezuela",
    "cuba", "mexico", "canada", "france", "germany", "britain",
    "brazil", "colombia", "philippines", "egypt", "libya", "yemen",
    "somalia", "sudan", "ethiopia",
    # Political leaders
    "zelensky", "putin", "trump", "vance", "rubio",
    # Political roles / institutions
    "president", "senator", "congress", "senate", "parliament",
    "governor", "chancellor", "minister", "supreme court",
    # US politics
    "republican", "democrat", "cabinet", "impeach",
    "executive", "legislation",
    # US domestic policy
    "budget", "regulation", "nomination", "confirmation",
    "department of",           # cabinet dept titles
    "doge",                    # Dept of Government Efficiency
    "shutdown", "debt ceiling", "indictment", "pardon",
    "executive order", "national emergency",
    # Trade & economic policy
    "tariff", "tariffs", "trade", "import", "export",
    "embargo", "customs", "commerce", "duty", "reciprocal",
    "liberation day",          # Trump tariff branding
    "trade war", "trade deal", "trade agreement",
    # Foreign policy
    "diplomatic", "foreign policy", "united nations",
    "ambassador", "embassy", "alliance", "bilateral",
    "sovereignty", "extradition", "deportation",
    # Events / actions
    "election", "ceasefire", "invasion", "military",
    "nuclear", "sanctions", "summit", "treaty", "coup",
    # Policy
    "healthcare", "immigration", "climate", "border",
    # Economic / financial
    "inflation", "gdp", "federal reserve", "recession",
    "interest rate", "treasury", "deficit",
    # AI / tech policy
    "ai safety", "ai regulation", "artificial intelligence",
})


def _is_geo_series(series: dict) -> bool:
    """Return True if the series title contains a geo/political keyword."""
    title = (series.get("title") or series.get("ticker") or "").lower()
    return any(kw in title for kw in _GEO_SERIES_KEYWORDS)


# ── Market cache ──────────────────────────────────────────────────────────────

_REFRESH_DEBOUNCE_SECONDS = 60  # min seconds between back-to-back refreshes
_REFRESH_FAILURE_BACKOFF_SECONDS = 60
_REFRESH_FAILURE_MAX_BACKOFF_SECONDS = 300
_TEST_MARKET_TICKER_PREFIX = "KXTEST"
_GEO_MARKET_WARMUP_WORKERS = 3

# Pagination safety caps for _fetch_all_markets. Kalshi's /markets response
# can exceed 200k rows (predominantly sports MVE markets) and is not ordered
# in a way callers can rely on; never depend on response ordering — if a
# specific family/series matters, fetch it directly via series_ticker= or
# prove complete pagination reached it.
_FETCH_MAX_PAGES = 1000
_FETCH_MAX_ROWS = 200_000
_FETCH_TIMEOUT_SECONDS = 60.0

# ── Universe-shape watcher thresholds (PROFIT-UNIVERSE-001) ───────────────────
# Aggregate population-shape drift detection. Complementary to the per-family
# coverage WARN below — this catches the *cumulative* failure mode the
# 2026-05-12 zero-trade incident demonstrated, where the bot's effective
# universe became sports-only without any operator-visible signal. Spec lives
# at docs/superpowers/specs/2026-05-24-universe-shape-watcher-design.md.
_UNIVERSE_WATCH_MIN_G4_ELIGIBLE = int(
    os.getenv("UNIVERSE_WATCH_MIN_G4_ELIGIBLE", "5")
)
_UNIVERSE_WATCH_MIN_PRIOR_COVERED_RATIO = float(
    os.getenv("UNIVERSE_WATCH_MIN_PRIOR_COVERED_RATIO", "0.10")
)
_UNIVERSE_WATCH_MAX_SPORTS_SHARE = float(
    os.getenv("UNIVERSE_WATCH_MAX_SPORTS_SHARE", "0.95")
)
_UNIVERSE_WATCH_MIN_EXPECTED_PRESENT_RATIO = float(
    os.getenv("UNIVERSE_WATCH_MIN_EXPECTED_PRESENT_RATIO", "0.50")
)

# Operator-mandated policy families whose presence is asserted at refresh
# time. These are the geo/policy/macro families with priors registered in
# analysis/regime_classifier._SERIES_PRIORS, plus a few operator-named
# series. If any of these are returned by Kalshi but missed by the bot,
# we want an operator-visible warning rather than silent zero-trade.
_EXPECTED_POLICY_SERIES: tuple[str, ...] = (
    "KXCPIYOY",
    "KXCPICOREYOY",
    "KXMOCTRUMP25",
    "KXFISAEXTEND",
    "KXTRUMPACT",
    "KXAPRPOTUS",
    "KXPOLLPOTUS",
    "KXTRUMPSAY",
    "KXSBUDGETRES",
    "KXEFFTARIFF",
    "KXVOTESAVEAMERICA",
)


def _is_excluded_test_market(market: KalshiMarket) -> bool:
    """Return True for explicit test-market tickers that must never enter shared caches."""
    return market.ticker.upper().startswith(_TEST_MARKET_TICKER_PREFIX)


def _attach_regime_weights(market: KalshiMarket) -> KalshiMarket:
    market.regime_weights = compute_regime_weights(market)
    return market


def _market_belongs_to_series(market: KalshiMarket, series_prefix: str) -> bool:
    """Return True if `market` belongs to `series_prefix`.

    Matches via the dedicated `series_ticker` attribute first (exact or
    `prefix-...` form), then falls back to the market ticker itself
    (`prefix-...` form). Mirrors the precedence used by
    `analysis.regime_classifier._series_prior`, which treats `series_ticker`
    as authoritative when present and only consults the market ticker as a
    fallback.
    """
    prefix = series_prefix.upper()
    series_ticker = (getattr(market, "series_ticker", "") or "").upper()
    if series_ticker == prefix or series_ticker.startswith(prefix + "-"):
        return True
    ticker = (market.ticker or "").upper()
    return ticker.startswith(prefix + "-") or ticker == prefix


def _is_sports_family(series_ticker: str) -> bool:
    """Return True if the series ticker matches a known sports prefix.

    Uses `MARKET_SERIES_BLOCKLIST_PREFIXES` from config — the same list the
    matcher uses to drop sports series at intake. Centralizing the check
    here keeps the watcher's sports-share metric aligned with the
    blocklist's semantics.
    """
    upper = (series_ticker or "").upper()
    return any(upper.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES)


def _regime_confidence_of(market: KalshiMarket) -> float:
    """Compute regime_confidence for a market via compute_regime_weights.

    Mirrors the math in tasks/blend_task._regime_confidence so callers
    that need to predict G4 eligibility (rc >= 0.20) without invoking
    the full blender can do so cheaply. Returns 0.0 on any failure
    rather than raising — this helper is a diagnostic and must not
    affect intake flow.
    """
    try:
        weights = market.regime_weights or compute_regime_weights(market)
    except Exception:
        return 0.0
    if not weights:
        return 0.0
    try:
        entropy = -sum(
            w * math.log(w) for w in weights.values() if w > 0
        )
    except Exception:
        return 0.0
    max_entropy = math.log(3)
    if max_entropy <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - entropy / max_entropy))


def _emit_universe_shape_diagnostic(
    markets: list[KalshiMarket],
    *,
    n_series_discovered: int,
    geo_tickers_set: set[str],
) -> None:
    """Emit a single structured INFO line per cache refresh with universe-
    shape diagnostics, plus an operator-visible WARN on ALARM verdict.

    PROFIT-UNIVERSE-001 spec:
    docs/superpowers/specs/2026-05-24-universe-shape-watcher-design.md.

    This is the cumulative-drift signal the 2026-05-12 incident would
    have surfaced at hour 1 instead of day 13. Different signal from the
    per-family coverage WARN: that one flags individual missing series;
    this one flags aggregate population shape.

    Verdict ladder (first match wins):
      ALARM    g4_eligible == 0  OR  expected_present_ratio < min OR
               sports_share > max (with non-zero universe)
      DEGRADED g4_eligible < min  OR  prior_covered_ratio < min  OR
               any expected family missing
      NORMAL   otherwise

    Read-only — no Kalshi calls, no DB writes; pure function over the
    cache state already in memory.
    """
    geo_market_count = len(markets)
    prior_covered_count = sum(
        1 for m in markets if m.regime_weights
    )
    expected_in_catalog = [
        s for s in _EXPECTED_POLICY_SERIES if s in geo_tickers_set
    ]
    expected_present_in_cache = [
        s for s in expected_in_catalog
        if any(_market_belongs_to_series(m, s) for m in markets)
    ]
    expected_missing = [
        s for s in expected_in_catalog
        if s not in expected_present_in_cache
    ]
    sports_market_count = sum(
        1 for m in markets if _is_sports_family(
            getattr(m, "series_ticker", "") or m.ticker
        )
    )
    sports_share = (
        sports_market_count / geo_market_count if geo_market_count > 0 else 0.0
    )
    g4_eligible_count = sum(
        1 for m in markets if _regime_confidence_of(m) >= 0.20
    )

    expected_present_ratio = (
        len(expected_present_in_cache) / len(expected_in_catalog)
        if expected_in_catalog
        else 1.0
    )
    prior_covered_ratio = (
        prior_covered_count / geo_market_count if geo_market_count > 0 else 0.0
    )

    if (
        g4_eligible_count == 0
        or (
            expected_in_catalog
            and expected_present_ratio < _UNIVERSE_WATCH_MIN_EXPECTED_PRESENT_RATIO
        )
        or (
            sports_share > _UNIVERSE_WATCH_MAX_SPORTS_SHARE
            and geo_market_count > 0
        )
    ):
        verdict = "ALARM"
    elif (
        g4_eligible_count < _UNIVERSE_WATCH_MIN_G4_ELIGIBLE
        or prior_covered_ratio < _UNIVERSE_WATCH_MIN_PRIOR_COVERED_RATIO
        or expected_missing
    ):
        verdict = "DEGRADED"
    else:
        verdict = "NORMAL"

    log.info(
        "universe-shape: geo_markets=%d series=%d prior_covered=%d "
        "expected_present=%d/%d expected_missing=%d sports_share=%.2f "
        "g4_eligible=%d verdict=%s",
        geo_market_count, n_series_discovered, prior_covered_count,
        len(expected_present_in_cache), len(expected_in_catalog),
        len(expected_missing), sports_share, g4_eligible_count, verdict,
    )
    if verdict == "ALARM":
        log.warning(
            "universe-shape ALARM: bot's effective universe has lost "
            "tradeable shape. g4_eligible=%d (min=%d) expected_present=%d/%d "
            "sports_share=%.2f (max=%.2f) — investigate intake immediately.",
            g4_eligible_count, _UNIVERSE_WATCH_MIN_G4_ELIGIBLE,
            len(expected_present_in_cache), len(expected_in_catalog),
            sports_share, _UNIVERSE_WATCH_MAX_SPORTS_SHARE,
        )


def _warn_on_missing_expected_families(
    markets: list[KalshiMarket],
    *,
    geo_tickers_set: set[str],
    per_series_counts: dict[str, tuple[int, int]] | None = None,
) -> None:
    """Emit operator-visible warning if expected policy families are missing.

    A family is treated as "expected" only when (a) it is in
    `_EXPECTED_POLICY_SERIES` and (b) the series-discovery pass surfaced its
    ticker — i.e. Kalshi currently lists the series.

    `per_series_counts` maps series_ticker -> (raw_open_count,
    eligible_after_filter_count). When provided, the helper distinguishes
    three causes of "missing from cache" so operators don't see false-
    positive warnings for legitimate Kalshi-side conditions:

      1. raw_open_count == 0   — series advertised but has zero open markets
         (e.g. all finalized). LEGITIMATE; emit at DEBUG, not WARN.
      2. raw_open_count > 0 and eligible == 0 — open markets exist but all
         exceed MAX_MARKET_DAYS_TO_EXPIRY or fail test-market exclusion.
         LEGITIMATE downstream-filter; emit at DEBUG, not WARN.
      3. eligible > 0 and family still not in cache — true intake-path
         bug. WARN at operator-visible level.

    Without `per_series_counts` (backward-compat path), the function
    falls back to the original logic and warns on any missing family
    that's in the catalog.
    """
    expected_present_in_discovery = [
        s for s in _EXPECTED_POLICY_SERIES if s in geo_tickers_set
    ]
    if not expected_present_in_discovery:
        return

    truly_missing: list[str] = []
    series_empty: list[str] = []
    all_filtered: list[str] = []
    for prefix in expected_present_in_discovery:
        in_cache = any(_market_belongs_to_series(m, prefix) for m in markets)
        if in_cache:
            continue
        if per_series_counts is None:
            truly_missing.append(prefix)
            continue
        raw, eligible = per_series_counts.get(prefix, (0, 0))
        if raw == 0:
            series_empty.append(prefix)
        elif eligible == 0:
            all_filtered.append(prefix)
        else:
            truly_missing.append(prefix)

    if series_empty:
        log.debug(
            "Expected policy families with zero open markets (Kalshi-side, "
            "legitimate): %s",
            ",".join(series_empty),
        )
    if all_filtered:
        log.debug(
            "Expected policy families with open markets all excluded by "
            "downstream filters (MAX_MARKET_DAYS_TO_EXPIRY or test-market): "
            "%s",
            ",".join(all_filtered),
        )
    if truly_missing:
        log.warning(
            "Expected policy families absent from intake despite being in "
            "Kalshi series catalog AND having eligible open markets: %s. "
            "Series-targeted fetch path may be broken — investigate.",
            ",".join(truly_missing),
        )


class MarketCache:
    def __init__(
        self,
        rest_client: KalshiRestClient,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self._client          = rest_client
        self._markets:        list[KalshiMarket] = []
        self._last_fetch:     float = 0.0
        self._all_markets:    list[KalshiMarket] = []
        self._all_last_fetch: float = 0.0
        self._series_metadata_by_ticker: dict[str, KalshiSeriesMetadata] = {}
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock            = asyncio.Lock()
        self._all_lock        = asyncio.Lock()
        self._next_geo_refresh_after = 0.0
        self._geo_refresh_failures = 0

    def _horizon_time(self) -> datetime:
        """Return the refresh clock used for market-horizon admission."""

        now = self._now_provider()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("market cache clock must be timezone-aware")
        return now.astimezone(timezone.utc)

    async def get_markets(self) -> list[KalshiMarket]:
        async with self._lock:
            age = time.monotonic() - self._last_fetch
            if age > MARKET_CACHE_TTL_SECONDS or not self._markets:
                await self._refresh()
        return list(self._markets)

    def get_markets_snapshot(self) -> list[KalshiMarket]:
        """Return the current cached markets without refreshing.

        Shadow-only diagnostics use this so observability cannot trigger REST
        refreshes or contend with the live matcher path.
        """
        return list(self._markets)

    async def _refresh(self) -> None:
        now = time.monotonic()
        if now < self._next_geo_refresh_after:
            log.debug(
                "Market cache refresh backed off for %.0fs after %d failure(s)",
                self._next_geo_refresh_after - now,
                self._geo_refresh_failures,
            )
            return
        if now - self._last_fetch < _REFRESH_DEBOUNCE_SECONDS:
            log.debug("Market cache refresh debounced (last refresh %.0fs ago)", now - self._last_fetch)
            return
        loop = asyncio.get_running_loop()
        try:
            horizon_now = self._horizon_time()
            markets, n_series = await loop.run_in_executor(
                None, self._fetch_geo_markets, horizon_now,
            )
            self._markets    = markets
            self._last_fetch = time.monotonic()
            self._next_geo_refresh_after = 0.0
            self._geo_refresh_failures = 0
            log.info(
                "Market cache refreshed: %d geo markets from %d series",
                len(markets), n_series,
            )
        except Exception as exc:
            self._geo_refresh_failures += 1
            backoff_seconds = min(
                _REFRESH_FAILURE_BACKOFF_SECONDS * (2 ** (self._geo_refresh_failures - 1)),
                _REFRESH_FAILURE_MAX_BACKOFF_SECONDS,
            )
            self._next_geo_refresh_after = time.monotonic() + backoff_seconds
            log.error(
                "Market cache refresh failed: %s; retry in %.0fs",
                exc,
                backoff_seconds,
            )

    def _fetch_geo_market_pages(
        self,
        geo_tickers: list[str],
    ) -> list[tuple[list[KalshiMarket], Exception | None]]:
        """Fetch one open-market page per series without sharing sessions."""

        if not geo_tickers:
            return []

        reader_factory = getattr(type(self._client), "fork_public_market_reader", None)
        if not callable(reader_factory):
            # Lightweight test doubles and historical injected clients retain the
            # exact serial contract. Production KalshiRestClient takes the worker
            # path below and shares its request gate with normal REST calls.
            fetched = []
            for series_ticker in geo_tickers:
                try:
                    page, _ = self._client.get_markets(
                        status="open",
                        series_ticker=series_ticker,
                        limit=200,
                    )
                except Exception as exc:
                    fetched.append(([], exc))
                else:
                    fetched.append((page, None))
            return fetched

        jobs = SimpleQueue()
        for index, series_ticker in enumerate(geo_tickers):
            jobs.put((index, series_ticker))
        fetched: list[tuple[list[KalshiMarket], Exception | None] | None] = [None] * len(geo_tickers)

        def fetch_worker() -> None:
            reader = self._client.fork_public_market_reader()
            while True:
                try:
                    index, series_ticker = jobs.get_nowait()
                except Empty:
                    return
                try:
                    page, _ = reader.get_markets(
                        status="open",
                        series_ticker=series_ticker,
                        limit=200,
                    )
                except Exception as exc:
                    fetched[index] = ([], exc)
                else:
                    fetched[index] = (page, None)

        worker_count = min(_GEO_MARKET_WARMUP_WORKERS, len(geo_tickers))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="kalshi-market-warmup",
        ) as executor:
            futures = [executor.submit(fetch_worker) for _ in range(worker_count)]
            for future in futures:
                future.result()

        return [entry if entry is not None else ([], RuntimeError("warmup job missing result")) for entry in fetched]

    def _fetch_geo_markets(
        self,
        horizon_now: datetime | None = None,
    ) -> tuple[list, int]:
        """
        Synchronous: discover geo/political series then fetch their open markets.

        Strategy:
          1. Fetch all Kalshi series (~8k+) from /series endpoint.
          2. Keyword-match series titles to identify geo/political ones.
          3. For each matched series, fetch open markets.
          4. Apply the days-to-expiry filter.

        Runs in a thread pool executor so it doesn't block the event loop.
        """
        all_series = self._client.get_all_series()
        series_metadata_by_ticker = normalize_series_list(all_series)

        # Pass 1: keyword-match series titles to find geo/political candidates
        keyword_matched = [s for s in all_series if _is_geo_series(s)]

        # Pass 2: drop sports leagues from geo-relevant countries (e.g. Saudi
        # Pro League matches "saudi", J-League matches "japan").
        geo_tickers = [
            s["ticker"] for s in keyword_matched
            if not any(
                s["ticker"].upper().startswith(p)
                for p in MARKET_SERIES_BLOCKLIST_PREFIXES
            )
        ]
        log.info(
            "Series discovery: %d geo/political of %d total (%d dropped by sports blocklist)",
            len(geo_tickers), len(all_series), len(keyword_matched) - len(geo_tickers),
        )

        filtered = []
        horizon_now = horizon_now or self._horizon_time()
        per_series_counts: dict[str, tuple[int, int]] = {}
        fetch_failures: list[str] = []
        for series_ticker, (page, fetch_error) in zip(
            geo_tickers,
            self._fetch_geo_market_pages(geo_tickers),
        ):
            if fetch_error is not None:
                log.debug("Skipping series %s: %s", series_ticker, fetch_error)
                per_series_counts[series_ticker] = (0, 0)
                fetch_failures.append(series_ticker)
                continue
            raw_count = len(page)
            eligible_count = 0
            for m in page:
                if _is_excluded_test_market(m):
                    continue
                horizon = evaluate_market_horizon(
                    m.close_time,
                    now=horizon_now,
                    max_days_to_close=MAX_MARKET_DAYS_TO_EXPIRY,
                )
                if horizon.eligible:
                    filtered.append(_attach_regime_weights(m))
                    eligible_count += 1
            per_series_counts[series_ticker] = (raw_count, eligible_count)

        if fetch_failures:
            sample = ",".join(fetch_failures[:5])
            log.warning(
                "Market cache warmup failed for %d/%d series; sample=%s",
                len(fetch_failures),
                len(geo_tickers),
                sample,
            )
            raise RuntimeError(
                f"market cache warmup failed for {len(fetch_failures)}/{len(geo_tickers)} series"
            )

        geo_tickers_set = set(geo_tickers)
        _warn_on_missing_expected_families(
            filtered,
            geo_tickers_set=geo_tickers_set,
            per_series_counts=per_series_counts,
        )
        _emit_universe_shape_diagnostic(
            filtered,
            n_series_discovered=len(geo_tickers),
            geo_tickers_set=geo_tickers_set,
        )

        self._series_metadata_by_ticker = {
            ticker: series_metadata_by_ticker[ticker]
            for ticker in geo_tickers
            if ticker in series_metadata_by_ticker
        }
        return filtered, len(geo_tickers)

    def get_series_metadata_snapshot(self) -> dict[str, KalshiSeriesMetadata]:
        return dict(self._series_metadata_by_ticker)

    def has_market_snapshot(self) -> bool:
        return bool(self._markets)

    async def get_all_markets(self) -> list[KalshiMarket]:
        """
        Return all active Kalshi markets (up to 2000), regardless of category.
        Used exclusively by the fade signal to search sports + geo markets.
        Cached for MARKET_CACHE_TTL_SECONDS (same as geo cache).
        """
        async with self._all_lock:
            age = time.monotonic() - self._all_last_fetch
            if age > MARKET_CACHE_TTL_SECONDS or not self._all_markets:
                await self._refresh_all()
        return list(self._all_markets)

    async def _refresh_all(self) -> None:
        if time.monotonic() - self._all_last_fetch < _REFRESH_DEBOUNCE_SECONDS:
            log.debug("All-markets cache refresh debounced (last refresh %.0fs ago)", time.monotonic() - self._all_last_fetch)
            return
        loop = asyncio.get_running_loop()
        try:
            horizon_now = self._horizon_time()
            markets = await loop.run_in_executor(
                None, self._fetch_all_markets, horizon_now,
            )
            self._all_markets    = markets
            self._all_last_fetch = time.monotonic()
            log.info("All-markets cache refreshed: %d active markets", len(markets))
        except Exception as exc:
            log.error("All-markets cache refresh failed: %s", exc)

    def _fetch_all_markets(
        self,
        horizon_now: datetime | None = None,
    ) -> list[KalshiMarket]:
        """
        Synchronous: page through all active markets via cursor-complete
        pagination, with safety caps (page cap, row cap, wallclock timeout).
        Applies the same days-to-expiry filter as the geo cache.
        Runs in a thread pool executor.

        Never depends on Kalshi response ordering. Kalshi may return up to
        ~200k markets (predominantly sports MVE) and the response order is
        not stable; the prior 10-page (2000-row) cap silently truncated the
        universe when sports volume crossed that threshold, producing a
        sports-only effective cache. Callers needing a specific family must
        fetch it directly via `_fetch_geo_markets`' per-series query rather
        than relying on this stream.

        Emits a structured INFO log on every refresh and a WARNING when any
        safety cap halts pagination before cursor exhaustion.
        """
        markets: list[KalshiMarket] = []
        cursor: str | None = None
        pages_fetched = 0
        markets_seen = 0
        cursor_exhausted = False
        cap_reached: str | None = None
        start = time.monotonic()
        horizon_now = horizon_now or self._horizon_time()

        # `status="open"` is the request-parameter contract (see geo path
        # above for the full explanation). Response `KalshiMarket.status`
        # reports tradeable rows as `"active"`, gated separately downstream.
        while True:
            if pages_fetched >= _FETCH_MAX_PAGES:
                cap_reached = "max_pages"
                break
            if markets_seen >= _FETCH_MAX_ROWS:
                cap_reached = "max_rows"
                break
            if time.monotonic() - start >= _FETCH_TIMEOUT_SECONDS:
                cap_reached = "timeout"
                break

            page, cursor = self._client.get_markets(
                status="open", cursor=cursor, limit=200
            )
            pages_fetched += 1
            markets_seen += len(page)
            for m in page:
                if _is_excluded_test_market(m):
                    continue
                horizon = evaluate_market_horizon(
                    m.close_time,
                    now=horizon_now,
                    max_days_to_close=MAX_MARKET_DAYS_TO_EXPIRY,
                )
                if horizon.eligible:
                    markets.append(_attach_regime_weights(m))
            if not cursor:
                cursor_exhausted = True
                break

        log.info(
            "All-markets fetch: pages_fetched=%d markets_seen=%d kept=%d "
            "cursor_exhausted=%s cap_reached=%s elapsed=%.1fs",
            pages_fetched, markets_seen, len(markets),
            cursor_exhausted, cap_reached, time.monotonic() - start,
        )
        if cap_reached is not None:
            # Reaching a safety cap before cursor exhaustion means the
            # effective universe is a truncated sample of whatever order
            # Kalshi returned. Downstream callers cannot rely on family
            # coverage; operator must triage.
            log.warning(
                "All-markets pagination halted early: cap=%s after %d pages / "
                "%d rows. Effective universe is a Kalshi-order-dependent "
                "prefix; family coverage is not guaranteed.",
                cap_reached, pages_fetched, markets_seen,
            )
        return markets


# ── Matcher ───────────────────────────────────────────────────────────────────

class MarketMatcher:
    """
    Matches a NewsItem to relevant Kalshi markets.

    During paper trading, uses lower thresholds and returns more candidates
    so we collect the maximum amount of resolution data.
    """

    def __init__(
        self,
        rest_client: KalshiRestClient,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._cache = MarketCache(rest_client, now_provider=self._match_time)

    def _match_time(self) -> datetime:
        """Provide recorded replay time without weakening production horizon checks."""

        provider = getattr(self, "_now_provider", None)
        now = provider() if provider is not None else datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("market matcher clock must be timezone-aware")
        return now.astimezone(timezone.utc)

    async def find_candidates(
        self,
        news: NewsItem,
        max_results: int | None = None,
        *,
        refresh_cache: bool = True,
        emit_diagnostics: bool = True,
    ) -> list[tuple[KalshiMarket, float, dict[str, Any]]]:
        """
        Return (market, score, match_meta) triples sorted by score descending.

        Thresholds and candidate count are automatically adjusted based on
        whether the bot is in paper trading mode.
        ``emit_diagnostics`` controls MATCH_* telemetry only.
        """
        is_paper     = cfg.is_paper_trading
        min_score    = PAPER_MIN_MATCH_SCORE if is_paper else LIVE_MIN_MATCH_SCORE
        max_results  = max_results or (PAPER_MAX_CANDIDATES if is_paper else LIVE_MAX_CANDIDATES)

        # PROFIT-MATCH-DYNAMIC commit 4/5: load per-(token, market_prefix)
        # downweights once per find_candidates call. Re-read on every call to
        # pick up aggregator updates without a process restart. JSON parse is
        # microsecond-scale; weights file is small (one row per token×prefix
        # that has crossed MIN_TOTAL_FOR_DOWNWEIGHT). Missing file returns {}
        # for cold-start, but present dirty/malformed runtime weights fail
        # closed because they directly change candidate admission.
        from analysis.match_feedback import (
            MatcherWeightsUnverified,
            load_verified_weights as _load_match_weights,
        )
        try:
            _token_weights = _load_match_weights()
        except MatcherWeightsUnverified as exc:
            log.error("Matcher token weights unverified; failing closed: %s", exc)
            return []

        news_tokens = _tokenize(f"{news.headline} {news.body}")
        markets = (
            await self._cache.get_markets()
            if refresh_cache
            else self._cache.get_markets_snapshot()
        )

        headline_tokens = _tokenize(news.headline)
        match_time = self._match_time()
        news_publish_ts = news.published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        news_age_seconds = (match_time - news.published.astimezone(timezone.utc)).total_seconds()

        scored: list[tuple[KalshiMarket, float, dict[str, Any]]] = []
        for market in markets:
            horizon = evaluate_market_horizon(
                market.close_time,
                now=match_time,
                max_days_to_close=cfg.paper_admission_max_days_to_close,
            )
            if not horizon.eligible:
                continue
            market_title_tokens = _tokenize(market.title)
            market_tokens = (
                market_title_tokens
                | _tokenize(market.subtitle)
                | _contract_context_tokens(market)
            )
            # Require the market itself to contain at least one geopolitical token.
            # This prevents sports/financial markets from ever matching geo news.
            if not (market_tokens & _GEOPOLITICAL_BOOST):
                continue
            # Require at least one MEANINGFUL token from the news HEADLINE to
            # appear in the market title. Filter out short/numeric tokens first
            # to prevent date fragments like '1' (from 'Apr 1') or 's' (from
            # 'U.S.') from creating false positives.
            meaningful_hl = _meaningful_tokens(headline_tokens)
            meaningful_mt = _meaningful_tokens(market_title_tokens)
            overlap = meaningful_hl & meaningful_mt
            # Tiered gate: a specific named geo-entity (country, person) is
            # distinctive enough to pass alone. Generic words like "bank",
            # "people", "war", "attack" are too common -- require 2+ of them.
            geo_overlap     = overlap & _GEO_NAMED_ENTITIES
            generic_overlap = overlap - _GEO_NAMED_ENTITIES
            if not geo_overlap and len(generic_overlap) < 2:
                continue
            score = _similarity(news_tokens, market_tokens)

            days = _days_to_close(market.close_time, now=match_time)
            if days is not None:
                if days <= 1:
                    score *= 1.5
                elif days <= 7:
                    score *= 1.2
                elif days <= 14:
                    score *= 1.1

            structure_flags = _structure_quality_flags(
                overlap=overlap,
                geo_overlap=geo_overlap,
                generic_overlap=generic_overlap,
                headline_meaningful=meaningful_hl,
                market_title_meaningful=meaningful_mt,
            )
            score *= _weak_match_penalty_multiplier(set(structure_flags))
            ticker_prefix = (market.ticker or "").split("-", 1)[0]
            pre_weight_score = score
            token_weight_multiplier = 1.0

            # PROFIT-MATCH-DYNAMIC: apply per-(token, market_prefix)
            # downweight from the learning loop. Combine all overlap-token
            # weights so one generic bridge token does not dominate a stronger
            # multi-token match that also contains full-weight support.
            if overlap and _token_weights:
                weight_details = _token_downweight_details(
                    overlap,
                    ticker_prefix,
                    _token_weights,
                )
                token_weight_multiplier = weight_details["final_multiplier"]
                score *= token_weight_multiplier
                await _write_match_telemetry(
                    emit_diagnostics,
                    trade_log.log_match_weight_applied,
                    source=news.source,
                    headline=news.headline,
                    ticker=market.ticker,
                    market_title=market.title,
                    market_prefix=ticker_prefix,
                    tokens=weight_details["tokens"],
                    token_weights=weight_details["token_weights"],
                    composition_rule=weight_details["composition_rule"],
                    final_multiplier=weight_details["final_multiplier"],
                    pre_weight_score=pre_weight_score,
                    post_weight_score=score,
                )

            if score < min_score:
                continue

            score = round(score, 4)
            heuristic_flags = _match_quality_flags(
                overlap=overlap,
                geo_overlap=geo_overlap,
                generic_overlap=generic_overlap,
                headline_meaningful=meaningful_hl,
                market_title_meaningful=meaningful_mt,
                score=score,
                min_score=min_score,
            )
            overlap_ratio = len(overlap) / max(1, min(len(meaningful_hl), len(meaningful_mt)))
            match_meta = _compute_pre_llm_match_meta(
                news.headline,
                market.title,
                sorted(overlap),
            )
            lifecycle_id = build_lifecycle_id(
                venue="kalshi",
                ticker=market.ticker,
                source=news.source,
                url=getattr(news, "url", None),
                headline=news.headline,
                published=getattr(news, "published", None),
            )
            settlement_match = settlement_source_match(
                source=news.source,
                url=getattr(news, "url", None),
                source_hint_domain=getattr(news, "source_hint_domain", None),
                settlement_sources=getattr(market, "settlement_sources", ()) or (),
            )
            match_meta["lifecycle_id"] = lifecycle_id
            match_meta["settlement_source_match"] = settlement_match
            # P3.2 diagnostic field -- pure function, no behavior change.
            market_specificity = compute_specificity_score(market)
            await _write_match_telemetry(
                emit_diagnostics,
                trade_log.log_match_diagnostic,
                source=news.source,
                headline=news.headline,
                ticker=market.ticker,
                market_title=market.title,
                match_score=score,
                matched_tokens=sorted(overlap),
                token_overlap_count=len(overlap),
                geo_overlap_count=len(geo_overlap),
                generic_overlap_count=len(generic_overlap),
                headline_token_count=len(meaningful_hl),
                market_title_token_count=len(meaningful_mt),
                overlap_ratio=overlap_ratio,
                low_match_quality=bool(heuristic_flags),
                heuristic_flags=heuristic_flags,
                pre_llm_semantic_overlap_count=match_meta["pre_llm_semantic_overlap_count"],
                pre_llm_semantic_overlap_ratio=match_meta["pre_llm_semantic_overlap_ratio"],
                would_fail_pre_llm_gate=not match_meta["pre_llm_quality_pass"],
                pre_llm_headline_token_count=match_meta["pre_llm_headline_token_count"],
                pre_llm_market_token_count=match_meta["pre_llm_market_token_count"],
                pre_llm_filtered_stopword_count=match_meta["pre_llm_filtered_stopword_count"],
                pre_llm_filtered_generic_count=match_meta["pre_llm_filtered_generic_count"],
                pre_llm_semantic_token_types=match_meta["pre_llm_semantic_token_types"],
                publish_ts=news_publish_ts,
                age_at_match_seconds=news_age_seconds,
                market_specificity_score=market_specificity,
                venue="kalshi",
                lifecycle_id=lifecycle_id,
                settlement_source_match=settlement_match,
            )

            flag_set = set(heuristic_flags)
            ticker_lower = market.ticker.lower()
            # PROFIT-MATCH-001 (B') asymmetry fix: pre-fix predicate blocked
            # suppression whenever ANY overlap token sat in the ticker — which
            # preserved every entity-prefix-ticker match (e.g. `trump` ↔
            # `KXMOCTRUMP25`) regardless of off-topic headlines. Post-fix:
            # suppression is only blocked when at least ONE matched token is
            # OUTSIDE the ticker text. Pure entity-in-ticker matches now
            # suppress; coherent topic matches with non-ticker support tokens
            # still survive. Substring containment over `ticker_lower` per
            # spec §5.1 (NOT set-difference against `_tokenize(ticker)`, which
            # fails because Kalshi tickers tokenize to a single hyphenated
            # string).
            _has_supporting_non_ticker_token = any(
                token not in ticker_lower for token in overlap
            )
            # Path A (original): near-threshold score + structural weakness.
            # Catches fragile matches that barely passed the score floor.
            _near_threshold_weak = (
                "near_threshold_score" in flag_set
                and ("minimal_overlap" in flag_set or "single_named_entity_only" in flag_set)
            )
            # Path B: pure single-entity match with no additional semantic support.
            # A single named-entity token with no other overlap can inflate scores via
            # the geopolitical boost, bypassing near_threshold detection. Suppressing
            # score-independently is safe: any legitimately topic-aligned market will
            # embed the entity in its ticker (e.g. KXTRUMP-*), which the ticker guard
            # below preserves.
            _pure_single_entity = (
                "single_named_entity_only" in flag_set
                and "minimal_overlap" in flag_set
            )
            _meets_suppression_criteria = (
                bool(heuristic_flags)
                and not _has_supporting_non_ticker_token
                and (_near_threshold_weak or _pure_single_entity)
            )

            if (
                emit_diagnostics
                and ENABLE_MATCH_SUPPRESSION_DEBUG
                and _meets_suppression_criteria
            ):
                # MATCH-001 (operator-directed swallow-with-warning):
                # observability write must not abort find_candidates.
                # Matches series-fetch precedent at line ~460.
                try:
                    await write_trade_log_async(
                        trade_log.log_match_suppression_candidate,
                        source=news.source,
                        headline=news.headline,
                        ticker=market.ticker,
                        match_score=score,
                        overlap_count=len(overlap),
                        overlap_ratio=overlap_ratio,
                        heuristic_flags=heuristic_flags,
                        matched_tokens=sorted(overlap),
                    )
                except Exception as exc:
                    log.warning(
                        "MATCH-001 diagnostic write failed for %s: %s",
                        market.ticker,
                        exc,
                    )

            if ENABLE_LOW_QUALITY_MATCH_SUPPRESSION and _meets_suppression_criteria:
                reason = "+".join(sorted(flag_set))
                # MATCH-001 (operator-directed swallow-with-warning):
                # suppression-write must not abort the matching loop;
                # the `continue` below still fires on the success path.
                try:
                    await _write_match_telemetry(
                        emit_diagnostics,
                        trade_log.log_match_suppressed,
                        source=news.source,
                        headline=news.headline,
                        ticker=market.ticker,
                        market_title=market.title,
                        match_score=score,
                        matched_tokens=sorted(overlap),
                        heuristic_flags=heuristic_flags,
                        reason=reason,
                        raw_score=pre_weight_score,
                        adjusted_score=score,
                        threshold=min_score,
                        token_weight_multiplier=token_weight_multiplier,
                        venue=_diagnostic_venue_for_market(market, ticker_prefix),
                        market_prefix=ticker_prefix,
                    )
                except Exception as exc:
                    log.warning(
                        "MATCH-001 suppression write failed for %s: %s",
                        market.ticker,
                        exc,
                    )
                log.debug(
                    "[SUPPRESSED] %s -> %s (score=%.3f flags=%s)",
                    news.headline[:60], market.ticker, score, reason,
                )
                continue

            scored.append((market, score, match_meta))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = scored[:max_results]

        if results:
            log.debug(
                "[%s] Matched %d markets for '%s...' -- top: %s (%.3f)",
                "PAPER" if is_paper else "LIVE",
                len(results),
                news.headline[:50],
                results[0][0].ticker,
                results[0][1],
            )

        return results

    async def find_all_candidates(
        self, news: NewsItem, max_results: int = 1
    ) -> list[tuple[KalshiMarket, float]]:
        """
        Like find_candidates() but searches ALL active markets (geo + sports + entertainment).
        Used exclusively by the fade signal pipeline.

        Applies a relaxed gate: Jaccard similarity only, no geopolitical boost requirement,
        no named-entity gate. This allows sports/entertainment markets to match.
        """
        _FADE_MIN_SCORE = 0.02

        news_tokens = _tokenize(f"{news.headline} {news.body}")
        markets     = await self._cache.get_all_markets()
        match_time = self._match_time()

        scored: list[tuple[KalshiMarket, float]] = []
        for market in markets:
            if market.status not in ("open", "active"):
                continue
            horizon = evaluate_market_horizon(
                market.close_time,
                now=match_time,
                max_days_to_close=cfg.paper_admission_max_days_to_close,
            )
            if not horizon.eligible:
                continue
            market_tokens = _tokenize(market.title) | _tokenize(market.subtitle)
            if not market_tokens:
                continue
            score = _similarity(news_tokens, market_tokens)
            if score < _FADE_MIN_SCORE:
                continue
            days = _days_to_close(market.close_time, now=match_time)
            if days is not None:
                if days <= 1:
                    score *= 1.5
                elif days <= 7:
                    score *= 1.2
                elif days <= 14:
                    score *= 1.1
            scored.append((market, round(score, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = scored[:max_results]
        if results:
            log.debug(
                "[FADE] Matched %d market(s) for '%s...' -- top: %s (%.3f)",
                len(results), news.headline[:50],
                results[0][0].ticker, results[0][1],
            )
        return results

    async def refresh_cache(self) -> None:
        await self._cache._refresh()
