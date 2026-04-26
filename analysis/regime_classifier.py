"""
Regime classifier: computes a probabilistic regime weight vector for a market.

Returns {fast, interpretation, structural} weights summing to 1.0. These weights
drive how the decision blender weighs each lane's output — they do not block lanes.

Classification priority:
  1. Series-ticker prefix  (strongest signal — categorical)
  2. Time to close         (primary fallback — continuous)
  3. Title keyword nudges  (secondary adjustment — directional hints)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from kalshi import KalshiMarket

# ── Weight keys ───────────────────────────────────────────────────────────────

FAST           = "fast"
INTERPRETATION = "interpretation"
STRUCTURAL     = "structural"

_LANES = (FAST, INTERPRETATION, STRUCTURAL)

# ── Series-ticker prefix priors ───────────────────────────────────────────────
#
# Each entry maps a ticker prefix to (fast, interpretation, structural).
# Sports outcomes are driven by live events — speed advantage dominates.
# Monetary policy / polling evolve slowly — structural prior dominates.
# Crypto price markets are fast-moving but interpretation-heavy.

_SERIES_PRIORS: dict[str, tuple[float, float, float]] = {
    # US sports
    "KXNCAA":        (0.85, 0.10, 0.05),
    "KXNFL":         (0.85, 0.10, 0.05),
    "KXNBA":         (0.85, 0.10, 0.05),
    "KXMLB":         (0.85, 0.10, 0.05),
    "KXNHL":         (0.85, 0.10, 0.05),
    "KXMLS":         (0.85, 0.10, 0.05),
    # International soccer / sports leagues
    "KXSOCCER":      (0.85, 0.10, 0.05),
    "KXUCL":         (0.85, 0.10, 0.05),
    "KXUEL":         (0.85, 0.10, 0.05),
    "KXSAUDIPL":     (0.85, 0.10, 0.05),
    "KXJLEAGUE":     (0.85, 0.10, 0.05),
    "KXJBLEAGUE":    (0.85, 0.10, 0.05),
    "KXKLEAGUE":     (0.85, 0.10, 0.05),
    "KXLIGUE1":      (0.85, 0.10, 0.05),
    "KXBUNDESLIGA":  (0.85, 0.10, 0.05),
    "KXVENFUTVE":    (0.85, 0.10, 0.05),
    "KXBSL":         (0.85, 0.10, 0.05),
    "KXFIBAECUP":    (0.85, 0.10, 0.05),
    # Other individual sports
    "KXTENNIS":      (0.85, 0.10, 0.05),
    "KXGOLF":        (0.80, 0.15, 0.05),
    "KXBOXING":      (0.85, 0.10, 0.05),
    "KXMMA":         (0.85, 0.10, 0.05),
    "KXNASCAR":      (0.85, 0.10, 0.05),
    "KXFORMULA":     (0.85, 0.10, 0.05),
    "KXOLYMPIC":     (0.70, 0.20, 0.10),
    "KXMVESPORT":    (0.85, 0.10, 0.05),
    "KXMVECROSS":    (0.70, 0.20, 0.10),
    # Central bank rate decisions — known calendar, structural
    "KXCBDECISION":  (0.05, 0.30, 0.65),
    # Polling and approval ratings — slow-moving, structural
    "KXAPRPOTUS":    (0.05, 0.25, 0.70),
    "KXPOLLPOTUS":   (0.05, 0.25, 0.70),
    # Trump say markets — speech-driven, fast/interpretation
    "KXTRUMPSAY":        (0.55, 0.35, 0.10),
    "KXTRUMPSAYMONTH":   (0.30, 0.45, 0.25),
    # ── Legislative / calendar-driven policy markets ─────────────────────────
    # Bills, budget resolutions, scheduled votes — slow legislative cadence
    # with intermittent fast events. Interpretation lane carries the most
    # weight because the read-through from a news event to the binary
    # outcome requires synthesis (e.g. "does this floor vote count as
    # resolving the bill?"). Structural anchor for the calendar; minimal
    # fast contribution since legislative timing rarely breaks suddenly.
    # Weights chosen so regime_confidence (1 - H/log(3)) ≥ 0.20 — the
    # threshold below which G4 fails the readiness gate (see
    # tasks/trade_readiness_gate.py:G4_REGIME_CONFIDENCE_THRESHOLD).
    "KXSBUDGETRES":  (0.05, 0.65, 0.30),  # Budget / continuing resolution / ICE funding (rc≈0.28)
    "KXFISAEXTEND":  (0.05, 0.65, 0.30),  # FISA extension legislation (rc≈0.28)
    "KXVOTESAVEAMERICA": (0.05, 0.60, 0.35),  # Save America vote events (rc≈0.24)
    "KXEFFTARIFF":   (0.05, 0.60, 0.35),  # Effective tariff schedule (rc≈0.24)
    "KXMOCTRUMP25":  (0.10, 0.65, 0.25),  # Trump month-of-action calendar windows (rc≈0.22)
    # Macroeconomic data releases — known calendar, structural prior dominates,
    # interpretation handles the surprise-vs-consensus read-through. Same
    # shape as the existing KXCBDECISION prior (rc≈0.28).
    "KXCPIYOY":      (0.05, 0.30, 0.65),  # Headline CPI YoY
    "KXCPICOREYOY":  (0.05, 0.30, 0.65),  # Core CPI YoY
    "KXCPIEU":       (0.05, 0.30, 0.65),  # Euro-area inflation
    "KXEZGDPYOYF":   (0.05, 0.30, 0.65),  # Euro-area GDP forecast
    # ── Event-driven political / diplomatic markets ──────────────────────────
    # Decisions and announcements that may break suddenly. Fast lane carries
    # speed advantage; interpretation handles ambiguous wording. Low
    # structural weight because the timing is event-driven, not calendar.
    # Weights concentrated to clear G4=0.20 (see Trump-say existing prior at
    # rc=0.157 — too dispersed; we err on the more concentrated side here).
    "KXTRUMPACT":    (0.65, 0.25, 0.10),  # Executive actions / orders (rc≈0.22)
    "KXTRUMPENDORSE":(0.65, 0.25, 0.10),  # Endorsement events (rc≈0.22)
    "KXTRUMPCHINA":  (0.65, 0.25, 0.10),  # China-related decisions (rc≈0.22)
    "KXTRUMPCRYPTOCONF": (0.65, 0.25, 0.10),  # Crypto conference attendance / announcements (rc≈0.22)
    "KXVANCEPAKISTAN":(0.65, 0.25, 0.10),  # Vance / Pakistan diplomacy (rc≈0.22)
    "KXVISITVENEZUELA":(0.65, 0.25, 0.10),  # Venezuela visit / diplomacy (rc≈0.22)
    "KXPARDONSTRUMP":(0.15, 0.70, 0.15),  # Pardon decisions — interpretation-heavy (rc≈0.25)
    "KXLTGOVGANOMR": (0.10, 0.70, 0.20),  # Long-tail policy / personnel decisions (rc≈0.27)
    # ── Conflict / military events ───────────────────────────────────────────
    # Sudden kinetic events. Speed dominates; interpretation handles
    # escalation framing; structural lane has minimal calendar to anchor on.
    "KXTRUMPIRAN":   (0.70, 0.20, 0.10),  # Iran diplomacy / kinetic events (rc≈0.27)
    "KXARMOMINF":    (0.70, 0.20, 0.10),  # Armed-conflict / military (rc≈0.27)
    "KXELECTIONEMERGENCY": (0.70, 0.20, 0.10),  # Crisis / emergency events (rc≈0.27)
    # Crypto price markets — rapid, but direction requires interpretation
    "KXCRYPTO":      (0.65, 0.28, 0.07),
    "KXDOGE":        (0.65, 0.28, 0.07),
    "KXBTC":         (0.65, 0.28, 0.07),
    "KXETH":         (0.65, 0.28, 0.07),
    "KXSOL":         (0.65, 0.28, 0.07),
    "KXXRP":         (0.65, 0.28, 0.07),
    # Weather — meteorological base rates dominate
    "KXWEATHER":     (0.10, 0.25, 0.65),
    # Entertainment — event-based with interpretation component
    "KXENTERTAIN":   (0.40, 0.45, 0.15),
}

# ── Title keyword nudges ──────────────────────────────────────────────────────
#
# Minor directional adjustments applied after the primary classification.
# Each nudge shifts the named lane by +amount, taken equally from the
# other two. Applied once per matched set — not accumulated per keyword.

_STRUCTURAL_TITLE_SIGNALS: frozenset[str] = frozenset({
    "federal reserve", "fed rate", "interest rate", "rate cut", "rate hike",
    "cpi", "gdp", "inflation", "unemployment rate", "jobs report", "nonfarm",
    "approval rating", "polling average", "electoral college",
    "supreme court", "constitutional amendment",
})

_FAST_TITLE_SIGNALS: frozenset[str] = frozenset({
    "breaking", "developing", "just in", "live",
    "will score", "win tonight", "win today", "game winner", "overtime",
})

_NUDGE = 0.08  # weight units shifted per matched signal set


# ── Internal helpers ──────────────────────────────────────────────────────────

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


def _series_prior(market: KalshiMarket) -> Optional[tuple[float, float, float]]:
    """Return a regime prior tuple based on series-ticker prefix, or None."""
    # Check both series_ticker and the market's own ticker (series_ticker can be
    # empty even when the market's ticker has a recognisable prefix — see lessons.md).
    for candidate in (market.series_ticker or "", market.ticker):
        upper = candidate.upper()
        for prefix, weights in _SERIES_PRIORS.items():
            if upper.startswith(prefix):
                return weights
    return None


def _time_prior(days: float) -> tuple[float, float, float]:
    """Return (fast, interpretation, structural) based on days to close."""
    if days <= 0.25:    # ≤ 6 hours
        return (0.85, 0.10, 0.05)
    if days <= 1.0:     # 6 h – 1 day
        return (0.70, 0.22, 0.08)
    if days <= 3.0:     # 1 – 3 days
        return (0.45, 0.40, 0.15)
    if days <= 7.0:     # 3 – 7 days
        return (0.20, 0.50, 0.30)
    if days <= 14.0:    # 7 – 14 days
        return (0.10, 0.45, 0.45)
    return (0.10, 0.25, 0.65)  # > 14 days


def _apply_title_nudge(
    weights: tuple[float, float, float],
    title: str,
) -> tuple[float, float, float]:
    """Shift weights by _NUDGE toward the signalled lane, if any title signal matches."""
    lower = title.lower()
    fast_hit        = any(sig in lower for sig in _FAST_TITLE_SIGNALS)
    structural_hit  = any(sig in lower for sig in _STRUCTURAL_TITLE_SIGNALS)

    if not fast_hit and not structural_hit:
        return weights

    f, i, s = weights

    if fast_hit and not structural_hit:
        # Shift _NUDGE from (interpretation + structural) equally into fast.
        half = _NUDGE / 2
        f += _NUDGE
        i -= half
        s -= half
    elif structural_hit and not fast_hit:
        half = _NUDGE / 2
        s += _NUDGE
        f -= half
        i -= half
    # If both match, signals cancel — return unchanged.

    return (f, i, s)


def _normalize(weights: tuple[float, float, float]) -> dict[str, float]:
    """Clamp all weights to [0.02, 0.96] and normalize to sum to 1.0."""
    _MIN = 0.02
    clamped = tuple(max(_MIN, w) for w in weights)
    total = sum(clamped)
    f, i, s = (w / total for w in clamped)
    return {FAST: round(f, 4), INTERPRETATION: round(i, 4), STRUCTURAL: round(s, 4)}


# ── Public API ────────────────────────────────────────────────────────────────

def compute_regime_weights(market: KalshiMarket) -> dict[str, float]:
    """Return regime weight vector {fast, interpretation, structural} summing to 1.0.

    Classification order:
      1. Series-ticker prefix match  → categorical prior
      2. Time to close               → continuous fallback
      3. Market title keyword nudge  → minor directional adjustment
    """
    # Step 1: series-ticker prior
    raw = _series_prior(market)

    # Step 2: time-based fallback
    if raw is None:
        days = _days_to_close(market.close_time)
        raw = _time_prior(days) if days is not None else (0.33, 0.34, 0.33)

    # Step 3: title keyword nudge (applied to both series and time priors)
    title = f"{market.title} {market.subtitle}"
    raw = _apply_title_nudge(raw, title)

    return _normalize(raw)
