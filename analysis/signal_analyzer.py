"""
Signal analyzer: estimates the probability shift implied by a news headline.

Two modes:
  1. Keyword scoring (always available) — fast, deterministic, no API calls.
  2. LLM estimation via Claude API (optional) — higher quality, requires
     ANTHROPIC_API_KEY in environment.

The analyzer returns a probability ADJUSTMENT relative to the current market
price.  E.g. if the market is at 0.30 and the news suggests a +0.15 upward
shift, our estimated probability is 0.45.
"""

import asyncio
import json as _json
import time
from typing import Optional

from config import cfg, GEOPOLITICAL_SIGNALS
from feeds import NewsItem
from kalshi import KalshiMarket
from utils.logger import get_logger, trade_log

log = get_logger("signal_analyzer")

# Limit concurrent LLM calls to 1. With a single Ollama process on CPU,
# parallel calls don't improve throughput — they just spike latency.
# Using a semaphore here (not at the worker level) means this cap holds
# even if we add more consumer workers in the future.
_LLM_SEMAPHORE = asyncio.Semaphore(1)

# ── Ollama circuit breaker ─────────────────────────────────────────────────────
_OLLAMA_FAILURE_THRESHOLD = 3     # consecutive failures before circuit opens
_OLLAMA_PROBE_INTERVAL    = 300   # seconds between recovery probes (5 min)
_ollama_consecutive_failures: int = 0
_ollama_down_until: float = 0.0   # monotonic — skip Ollama until this time


def _extract_json(text: str) -> dict:
    """
    Extract the last valid JSON object from an LLM response string.

    Uses raw_decode() to scan forward from each '{', keeping the last
    successfully parsed result.  This handles LLM preamble like:
        'Consider {Russia}: {"relevant": true, ...}'
    where a greedy regex would capture from the first '{' to the last '}'
    and produce invalid JSON.

    Raises ValueError if no valid JSON object is found.
    """
    decoder = _json.JSONDecoder()
    last: dict | None = None
    i = 0
    while i < len(text):
        pos = text.find("{", i)
        if pos == -1:
            break
        try:
            obj, _ = decoder.raw_decode(text, pos)
            if isinstance(obj, dict):
                last = obj
        except _json.JSONDecodeError:
            pass
        i = pos + 1
    if last is None:
        raise ValueError(f"No valid JSON object found in LLM response: {text[:120]!r}")
    return last


# ── Keyword scoring ───────────────────────────────────────────────────────────

def _keyword_score(
    text: str,
    keyword_stats=None,   # KeywordStats instance or None
    series_ticker: str = "",
) -> tuple[float, str, list[str]]:
    """
    Scan text against GEOPOLITICAL_SIGNALS keyword lists.

    If keyword_stats is provided, each keyword's base strength is multiplied by
    its per-(keyword, series_ticker) accuracy multiplier from KeywordStats.

    Returns:
        (raw_shift, dominant_direction, matched_keywords)
        raw_shift is a signed float: positive = push toward YES, negative = push toward NO.
    """
    text_lower = text.lower()
    matched: list[str] = []
    direction_weights: dict[str, float] = {"yes": 0.0, "no": 0.0}

    for sig_def in GEOPOLITICAL_SIGNALS:
        keywords  = sig_def["keywords"]
        direction = sig_def["direction"]
        strength  = sig_def["strength"]

        hits = [kw for kw in keywords if kw.lower() in text_lower]
        if hits:
            matched.extend(hits)
            weight = strength * (1 + 0.1 * (len(hits) - 1))   # bonus for multiple hits
            if keyword_stats is not None and series_ticker:
                # Apply per-(keyword, series_ticker) accuracy multiplier.
                # Use the first hit in this signal group as the representative keyword;
                # all hits share the same direction and strength definition.
                mult = keyword_stats.get_multiplier(hits[0], series_ticker)
                weight *= mult
            direction_weights[direction] += weight

    net_shift = direction_weights["yes"] - direction_weights["no"]
    dominant  = "yes" if net_shift >= 0 else "no"
    return net_shift, dominant, matched


def _keyword_contributions(
    text: str,
    keyword_stats=None,
    series_ticker: str = "",
) -> list[dict]:
    """Return compact per-keyword contribution details for observability only."""
    text_lower = text.lower()
    contributions: list[dict] = []

    for sig_def in GEOPOLITICAL_SIGNALS:
        keywords = sig_def["keywords"]
        direction = sig_def["direction"]
        strength = sig_def["strength"]

        hits = [kw for kw in keywords if kw.lower() in text_lower]
        if not hits:
            continue

        group_weight = strength * (1 + 0.1 * (len(hits) - 1))
        multiplier = 1.0
        if keyword_stats is not None and series_ticker:
            multiplier = keyword_stats.get_multiplier(hits[0], series_ticker)
        applied_weight = group_weight * multiplier

        for keyword in hits:
            contributions.append({
                "keyword": keyword,
                "direction": direction,
                "weight": round(applied_weight, 4),
            })

    return contributions


def keyword_estimate(
    news: NewsItem,
    market: KalshiMarket,
    base_probability: Optional[float] = None,
    keyword_stats=None,   # KeywordStats instance or None
) -> tuple[float, str, list[str], str]:
    """
    Estimate the probability of YES resolution for the given market,
    given the news item, using keyword scoring.

    Returns:
        (estimated_probability, side, keywords_matched, reasoning)
    """
    combined_text = f"{news.headline} {news.body}"
    series_ticker = getattr(market, "series_ticker", "") or ""
    shift, direction, keywords = _keyword_score(
        combined_text, keyword_stats=keyword_stats, series_ticker=series_ticker
    )

    if base_probability is None:
        base_probability = market.yes_prob  # use current market price

    # Geo-entity coherence check: if the article and market both reference
    # specific countries/regions, they must share at least one. This prevents
    # signals from Iran news bleeding into Bank of Russia / PBOC markets.
    # Countries + their adjective forms; deliberately excludes generic words
    # like "war", "attack", "bank" that appear in many unrelated contexts.
    _GEO_COUNTRIES = frozenset({
        "ukraine", "russia", "china", "taiwan", "iran", "israel", "gaza",
        "korea", "pakistan", "india", "japan", "turkey", "saudi", "syria",
        "iraq", "afghanistan", "venezuela", "cuba", "mexico", "canada",
        "france", "germany", "britain", "lebanon",
        "russian", "chinese", "iranian", "ukrainian", "korean", "israeli",
        "european", "american", "british", "french", "german", "turkish",
        "japanese", "lebanese", "iraqi", "syrian",
    })
    article_text_lower = f"{news.headline} {news.body[:300] if news.body else ''}".lower()
    market_text_lower  = f"{market.title} {market.subtitle}".lower()
    article_countries  = {e for e in _GEO_COUNTRIES if e in article_text_lower}
    market_countries   = {e for e in _GEO_COUNTRIES if e in market_text_lower}
    if article_countries and market_countries and not (article_countries & market_countries):
        log.debug(
            "Geo-coherence suppressed: article=%s market=%s ticker=%s",
            article_countries, market_countries, market.ticker,
        )
        return (
            market.yes_prob, 0.05, keywords,
            f"Geo-entity mismatch: article mentions {article_countries} "
            f"but market concerns {market_countries} -- signal suppressed.",
        )

    # Dampen shift by current market price: shifts near extremes are smaller
    # because the market is already "priced in" somewhat
    dampen_factor = 1.0
    if base_probability > 0.8 and direction == "yes":
        dampen_factor = 0.4
    elif base_probability < 0.2 and direction == "no":
        dampen_factor = 0.4

    adjusted_shift = shift * dampen_factor
    estimated_prob = max(0.02, min(0.98, base_probability + adjusted_shift))

    edge = estimated_prob - market.yes_prob
    side = "yes" if edge > 0 else "no"

    reasoning = (
        f"Keyword analysis found {len(keywords)} signal(s): [{', '.join(keywords[:5])}]. "
        f"Net shift: {shift:+.3f} (dampened: {adjusted_shift:+.3f}). "
        f"Base prob: {base_probability:.3f} -> estimated: {estimated_prob:.3f}. "
        f"Edge vs market ({market.yes_price:.1f}c): {edge:+.3f}. "
        f"Betting {side.upper()}."
    )

    return estimated_prob, side, keywords, reasoning


# ── Optional LLM estimation ───────────────────────────────────────────────────

# Magnitude -> probability shift mapping (applied in code, not by the LLM)
_MAGNITUDE_SHIFT = {"none": 0.0, "small": 0.08, "moderate": 0.15, "large": 0.25}

_LLM_SYSTEM_PROMPT = """You are a geopolitical prediction market analyst.

Your task is to determine whether a news headline would cause traders to update
the probability of a specific Kalshi binary market.

You will be given:
MARKET: title, resolution criteria, current YES price
NEWS: headline and summary

Decision process:

Step 1 - Relevance
Is the headline directly related to the exact event described in the market?
If not, magnitude must be "none".

Step 2 - New Information
Does the headline contain concrete NEW information that traders likely did not
already price in?
Routine updates, speculation, rhetoric, and commentary are usually already priced in.

Step 3 - Direction
If the information is new and relevant, does it increase or decrease the
probability of the event resolving YES?

Step 4 - Magnitude
Estimate how much traders would likely move the price.

Magnitude definitions (probability change):
  none:     0 percentage points
  small:    3-8 percentage points
  moderate: 8-20 percentage points
  large:    >20 percentage points

Important rules:
- Most headlines should result in magnitude="none".
- If new_information=false, then direction="neutral" and magnitude="none".
- Only major unexpected developments justify "moderate" or "large".
- Actors must have official decision-making power over the market event. Statements
  by opposition figures, exiles, diaspora groups, foreign critics, or unofficial
  intermediaries do NOT move the probability. Ask yourself: "Does this person or
  group have actual authority to cause the market event to resolve?"
- Your JSON output must be internally consistent with your reasoning field. If your
  reasoning says the headline "does not directly impact" or is "not directly related
  to" the market event, then direction MUST be "neutral" and magnitude MUST be "none".

Respond with ONLY a JSON object:
{
  "relevant": <true|false>,
  "new_information": <true|false>,
  "direction": "yes" | "no" | "neutral",
  "magnitude": "none" | "small" | "moderate" | "large",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one concise sentence>"
}"""


def _build_user_msg(news, market) -> str:
    resolution = market.subtitle if market.subtitle else market.title
    return (
        f"MARKET TITLE: {market.title}\n"
        f"RESOLUTION CRITERIA: {resolution}\n"
        f"CURRENT YES PRICE: {market.yes_price:.1f} cents ({market.yes_prob:.1%})\n"
        f"MARKET CLOSES: {market.close_time}\n\n"
        f"NEWS HEADLINE: {news.headline}\n"
        f"SOURCE: {news.source}\n"
        f"SUMMARY: {news.body[:400] if news.body else '(none)'}"
    )


def _parse_llm_response(parsed: dict, market) -> tuple[float, float, str, str, str]:
    """
    Shared helper: extract (prob, confidence, reasoning, direction, magnitude)
    from a parsed LLM JSON dict.

    Applies the magnitude -> probability shift mapping and returns market.yes_prob
    unchanged when the model signals no new information.
    Returns raw direction and magnitude for storage in paper_trades.
    """
    confidence = float(parsed.get("confidence", 0.5))
    reasoning  = parsed.get("reasoning", "")
    relevant   = parsed.get("relevant", True)
    new_info   = parsed.get("new_information", True)
    direction  = parsed.get("direction", "neutral")
    magnitude  = parsed.get("magnitude", "none")

    if not relevant or not new_info or direction == "neutral" or magnitude == "none":
        prob = market.yes_prob
    else:
        base_shift = _MAGNITUDE_SHIFT.get(magnitude, 0.0)
        shift = base_shift * confidence
        if direction == "yes":
            prob = min(0.95, market.yes_prob + shift)
        else:
            prob = max(0.05, market.yes_prob - shift)

    return prob, confidence, reasoning, direction, magnitude


async def _ollama_ping() -> bool:
    """
    Lightweight Ollama health check (5s timeout).
    Calls GET /api/version on the native Ollama port.
    Does NOT count against the failure threshold -- used as a cheap
    connectivity probe before committing to a full 60s inference call.
    """
    import aiohttp
    base = cfg.ollama_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base}/api/version",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


async def _ollama_estimate(news, market):
    """
    Call local Ollama server (OpenAI-compatible endpoint).
    Returns (prob, confidence, reasoning) or None if Ollama is not running.
    """
    global _ollama_consecutive_failures, _ollama_down_until

    if _ollama_down_until > 0.0:
        if time.monotonic() < _ollama_down_until:
            return None  # circuit open -- not probe time yet
        # Probe window: run a cheap ping before committing to 60s inference.
        # A failed ping just extends the timer without incrementing the failure
        # counter -- prevents probes from keeping the circuit permanently open.
        if not await _ollama_ping():
            _ollama_down_until = time.monotonic() + _OLLAMA_PROBE_INTERVAL
            log.debug(
                "Ollama probe (ping) failed -- circuit stays open for %.0fm",
                _OLLAMA_PROBE_INTERVAL / 60,
            )
            return None
        log.info(
            "Ollama ping succeeded after %d failures -- attempting inference",
            _ollama_consecutive_failures,
        )
        _ollama_consecutive_failures = 0
        _ollama_down_until = 0.0

    import aiohttp

    payload = {
        "model": cfg.ollama_model,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user",   "content": _build_user_msg(news, market)},
        ],
        "max_tokens": 256,
        "temperature": 0,
        "repetition_penalty": 1.05,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{cfg.ollama_base_url}/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    log.debug("Ollama returned HTTP %d", resp.status)
                    return None
                data = await resp.json()

        text   = data["choices"][0]["message"]["content"].strip()
        parsed = _extract_json(text)
        prob, confidence, reasoning, direction, magnitude = _parse_llm_response(parsed, market)
        if _ollama_consecutive_failures > 0:
            log.info("Ollama recovered after %d consecutive failures", _ollama_consecutive_failures)
            _ollama_consecutive_failures = 0
            _ollama_down_until = 0.0
        log.debug("Ollama: dir=%s mag=%s conf=%.2f -> prob=%.3f for %s",
                  direction, magnitude, confidence, prob, market.ticker)
        return prob, confidence, reasoning, direction, magnitude

    except aiohttp.ClientConnectorError:
        _ollama_consecutive_failures += 1
        if _ollama_consecutive_failures >= _OLLAMA_FAILURE_THRESHOLD:
            _ollama_down_until = time.monotonic() + _OLLAMA_PROBE_INTERVAL
            log.warning(
                "Ollama circuit open -- %d consecutive failures, skipping for %.0fm",
                _ollama_consecutive_failures, _OLLAMA_PROBE_INTERVAL / 60,
            )
        else:
            log.debug("Ollama not running -- falling back to keyword scoring")
        return None
    except asyncio.TimeoutError:
        _ollama_consecutive_failures += 1
        if _ollama_consecutive_failures >= _OLLAMA_FAILURE_THRESHOLD:
            _ollama_down_until = time.monotonic() + _OLLAMA_PROBE_INTERVAL
            log.warning(
                "Ollama circuit open -- %d consecutive failures (timeout), skipping for %.0fm",
                _ollama_consecutive_failures, _OLLAMA_PROBE_INTERVAL / 60,
            )
        else:
            log.warning("Ollama estimation failed: timed out after 60s (model loading or CPU overloaded)")
        return None
    except _json.JSONDecodeError as exc:
        log.warning("Ollama estimation failed: invalid JSON in response -- %s", exc)
        return None
    except ValueError as exc:
        log.warning("Ollama estimation failed: %s", exc)
        return None
    except Exception as exc:
        log.warning("Ollama estimation failed: %s (%s)", exc, type(exc).__name__)
        return None


async def _anthropic_estimate(news, market):
    """
    Call Anthropic Claude API (fallback if Ollama unavailable).
    Returns None if ANTHROPIC_API_KEY is not set or the call fails.
    """
    if not cfg.anthropic_api_key:
        return None

    try:
        import anthropic

        client   = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_msg(news, market)}],
        )

        text   = response.content[0].text.strip()
        parsed = _extract_json(text)
        prob, confidence, reasoning, direction, magnitude = _parse_llm_response(parsed, market)
        log.debug("Anthropic: dir=%s mag=%s conf=%.2f -> prob=%.3f for %s",
                  direction, magnitude, confidence, prob, market.ticker)
        return prob, confidence, reasoning, direction, magnitude

    except ImportError:
        log.warning("anthropic package not installed -- Anthropic estimation disabled")
        return None
    except Exception as exc:
        log.warning("Anthropic estimation failed: %s", exc)
        return None


async def llm_estimate(news, market):
    """
    Best-available LLM estimate. Tries Ollama first (free, local, no rate
    limits), then Anthropic (requires ANTHROPIC_API_KEY). Returns None if
    both are unavailable, triggering keyword-only fallback in the caller.

    Serialized via _LLM_SEMAPHORE — only one call runs at a time regardless
    of how many consumer workers are active.
    """
    async with _LLM_SEMAPHORE:
        result = await _ollama_estimate(news, market)
        if result:
            return result
        return await _anthropic_estimate(news, market)


# ── Combined estimator ────────────────────────────────────────────────────────

async def estimate_probability(
    news: NewsItem,
    market: KalshiMarket,
    keyword_stats=None,   # KeywordStats instance or None
) -> tuple[float, float, list[str], str, Optional[str], Optional[str], Optional[float]]:
    """
    Best-available probability estimate for this (news, market) pair.

    Tries LLM first; falls back to keyword scoring.

    Args:
        keyword_stats: optional KeywordStats instance for per-(keyword, series_ticker)
                       accuracy multipliers. Pass None to skip multipliers.

    Returns:
        (estimated_probability, confidence, keywords_matched, reasoning,
         llm_direction, llm_magnitude, llm_confidence)
        llm_* fields are None when keyword-only path is used.
    """
    # Always run keyword scoring for the keyword list
    base_probability = market.yes_prob
    combined_text = f"{news.headline} {news.body}"
    series_ticker = getattr(market, "series_ticker", "") or ""
    kw_prob, kw_side, keywords, kw_reasoning = keyword_estimate(
        news, market, keyword_stats=keyword_stats
    )
    keyword_contribs = _keyword_contributions(
        combined_text, keyword_stats=keyword_stats, series_ticker=series_ticker
    )

    if not keywords:
        # News doesn't seem related to this market
        trade_log.log_signal_analysis_detail(
            ticker=market.ticker,
            source=news.source,
            headline=news.headline,
            method="keyword_gate",
            keywords=[],
            keyword_contributions=[],
            base_probability=base_probability,
            final_probability=market.yes_prob,
            market_price=market.yes_prob,
        )
        return market.yes_prob, 0.1, [], "No relevant keywords found -- no signal.", None, None, None

    # Try LLM if available
    llm_result = await llm_estimate(news, market)
    if llm_result:
        llm_prob, llm_confidence, llm_reasoning, llm_direction, llm_magnitude = llm_result
        # Use LLM probability directly -- keywords only gate the match, not the estimate.
        # Blending was removed because it allowed keyword scores to manufacture bets
        # that the LLM explicitly said were not market-moving (e.g. LLM: 0.50 + keywords
        # push kw_prob to 0.65 -> blended 0.545 -> bet placed against LLM advice).
        reasoning = (
            f"[LLM] {llm_reasoning} "
            f"(LLM: {llm_prob:.3f}, Keywords(ref): {kw_prob:.3f})"
        )
        trade_log.log_signal_analysis_detail(
            ticker=market.ticker,
            source=news.source,
            headline=news.headline,
            method="llm",
            keywords=keywords,
            keyword_contributions=keyword_contribs,
            base_probability=base_probability,
            final_probability=llm_prob,
            market_price=market.yes_prob,
            llm_direction=llm_direction,
            llm_magnitude=llm_magnitude,
            llm_confidence=llm_confidence,
        )
        return llm_prob, llm_confidence, keywords, reasoning, llm_direction, llm_magnitude, llm_confidence

    # Keyword only
    confidence = min(0.7, 0.3 + 0.05 * len(keywords))   # more keywords -> more confident
    trade_log.log_signal_analysis_detail(
        ticker=market.ticker,
        source=news.source,
        headline=news.headline,
        method="keyword",
        keywords=keywords,
        keyword_contributions=keyword_contribs,
        base_probability=base_probability,
        final_probability=kw_prob,
        market_price=market.yes_prob,
    )
    return kw_prob, confidence, keywords, kw_reasoning, None, None, None
