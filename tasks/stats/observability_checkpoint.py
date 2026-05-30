"""Unified observability snapshot for the operator reports.

`daily_review.py` and `pipeline_impact_audit.py` were written before the
readiness gate, the edge-prioritization experiment, and the rot-audit layer
existed, so neither surfaced them. This module is the single source both reports
read for that "are-we-ready / is-the-experiment-working / is-the-infra-healthy"
layer:

  * POST_FIX_NEW live-readiness verdict + corpus completion (the gate to live);
  * ENABLE_NEWS_EDGE_PRIORITIZATION A/B verdict (the active paper experiment);
  * feed health, regime-prior orphans, market-horizon drift, active edge-series,
    matcher-weight tuning depth (from the artifacts the daily aggregator writes).

Readiness + A/B are computed fresh (cheap DB/log reads); the network-probing
audits are read from their latest artifacts so the morning report stays fast.
``summarize`` is pure + robust to any missing/failed sub-collector.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DATA_DEFAULT = Path("data")
_DB_DEFAULT = Path("data/paper_trades.db")
_LOGS_DEFAULT = Path("logs/trades")


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def summarize(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Flatten a checkpoint into report headlines. Pure; never raises."""
    cp = _d(checkpoint)
    rd = _d(cp.get("readiness"))
    rd = {} if "error" in rd else rd
    ab = _d(cp.get("edge_ab"))
    ab = {} if "error" in ab else ab
    fh, rp, mh = _d(cp.get("feed_health")), _d(cp.get("regime_priors")), _d(cp.get("market_horizon"))
    es, mw = cp.get("edge_series"), cp.get("matcher_weights")
    ev, op = _d(ab.get("resolved_ev")), _d(ab.get("opportunity_rate"))
    flag = cp.get("flag_enabled")
    return {
        "readiness_verdict": rd.get("readiness", "n/a"),
        "readiness_reason": rd.get("reason"),
        "readiness_complete": rd.get("post_clean_start_production_proxy_complete_rows"),
        "readiness_required": rd.get("min_trades_required"),
        "edge_flag": ("ON" if flag else "OFF") if flag is not None else "n/a",
        "ab_verdict": ev.get("verdict", "n/a"),
        "ab_opps_before": op.get("before_per_day"),
        "ab_opps_after": op.get("after_per_day"),
        "ab_ev_before": _d(ev.get("before")).get("mean_pnl_ev"),
        "ab_ev_after": _d(ev.get("after")).get("mean_pnl_ev"),
        "feed_live": _d(fh.get("by_health")).get("live"),
        "feed_unhealthy": len(fh.get("unhealthy", []) or []),
        "regime_orphans": len(rp.get("orphaned_priors", []) or []),
        "regime_candidates": len(rp.get("missing_prior_candidates", {}) or {}),
        "market_drift_flags": len(mh.get("flags", []) or []),
        "market_p90_days": _d(mh.get("days_quantiles")).get("p90"),
        "edge_series_count": len(es) if isinstance(es, dict) else 0,
        "matcher_tokens": len(mw) if isinstance(mw, dict) else 0,
    }


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def collect(
    *,
    now: datetime | None = None,
    db_path: Path = _DB_DEFAULT,
    logs_dir: Path = _LOGS_DEFAULT,
    data_dir: Path = _DATA_DEFAULT,
) -> dict[str, Any]:
    """Gather the unified snapshot. Each sub-collector is isolated; a failure
    stores an {"error": ...} stub rather than breaking the report."""
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        from config import ENABLE_NEWS_EDGE_PRIORITIZATION
        flag = bool(ENABLE_NEWS_EDGE_PRIORITIZATION)
    except Exception:
        flag = None
    cp: dict[str, Any] = {"generated_utc": now.isoformat(), "flag_enabled": flag}

    try:
        from scripts.edge_replay.post_fix_new_readiness_status import (
            DEFAULT_CLEAN_START_TS, DEFAULT_MIN_TICKERS, DEFAULT_MIN_TRADES, collect_readiness,
        )
        cp["readiness"] = collect_readiness(
            db_path=db_path, clean_start_ts=DEFAULT_CLEAN_START_TS,
            min_trades=DEFAULT_MIN_TRADES, min_tickers=DEFAULT_MIN_TICKERS,
        )
    except Exception as exc:
        cp["readiness"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    try:
        from tasks.stats.edge_activation_compare import compare as edge_compare
        cp["edge_ab"] = edge_compare(db_path=db_path, logs_dir=logs_dir, now=now, write=False)
    except Exception as exc:
        cp["edge_ab"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    for key, fname in (
        ("feed_health", "feed_health.json"),
        ("regime_priors", "regime_prior_audit.json"),
        ("market_horizon", "market_horizon_audit.json"),
        ("edge_series", "news_edge_series.json"),
        ("matcher_weights", "matcher_token_weights.json"),
    ):
        cp[key] = _load_json(data_dir / fname)
    return cp


def summary_lines(checkpoint: "dict[str, Any] | None" = None) -> list[str]:
    """Render the GATES & EXPERIMENTS section. Both reports append these lines."""
    cp = collect() if checkpoint is None else checkpoint
    s = summarize(cp)
    rc, rr = s["readiness_complete"], s["readiness_required"]
    pct = f"{100 * rc / rr:.0f}%" if isinstance(rc, (int, float)) and rr else "n/a"
    lines = [
        "GATES & EXPERIMENTS",
        f"  Live-readiness (POST_FIX_NEW)    : {s['readiness_verdict']}  "
        f"({rc}/{rr} resolved, {pct} of gate)",
    ]
    if s["readiness_reason"]:
        lines.append(f"    reason                         : {s['readiness_reason']}")
    lines += [
        f"  Edge-prioritization flag         : {s['edge_flag']}",
        f"    A/B verdict                    : {s['ab_verdict']}",
        f"    opp rate before->after (/day)  : {s['ab_opps_before']} -> {s['ab_opps_after']}",
        f"    edge-cluster EV before->after  : ${s['ab_ev_before']} -> ${s['ab_ev_after']}",
        f"  Feeds                            : {s['feed_live']} live, {s['feed_unhealthy']} unhealthy",
        f"  Regime priors                    : {s['regime_orphans']} orphaned, "
        f"{s['regime_candidates']} missing-prior candidate(s)",
        f"  Market horizon                   : p90={s['market_p90_days']}d, "
        f"{s['market_drift_flags']} drift flag(s)",
        f"  Edge series (active, 45d)        : {s['edge_series_count']}",
        f"  Matcher token-weights tracked    : {s['matcher_tokens']}",
    ]
    return lines


if __name__ == "__main__":  # pragma: no cover
    for line in summary_lines():
        print(line)
