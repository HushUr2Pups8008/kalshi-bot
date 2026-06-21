"""Before/after comparison for the ENABLE_NEWS_EDGE_PRIORITIZATION activation.

The flag was flipped on 2026-05-30 as a paper-mode monitored experiment (operator
override of the replay-EV gate). This splits the edge cluster's paper trades +
opportunities at the activation timestamp and reports:

  * opportunity rate (per day) before vs after — the LEADING indicator, visible in days;
  * realized per-trade EV (mean P&L, 95% CI) + win rate before vs after — the LAGGING
    verdict, visible once enough trades resolve.

A degraded after-EV is the documented rollback trigger (set the flag false + restart).
Read-only over the trade path. Pure cohort math; DB/log access is the adapter.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import NEWS_EDGE_SERIES
from tasks.stats.edge_series import (
    _LOGS_DIR_DEFAULT,
    _parse_iso,
    _series_of,
    active_edge_series,
    read_opportunity_events,
)
from utils.output_paths import DERIVED_STATE_DIR, PAPER_TRADES_DB

_DB_DEFAULT = PAPER_TRADES_DB
_MARKER_DEFAULT = Path("data/edge_prioritization_activation.json")
_PATH_DEFAULT = DERIVED_STATE_DIR / "edge_activation_compare.json"
_MIN_AFTER_N = 10


# ── pure cohort math ──────────────────────────────────────────────────────────

def _is_resolved(t: dict[str, Any]) -> bool:
    if t.get("resolved") in (1, True, "1", "true", "True"):
        return True
    ry = t.get("resolved_yes")
    return ry not in (None, "")


def _mean_ci95(values: list[float]) -> "list[float] | None":
    n = len(values)
    if n < 2:
        return None
    mean = statistics.mean(values)
    half = 1.96 * statistics.stdev(values) / (n ** 0.5)
    return [round(mean - half, 4), round(mean + half, 4)]


def cohort_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Realized-EV stats for a cohort of paper trades. Pure."""
    resolved = [t for t in trades if _is_resolved(t)]
    pnls = [float(t["pnl_dollars"]) for t in resolved if t.get("pnl_dollars") is not None]
    edges = [float(t["edge"]) for t in trades if t.get("edge") is not None]
    wins = sum(1 for p in pnls if p > 0)
    nres = len(pnls)
    return {
        "n": len(trades),
        "resolved": nres,
        "wins": wins,
        "win_rate": round(wins / nres, 4) if nres else None,
        "mean_edge": round(sum(edges) / len(edges), 4) if edges else None,
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "mean_pnl_ev": round(sum(pnls) / nres, 4) if nres else None,
        "ev_ci95": _mean_ci95(pnls),
    }


def _stratify_verdict(edge: dict[str, Any], non_edge: dict[str, Any]) -> str:
    ee, ne = edge.get("resolved", 0), non_edge.get("resolved", 0)
    if ee == 0 and ne == 0:
        return "un-evaluable: 0 resolved trades in either stratum — no EV signal yet"
    if ne == 0:
        return (
            f"un-evaluable: {ee} resolved trades, all on currently-active edge series; 0 on "
            "currently-non-edge series — no contrast group (PROFIT-THRUPUT-001 structural ceiling)."
        )
    if ee == 0:
        return f"un-evaluable: 0 current-active-edge resolved vs {ne} currently-non-edge"
    ev_e, ev_n = edge.get("mean_pnl_ev"), non_edge.get("mean_pnl_ev")
    if ev_e is None or ev_n is None:
        return "insufficient resolved data in one stratum"
    rel = "higher" if ev_e > ev_n else ("lower" if ev_e < ev_n else "equal to")
    return (
        f"DESCRIPTIVE (not causal): current-active-edge per-trade EV ${ev_e} {rel} than "
        f"currently-non-edge ${ev_n} (n={ee} vs {ne}; current membership applied retroactively) "
        "-- directional only, no power at this n"
    )


def stratify_by_edge_series(
    trades: list[dict[str, Any]], edge_series: "frozenset[str] | set[str]"
) -> dict[str, Any]:
    """Cross-sectional descriptive EV split of recorded trades by CURRENT edge-set membership.

    Splits ALL recorded resolved trades by whether their series is in the *current* active
    edge set (``edge_series``). IMPORTANT: membership is applied RETROACTIVELY at report time
    -- a historical trade moves strata as the active set ages -- so this is a DESCRIPTIVE
    comparison (currently-active series vs currently-aged-out/inactive series), NOT causal
    evidence the flag steered toward better markets at trade time. It is the recorded-corpus
    companion to the temporal A/B (``compare_verdict``), which is the before/after but needs
    live AFTER-cohort trades. Reports series composition + n so single-series strata are not
    over-read. Pure.
    """
    edge_t: list[dict[str, Any]] = []
    non_edge_t: list[dict[str, Any]] = []
    for t in trades:
        target = edge_t if _covered(_series_of(t.get("ticker") or ""), edge_series) else non_edge_t
        target.append(t)
    edge_stats, non_edge_stats = cohort_stats(edge_t), cohort_stats(non_edge_t)

    def _series_present(rows: list[dict[str, Any]]) -> list[str]:
        return sorted({_series_of(t.get("ticker") or "") for t in rows if _is_resolved(t)})

    return {
        "edge": edge_stats,
        "non_edge": non_edge_stats,
        "edge_series_present": _series_present(edge_t),
        "non_edge_series_present": _series_present(non_edge_t),
        "verdict": _stratify_verdict(edge_stats, non_edge_stats),
    }


def split_by_activation(
    rows: list[dict[str, Any]],
    activation_ts: str,
    ts_key: str = "ts",
) -> "tuple[list, list]":
    """Split rows into (before, after) at activation_ts. ts == activation → after."""
    act = _parse_iso(activation_ts)
    before, after = [], []
    for r in rows:
        ts = _parse_iso(r.get(ts_key))
        if ts is None or act is None:
            continue
        (after if ts >= act else before).append(r)
    return before, after


def compare_verdict(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    min_after_n: int = _MIN_AFTER_N,
) -> str:
    na = after.get("resolved", 0)
    if na < min_after_n:
        return f"insufficient AFTER data ({na}/{min_after_n} resolved) — keep accumulating"
    b, a = before.get("mean_pnl_ev"), after.get("mean_pnl_ev")
    if b is None:
        return "no BEFORE baseline to compare"
    if a > b:
        return f"IMPROVED: after per-trade EV ${a} > before ${b}"
    if a < b:
        return f"DEGRADED: after per-trade EV ${a} < before ${b} — consider rollback (flag=false + restart)"
    return "no change in per-trade EV"


def opportunities_per_day(
    before_events: list,
    after_events: list,
    *,
    before_days: float,
    after_days: float,
) -> dict[str, Any]:
    return {
        "before_count": len(before_events),
        "after_count": len(after_events),
        "before_per_day": round(len(before_events) / before_days, 3) if before_days > 0 else None,
        "after_per_day": round(len(after_events) / after_days, 3) if after_days > 0 else None,
    }


# ── adapter (read-only) ───────────────────────────────────────────────────────

def _covered(series: str, edge_series: "frozenset[str] | set[str]") -> bool:
    return any(series.startswith(p) for p in edge_series)


def read_trades(db_path: Path = _DB_DEFAULT) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(paper_trades)")}
        rows = conn.execute("SELECT * FROM paper_trades").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    def g(r, name):
        return r[name] if name in cols else None

    return [
        {
            "ticker": g(r, "ticker") or "",
            "ts": g(r, "ts") or "",
            "edge": g(r, "edge"),
            "pnl_dollars": g(r, "pnl_dollars"),
            "resolved": g(r, "resolved"),
            "resolved_yes": g(r, "resolved_yes"),
        }
        for r in rows
    ]


def read_activation_ts(marker_path: Path = _MARKER_DEFAULT) -> "str | None":
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        return data.get("activated_utc")
    except (json.JSONDecodeError, OSError):
        return None


def compare(
    *,
    db_path: Path = _DB_DEFAULT,
    logs_dir: Path = _LOGS_DIR_DEFAULT,
    marker_path: Path = _MARKER_DEFAULT,
    path: Path = _PATH_DEFAULT,
    now: datetime | None = None,
    edge_series: "frozenset[str] | None" = None,
    write: bool = True,
) -> dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc)
    activation_ts = read_activation_ts(marker_path)
    if activation_ts is None:
        return {"error": f"no activation marker at {marker_path}"}
    act = _parse_iso(activation_ts)

    if edge_series is None:
        edge_series = active_edge_series(seed=NEWS_EDGE_SERIES) or frozenset(NEWS_EDGE_SERIES)

    # Resolved-EV cohorts (edge cluster only — the flag only steers edge-series retrieval).
    all_trades = read_trades(db_path)
    edge_trades = [t for t in all_trades if _covered(_series_of(t["ticker"]), edge_series)]
    tb, ta = split_by_activation(edge_trades, activation_ts)

    # Opportunity rate cohorts (leading indicator) over full history.
    opps = read_opportunity_events(logs_dir, now=now, window_days=3650)
    edge_opps = [(s, ts) for (s, ts) in opps if _covered(s, edge_series)]
    ob = [(s, ts) for (s, ts) in edge_opps if (_parse_iso(ts) or now) < act]
    oa = [(s, ts) for (s, ts) in edge_opps if (_parse_iso(ts) or act) >= act]
    before_dts = [d for d in (_parse_iso(ts) for _, ts in ob) if d is not None]
    before_days = max((act - min(before_dts)).total_seconds() / 86400, 1.0) if before_dts else 1.0
    after_days = max((now - act).total_seconds() / 86400, 0.01)

    before_stats, after_stats = cohort_stats(tb), cohort_stats(ta)
    report = {
        "generated_utc": now.isoformat(),
        "activation_utc": activation_ts,
        "edge_series": sorted(edge_series),
        "resolved_ev": {
            "before": before_stats,
            "after": after_stats,
            "verdict": compare_verdict(before_stats, after_stats),
        },
        "opportunity_rate": opportunities_per_day(
            ob, oa, before_days=before_days, after_days=after_days
        ),
        # Cross-sectional (replay-based) EV eval: edge-series vs non-edge over the full
        # recorded corpus -- the alternative to the temporal A/B, which cannot read out
        # without live AFTER-cohort trades.
        "stratification": stratify_by_edge_series(all_trades, edge_series),
    }
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":  # pragma: no cover
    r = compare()
    if "error" in r:
        print(f"edge_activation_compare: {r['error']}")
        raise SystemExit(0)
    ev, op = r["resolved_ev"], r["opportunity_rate"]
    print(f"edge-prioritization before/after (activated {r['activation_utc']}):")
    print(f"  opportunity rate: {op['before_per_day']}/day before -> "
          f"{op['after_per_day']}/day after ({op['before_count']} vs {op['after_count']} opps)")
    print(f"  resolved EV: before mean=${ev['before']['mean_pnl_ev']} "
          f"(n={ev['before']['resolved']}, win={ev['before']['win_rate']}) | "
          f"after mean=${ev['after']['mean_pnl_ev']} (n={ev['after']['resolved']})")
    print(f"  VERDICT: {ev['verdict']}")
