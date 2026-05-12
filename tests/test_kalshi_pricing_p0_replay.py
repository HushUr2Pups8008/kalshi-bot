"""
P-1 RED tests for the post-P0 replay-corpus quality guard (P0-REG-022) and
the ts-sentinel cohort filter (LD-7 / CR-F).

These tests lock in:
  * Post-P0 replay corpus must contain non-50 prices OR explicit skips.
  * Cohort filter is gated by `bot_state.p0_price_fix_deployed_ts` —
    NOT by the presence/absence of `p0_contract_version=1` on JSONL rows
    (SQLite rows never carry that field, so field-absence would leak
    pre-P0 SQLite rows; the ts sentinel is the only correct predicate).

Expected RED today because:
  * `filter_post_p0_rows` does not exist in `scripts/edge_replay/build_replay_dataset.py`.
  * No production cohort cut is wired through `bot_state.p0_price_fix_deployed_ts`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


# Module-under-test — RED on import until P-8 ships filter_post_p0_rows.
from scripts.edge_replay.build_replay_dataset import (  # type: ignore[import-not-found]  # noqa: E402
    filter_post_p0_rows,
)


# Synthetic rows mirror the dual-source corpus shape: SQLite (paper_trades)
# rows do NOT carry p0_contract_version; JSONL rows DO carry it post-P0.
SENTINEL_TS = "2026-05-15T00:00:00+00:00"


def _row(*, decision_ts: str, source: str, price_cents: int = 42,
         p0_contract_version: int | None = None,
         price_available: bool = True, skip_reason: str | None = None) -> dict:
    row = {
        "decision_ts": decision_ts,
        "source": source,
        "price_cents": price_cents,
        "price_available": price_available,
    }
    if p0_contract_version is not None:
        row["p0_contract_version"] = p0_contract_version
    if skip_reason is not None:
        row["skip_reason"] = skip_reason
    return row


# ---------------------------------------------------------------------------
# P0-REG-022 — replay corpus quality
# ---------------------------------------------------------------------------

def test_p0_reg_022_post_fix_corpus_quality_rejects_violating_row_set():
    """REG-022 (synthetic negative case): a corpus where every row is
    `price_cents=50, price_available=True, skip_reason=None` is the exact
    contamination shape P0 was designed to eliminate. The corpus-quality
    helper must reject this row set, not accept it.

    Locks-in: post-P0 replay corpora carry real executable prices OR
    explicit skip reasons; a corpus that is entirely silent-50 is
    contaminated.
    """
    contaminated_rows = [
        {"price_cents": 50, "price_available": True, "skip_reason": None}
        for _ in range(10)
    ]
    # Expected target helper (does not yet exist in scripts/edge_replay/
    # build_replay_dataset.py — RED until P-8 ships).
    from scripts.edge_replay.build_replay_dataset import (  # type: ignore[import-not-found]  # noqa: E402
        assert_corpus_quality_or_raise,
    )
    with pytest.raises(AssertionError):
        assert_corpus_quality_or_raise(contaminated_rows)


@pytest.mark.skipif(
    not Path(__file__).parent.parent.joinpath(
        "logs/edge_replay/cycle17c-e3"
    ).exists(),
    reason="real replay corpus path not present in this checkout",
)
def test_p0_reg_022_real_corpus_has_non_50_prices_or_explicit_skips():
    """Real replay corpus probe — runs only when the cycle17c-e3 path exists.

    The assertion is intentionally the same as the synthetic case, but
    consumes whichever JSONL rows are present. Marked skipif when absent.
    """
    corpus_dir = Path(__file__).parent.parent / "logs" / "edge_replay" / "cycle17c-e3"
    jsonls = list(corpus_dir.glob("*.jsonl"))
    if not jsonls:
        pytest.skip("no JSONL files in cycle17c-e3 corpus")

    import json
    rows: list[dict] = []
    for p in jsonls:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not rows:
        pytest.skip("corpus had no parseable rows")

    non_50_available = [
        r for r in rows
        if r.get("price_available") is True and r.get("price_cents") != 50
    ]
    suspect = [
        r for r in rows
        if r.get("price_cents") == 50 or r.get("price_available") is False
    ]
    assert non_50_available or all(
        r.get("skip_reason") for r in suspect
    ), "real corpus failed P0-REG-022 invariant"


# ---------------------------------------------------------------------------
# LD-7 / CR-F — cohort filter behavior
# ---------------------------------------------------------------------------

def test_p0_cohort_filter_drops_pre_sentinel_rows():
    """Pre-sentinel rows drop; post-sentinel rows survive."""
    rows = [
        _row(decision_ts="2026-05-10T12:00:00+00:00", source="sqlite",
             price_cents=42),  # pre
        _row(decision_ts="2026-05-14T23:59:59+00:00", source="jsonl",
             price_cents=42, p0_contract_version=1),  # pre (1s before)
        _row(decision_ts="2026-05-15T00:00:01+00:00", source="sqlite",
             price_cents=42),  # post
        _row(decision_ts="2026-05-16T12:00:00+00:00", source="jsonl",
             price_cents=42, p0_contract_version=1),  # post
    ]
    kept = list(filter_post_p0_rows(rows, SENTINEL_TS))
    kept_ts = sorted(r["decision_ts"] for r in kept)
    assert kept_ts == [
        "2026-05-15T00:00:01+00:00",
        "2026-05-16T12:00:00+00:00",
    ], f"unexpected kept rows: {kept_ts}"


def test_p0_cohort_filter_uses_bot_state_sentinel_not_jsonl_field():
    """The cohort cut is the ts sentinel — NOT the presence of
    p0_contract_version. SQLite rows never carry that field; filtering on
    it would silently drop all SQLite rows."""
    rows = [
        # Pre-sentinel SQLite (no p0_contract_version): MUST drop.
        _row(decision_ts="2026-05-13T00:00:00+00:00", source="sqlite",
             price_cents=42),
        # Pre-sentinel JSONL (no p0_contract_version yet): MUST drop.
        _row(decision_ts="2026-05-13T01:00:00+00:00", source="jsonl",
             price_cents=42),
        # Post-sentinel SQLite (still no p0_contract_version — by design):
        # MUST keep.
        _row(decision_ts="2026-05-16T01:00:00+00:00", source="sqlite",
             price_cents=42),
        # Post-sentinel JSONL with field present: MUST keep.
        _row(decision_ts="2026-05-16T02:00:00+00:00", source="jsonl",
             price_cents=42, p0_contract_version=1),
    ]
    kept = list(filter_post_p0_rows(rows, SENTINEL_TS))
    assert len(kept) == 2, (
        f"expected exactly 2 post-sentinel rows kept, got {len(kept)}: "
        f"{[r['decision_ts'] for r in kept]}"
    )
    kept_sources = sorted(r["source"] for r in kept)
    assert kept_sources == ["jsonl", "sqlite"], (
        f"cohort filter must keep BOTH a SQLite and a JSONL post-sentinel "
        f"row regardless of p0_contract_version presence; kept_sources={kept_sources}"
    )


def test_p0_cohort_filter_field_absence_does_not_leak_pre_p0_rows():
    """Regression guard: ensure the predicate doesn't accidentally use
    field-presence as a proxy. A pre-sentinel JSONL row WITHOUT
    p0_contract_version still drops; a post-sentinel SQLite row WITHOUT
    the field still keeps."""
    rows = [
        # Pre-sentinel JSONL with NO p0_contract_version — must drop on ts alone.
        _row(decision_ts="2026-05-01T00:00:00+00:00", source="jsonl",
             price_cents=42),
        # Post-sentinel SQLite with NO p0_contract_version — must keep on ts alone.
        _row(decision_ts="2026-06-01T00:00:00+00:00", source="sqlite",
             price_cents=42),
    ]
    kept = list(filter_post_p0_rows(rows, SENTINEL_TS))
    assert len(kept) == 1
    assert kept[0]["decision_ts"] == "2026-06-01T00:00:00+00:00"
    assert "p0_contract_version" not in kept[0], (
        "test fixture corruption: should not have injected p0_contract_version"
    )


def test_p0_cohort_filter_accepts_datetime_sentinel():
    """Sentinel passed as datetime (not just ISO string) should work too —
    callers may pass either form pulled from bot_state."""
    rows = [
        _row(decision_ts="2026-05-13T00:00:00+00:00", source="sqlite"),
        _row(decision_ts="2026-05-16T00:00:00+00:00", source="sqlite"),
    ]
    sentinel_dt = datetime(2026, 5, 15, 0, 0, 0, tzinfo=timezone.utc)
    kept = list(filter_post_p0_rows(rows, sentinel_dt))
    assert len(kept) == 1
    assert kept[0]["decision_ts"] == "2026-05-16T00:00:00+00:00"
