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
import os
import time
from typing import Any, Optional

from config import cfg, GEOPOLITICAL_SIGNALS
from utils.runtime_overrides import is_keyword_disabled
from feeds import NewsItem
from kalshi import KalshiMarket
from utils.log_records import SignalAnalysisDetail
from utils.logger import get_logger, trade_log, write_trade_log_async

log = get_logger("signal_analyzer")


def _emit_extraction_trace_step(
    step_name: str,
    *,
    ticker: str,
    input_signal_magnitude: float,
    output_signal_magnitude: float,
    intermediate_state: dict[str, Any],
) -> None:
    """Debug-only extraction trace hook, gated on KALSHI_EXTRACTION_TRACE=1."""
    if os.environ.get("KALSHI_EXTRACTION_TRACE") != "1":
        return
    log.debug(
        _json.dumps(
            {
                "type": "EXTRACTION_TRACE_STEP",
                "step_name": step_name,
                "ticker": ticker,
                "input_signal_magnitude": input_signal_magnitude,
                "output_signal_magnitude": output_signal_magnitude,
                "intermediate_state": intermediate_state,
            },
            sort_keys=True,
        )
    )

# Limit concurrent LLM calls to 1. With a single Ollama process on CPU,
# parallel calls don't improve throughput — they just spike latency.
# Using a semaphore here (not at the worker level) means this cap holds
# even if we add more consumer workers in the future.
_LLM_SEMAPHORE = asyncio.Semaphore(1)
_llm_in_flight: int = 0

# ── Ollama circuit breaker ─────────────────────────────────────────────────────
_OLLAMA_FAILURE_THRESHOLD = 3     # consecutive failures before circuit opens
_OLLAMA_PROBE_INTERVAL    = 300   # seconds between recovery probes (5 min)
_ollama_consecutive_failures: int = 0
_ollama_down_until: float = 0.0   # monotonic — skip Ollama until this time


def _market_price_in_band(price: float, low: float, high: float) -> bool:
    if high >= 1.0:
        return low <= price <= high
    return low <= price < high


def _llm_routing_reason(news: NewsItem, market: KalshiMarket) -> str | None:
    del news
    if not cfg.enable_llm_routing_filter:
        return None
    price = float(market.yes_prob)
    if any(_market_price_in_band(price, low, high) for low, high in cfg.llm_excluded_price_bands):
        return "price_band_excluded"
    if cfg.llm_allowed_price_bands and not any(
        _market_price_in_band(price, low, high) for low, high in cfg.llm_allowed_price_bands
    ):
        return "price_band_excluded"
    return None


def _llm_meta(
    *,
    attempted: bool,
    status: str,
    provider: str | None = None,
    result_used: bool = False,
    latency_ms: int | None = None,
    total_stage_ms: int | None = None,
    queue_wait_ms: int | None = None,
    http_round_trip_ms: int | None = None,
    parse_ms: int | None = None,
    http_status: int | None = None,
    contention_observed: bool | None = None,
    in_flight_at_entry: int | None = None,
    prompt: str | None = None,
    raw_response: str | None = None,
) -> dict[str, Any]:
    meta = {
        "attempted": attempted,
        "status": status,
        "provider": provider,
        "result_used": result_used,
        "latency_ms": latency_ms,
        "total_stage_ms": total_stage_ms,
        "queue_wait_ms": queue_wait_ms,
        "http_round_trip_ms": http_round_trip_ms,
        "parse_ms": parse_ms,
        "http_status": http_status,
        "contention_observed": contention_observed,
        "in_flight_at_entry": in_flight_at_entry,
    }
    if prompt is not None:
        meta["prompt"] = prompt
    if raw_response is not None:
        meta["raw_response"] = raw_response
    return meta


# Keys that `_build_llm_meta_kwargs` projects from `_llm_meta()` output into the
# SignalAnalysisDetail constructor. These are the 11 LLM-meta fields shared by
# all three signal-analysis-detail callsites (LLM, no-keyword, keyword paths).
# `attempted` and `result_used` are NOT in this projection because their values
# differ across callsites and are passed explicitly. Keep this tuple in sync
# with the corresponding fields on SignalAnalysisDetail.
_LLM_META_PROJECTION_KEYS: tuple[str, ...] = (
    "status", "provider",
    "latency_ms", "total_stage_ms", "queue_wait_ms",
    "http_round_trip_ms", "parse_ms", "http_status",
    "contention_observed", "in_flight_at_entry",
)


def _build_llm_meta_kwargs(llm_meta: dict[str, Any]) -> dict[str, Any]:
    """Project shared LLM-meta fields into SignalAnalysisDetail kwarg form.

    Returns a dict with `llm_attempted` plus the 10 `llm_<status|provider|*_ms|
    *_status|contention_observed|in_flight_at_entry>` fields, ready to be
    `**`-spread into a SignalAnalysisDetail constructor. Callers add
    `llm_result_used` separately because its value varies per branch.
    """
    return {
        "llm_attempted": llm_meta["attempted"],
        **{f"llm_{k}": llm_meta.get(k) for k in _LLM_META_PROJECTION_KEYS[2:]},
        "llm_result_status": llm_meta["status"],
        "llm_provider": llm_meta["provider"],
    }


def _signal_detail_venue(
    market: KalshiMarket, match_meta: dict[str, Any] | None
) -> str:
    raw = None
    if isinstance(match_meta, dict):
        raw = match_meta.get("venue")
    if raw is None:
        raw = getattr(market, "venue", None)
    if raw is None:
        return "kalshi"
    if hasattr(raw, "value"):
        raw = raw.value
    text = str(raw).strip().lower()
    return text or "kalshi"


async def _ollama_check_circuit() -> tuple[bool, dict[str, Any] | None]:
    """Circuit-breaker gate before Ollama HTTP commit.

    Returns `(may_proceed, early_meta)`:
      - `(True, None)`: circuit closed (or recovered via probe). Caller may post.
      - `(False, meta)`: circuit open OR probe failed. Caller short-circuits with
        the returned `_llm_meta` dict.

    Side effects (preserved verbatim from prior inline block):
      - Resets `_ollama_consecutive_failures` and `_ollama_down_until` on probe
        success.
      - Extends `_ollama_down_until` on probe failure WITHOUT incrementing the
        failure counter (probes must not keep the circuit permanently open).
      - Emits `[LLM_HEALTH]` warning + ping-success info logs.
    """
    global _ollama_consecutive_failures, _ollama_down_until

    if _ollama_down_until <= 0.0:
        return True, None

    if time.monotonic() < _ollama_down_until:
        return False, _llm_meta(attempted=True, status="ollama_circuit_open", provider="ollama")

    # Probe window: run a cheap ping before committing to 60s inference.
    if not await _ollama_ping():
        _ollama_down_until = time.monotonic() + _OLLAMA_PROBE_INTERVAL
        log.warning(
            "[LLM_HEALTH] provider=ollama probe_failed=true retry_in_s=%d",
            _OLLAMA_PROBE_INTERVAL,
        )
        return False, _llm_meta(attempted=True, status="ollama_probe_failed", provider="ollama")

    log.info(
        "Ollama ping succeeded after %d failures -- attempting inference",
        _ollama_consecutive_failures,
    )
    _ollama_consecutive_failures = 0
    _ollama_down_until = 0.0
    return True, None


def _ollama_record_failure(exc_type: str, latency_ms: int) -> dict[str, Any]:
    """Record a per-request Ollama failure and return the failure `_llm_meta`.

    Handles `exc_type in {"unavailable", "timeout"}` — both share counter-
    increment + circuit-open logic. The generic `Exception` path is NOT
    routed through here because it does not increment the counter (treats
    code errors distinctly from Ollama-availability errors).

    Side effects:
      - Increments `_ollama_consecutive_failures`.
      - Opens the circuit (`_ollama_down_until = ...`) when threshold reached.
      - Emits `[LLM_HEALTH]` warning on circuit open, or a per-exc_type
        debug/warning when threshold not yet reached.
    """
    global _ollama_consecutive_failures, _ollama_down_until

    _ollama_consecutive_failures += 1
    if _ollama_consecutive_failures >= _OLLAMA_FAILURE_THRESHOLD:
        _ollama_down_until = time.monotonic() + _OLLAMA_PROBE_INTERVAL
        log.warning(
            "[LLM_HEALTH] provider=ollama circuit_open=true failures=%d retry_in_s=%d reason=%s",
            _ollama_consecutive_failures, _OLLAMA_PROBE_INTERVAL, exc_type,
        )
    elif exc_type == "unavailable":
        log.debug("Ollama not running -- falling back to keyword scoring")
    elif exc_type == "timeout":
        log.warning(
            "Ollama estimation failed: timed out after %ss (model loading or CPU overloaded)",
            cfg.ollama_request_timeout_seconds,
        )

    status_map = {"unavailable": "ollama_unavailable", "timeout": "ollama_timeout"}
    return _llm_meta(
        attempted=True,
        status=status_map[exc_type],
        provider="ollama",
        latency_ms=latency_ms,
    )


def _ollama_build_payload(news, market) -> dict[str, Any]:
    """Construct the OpenAI-compatible /v1/chat/completions request body.

    Pure: no I/O, no module-state writes. Caller must POST the dict.

    NOTE: `repetition_penalty` and `think` are deliberately absent. Both are
    Ollama-native API fields, NOT standard OpenAI Chat Completions fields, and
    /v1/chat/completions returns 422 for unrecognized fields. See CLAUDE.md
    gotchas about the OpenAI-compat endpoint.
    """
    return {
        "model": cfg.ollama_model,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user",   "content": _build_user_msg(news, market)},
        ],
        "max_tokens": 256,
        "temperature": 0,
    }


def _status_log_marker(status: str) -> str:
    if status in {"ollama_success", "anthropic_success"}:
        return "[LLM_LATENCY]"
    if status in {"ollama_circuit_open", "ollama_probe_failed", "ollama_unavailable"}:
        return "[LLM_HEALTH]"
    return "[LLM_FALLBACK]"


def _emit_llm_observability_log(meta: dict[str, Any], *, ticker: str) -> None:
    status = str(meta.get("status") or "unknown")
    provider = str(meta.get("provider") or "none")
    marker = _status_log_marker(status)
    level = log.debug if marker == "[LLM_LATENCY]" else log.warning
    level(
        "%s provider=%s ticker=%s status=%s attempted=%s result_used=%s "
        "total_ms=%s queue_wait_ms=%s http_ms=%s parse_ms=%s http_status=%s "
        "in_flight_at_entry=%s contention=%s",
        marker,
        provider,
        ticker,
        status,
        meta.get("attempted"),
        meta.get("result_used"),
        meta.get("total_stage_ms", "n/a"),
        meta.get("queue_wait_ms", "n/a"),
        meta.get("http_round_trip_ms", "n/a"),
        meta.get("parse_ms", "n/a"),
        meta.get("http_status", "n/a"),
        meta.get("in_flight_at_entry", "n/a"),
        meta.get("contention_observed", "n/a"),
    )


def _emit_llm_routing_log(*, ticker: str, source: str, reason: str, market_price: float) -> None:
    log.info(
        "[LLM_FALLBACK] provider=route ticker=%s source=%s status=llm_skipped_routing_%s "
        "reason=%s market_price=%.4f",
        ticker,
        source,
        reason,
        reason,
        market_price,
    )


def _finalize_llm_meta(
    meta: dict[str, Any],
    *,
    queue_wait_ms: int,
    total_stage_ms: int,
    contention_observed: bool,
    in_flight_at_entry: int,
) -> dict[str, Any]:
    completed = dict(meta)
    completed["queue_wait_ms"] = queue_wait_ms
    completed["total_stage_ms"] = total_stage_ms
    completed["contention_observed"] = contention_observed
    completed["in_flight_at_entry"] = in_flight_at_entry
    return completed


def _pre_llm_log_fields(
    *,
    match_meta: dict[str, Any] | None,
    keywords_present: bool,
    keyword_override: bool | None = None,
    keyword_override_mode: str | None = None,
    keyword_signal_strength: float | None = None,
    gate_enforced: bool = False,
) -> dict[str, Any]:
    if match_meta is None:
        return {}
    pre_llm_quality_pass = match_meta["pre_llm_quality_pass"]
    if keyword_override is None:
        keyword_override = (not pre_llm_quality_pass and keywords_present)
    fields: dict[str, Any] = {
        "pre_llm_quality_pass": pre_llm_quality_pass,
        "pre_llm_semantic_overlap_count": match_meta["pre_llm_semantic_overlap_count"],
        "pre_llm_semantic_overlap_ratio": match_meta["pre_llm_semantic_overlap_ratio"],
        "pre_llm_would_block": (not pre_llm_quality_pass and not keyword_override),
        "pre_llm_keyword_override": keyword_override,
        "pre_llm_gate_reason": match_meta["pre_llm_gate_reason"],
    }
    if keyword_override_mode is not None:
        fields["pre_llm_keyword_override_mode"] = keyword_override_mode
    if keyword_signal_strength is not None:
        fields["pre_llm_keyword_signal_strength"] = keyword_signal_strength
    if gate_enforced:
        fields["pre_llm_gate_enforced"] = True
    if "pre_llm_headline_token_count" in match_meta:
        fields["pre_llm_headline_token_count"] = match_meta["pre_llm_headline_token_count"]
    if "pre_llm_market_token_count" in match_meta:
        fields["pre_llm_market_token_count"] = match_meta["pre_llm_market_token_count"]
    if "pre_llm_filtered_stopword_count" in match_meta:
        fields["pre_llm_filtered_stopword_count"] = match_meta["pre_llm_filtered_stopword_count"]
    if "pre_llm_filtered_generic_count" in match_meta:
        fields["pre_llm_filtered_generic_count"] = match_meta["pre_llm_filtered_generic_count"]
    if "pre_llm_semantic_token_types" in match_meta:
        fields["pre_llm_semantic_token_types"] = match_meta["pre_llm_semantic_token_types"]
    return fields


def _pre_llm_keyword_override_mode() -> str:
    mode = str(getattr(cfg, "pre_llm_match_gate_keyword_override_mode", "") or "").strip().lower()
    if mode:
        return mode
    return "any_hit" if getattr(cfg, "pre_llm_match_gate_keyword_override_any_hit", True) else "disabled"


def _count_matched_signal_groups(text: str) -> int:
    """Count `GEOPOLITICAL_SIGNALS` groups that have at least one keyword hit.

    Used by the `all_required` keyword override mode, which fires only when every
    configured signal group contributes at least one keyword — see ROADMAP P2.1.
    """
    text_lower = text.lower()
    count = 0
    for sig_def in GEOPOLITICAL_SIGNALS:
        for kw in sig_def["keywords"]:
            if is_keyword_disabled(kw):
                continue
            if kw.lower() in text_lower:
                count += 1
                break
    return count


def _should_keyword_override_pre_llm_gate(
    *,
    match_meta: dict[str, Any] | None,
    keywords_present: bool,
    keyword_signal_strength: float,
    matched_signal_groups: int = 0,
) -> tuple[bool, str]:
    mode = _pre_llm_keyword_override_mode()
    if match_meta is None or match_meta["pre_llm_quality_pass"] or not keywords_present:
        return False, mode
    if mode == "disabled":
        return False, mode
    if mode == "min_signal":
        return keyword_signal_strength >= cfg.pre_llm_match_gate_keyword_override_min_signal, mode
    if mode == "all_required":
        total = len(GEOPOLITICAL_SIGNALS)
        return total > 0 and matched_signal_groups >= total, mode
    return True, mode


def _ollama_http_status_category(status_code: int) -> str:
    if 400 <= status_code <= 499:
        return "ollama_http_4xx"
    if 500 <= status_code <= 599:
        return "ollama_http_5xx"
    return "ollama_http_error"


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

        hits = [
            kw for kw in keywords
            if kw.lower() in text_lower and not is_keyword_disabled(kw)
        ]
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

        hits = [
            kw for kw in keywords
            if kw.lower() in text_lower and not is_keyword_disabled(kw)
        ]
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

    _emit_extraction_trace_step(
        "keyword_path",
        ticker=market.ticker,
        input_signal_magnitude=shift,
        output_signal_magnitude=estimated_prob - base_probability,
        intermediate_state={
            "keywords": keywords,
            "direction": direction,
            "raw_shift": shift,
            "adjusted_shift": adjusted_shift,
            "estimated_probability": estimated_prob,
        },
    )

    return estimated_prob, side, keywords, reasoning


# ── Optional LLM estimation ───────────────────────────────────────────────────

# Magnitude -> probability shift mapping (applied in code, not by the LLM).
# PROFIT-ALIGN-011 (2026-05-25): make the table runtime-tunable via cfg so
# operators can recalibrate from empirical CALIBRATION_OBSERVATION evidence
# (PROFIT-ALIGN-002) without code changes. Defaults preserve the historical
# constants. Env overrides: MAGNITUDE_SHIFT_SMALL / _MODERATE / _LARGE.
def _magnitude_shift_table() -> dict[str, float]:
    """Return the magnitude→shift table from cfg (env-overridable).

    Re-read on every call so an operator override via runtime_overrides
    (future) or env-reload picks up immediately. Performance-trivial.
    """
    return {
        "none":     0.0,
        "small":    float(getattr(cfg, "magnitude_shift_small", 0.08)),
        "moderate": float(getattr(cfg, "magnitude_shift_moderate", 0.15)),
        "large":    float(getattr(cfg, "magnitude_shift_large", 0.25)),
    }


# Legacy module-level mapping retained for tests + existing call sites that
# import `_MAGNITUDE_SHIFT` directly. Reflects current cfg defaults.
_MAGNITUDE_SHIFT = _magnitude_shift_table()

_LLM_SYSTEM_PROMPT = """You are a geopolitical prediction market analyst.

Your task is to determine whether a news headline would cause traders to update
the probability of a specific Kalshi binary market.

You will be given:
MARKET: title and resolution criteria
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
- Classify magnitude based on the evidence in the headline and summary -- do not default to "none" absent evidence of movement.
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
    # v0.29.48 (P0-GATE / P0.4 experiment): `CURRENT YES PRICE` removed from
    # the LLM prompt to test the price-in-prompt anchoring hypothesis. See
    # ROADMAP P0.3 Verdict AMENDMENT (2026-04-24). Original line was
    #   f"CURRENT YES PRICE: {market.yes_price:.1f} cents ({market.yes_prob:.1%})\n"
    # P0.4 experiment closed early per ROADMAP.md:228; revert condition no
    # longer pending. Original line preserved here for reference if anchoring
    # behavior needs to be restored.
    resolution = market.subtitle if market.subtitle else market.title
    return (
        f"MARKET TITLE: {market.title}\n"
        f"RESOLUTION CRITERIA: {resolution}\n"
        f"MARKET CLOSES: {market.close_time}\n\n"
        f"NEWS HEADLINE: {news.headline}\n"
        f"SOURCE: {news.source}\n"
        f"SUMMARY: {news.body[:400] if news.body else '(none)'}"
    )


def _build_prompt_text(news, market) -> str:
    return f"SYSTEM:\n{_LLM_SYSTEM_PROMPT}\n\nUSER:\n{_build_user_msg(news, market)}"


def _emit_llm_prompt_response_debug(
    meta: dict[str, Any],
    *,
    news: NewsItem,
    market: KalshiMarket,
    is_synthetic_probe: bool,
) -> None:
    if is_synthetic_probe:
        return
    prompt = meta.get("prompt")
    raw_response = meta.get("raw_response")
    if not isinstance(prompt, str) or not isinstance(raw_response, str):
        return
    log.debug(
        _json.dumps(
            {
                "type": "LLM_PROMPT_RESPONSE",
                "market_ticker": market.ticker,
                "source": news.source,
                "headline": news.headline,
                "provider": meta.get("provider"),
                "status": meta.get("status"),
                "prompt": prompt,
                "raw_response": raw_response,
            },
            sort_keys=True,
        )
    )


def _probability_from_llm_verdict(
    *,
    market,
    confidence: float,
    direction: str,
    magnitude: str,
    relevant: bool = True,
    new_information: bool = True,
) -> float:
    """Apply the parsed LLM verdict to the current market price."""
    if (
        not relevant
        or not new_information
        or direction == "neutral"
        or magnitude == "none"
    ):
        return market.yes_prob
    base_shift = _magnitude_shift_table().get(magnitude, 0.0)
    shift = base_shift * confidence
    if direction == "yes":
        return min(0.95, market.yes_prob + shift)
    return max(0.05, market.yes_prob - shift)


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

    # PROFIT-LLM-002 (2026-05-24): Reconcile internally-inconsistent LLM
    # output. The prompt enforces ONE side of the consistency rule
    # (new_information=false → direction="neutral" AND magnitude="none")
    # but does NOT enforce the converse. In production, ~3% of LLM runs
    # emit direction in {yes, no} with high confidence (≥0.6) BUT
    # magnitude="none" — silently zeroing out the shift. 7-day funnel
    # diagnostic (2026-05-24) found 10 such records, ALL on
    # KXTXRUNOFFENDORSE markets where Trump's Paxton-over-Cornyn
    # endorsement was the resolution-grade news the bot saw, parsed
    # correctly as direction=no with confidence=0.95, but produced no
    # trade because magnitude was "none". Upgrading to "small" here
    # restores the minimum-credible shift (8 pp * confidence) when the
    # LLM is confident enough in a direction. Threshold 0.6 chosen
    # because the production anomalies cluster at confidence=0.95;
    # 0.6 leaves room for variants without admitting low-conviction
    # directions. NOTE: this modifies the magnitude returned by this
    # function, so downstream stores in paper_trades + SIGNAL_ANALYSIS_DETAIL
    # will record the upgraded magnitude ("small"). The raw LLM output
    # is captured in llm_capture.jsonl (PROFIT-PHASE3-001) for audit;
    # see scripts/edge_replay/llm_capture.py.
    if (
        direction in ("yes", "no")
        and magnitude == "none"
        and confidence >= 0.6
    ):
        magnitude = "small"

    prob = _probability_from_llm_verdict(
        market=market,
        confidence=confidence,
        direction=direction,
        magnitude=magnitude,
        relevant=relevant,
        new_information=new_info,
    )

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
                timeout=aiohttp.ClientTimeout(total=cfg.ollama_probe_timeout_seconds),
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


async def _ollama_post(payload, prompt_text, t0):
    """POST to Ollama OpenAI-compat /v1/chat/completions and extract content text.

    Returns `(content_text, early_meta, http_round_trip_ms, http_status)`:
      - On HTTP success with valid JSON envelope and non-empty content:
        `(text, None, http_round_trip_ms, http_status)`. Caller proceeds.
      - On any HTTP-level failure (non-200, empty body, envelope JSON
        decode error, response shape error, empty content):
        `(None, failure_meta, http_round_trip_ms, http_status)`. Caller
        short-circuits with the returned `_llm_meta`.

    `t0` is the wall-clock start time of the calling LLM stage; used to
    populate `latency_ms` in failure metas (preserves prior semantic).

    Connection-level exceptions (ClientConnectorError, asyncio.TimeoutError)
    propagate to the caller's except handlers — NOT caught here.
    """
    import aiohttp

    async with aiohttp.ClientSession() as session:
        http_start = time.monotonic()
        async with session.post(
            f"{cfg.ollama_base_url}/chat/completions",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=cfg.ollama_request_timeout_seconds),
        ) as resp:
            raw = await resp.text()
            http_round_trip_ms = int((time.monotonic() - http_start) * 1000)
            http_status = resp.status

            if http_status != 200:
                latency_ms = int((time.monotonic() - t0) * 1000)
                body_snippet = raw[:200]
                log.warning(
                    "Ollama HTTP %d from /v1/chat/completions -- body: %s",
                    http_status, body_snippet,
                )
                return None, _llm_meta(
                    attempted=True,
                    status=_ollama_http_status_category(http_status),
                    provider="ollama",
                    latency_ms=latency_ms,
                    http_round_trip_ms=http_round_trip_ms,
                    http_status=http_status,
                    prompt=prompt_text,
                    raw_response=raw,
                ), http_round_trip_ms, http_status

            if not raw.strip():
                latency_ms = int((time.monotonic() - t0) * 1000)
                log.warning("Ollama estimation failed: empty response body")
                return None, _llm_meta(
                    attempted=True,
                    status="ollama_empty_response",
                    provider="ollama",
                    latency_ms=latency_ms,
                    http_round_trip_ms=http_round_trip_ms,
                    http_status=http_status,
                    prompt=prompt_text,
                    raw_response=raw,
                ), http_round_trip_ms, http_status

            try:
                data = _json.loads(raw)
            except _json.JSONDecodeError as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                log.warning("Ollama estimation failed: malformed JSON body -- %s", exc)
                return None, _llm_meta(
                    attempted=True,
                    status="ollama_malformed_response",
                    provider="ollama",
                    latency_ms=latency_ms,
                    http_round_trip_ms=http_round_trip_ms,
                    http_status=http_status,
                    prompt=prompt_text,
                    raw_response=raw,
                ), http_round_trip_ms, http_status

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning("Ollama estimation failed: malformed response shape -- %s", exc)
        return None, _llm_meta(
            attempted=True,
            status="ollama_malformed_response",
            provider="ollama",
            latency_ms=latency_ms,
            http_round_trip_ms=http_round_trip_ms,
            http_status=200,
            prompt=prompt_text,
            raw_response=raw,
        ), http_round_trip_ms, 200

    if not isinstance(text, str) or not text.strip():
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning("Ollama estimation failed: empty choice content")
        return None, _llm_meta(
            attempted=True,
            status="ollama_empty_response",
            provider="ollama",
            latency_ms=latency_ms,
            http_round_trip_ms=http_round_trip_ms,
            http_status=200,
            prompt=prompt_text,
            raw_response=text if isinstance(text, str) else raw,
        ), http_round_trip_ms, 200

    return text, None, http_round_trip_ms, http_status


def _ollama_extract_and_validate(text, market, t0, http_round_trip_ms, prompt_text):
    """Extract LLM JSON from response text, parse, and apply budget gate.

    Returns `(result_tuple_or_None, meta)`:
      - On parse failure: `(None, ollama_parse_failure_meta)`.
      - On budget exceeded: `(None, ollama_slow_budget_exceeded_meta)`.
      - On success: `((prob, confidence, reasoning, direction, magnitude),
                      ollama_success_meta)`.

    PRESERVES `_extract_json(text.strip())` invocation verbatim — load-bearing
    `raw_decode`-based JSON extraction per CLAUDE.md gotcha. Do not rewrap.
    """
    parse_start = time.monotonic()
    try:
        parsed = _extract_json(text.strip())
    except ValueError as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning("Ollama estimation failed: %s", exc)
        return None, _llm_meta(
            attempted=True,
            status="ollama_parse_failure",
            provider="ollama",
            latency_ms=latency_ms,
            http_round_trip_ms=http_round_trip_ms,
            parse_ms=int((time.monotonic() - parse_start) * 1000),
            http_status=200,
            prompt=prompt_text,
            raw_response=text,
        )

    parse_ms = int((time.monotonic() - parse_start) * 1000)
    prob, confidence, reasoning, direction, magnitude = _parse_llm_response(parsed, market)
    latency_ms = int((time.monotonic() - t0) * 1000)

    if latency_ms > cfg.ollama_stage_budget_seconds * 1000:
        log.warning(
            "Ollama estimation exceeded stage budget: %dms > %dms",
            latency_ms,
            cfg.ollama_stage_budget_seconds * 1000,
        )
        return None, _llm_meta(
            attempted=True,
            status="ollama_slow_budget_exceeded",
            provider="ollama",
            latency_ms=latency_ms,
            http_round_trip_ms=http_round_trip_ms,
            parse_ms=parse_ms,
            http_status=200,
            prompt=prompt_text,
            raw_response=text,
        )

    return (prob, confidence, reasoning, direction, magnitude), _llm_meta(
        attempted=True,
        status="ollama_success",
        provider="ollama",
        result_used=True,
        latency_ms=latency_ms,
        http_round_trip_ms=http_round_trip_ms,
        parse_ms=parse_ms,
        http_status=200,
        prompt=prompt_text,
        raw_response=text,
    )


def _i3_capture_signal_llm_call(*, news, market, prompt_text, payload, response_text):
    """I-3 capture hook for the signal-analyzer LLM path.

    Additive: any exception inside the capture layer is swallowed by
    ``capture_llm_response`` itself. This wrapper exists so the call site
    in ``_ollama_estimate_detailed`` stays a single line, and so the
    response-parsing failure (``response_text`` is JSON-extracted text, not
    a structured dict) is also fail-soft.

    The ``response`` field handed to the capture layer is the raw response
    text — preserving operator visibility into the actual bytes the model
    emitted. The repeat-call for verification is intentionally disabled
    here for now; enabling it requires routing an HTTP-bound re-post
    through the same circuit-breaker as ``_ollama_post`` (deferred to a
    follow-up: I-3 ships the WRITE path; I-2 wires the read+replay path).
    """
    try:
        from scripts.edge_replay.llm_capture import (
            capture_llm_response,
            signal_capture_row_id,
        )

        # PROFIT-PHASE3 I-1: build the join key via the shared helper so it
        # exactly matches what PaperTrader.record_trade persists on the trade
        # row (enabling the capture->trade join the corpus builder needs).
        # Uses news.item_id (the dedup hash) -- NOT news.id, which does not
        # exist on NewsItem and previously made every signal on a ticker
        # collide on one empty-news-id key.
        row_id = signal_capture_row_id(
            getattr(market, "ticker", "unknown"),
            getattr(news, "item_id", "") or "",
        )
        capture_llm_response(
            row_id=row_id,
            prompt_template=_LLM_SYSTEM_PROMPT,
            prompt_filled=prompt_text,
            model_id=cfg.ollama_model,
            endpoint_type="openai_compat",
            request_payload=payload,
            response=response_text,
            repeat_call=None,
            logger=log,
        )
    except Exception:  # noqa: BLE001 — additive hook, never fail production
        # capture_llm_response is itself fail-soft, but defend against import
        # errors / unforeseen failures (e.g. cfg.ollama_model missing).
        pass


async def _ollama_estimate_detailed(news, market):
    """
    Call local Ollama server (OpenAI-compatible endpoint).
    Returns (prob, confidence, reasoning) or None if Ollama is not running.
    """
    global _ollama_consecutive_failures, _ollama_down_until

    may_proceed, early_meta = await _ollama_check_circuit()
    if not may_proceed:
        return None, early_meta

    import aiohttp

    prompt_text = _build_prompt_text(news, market)
    payload = _ollama_build_payload(news, market)

    t0 = time.monotonic()
    try:
        text, early_meta, http_round_trip_ms, _http_status = await _ollama_post(
            payload, prompt_text, t0
        )
        if early_meta is not None:
            return None, early_meta

        result, meta = _ollama_extract_and_validate(
            text, market, t0, http_round_trip_ms, prompt_text
        )
        if result is None:
            return None, meta

        if _ollama_consecutive_failures > 0:
            log.info(
                "[LLM_HEALTH] provider=ollama recovered=true failures=%d",
                _ollama_consecutive_failures,
            )
            _ollama_consecutive_failures = 0
            _ollama_down_until = 0.0
        log.debug("Ollama: dir=%s mag=%s conf=%.2f -> prob=%.3f for %s",
                  result[3], result[4], result[1], result[0], market.ticker)

        # I-3 capture (rapid-learning v3) — additive only. Failures inside
        # capture_llm_response are swallowed; production return path is
        # untouched. Endpoint type is "openai_compat" per CLAUDE.md gotcha:
        # this path posts to /v1/chat/completions and `think: False` does
        # NOT apply here.
        _i3_capture_signal_llm_call(
            news=news,
            market=market,
            prompt_text=prompt_text,
            payload=payload,
            response_text=text,
        )

        return result, meta

    except aiohttp.ClientConnectorError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return None, _ollama_record_failure("unavailable", latency_ms)
    except asyncio.TimeoutError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return None, _ollama_record_failure("timeout", latency_ms)
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning("Ollama estimation failed: %s (%s)", exc, type(exc).__name__)
        return None, _llm_meta(attempted=True, status="ollama_error", provider="ollama", latency_ms=latency_ms)


async def _anthropic_estimate_detailed(news, market):
    """
    Call Anthropic Claude API (fallback if Ollama unavailable).
    Returns None if ANTHROPIC_API_KEY is not set or the call fails.
    """
    if not cfg.anthropic_api_key:
        return None, _llm_meta(attempted=False, status="anthropic_unavailable", provider="anthropic")

    t0 = time.monotonic()
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        user_msg = _build_user_msg(news, market)
        prompt_text = _build_prompt_text(news, market)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        text   = response.content[0].text.strip()
        try:
            parsed = _extract_json(text)
        except ValueError as exc:
            log.warning("Anthropic estimation failed: %s", exc)
            return None, _llm_meta(
                attempted=True,
                status="anthropic_parse_failure",
                provider="anthropic",
                latency_ms=latency_ms,
                prompt=prompt_text,
                raw_response=text,
            )
        prob, confidence, reasoning, direction, magnitude = _parse_llm_response(parsed, market)
        log.debug("Anthropic: dir=%s mag=%s conf=%.2f -> prob=%.3f for %s",
                  direction, magnitude, confidence, prob, market.ticker)
        return (prob, confidence, reasoning, direction, magnitude), _llm_meta(
            attempted=True,
            status="anthropic_success",
            provider="anthropic",
            result_used=True,
            latency_ms=latency_ms,
            prompt=prompt_text,
            raw_response=text,
        )

    except ImportError:
        log.warning("anthropic package not installed -- Anthropic estimation disabled")
        return None, _llm_meta(attempted=True, status="anthropic_import_error", provider="anthropic")
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.warning("Anthropic estimation failed: %s", exc)
        return None, _llm_meta(attempted=True, status="anthropic_error", provider="anthropic", latency_ms=latency_ms)


async def llm_estimate_detailed(news, market):
    """
    Best-available LLM estimate. Tries Ollama first (free, local, no rate
    limits), then Anthropic (requires ANTHROPIC_API_KEY). Returns None if
    both are unavailable, triggering keyword-only fallback in the caller.

    Serialized via _LLM_SEMAPHORE — only one call runs at a time regardless
    of how many consumer workers are active.
    """
    global _llm_in_flight

    queue_started = time.monotonic()
    contention_observed = _LLM_SEMAPHORE.locked() or _llm_in_flight > 0
    in_flight_at_entry = _llm_in_flight

    async with _LLM_SEMAPHORE:
        queue_wait_ms = int((time.monotonic() - queue_started) * 1000)
        stage_started = time.monotonic()
        _llm_in_flight += 1
        try:
            result, meta = await _ollama_estimate_detailed(news, market)
            if result:
                meta = _finalize_llm_meta(
                    meta,
                    queue_wait_ms=queue_wait_ms,
                    total_stage_ms=queue_wait_ms + int((time.monotonic() - stage_started) * 1000),
                    contention_observed=contention_observed,
                    in_flight_at_entry=in_flight_at_entry,
                )
                _emit_llm_observability_log(meta, ticker=market.ticker)
                return result, meta

            anthropic_result, anthropic_meta = await _anthropic_estimate_detailed(news, market)
            if anthropic_result:
                anthropic_meta = _finalize_llm_meta(
                    anthropic_meta,
                    queue_wait_ms=queue_wait_ms,
                    total_stage_ms=queue_wait_ms + int((time.monotonic() - stage_started) * 1000),
                    contention_observed=contention_observed,
                    in_flight_at_entry=in_flight_at_entry,
                )
                _emit_llm_observability_log(anthropic_meta, ticker=market.ticker)
                return anthropic_result, anthropic_meta

            final_meta = anthropic_meta if anthropic_meta["attempted"] else meta if meta["attempted"] else _llm_meta(
                attempted=False,
                status="no_provider_available",
                provider=None,
            )
            final_meta = _finalize_llm_meta(
                final_meta,
                queue_wait_ms=queue_wait_ms,
                total_stage_ms=queue_wait_ms + int((time.monotonic() - stage_started) * 1000),
                contention_observed=contention_observed,
                in_flight_at_entry=in_flight_at_entry,
            )
            _emit_llm_observability_log(final_meta, ticker=market.ticker)
            return None, final_meta
        finally:
            _llm_in_flight = max(0, _llm_in_flight - 1)


async def llm_estimate(news, market):
    result, _meta = await llm_estimate_detailed(news, market)
    return result


# ── Combined estimator ────────────────────────────────────────────────────────

async def estimate_probability(
    news: NewsItem,
    market: KalshiMarket,
    keyword_stats=None,   # KeywordStats instance or None
    match_meta: dict[str, Any] | None = None,
    is_startup_probe: bool = False,
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
    # P-5 CR-C / LD-2: refuse to operate on markets with no executable
    # price. Returning a sentinel (base_probability=0.5, no keywords) is
    # the upstream-safe equivalent of the legacy "no signal" path; the
    # caller in main.py already short-circuits on empty keywords+no LLM
    # signal. Defensive `getattr` keeps SimpleNamespace probe stubs
    # compatible — they default to "tradeable".
    if not getattr(market, "price_available", True):
        log.debug(
            "[ANALYSIS] price_unavailable ticker=%s source=%s -- estimate_probability skipped",
            market.ticker,
            news.source,
        )
        return 0.5, 0.0, [], "price_unavailable -- no estimate", None, None, None
    # Always run keyword scoring for the keyword list
    base_probability = market.yes_prob
    combined_text = f"{news.headline} {news.body}"
    series_ticker = getattr(market, "series_ticker", "") or ""
    kw_prob, kw_side, keywords, kw_reasoning = keyword_estimate(
        news, market, keyword_stats=keyword_stats
    )
    keywords_present = bool(keywords)
    keyword_signal_strength = abs(kw_prob - base_probability)
    matched_signal_groups = _count_matched_signal_groups(combined_text) if keywords_present else 0
    keyword_override, keyword_override_mode = _should_keyword_override_pre_llm_gate(
        match_meta=match_meta,
        keywords_present=keywords_present,
        keyword_signal_strength=keyword_signal_strength,
        matched_signal_groups=matched_signal_groups,
    )
    pre_llm_fields = _pre_llm_log_fields(
        match_meta=match_meta,
        keywords_present=keywords_present,
        keyword_override=keyword_override,
        keyword_override_mode=keyword_override_mode,
        keyword_signal_strength=keyword_signal_strength,
    )
    signal_detail_venue = _signal_detail_venue(market, match_meta)
    probe_fields = (
        {"is_startup_probe": True, "is_synthetic_probe": True}
        if is_startup_probe
        else {}
    )
    keyword_contribs = _keyword_contributions(
        combined_text, keyword_stats=keyword_stats, series_ticker=series_ticker
    )

    routing_reason = _llm_routing_reason(news, market)
    pre_llm_gate_enforced = bool(
        cfg.enable_pre_llm_match_gate
        and not cfg.pre_llm_match_gate_diagnostics_only
        and routing_reason is None
        and pre_llm_fields.get("pre_llm_would_block", False)
    )
    if pre_llm_gate_enforced:
        llm_meta = _llm_meta(
            attempted=False,
            status="llm_skipped_match_quality_gate",
            provider=None,
            result_used=False,
        )
        log.debug(
            "[PRE_LLM_GATE] suppressed ticker=%s source=%s reason=%s keyword_override=%s",
            market.ticker,
            news.source,
            pre_llm_fields.get("pre_llm_gate_reason"),
            pre_llm_fields.get("pre_llm_keyword_override"),
        )
        llm_result = None
        pre_llm_fields = _pre_llm_log_fields(
            match_meta=match_meta,
            keywords_present=keywords_present,
            keyword_override=keyword_override,
            keyword_override_mode=keyword_override_mode,
            keyword_signal_strength=keyword_signal_strength,
            gate_enforced=True,
        )
    elif routing_reason is not None:
        llm_meta = _llm_meta(
            attempted=False,
            status=f"llm_skipped_routing_{routing_reason}",
            provider=None,
            result_used=False,
        )
        _emit_llm_routing_log(
            ticker=market.ticker,
            source=news.source,
            reason=routing_reason,
            market_price=market.yes_prob,
        )
        await write_trade_log_async(
            trade_log.log_llm_skipped_routing,
            ticker=market.ticker,
            source=news.source,
            headline=news.headline,
            reason=routing_reason,
            market_price=market.yes_prob,
        )
        llm_result = None
    else:
        # PROFIT-ALIGN-010 (2026-05-25): runtime LLM dedup cache. Same
        # prompt within TTL → reuse prior LLM verdict fields, skip the LLM
        # call, then recompute probability against the current market quote.
        # Distinct from the replay-CI llm_cache.sqlite
        # (deterministic-replay only). See analysis/llm_dedup_cache.py.
        from analysis.llm_dedup_cache import (
            lookup as _llm_dedup_lookup,
            store as _llm_dedup_store,
        )
        _cache_ttl = int(getattr(cfg, "llm_dedup_cache_ttl_seconds", 0))
        _prompt_text = _build_prompt_text(news, market)
        _cached = _llm_dedup_lookup(
            prompt_text=_prompt_text,
            ttl_seconds=_cache_ttl,
        )
        if _cached is not None:
            _confidence, _reasoning, _direction, _magnitude = _cached
            _prob = _probability_from_llm_verdict(
                market=market,
                confidence=float(_confidence),
                direction=str(_direction),
                magnitude=str(_magnitude),
            )
            llm_result = (_prob, _confidence, _reasoning, _direction, _magnitude)
            llm_meta = _llm_meta(
                attempted=False,
                status="llm_dedup_cache_hit",
                provider="cache",
                result_used=True,
            )
        else:
            llm_result, llm_meta = await llm_estimate_detailed(news, market)
            if llm_result is not None:
                _, _confidence, _reasoning, _direction, _magnitude = llm_result
                _llm_dedup_store(
                    prompt_text=_prompt_text,
                    result=(_confidence, _reasoning, _direction, _magnitude),
                    ttl_seconds=_cache_ttl,
                )
        _emit_llm_prompt_response_debug(
            llm_meta,
            news=news,
            market=market,
            is_synthetic_probe=is_startup_probe,
        )

    if llm_result:
        llm_prob, llm_confidence, llm_reasoning, llm_direction, llm_magnitude = llm_result
        llm_probability_movement = abs(llm_prob - base_probability)
        llm_useful = llm_probability_movement >= 0.01
        pre_llm_would_block_and_useful = (
            pre_llm_fields.get("pre_llm_would_block", False) and llm_useful
        )
        # Use LLM probability directly -- keywords only gate the match, not the estimate.
        # Blending was removed because it allowed keyword scores to manufacture bets
        # that the LLM explicitly said were not market-moving (e.g. LLM: 0.50 + keywords
        # push kw_prob to 0.65 -> blended 0.545 -> bet placed against LLM advice).
        reasoning = (
            f"[LLM] {llm_reasoning} "
            f"(LLM: {llm_prob:.3f}, Keywords(ref): {kw_prob:.3f})"
        )
        _emit_extraction_trace_step(
            "llm_path",
            ticker=market.ticker,
            input_signal_magnitude=kw_prob - base_probability,
            output_signal_magnitude=llm_prob - base_probability,
            intermediate_state={
                "llm_direction": llm_direction,
                "llm_magnitude": llm_magnitude,
                "llm_confidence": llm_confidence,
                "llm_probability": llm_prob,
                "keyword_probability": kw_prob,
            },
        )
        detail = SignalAnalysisDetail(
            ticker=market.ticker,
            source=news.source,
            headline=news.headline,
            method="llm",
            keywords=keywords,
            keyword_contributions=keyword_contribs,
            base_probability=base_probability,
            final_probability=llm_prob,
            market_price=market.yes_prob,
            venue=signal_detail_venue,
            llm_direction=llm_direction,
            llm_magnitude=llm_magnitude,
            llm_confidence=llm_confidence,
            llm_result_used=True,
            llm_routing_passed=True,
            llm_probability_movement=llm_probability_movement,
            llm_useful=llm_useful,
            pre_llm_would_block_and_useful=pre_llm_would_block_and_useful,
            **_build_llm_meta_kwargs(llm_meta),
            **probe_fields,
            **pre_llm_fields,
        )
        await write_trade_log_async(trade_log.log_signal_analysis_detail, detail)

        # PROFIT-MATCH-DYNAMIC (commit 2/5): emit per-call feedback signal
        # for the matcher learning loop. Inference rules:
        #   - direction in {yes, no}                      → true_positive
        #   - dir=neutral + mag=none + confidence >= 0.7  → false_positive_neutral
        #   - otherwise (low-confidence neutral)          → undetermined, skip
        # Aggregator (see scripts/match_feedback_aggregator.py) rolls these
        # into per-(token × market_prefix) FP counters that feed the runtime
        # downweight list at data/matcher_token_weights.json.
        verdict = None
        if llm_direction in ("yes", "no"):
            verdict = "true_positive"
        elif (
            llm_direction == "neutral"
            and llm_magnitude == "none"
            and llm_confidence is not None
            and float(llm_confidence) >= 0.7
        ):
            verdict = "false_positive_neutral"
        if verdict:
            try:
                from analysis.market_matcher import _tokenize as _matcher_tokenize
                meta_tokens = []
                if isinstance(match_meta, dict):
                    raw_tokens = (
                        match_meta.get("matched_tokens")
                        or match_meta.get("polymarket_matched_tokens")
                        or match_meta.get("pre_llm_semantic_overlap_tokens")
                    )
                    if isinstance(raw_tokens, list):
                        meta_tokens = [str(token) for token in raw_tokens]
                overlap_tokens = (
                    sorted(meta_tokens)
                    if meta_tokens
                    else sorted(
                        _matcher_tokenize(market.title or "")
                        & _matcher_tokenize(news.headline or "")
                    )
                )
            except Exception:
                overlap_tokens = []
            from analysis.match_feedback import market_prefix_for as _market_prefix_for
            ticker_prefix = _market_prefix_for(market)
            review_venue = None
            if isinstance(match_meta, dict):
                raw_venue = match_meta.get("venue")
                if raw_venue is not None:
                    review_venue = str(raw_venue).strip().lower() or None
            if review_venue is None:
                raw_venue = getattr(market, "venue", None)
                if hasattr(raw_venue, "value"):
                    raw_venue = raw_venue.value
                if raw_venue is not None:
                    review_venue = str(raw_venue).strip().lower() or None
            if review_venue is None and ticker_prefix == "polymarket_us":
                review_venue = ticker_prefix
            # PROFIT-MATCH-003 (L2-a): carry the matcher score (threaded onto
            # match_meta by main._process_candidate) so the feedback loop can
            # score-gate the false_positive_neutral signal. None when absent
            # (e.g. the startup probe) -> consumer treats it as marginal.
            review_match_score = None
            if isinstance(match_meta, dict):
                raw_score = match_meta.get("match_score")
                if raw_score is not None:
                    try:
                        review_match_score = float(raw_score)
                    except (TypeError, ValueError):
                        review_match_score = None
            await write_trade_log_async(
                trade_log.log_match_llm_review,
                ticker=market.ticker,
                market_title=market.title or "",
                market_prefix=ticker_prefix,
                venue=review_venue,
                headline=news.headline or "",
                source=news.source or "",
                matched_tokens=overlap_tokens,
                llm_relevant=(verdict != "false_positive_neutral"),
                llm_direction=llm_direction,
                llm_magnitude=llm_magnitude,
                llm_confidence=float(llm_confidence) if llm_confidence is not None else 0.0,
                verdict=verdict,
                match_score=review_match_score,
            )

        return llm_prob, llm_confidence, keywords, reasoning, llm_direction, llm_magnitude, llm_confidence

    if not keywords:
        # No keyword support and no LLM estimate available: stop here.
        _emit_extraction_trace_step(
            "final_estimate",
            ticker=market.ticker,
            input_signal_magnitude=kw_prob - base_probability,
            output_signal_magnitude=0.0,
            intermediate_state={
                "reason": "no_keywords_no_llm_estimate",
                "estimated_probability": market.yes_prob,
                "llm_status": llm_meta["status"],
            },
        )
        detail = SignalAnalysisDetail(
            ticker=market.ticker,
            source=news.source,
            headline=news.headline,
            method="keyword_gate",
            keywords=[],
            keyword_contributions=[],
            base_probability=base_probability,
            final_probability=market.yes_prob,
            market_price=market.yes_prob,
            venue=signal_detail_venue,
            llm_result_used=False,
            llm_routing_passed=(routing_reason is None),
            llm_routing_reason=routing_reason,
            **_build_llm_meta_kwargs(llm_meta),
            **probe_fields,
            **pre_llm_fields,
        )
        await write_trade_log_async(trade_log.log_signal_analysis_detail, detail)
        return market.yes_prob, 0.1, [], "No relevant keywords found -- no signal.", None, None, None

    # Keyword only
    confidence = min(0.7, 0.3 + 0.05 * len(keywords))   # more keywords -> more confident
    _emit_extraction_trace_step(
        "final_estimate",
        ticker=market.ticker,
        input_signal_magnitude=kw_prob - base_probability,
        output_signal_magnitude=kw_prob - base_probability,
        intermediate_state={
            "reason": "keyword_only",
            "estimated_probability": kw_prob,
            "confidence": confidence,
        },
    )
    detail = SignalAnalysisDetail(
        ticker=market.ticker,
        source=news.source,
        headline=news.headline,
        method="keyword",
        keywords=keywords,
        keyword_contributions=keyword_contribs,
        base_probability=base_probability,
        final_probability=kw_prob,
        market_price=market.yes_prob,
        venue=signal_detail_venue,
        llm_result_used=False,
        llm_routing_passed=(routing_reason is None),
        llm_routing_reason=routing_reason,
        **_build_llm_meta_kwargs(llm_meta),
        **probe_fields,
        **pre_llm_fields,
    )
    await write_trade_log_async(trade_log.log_signal_analysis_detail, detail)
    return kw_prob, confidence, keywords, kw_reasoning, None, None, None
