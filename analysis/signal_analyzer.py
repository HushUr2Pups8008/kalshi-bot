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
import re
from typing import Optional

from config import cfg, GEOPOLITICAL_SIGNALS
from feeds import NewsItem
from kalshi import KalshiMarket
from utils.logger import get_logger

log = get_logger("signal_analyzer")


# ── Keyword scoring ───────────────────────────────────────────────────────────

def _keyword_score(text: str) -> tuple[float, str, list[str]]:
    """
    Scan text against GEOPOLITICAL_SIGNALS keyword lists.

    Returns:
        (raw_shift, dominant_direction, matched_keywords)
        raw_shift is a signed float: positive = push toward YES, negative = push toward NO.
    """
    text_lower = text.lower()
    total_shift = 0.0
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
            direction_weights[direction] += weight

    net_shift = direction_weights["yes"] - direction_weights["no"]
    dominant  = "yes" if net_shift >= 0 else "no"
    return net_shift, dominant, matched


def keyword_estimate(
    news: NewsItem,
    market: KalshiMarket,
    base_probability: Optional[float] = None,
) -> tuple[float, str, list[str], str]:
    """
    Estimate the probability of YES resolution for the given market,
    given the news item, using keyword scoring.

    Returns:
        (estimated_probability, side, keywords_matched, reasoning)
    """
    combined_text = f"{news.headline} {news.body}"
    shift, direction, keywords = _keyword_score(combined_text)

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
            f"but market concerns {market_countries} — signal suppressed.",
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
        f"Base prob: {base_probability:.3f} → estimated: {estimated_prob:.3f}. "
        f"Edge vs market ({market.yes_price:.1f}¢): {edge:+.3f}. "
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


def _parse_llm_response(parsed: dict, market) -> tuple[float, float, str]:
    """
    Shared helper: extract (prob, confidence, reasoning) from a parsed LLM JSON dict.

    Applies the magnitude -> probability shift mapping and returns market.yes_prob
    unchanged when the model signals no new information.
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

    return prob, confidence, reasoning


async def _ollama_estimate(news, market):
    """
    Call local Ollama server (OpenAI-compatible endpoint).
    Returns (prob, confidence, reasoning) or None if Ollama is not running.
    """
    import aiohttp
    import json as _json

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

        text  = data["choices"][0]["message"]["content"].strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON in Ollama response: {text[:100]}")

        parsed = _json.loads(match.group())
        prob, confidence, reasoning = _parse_llm_response(parsed, market)
        log.debug("Ollama: dir=%s mag=%s conf=%.2f -> prob=%.3f for %s",
                  parsed.get("direction"), parsed.get("magnitude"),
                  confidence, prob, market.ticker)
        return prob, confidence, reasoning

    except aiohttp.ClientConnectorError:
        log.debug("Ollama not running -- falling back to keyword scoring")
        return None
    except asyncio.TimeoutError:
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
        import json as _json

        client   = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_msg(news, market)}],
        )

        text  = response.content[0].text.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON in Anthropic response: {text[:100]}")

        parsed = _json.loads(match.group())
        prob, confidence, reasoning = _parse_llm_response(parsed, market)
        log.debug("Anthropic: dir=%s mag=%s conf=%.2f -> prob=%.3f for %s",
                  parsed.get("direction"), parsed.get("magnitude"),
                  confidence, prob, market.ticker)
        return prob, confidence, reasoning

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
    """
    result = await _ollama_estimate(news, market)
    if result:
        return result
    return await _anthropic_estimate(news, market)


# ── Combined estimator ────────────────────────────────────────────────────────

async def estimate_probability(
    news: NewsItem,
    market: KalshiMarket,
) -> tuple[float, float, list[str], str]:
    """
    Best-available probability estimate for this (news, market) pair.

    Tries LLM first; falls back to keyword scoring.

    Returns:
        (estimated_probability, confidence, keywords_matched, reasoning)
    """
    # Always run keyword scoring for the keyword list
    kw_prob, kw_side, keywords, kw_reasoning = keyword_estimate(news, market)

    if not keywords:
        # News doesn't seem related to this market
        return market.yes_prob, 0.1, [], "No relevant keywords found — no signal."

    # Try LLM if available
    llm_result = await llm_estimate(news, market)
    if llm_result:
        llm_prob, llm_confidence, llm_reasoning = llm_result
        # Use LLM probability directly — keywords only gate the match, not the estimate.
        # Blending was removed because it allowed keyword scores to manufacture bets
        # that the LLM explicitly said were not market-moving (e.g. LLM: 0.50 + keywords
        # push kw_prob to 0.65 -> blended 0.545 -> bet placed against LLM advice).
        reasoning = (
            f"[LLM] {llm_reasoning} "
            f"(LLM: {llm_prob:.3f}, Keywords(ref): {kw_prob:.3f})"
        )
        return llm_prob, llm_confidence, keywords, reasoning

    # Keyword only
    confidence = min(0.7, 0.3 + 0.05 * len(keywords))   # more keywords → more confident
    return kw_prob, confidence, keywords, kw_reasoning
