"""PROFIT-MATCH-DYNAMIC commit 3/5 — matcher-feedback aggregator + token weights.

The matcher emits MATCH_DIAGNOSTIC for every candidate (headline, market) pair.
The signal-analyzer emits MATCH_LLM_REVIEW for every pair that survived to the
LLM (verdict ∈ {true_positive, false_positive_neutral}). This module rolls
those events into per-(token × market_prefix) counters that the matcher
consumes as runtime token downweights.

State lives in two files (both gitignored, runtime-managed):

  - ``data/match_token_fp_counters.db`` — append-only SQLite, one row per
    MATCH_LLM_REVIEW event aggregated into a rolling 14-day counter table.
  - ``data/matcher_token_weights.json`` — runtime-readable downweight map
    keyed by ``"{market_prefix}:{token}"``, values in ``[0.10, 1.00]``.
    Matcher (commit 4/5) reads this on every tokenization pass.

The auto-update rule (commit 5/5 builds on this module):

    downweight(token, prefix) = max(0.10, 1.0 - fp_rate)
    where fp_rate = false_positive_neutral_count /
                    (false_positive_neutral_count + true_positive_count)

    Active only when (true_positive_count + false_positive_neutral_count) >= MIN_TOTAL.
    Recovery: downweight returns to 1.0 when fp_rate < RECOVERY_THRESHOLD over
    the most recent 7d window.

Hard floor 0.10 prevents the loop from completely silencing a token —
legitimate edge cases survive at low weight, can recover.

Operator override path: seed weights can be hand-edited into the JSON file;
the aggregator preserves operator-pinned entries (marked with ``"pinned": true``)
and never overwrites them.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from polymarket.domain_key import pm_domain_key


class MatcherWeightsUnverified(RuntimeError):
    """Raised when matcher token weights are present but unsafe to use."""

# Tunables. Operator may bump via runtime overrides in a follow-up.
MIN_TOTAL_FOR_DOWNWEIGHT: int = 10
"""Minimum (TP + FP) observations on a (token, prefix) pair before its
downweight is computed. Below this, weight stays 1.0 (no penalty).

Set deliberately low (10) per operator request "we're not waiting 14d" —
the seed list (commit 5/5) handles cold-start, this floor handles
ongoing learn-rate for new patterns."""

DOWNWEIGHT_FLOOR: float = 0.10
"""Never auto-drop a token's weight below this. Preserves residual signal
for legitimate edge cases. Operator can override via pinned entries."""

RECOVERY_THRESHOLD: float = 0.25
"""When a previously-downweighted token's recent fp_rate drops below this,
weight returns to 1.0. Prevents permanent damnation from a transient
news cycle."""

ACTIVATE_THRESHOLD: float = 0.40
"""fp_rate must EXCEED this for an auto-downweight to engage. Tight
threshold avoids penalizing borderline matchers (legitimate edges
sometimes look like false positives at low N)."""

L2_NEUTRAL_FP_MARGINAL_MAX_SCORE: float = 0.12
"""PROFIT-MATCH-003 (L2-a): a ``false_positive_neutral`` verdict only counts
toward downweighting when the match's ``match_score`` is BELOW this band. The
loop's only negative signal is "the LLM saw the right market but this headline
carried no directional edge" -- which on a clearly-correct (higher-score) match
is *not* evidence of a wrong match, so counting it poisons correct markets
(PROFIT-MATCH-002 was the defining-token symptom; this is the root). Neutral
verdicts on marginal (low-score, possibly wrong-market) matches still count.
``true_positive`` always counts. Missing match_score is treated as marginal
(conservative -- preserves prior behaviour). The producer side that emits
``match_score`` onto MATCH_LLM_REVIEW is wired in main._process_candidate +
analysis/signal_analyzer + utils/logger (without it this gate is a no-op).
Tunable; validate via replay-EV (the bootstrap corpus) before tightening."""

_DB_PATH_DEFAULT: Path = Path("data/match_token_fp_counters.db")
_WEIGHTS_PATH_DEFAULT: Path = Path("data/matcher_token_weights.json")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS match_token_fp_counters (
    token TEXT NOT NULL,
    market_prefix TEXT NOT NULL,
    -- Rolling buckets per UTC day, kept for 14d. Aggregator queries
    -- against this with a window predicate (WHERE day_utc >= ...).
    day_utc TEXT NOT NULL,
    fp_neutral_count INTEGER NOT NULL DEFAULT 0,
    true_positive_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (token, market_prefix, day_utc)
);

CREATE INDEX IF NOT EXISTS idx_match_token_fp_window
    ON match_token_fp_counters (day_utc);
"""


def market_prefix_for(market: Any) -> str:
    """Return the matcher-feedback prefix used by review and weight events."""
    series_ticker = str(getattr(market, "series_ticker", "") or "").strip()
    ticker = str(getattr(market, "ticker", "") or getattr(market, "market_id", "") or "")
    if series_ticker and series_ticker != "polymarket_us":
        return series_ticker
    if series_ticker == "polymarket_us" and ticker and not ticker.upper().startswith("KX"):
        return pm_domain_key(ticker)
    return ticker.split("-", 1)[0]


def _is_bare_polymarket_prefix(market_prefix: str) -> bool:
    return str(market_prefix or "").strip() == "polymarket_us"


_DEFINING_TOKEN_MIN_LEN: int = 4
"""Minimum token length for the defining-token guard. Below this, a substring
match against the series ticker is too likely to be an incidental fragment
('out' in KXSCHUMEROUT, 'cab'/'ave' in KXCABLEAVE) rather than the market's
subject word, so we do not treat it as defining."""


def is_market_defining_token(token: str, market_prefix: str) -> bool:
    """True when ``token`` is the market's own subject word, encoded into its
    series ticker (e.g. ``iran`` in ``KXVISITIRAN``, ``tariffs`` in
    ``KXNEWTARIFFS``, ``zelenskyy`` in ``KXZELENSKYYOUT``).

    The matcher-feedback loop exists to downweight tokens that *bridge* a
    headline to a market where the token does not belong. A market's own
    ticker-encoded token is never such a bridge — it is the single most
    reliable signal that the *correct* market was found. But the loop's only
    negative signal is ``false_positive_neutral`` (the LLM saw the right
    market, yet this particular headline carried no directional edge), so a
    correctly-matched, high-traffic market accumulates fp_rate -> 1.0 and its
    defining token gets floored to ``DOWNWEIGHT_FLOOR`` (0.10). Once floored, a
    single-overlap match on that token scores below the matcher threshold and
    the correct market can no longer surface as a tradeable opportunity — a
    self-poisoning loop that throttles opportunity throughput (the binding
    constraint on trade volume per CLAUDE.md). Callers use this predicate to
    keep a market's own subject token at full weight regardless of the learned
    downweight. The guard only ever *raises* a weight back to 1.0; it never
    bypasses the matcher's structural precision gate or score threshold.
    See docs/profit_path_debt_log.md (PROFIT-MATCH-DYNAMIC self-poisoning).
    """
    if not token or not market_prefix:
        return False
    if len(token) < _DEFINING_TOKEN_MIN_LEN:
        return False
    prefix = market_prefix.lower()
    # PROFIT-VENUE-PARITY V08: for Polymarket per-family prefixes
    # ('polymarket_us:<family-stem>'), match only against the family stem. The
    # constant 'polymarket_us' venue segment is present in EVERY PM prefix, so
    # without this strip a generic substring of it ('market', 'poly') would
    # falsely read as a defining token across ALL PM families and be held at
    # weight 1.0 — defeating downweight of exactly the kind of generic bridge
    # token the loop exists to penalise. Kalshi (KX*) prefixes have no
    # 'polymarket_us:' segment, so this branch never fires for them.
    if prefix.startswith("polymarket_us:"):
        prefix = prefix.split(":", 1)[1]
    return token.lower() in prefix


@dataclass(frozen=True)
class TokenStats:
    token: str
    market_prefix: str
    fp_neutral: int
    true_positive: int

    @property
    def total(self) -> int:
        return self.fp_neutral + self.true_positive

    @property
    def fp_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.fp_neutral / self.total


def _open(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    return conn


def ingest_review_events(
    events: Iterable[dict[str, Any]],
    *,
    db_path: Path = _DB_PATH_DEFAULT,
) -> int:
    """Aggregate MATCH_LLM_REVIEW records into the counter table.

    Caller passes parsed JSON event dicts. We extract:
      - ts → day_utc (YYYY-MM-DD)
      - market_prefix
      - matched_tokens (list of overlap tokens with this market)
      - verdict ∈ {true_positive, false_positive_neutral}

    For each (token, market_prefix, day_utc) triple, we increment the
    corresponding counter. Returns the number of (token, event) increments
    applied (so a single 3-token event with verdict TP adds 3 to the return).
    """
    conn = _open(db_path)
    try:
        applied = 0
        with conn:
            for ev in events:
                if ev.get("type") != "MATCH_LLM_REVIEW":
                    continue
                verdict = ev.get("verdict")
                if verdict not in ("true_positive", "false_positive_neutral"):
                    continue
                # PROFIT-MATCH-003 (L2-a): score-gate the neutral signal. A
                # neutral verdict on a clearly-correct (higher-score) match is
                # "right market, no directional edge from this headline", NOT a
                # wrong match -- counting it poisons correct markets. Only count
                # neutral toward downweighting on marginal (low-score) matches.
                # true_positive always counts.
                if verdict == "false_positive_neutral":
                    try:
                        match_score = float(ev.get("match_score"))
                    except (TypeError, ValueError):
                        match_score = 0.0  # missing -> treat as marginal (counts)
                    if match_score >= L2_NEUTRAL_FP_MARGINAL_MAX_SCORE:
                        continue
                ts = ev.get("ts") or ""
                day_utc = ts[:10] if len(ts) >= 10 else ""
                if not day_utc:
                    continue
                prefix = ev.get("market_prefix") or ""
                if not prefix or _is_bare_polymarket_prefix(str(prefix)):
                    continue
                tokens = ev.get("matched_tokens") or []
                tp_col = "true_positive_count" if verdict == "true_positive" else "fp_neutral_count"
                for tok in tokens:
                    if not tok:
                        continue
                    conn.execute(
                        f"""
                        INSERT INTO match_token_fp_counters
                            (token, market_prefix, day_utc, {tp_col})
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(token, market_prefix, day_utc) DO UPDATE
                            SET {tp_col} = {tp_col} + 1
                        """,
                        (tok, prefix, day_utc),
                    )
                    applied += 1
        return applied
    finally:
        conn.close()


def aggregate_window(
    *,
    window_days: int = 14,
    today_utc: str | None = None,
    db_path: Path = _DB_PATH_DEFAULT,
) -> list[TokenStats]:
    """Return per-(token, prefix) stats summed over the last ``window_days``.

    ``today_utc`` is a YYYY-MM-DD string; defaults to today via UTC clock.
    Test code passes an explicit value for determinism.
    """
    if today_utc is None:
        from datetime import datetime, timezone
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from datetime import datetime, timedelta
    cutoff_dt = datetime.strptime(today_utc, "%Y-%m-%d") - timedelta(days=window_days)
    cutoff = cutoff_dt.strftime("%Y-%m-%d")

    conn = _open(db_path)
    try:
        rows = conn.execute(
            """
            SELECT token, market_prefix,
                   SUM(fp_neutral_count) AS fp,
                   SUM(true_positive_count) AS tp
            FROM match_token_fp_counters
            WHERE day_utc > ?
            GROUP BY token, market_prefix
            ORDER BY fp DESC, tp ASC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [
        TokenStats(token=r[0], market_prefix=r[1], fp_neutral=int(r[2] or 0),
                   true_positive=int(r[3] or 0))
        for r in rows
    ]


def load_weights(
    weights_path: Path = _WEIGHTS_PATH_DEFAULT,
) -> dict[str, dict[str, Any]]:
    """Load the runtime weights file. Returns {} if absent or malformed.

    Schema::

        {
          "<market_prefix>:<token>": {
            "weight": <float>,
            "fp_rate": <float>,
            "total": <int>,
            "pinned": <bool>,       # operator override, never overwritten
            "updated_utc": <iso8601>
          }
        }
    """
    if not weights_path.exists():
        return {}
    try:
        data = json.loads(weights_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def load_verified_weights(
    weights_path: Path = _WEIGHTS_PATH_DEFAULT,
    *,
    repo_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load weights only when the runtime file is parseable and git-clean.

    Missing weights are a cold-start state and return ``{}``. A present but
    malformed, untracked, staged, or unstaged-dirty weights file can directly
    change candidate admission, so it is treated as unverified unless the
    operator explicitly sets ``MATCHER_WEIGHTS_ALLOW_DIRTY=true``.
    """
    if not weights_path.exists():
        return {}
    try:
        data = json.loads(weights_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise MatcherWeightsUnverified(f"matcher weights malformed: {weights_path}") from exc
    if not isinstance(data, dict):
        raise MatcherWeightsUnverified(f"matcher weights malformed: {weights_path}")

    if os.getenv("MATCHER_WEIGHTS_ALLOW_DIRTY", "").strip().lower() == "true":
        return data

    root = repo_root or weights_path.resolve().parents[1]
    rel_path = str(weights_path.resolve().relative_to(root.resolve()))
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", rel_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:
        raise MatcherWeightsUnverified(f"matcher weights untracked: {weights_path}")

    unstaged = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "--", rel_path],
        capture_output=True,
        text=True,
        check=False,
    )
    staged = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--quiet", "--", rel_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if unstaged.returncode != 0 or staged.returncode != 0:
        raise MatcherWeightsUnverified(f"matcher weights dirty: {weights_path}")

    return data


def write_weights(
    weights: dict[str, dict[str, Any]],
    weights_path: Path = _WEIGHTS_PATH_DEFAULT,
) -> None:
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.write_text(
        json.dumps(weights, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def get_token_weight(
    token: str,
    market_prefix: str,
    weights: dict[str, dict[str, Any]] | None = None,
    weights_path: Path = _WEIGHTS_PATH_DEFAULT,
) -> float:
    """Look up the current downweight for a (token, market_prefix) pair.

    Returns 1.0 if no entry exists (no penalty). Hot-path callable — accepts
    a pre-loaded weights dict to avoid re-reading the file every match cycle.

    A market's own ticker-defining token is never downweighted (see
    ``is_market_defining_token``) — the learned weight is bypassed entirely.
    """
    if is_market_defining_token(token, market_prefix):
        return 1.0
    if weights is None:
        weights = load_weights(weights_path)
    key = f"{market_prefix}:{token}"
    entry = weights.get(key)
    if not entry:
        return 1.0
    w = entry.get("weight")
    try:
        return float(w) if w is not None else 1.0
    except (TypeError, ValueError):
        return 1.0


def _weight_entry_status(entry: dict[str, Any]) -> str:
    if entry.get("pinned") is True:
        return "pinned"
    if entry.get("_seed_status") == "provisional":
        return "provisional"
    try:
        weight = float(entry.get("weight", 1.0))
    except (TypeError, ValueError):
        weight = 1.0
    try:
        fp_rate = float(entry.get("fp_rate", 1.0))
    except (TypeError, ValueError):
        fp_rate = 1.0
    try:
        total = int(entry.get("total", 0))
    except (TypeError, ValueError):
        total = 0
    if weight >= 1.0 and total >= MIN_TOTAL_FOR_DOWNWEIGHT and fp_rate < RECOVERY_THRESHOLD:
        return "recovered"
    return "automatic"


def summarize_weight_status(weights: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Summarize matcher weights by operator/provisional/adaptive status."""
    counts = {
        "pinned": 0,
        "provisional": 0,
        "automatic": 0,
        "recovered": 0,
    }
    entries: dict[str, dict[str, Any]] = {}
    for key, entry in sorted(weights.items()):
        safe_entry = entry if isinstance(entry, dict) else {}
        status = _weight_entry_status(safe_entry)
        counts[status] += 1
        try:
            weight = float(safe_entry.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        entries[key] = {
            "status": status,
            "weight": weight,
        }
        if "fp_rate" in safe_entry:
            entries[key]["fp_rate"] = safe_entry["fp_rate"]
        if "total" in safe_entry:
            entries[key]["total"] = safe_entry["total"]

    return {
        "total_entries": len(entries),
        "counts": counts,
        "entries": entries,
    }


def update_weights_from_stats(
    stats: Iterable[TokenStats],
    *,
    weights: dict[str, dict[str, Any]] | None = None,
    weights_path: Path = _WEIGHTS_PATH_DEFAULT,
    now_iso: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Apply the activation / recovery rules from this module's docstring.

    Returns the updated weights dict. Caller writes it out via write_weights.
    Operator-pinned entries (entry.get("pinned") == True) are NEVER overwritten.
    """
    if weights is None:
        weights = load_weights(weights_path)
    if now_iso is None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

    for s in stats:
        key = f"{s.market_prefix}:{s.token}"
        existing = weights.get(key, {})
        if existing.get("pinned") is True:
            # Operator-pinned. Never mutate. Still update bookkeeping fields
            # so the operator can see latest fp_rate without losing their pin.
            existing["fp_rate"] = round(s.fp_rate, 4)
            existing["total"] = s.total
            existing["updated_utc"] = now_iso
            weights[key] = existing
            continue
        if s.total < MIN_TOTAL_FOR_DOWNWEIGHT:
            # Insufficient evidence — leave weight at 1.0 (or remove existing
            # downweight if data shrank below threshold).
            if existing.get("weight", 1.0) < 1.0:
                existing.update(weight=1.0, fp_rate=round(s.fp_rate, 4),
                                total=s.total, updated_utc=now_iso)
                weights[key] = existing
            continue
        if s.fp_rate >= ACTIVATE_THRESHOLD:
            new_weight = max(DOWNWEIGHT_FLOOR, 1.0 - s.fp_rate)
        elif s.fp_rate < RECOVERY_THRESHOLD:
            new_weight = 1.0
        else:
            # Hysteresis band: keep the existing weight (avoid flapping).
            new_weight = float(existing.get("weight", 1.0))
        weights[key] = {
            "weight": round(new_weight, 4),
            "fp_rate": round(s.fp_rate, 4),
            "total": s.total,
            "pinned": False,
            "updated_utc": now_iso,
        }
    return weights
