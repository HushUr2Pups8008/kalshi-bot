"""Observability for `_SERIES_PRIORS` rot (Phase 1 of the durability scope).

`analysis/regime_classifier._SERIES_PRIORS` is a hand-curated map of series-prefix
→ regime weights. It rots two ways: entries **orphan** when Kalshi retires a
series, and new opportunity-producing series may **lack a prior** (falling back to
`_time_prior`). This module surfaces both — read-only, over the prior keys + the
recent market universe + opportunity history. It changes **no** trade behavior; it
only makes the rot visible so an operator (or a later Phase-2 auto-derivation) can
act. Matching uses prefix semantics because `regime_classifier` applies priors by
series-ticker prefix, not exact match.

Run standalone or from the match-feedback aggregator::

    python -m tasks.stats.regime_prior_audit
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.regime_classifier import _SERIES_PRIORS
from config import MARKET_SERIES_BLOCKLIST_PREFIXES
from tasks.stats.edge_series import (
    DEFAULT_WINDOW_DAYS,
    _LOGS_DIR_DEFAULT,
    _parse_iso,
    _series_of,
    read_opportunity_events,
)

DEFAULT_MIN_OPPS = 3
_PATH_DEFAULT = Path("data/regime_prior_audit.json")


def find_orphaned_priors(
    prior_series: "frozenset[str] | set[str]",
    seen_series: "frozenset[str] | set[str]",
) -> frozenset[str]:
    """Priors whose prefix matches NO series in the recent market universe.

    Prefix semantics mirror `regime_classifier._series_prior`: a prior ``P``
    "covers" any market series ``S`` with ``S.startswith(P)``.
    """
    return frozenset(
        p for p in prior_series
        if not any(s.startswith(p) for s in seen_series)
    )


def find_missing_prior_candidates(
    opp_counts: dict[str, int],
    prior_series: "frozenset[str] | set[str]",
    *,
    min_opps: int = DEFAULT_MIN_OPPS,
) -> dict[str, int]:
    """Series producing >= ``min_opps`` opportunities but covered by NO prior."""
    return {
        s: c
        for s, c in opp_counts.items()
        if c >= min_opps and not any(s.startswith(p) for p in prior_series)
    }


def read_recent_market_series(
    logs_dir: Path = _LOGS_DIR_DEFAULT,
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> frozenset[str]:
    """Series prefixes seen in NEW_MARKET records within the window (read-only)."""
    from datetime import timedelta

    cutoff = now - timedelta(days=window_days)
    seen: set[str] = set()
    for jsonl in sorted(logs_dir.rglob("*.jsonl")):
        try:
            with jsonl.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or '"NEW_MARKET"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "NEW_MARKET":
                        continue
                    t = _parse_iso(rec.get("ts"))
                    if t is None or t < cutoff:
                        continue
                    series = _series_of(rec.get("series_ticker") or rec.get("ticker", ""))
                    if series:
                        seen.add(series)
        except OSError:
            continue
    return frozenset(seen)


def audit(
    *,
    logs_dir: Path = _LOGS_DIR_DEFAULT,
    path: Path = _PATH_DEFAULT,
    now: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_opps: int = DEFAULT_MIN_OPPS,
    write: bool = True,
) -> dict[str, Any]:
    """Produce the rot report (orphaned priors + missing-prior candidates).

    Read-only over the trade path. Optionally writes ``data/regime_prior_audit.json``
    for the pipeline feedback surface. Safe to run from the aggregator.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    prior_series = frozenset(p.upper() for p in _SERIES_PRIORS)
    seen = read_recent_market_series(logs_dir, now=now, window_days=window_days)

    opp_counts: dict[str, int] = {}
    for series, _ts in read_opportunity_events(logs_dir, now=now, window_days=window_days):
        opp_counts[series] = opp_counts.get(series, 0) + 1

    # A prior is "active" if its series had a recent market OR produced a recent
    # opportunity (an event series whose runoff just ended still produced opps).
    active = seen | frozenset(opp_counts)
    orphaned_raw = find_orphaned_priors(prior_series, active)
    # Blocklisted series (sports/crypto) are intentionally priored-but-filtered —
    # they never reach the bot's market universe, so exclude them from "orphaned"
    # (they are a separate, minor list-hygiene matter, reported separately).
    blocklist = frozenset(MARKET_SERIES_BLOCKLIST_PREFIXES)
    blocklisted_priors = frozenset(
        p for p in orphaned_raw if any(p.startswith(b) for b in blocklist)
    )
    orphaned = orphaned_raw - blocklisted_priors
    candidates = find_missing_prior_candidates(opp_counts, prior_series, min_opps=min_opps)

    report = {
        "generated_utc": now.isoformat(),
        "window_days": window_days,
        "prior_count": len(prior_series),
        "seen_series_count": len(seen),
        "orphaned_priors": sorted(orphaned),
        "blocklisted_priors": sorted(blocklisted_priors),
        "missing_prior_candidates": dict(sorted(candidates.items(), key=lambda kv: -kv[1])),
    }
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":  # pragma: no cover
    r = audit()
    print(
        f"regime_prior_audit: {len(r['orphaned_priors'])} orphaned priors, "
        f"{len(r['missing_prior_candidates'])} missing-prior candidates, "
        f"{len(r['blocklisted_priors'])} blocklisted-priored "
        f"(of {r['prior_count']} priors, {r['seen_series_count']} series seen)"
    )
    if r["orphaned_priors"]:
        print(f"  orphaned: {r['orphaned_priors']}")
    if r["missing_prior_candidates"]:
        print(f"  candidates: {r['missing_prior_candidates']}")
