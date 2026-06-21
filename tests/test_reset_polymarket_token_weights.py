"""No-harm pins for the PM token-weight reset migration (PROFIT-VENUE-PARITY V17).

The reset must remove ONLY polymarket_us-prefixed entries from BOTH stores and
leave Kalshi (KX*) entries byte-identical. A reset that touches KX would wipe
hard-won Kalshi calibration; a reset that misses the counters DB would be
silently undone by the next aggregation.
"""
from __future__ import annotations

import json
import sqlite3

from scripts.reset_polymarket_token_weights import reset_polymarket_weights


def _seed_counters(path):
    conn = sqlite3.connect(str(path))
    with conn:
        conn.execute(
            """CREATE TABLE match_token_fp_counters (
                token TEXT NOT NULL, market_prefix TEXT NOT NULL, day_utc TEXT NOT NULL,
                fp_neutral_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (token, market_prefix, day_utc))"""
        )
        conn.executemany(
            "INSERT INTO match_token_fp_counters VALUES (?,?,?,?)",
            [
                ("iran", "polymarket_us", "2026-06-10", 3),  # old venue bucket
                ("dem", "polymarket_us:ewc-usse-me", "2026-06-10", 1),  # new per-family
                ("trump", "KXPRES", "2026-06-10", 2),  # KX — must survive
                ("rate", "KXFED", "2026-06-11", 5),  # KX — must survive
            ],
        )
    conn.close()


def test_reset_removes_only_pm_entries_both_stores(tmp_path):
    weights = tmp_path / "matcher_token_weights.json"
    weights.write_text(
        json.dumps(
            {
                "polymarket_us": {"weight": 0.1},
                "polymarket_us:ewc-usse-me:dem": {"weight": 0.11},
                "KXPRES:trump": {"weight": 0.3},
                "KXFED:rate": {"weight": 0.9},
            }
        ),
        encoding="utf-8",
    )
    counters = tmp_path / "match_token_fp_counters.db"
    _seed_counters(counters)
    backup = tmp_path / "backup"

    summary = reset_polymarket_weights(
        weights_path=weights, counters_path=counters, backup_dir=backup, execute=True
    )

    # weights: 2 PM dropped, 2 KX kept, exactly those two survive
    after = json.loads(weights.read_text(encoding="utf-8"))
    assert set(after) == {"KXPRES:trump", "KXFED:rate"}
    assert after["KXPRES:trump"] == {"weight": 0.3}  # KX value untouched
    assert summary["weights"]["pm_removed"] == 2
    assert summary["verify_pm_keys_remaining"] == 0

    # counters: 2 PM rows dropped (bare + per-family), 2 KX rows survive
    conn = sqlite3.connect(str(counters))
    rows = {(r[0], r[1]) for r in conn.execute("SELECT token, market_prefix FROM match_token_fp_counters")}
    conn.close()
    assert rows == {("trump", "KXPRES"), ("rate", "KXFED")}
    assert summary["counters"]["pm_removed"] == 2
    assert summary["verify_pm_rows_remaining"] == 0

    # backup of both files exists before the destructive write
    assert (backup / "matcher_token_weights.json").exists()
    assert (backup / "match_token_fp_counters.db").exists()


def test_dry_run_writes_nothing(tmp_path):
    weights = tmp_path / "matcher_token_weights.json"
    original = json.dumps({"polymarket_us": {"weight": 0.1}, "KXPRES:trump": {"weight": 0.3}})
    weights.write_text(original, encoding="utf-8")
    counters = tmp_path / "match_token_fp_counters.db"
    _seed_counters(counters)

    summary = reset_polymarket_weights(
        weights_path=weights, counters_path=counters, backup_dir=None, execute=False
    )

    assert weights.read_text(encoding="utf-8") == original  # untouched
    assert summary["weights"]["pm_removed"] == 1  # reports what WOULD go
    assert summary["counters"]["pm_removed"] == 2
    conn = sqlite3.connect(str(counters))
    assert conn.execute("SELECT COUNT(*) FROM match_token_fp_counters").fetchone()[0] == 4
    conn.close()
