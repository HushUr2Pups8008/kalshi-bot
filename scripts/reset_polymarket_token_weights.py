#!/usr/bin/env python3
"""Reset the poisoned Polymarket token-downweight learning (PROFIT-VENUE-PARITY V17).

WHY: before the V03/V07 fix, every Polymarket market shared the single
``polymarket_us`` matcher-feedback bucket and PM's L2-a score-gate was a no-op,
so every PM false_positive_neutral verdict downweighted that token for ALL PM
markets. The learned ``polymarket_us:*`` weights are therefore corrupt (learned
under the broken gate) and must be RESET, not migrated — token->market
association is lossy once aggregated. New per-family buckets cold-start at
weight 1.0 (no penalty; fail-safe) and re-learn correctly under the fixed gate.

Resets BOTH stores so the reset is not silently undone:
  * data/matcher_token_weights.json   — the runtime downweight list the matcher reads.
  * data/match_token_fp_counters.db    — the rolling FP counters the aggregator
                                         re-derives weights from (else the next
                                         aggregation regenerates the poisoned weights).

This is a FULL Polymarket reset: it removes the old bare bucket and new
per-family ``polymarket_us:<family>:...`` rows/keys. For the narrower
bare-bucket-only quarantine, use:
``scripts/polymarket_feedback_state_audit.py --apply-quarantine --confirm-runtime-mutation``.

ONLY ``polymarket_us``-prefixed keys/rows are removed; Kalshi (KX*) entries are
left byte-identical. Dry-run by default; pass --execute to write. Backs up both
files to logs/backups/<utc-ts>/ before writing.

DEPLOY ORDER (load-bearing): the bot must be running the V03/V07 code (PR
merged + synced) when it next aggregates, or it re-poisons the fresh keys.
Stop the bot, run --execute, restart on the new code.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_WEIGHTS = _REPO_ROOT / "data" / "matcher_token_weights.json"
_DEFAULT_COUNTERS = _REPO_ROOT / "data" / "match_token_fp_counters.db"
_DEFAULT_BACKUP_ROOT = _REPO_ROOT / "logs" / "backups"
_PM_PREFIX = "polymarket_us"
# GLOB (not LIKE): the family separator ':' is preceded by a literal '_' in the
# venue segment. SQLite LIKE treats '_' as a single-char wildcard, so
# LIKE 'polymarket_us:%' would also match 'polymarketXus:...'. GLOB treats '_'
# literally and '*' as the wildcard, scoping the predicate to exactly the
# 'polymarket_us:' namespace. Kalshi (KX*) rows can never match either way.
_PM_GLOB = _PM_PREFIX + ":*"


def _is_pm_key(key: str) -> bool:
    # Matches the venue bucket: bare 'polymarket_us:<token>' (old) AND the new
    # per-family 'polymarket_us:<family>:<token>'. Kalshi keys (KX*) never match.
    return key == _PM_PREFIX or key.startswith(_PM_PREFIX + ":")


def reset_polymarket_weights(
    *,
    weights_path: Path,
    counters_path: Path,
    backup_dir: Path | None,
    execute: bool,
) -> dict:
    """Reset PM-prefixed entries from both stores. Returns a summary dict.

    Pure-ish core for testing: when ``execute`` is False, nothing is written.
    ``backup_dir`` (when given and execute) receives copies of both files first.
    """
    summary: dict = {"executed": execute, "weights": {}, "counters": {}, "backup_dir": None}

    # --- weights JSON ---
    weights = json.loads(weights_path.read_text(encoding="utf-8")) if weights_path.exists() else {}
    pm_keys = [k for k in weights if _is_pm_key(k)]
    kx_keys = [k for k in weights if not _is_pm_key(k)]
    summary["weights"] = {
        "path": str(weights_path),
        "pm_removed": len(pm_keys),
        "kx_kept": len(kx_keys),
        "pm_keys_sample": pm_keys[:5],
    }

    # --- counters DB ---
    pm_rows = kx_rows = 0
    counters_exists = counters_path.exists()
    if counters_exists:
        conn = sqlite3.connect(str(counters_path))
        try:
            pm_rows = conn.execute(
                "SELECT COUNT(*) FROM match_token_fp_counters WHERE market_prefix = ? OR market_prefix GLOB ?",
                (_PM_PREFIX, _PM_GLOB),
            ).fetchone()[0]
            kx_rows = conn.execute(
                "SELECT COUNT(*) FROM match_token_fp_counters WHERE NOT (market_prefix = ? OR market_prefix GLOB ?)",
                (_PM_PREFIX, _PM_GLOB),
            ).fetchone()[0]
        finally:
            conn.close()
    summary["counters"] = {
        "path": str(counters_path),
        "exists": counters_exists,
        "pm_removed": pm_rows,
        "kx_kept": kx_rows,
    }

    if not execute:
        return summary

    # --- backup BEFORE writing ---
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        if weights_path.exists():
            shutil.copy2(weights_path, backup_dir / weights_path.name)
        if counters_exists:
            shutil.copy2(counters_path, backup_dir / counters_path.name)
        summary["backup_dir"] = str(backup_dir)

    # --- write weights (drop PM keys) ---
    if pm_keys:
        for k in pm_keys:
            del weights[k]
        tmp = weights_path.with_suffix(weights_path.suffix + ".tmp")
        tmp.write_text(json.dumps(weights, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(weights_path)

    # --- purge counters (delete PM rows) ---
    if counters_exists and pm_rows:
        conn = sqlite3.connect(str(counters_path))
        try:
            with conn:
                conn.execute(
                    "DELETE FROM match_token_fp_counters WHERE market_prefix = ? OR market_prefix GLOB ?",
                    (_PM_PREFIX, _PM_GLOB),
                )
        finally:
            conn.close()

    # --- verify ---
    after = json.loads(weights_path.read_text(encoding="utf-8")) if weights_path.exists() else {}
    summary["verify_pm_keys_remaining"] = sum(1 for k in after if _is_pm_key(k))
    summary["verify_kx_keys_remaining"] = sum(1 for k in after if not _is_pm_key(k))
    if counters_exists:
        conn = sqlite3.connect(str(counters_path))
        try:
            summary["verify_pm_rows_remaining"] = conn.execute(
                "SELECT COUNT(*) FROM match_token_fp_counters WHERE market_prefix = ? OR market_prefix GLOB ?",
                (_PM_PREFIX, _PM_GLOB),
            ).fetchone()[0]
        finally:
            conn.close()
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Write full PM reset changes (default: dry-run). "
            "For bare-only quarantine, use scripts/polymarket_feedback_state_audit.py "
            "--apply-quarantine --confirm-runtime-mutation."
        ),
    )
    p.add_argument("--weights", type=Path, default=_DEFAULT_WEIGHTS)
    p.add_argument("--counters", type=Path, default=_DEFAULT_COUNTERS)
    args = p.parse_args(argv)

    backup_dir = None
    if args.execute:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = _DEFAULT_BACKUP_ROOT / f"pm_weight_reset_{ts}"

    summary = reset_polymarket_weights(
        weights_path=args.weights,
        counters_path=args.counters,
        backup_dir=backup_dir,
        execute=args.execute,
    )
    mode = "EXECUTED" if args.execute else "DRY-RUN (use --execute to write)"
    print(f"PM token-weight reset [{mode}]")
    print(f"  weights : drop {summary['weights']['pm_removed']} PM keys, keep {summary['weights']['kx_kept']} KX")
    print(f"  counters: drop {summary['counters']['pm_removed']} PM rows, keep {summary['counters']['kx_kept']} KX")
    if args.execute:
        print(f"  backup  : {summary['backup_dir']}")
        print(f"  verify  : PM keys remaining={summary.get('verify_pm_keys_remaining')} "
              f"(want 0), KX keys={summary.get('verify_kx_keys_remaining')}, "
              f"PM counter rows remaining={summary.get('verify_pm_rows_remaining')} (want 0)")
        if summary.get("verify_pm_keys_remaining") or summary.get("verify_pm_rows_remaining"):
            print("  ERROR: PM entries still present after reset", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
