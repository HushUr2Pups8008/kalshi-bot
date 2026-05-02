"""Readiness-gate threshold calibration audit (G1 + G4).

Walks each existing categorical regime prior + the time-fallback bands
through ``regime_confidence`` and reports which clear the current
``G4_REGIME_CONFIDENCE_THRESHOLD``. Then computes the empirical
``scaled_confidence`` distribution from production BLEND_DECISION
records and reports which fraction would clear the current
``G1_CONFIDENCE_THRESHOLD``.

Origin: PROFIT-EDGE-002 (v0.29.57) and PROFIT-EDGE-003 (v0.29.58)
calibration analyses, originally executed inline as `/tmp/*.py` scripts
on 2026-04-26.

Usage
-----
::

    .venv/bin/python scripts/simulations/threshold_calibration.py
    .venv/bin/python scripts/simulations/threshold_calibration.py --window 7
    .venv/bin/python scripts/simulations/threshold_calibration.py --no-production

The simulation never mutates state and is safe to run while the bot is
active.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Ensure repo root on path when invoked directly (e.g. ``python scripts/...``).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.regime_classifier import _SERIES_PRIORS  # noqa: E402
from scripts.simulations._common import regime_confidence  # noqa: E402
from tasks.trade_readiness_gate import (  # noqa: E402
    G1_CONFIDENCE_THRESHOLD,
    G4_REGIME_CONFIDENCE_THRESHOLD,
)

# Time-fallback bands from analysis/regime_classifier.py:_time_prior. Reproduced
# here so the simulation can audit them without importing the private helper.
_TIME_FALLBACK_BANDS: tuple[tuple[str, tuple[float, float, float]], ...] = (
    ("≤6h",        (0.85, 0.10, 0.05)),
    ("6h–1d",     (0.70, 0.22, 0.08)),
    ("1–3d",      (0.45, 0.40, 0.15)),
    ("3–7d",      (0.20, 0.50, 0.30)),
    ("7–14d",     (0.10, 0.45, 0.45)),
    (">14d",      (0.10, 0.25, 0.65)),
)


@dataclass
class PriorAudit:
    label: str
    weights: tuple[float, float, float]
    rc: float

    @property
    def passes_g4(self) -> bool:
        return self.rc >= G4_REGIME_CONFIDENCE_THRESHOLD


def audit_g4_against_priors() -> list[PriorAudit]:
    """Compute the regime_confidence each existing prior produces and check
    it against the current G4 threshold."""
    out: list[PriorAudit] = []
    for prefix, weights in _SERIES_PRIORS.items():
        rc = regime_confidence(weights)
        out.append(PriorAudit(label=f"{prefix}", weights=weights, rc=rc))
    for band, weights in _TIME_FALLBACK_BANDS:
        rc = regime_confidence(weights)
        out.append(PriorAudit(label=f"<time fallback {band}>", weights=weights, rc=rc))
    return out


def _trade_log_files(window_days: int, archive_root: Path) -> list[Path]:
    """Resolve archive trade-log files for the most recent ``window_days``.

    Defaults to scanning the partitioned 2026 archive plus the live tail.
    """
    archive_files = sorted(archive_root.glob("2026/*/*.jsonl"))
    files = archive_files[-window_days:] if archive_files else []
    live = archive_root.parent / "live" / "trades.jsonl"
    if live.exists():
        files.append(live)
    return files


def collect_production_scaled_conf(
    archive_root: Path | None = None,
    window_days: int = 9,
) -> list[tuple[float, float, float, str]]:
    """Read ``BLEND_DECISION`` records and return ``(scaled, blended_conf,
    regime_conf, ticker)`` tuples.

    Pure read — never mutates the archive.
    """
    archive_root = archive_root or _REPO_ROOT / "logs" / "trades" / "archive"
    files = _trade_log_files(window_days, archive_root)
    records: list[tuple[float, float, float, str]] = []
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("type") != "BLEND_DECISION":
                        continue
                    bc = rec.get("blended_confidence")
                    rc = rec.get("regime_confidence")
                    if bc is None or rc is None:
                        continue
                    records.append((bc * rc, bc, rc, rec.get("market_ticker", "")))
        except OSError:
            continue
    return records


def percentile(values: list[float], pct: int) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    idx = max(0, min(len(values) - 1, int(round(pct / 100 * (len(values) - 1)))))
    return values[idx]


def _print_g4_report(audits: list[PriorAudit]) -> None:
    print("=" * 100)
    print(f"G4 audit  (G4_REGIME_CONFIDENCE_THRESHOLD = {G4_REGIME_CONFIDENCE_THRESHOLD})")
    print("=" * 100)
    audits = sorted(audits, key=lambda a: -a.rc)
    print(f"  {'Prior':40s} {'Weights':25s} {'rc':>6s}  G4")
    pass_count = 0
    for a in audits:
        flag = "PASS" if a.passes_g4 else "FAIL"
        if a.passes_g4:
            pass_count += 1
        print(f"  {a.label:40s} {str(a.weights):25s} {a.rc:6.4f}  {flag}")
    print(f"\n  Summary: {pass_count} of {len(audits)} priors clear G4 = {G4_REGIME_CONFIDENCE_THRESHOLD}")


def _print_g1_report(records: list[tuple[float, float, float, str]]) -> None:
    print()
    print("=" * 100)
    print("G1 audit — production scaled_confidence distribution")
    print(f"  G1_CONFIDENCE_THRESHOLD = {G1_CONFIDENCE_THRESHOLD}")
    print("=" * 100)
    if not records:
        print("  (no BLEND_DECISION records found — bot may not have run, or window is too narrow)")
        return
    sc = sorted(r[0] for r in records)
    print(f"\n  Production records analysed: {len(sc)}")
    print("\n  scaled_confidence distribution:")
    for pct in (0, 25, 50, 75, 90, 95, 99, 100):
        print(f"    P{pct:>3d}: {percentile(sc, pct):.4f}")
    print("\n  Pass-rate sweep (records ≥ threshold):")
    for thr in (0.35, 0.20, 0.15, 0.10, 0.08, 0.05, 0.03, 0.02):
        n = sum(1 for v in sc if v >= thr)
        marker = "  ← current" if abs(thr - G1_CONFIDENCE_THRESHOLD) < 1e-9 else ""
        print(f"    G1 ≥ {thr}: {n:5d}/{len(sc)} ({100*n/len(sc):5.1f}%){marker}")

    by_series: dict[str, list[float]] = defaultdict(list)
    for sc_val, _, _, ticker in records:
        prefix = ticker.split("-", 1)[0] if ticker else "<unknown>"
        by_series[prefix].append(sc_val)
    print("\n  Top-10 series by scaled_confidence (max):")
    series_max = sorted(
        ((prefix, max(vals), len(vals)) for prefix, vals in by_series.items()),
        key=lambda t: -t[1],
    )[:10]
    print(f"    {'series':30s}  {'n':>5s}  {'max':>7s}  {'p50':>7s}")
    for prefix, mx, n in series_max:
        p50 = sorted(by_series[prefix])[len(by_series[prefix]) // 2]
        print(f"    {prefix:30s}  {n:5d}  {mx:7.4f}  {p50:7.4f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0] if __doc__ else None,
    )
    parser.add_argument(
        "--window",
        type=int,
        default=9,
        help="trade-log archive days to scan for production scaled_conf (default: 9)",
    )
    parser.add_argument(
        "--no-production",
        action="store_true",
        help="skip the production-data G1 distribution audit (G4 priors only)",
    )
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help="override the trade-log archive root (default: <repo>/logs/trades/archive)",
    )
    args = parser.parse_args(argv)

    audits = audit_g4_against_priors()
    _print_g4_report(audits)

    if not args.no_production:
        records = collect_production_scaled_conf(
            archive_root=args.archive_root,
            window_days=args.window,
        )
        _print_g1_report(records)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
