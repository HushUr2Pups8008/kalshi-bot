"""
MAC-TEST-002: Concurrent multi-market write test for EvidenceStore.

Verifies that 20 concurrent asyncio.gather() writes to 20 different market
tickers all succeed without OperationalError (i.e., WAL mode is active and
SQLite's global write-lock contention does not serialise to the point of
failure or data loss).

The test also serves as a regression guard: if PRAGMA journal_mode=WAL is
removed from _connect(), the test should fail with an OperationalError on
a busy concurrent write scenario (not guaranteed on every run, but reliably
detected by the assertion that all 20 dossiers are readable afterward).
"""

import asyncio
from pathlib import Path

import pytest

from tasks.evidence_store import DossierState, EvidenceRecord, EvidenceStore


def _dossier(ticker: str) -> DossierState:
    return DossierState(
        market_ticker=ticker,
        dossier_version=0,
        current_estimate=0.50,
        confidence=0.10,
        prior_estimate=0.50,
        created_ts="2026-04-20T00:00:00+00:00",
        updated_ts="2026-04-20T00:00:00+00:00",
    )


def _evidence(ticker: str, idx: int) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"ev-{ticker}-{idx}",
        market_ticker=ticker,
        source="Reuters",
        source_class="wire",
        headline=f"Evidence {idx} for {ticker}",
        ingested_ts="2026-04-20T00:01:00+00:00",
        content_hash=f"hash-{ticker}-{idx}",
        update_type="state",
        dossier_version_before=0,
        dossier_version_after=1,
        published_ts="2026-04-20T00:00:00+00:00",
        headline_ngram_fingerprint=f"fp-{ticker}-{idx}",
        quality_score=0.75,
        original_weight=0.80,
    )


@pytest.mark.asyncio
async def test_concurrent_writes_to_20_markets_no_operational_error(tmp_path: Path):
    """MAC-TEST-002 regression guard: 20 concurrent market writes must all succeed."""
    store = EvidenceStore(tmp_path / "evidence.db")
    tickers = [f"KXCONC-TICKER{i:02d}" for i in range(20)]

    # Initialize all 20 dossiers sequentially (one-time setup)
    for ticker in tickers:
        await store.update_dossier(_dossier(ticker))

    # Fire 20 concurrent evidence writes across all markets simultaneously
    async def write_evidence(ticker: str, idx: int) -> None:
        await store.add_evidence(_evidence(ticker, idx))

    await asyncio.gather(*[write_evidence(t, i) for i, t in enumerate(tickers)])

    # Verify all 20 dossiers are readable and evidence is present
    for ticker in tickers:
        dossier = await store.get_dossier(ticker)
        assert dossier is not None, f"Dossier missing for {ticker} after concurrent write"
        assert dossier.market_ticker == ticker

    recent = [
        await store.get_recent_evidence(t, limit=5) for t in tickers
    ]
    for ticker, evs in zip(tickers, recent):
        assert len(evs) == 1, (
            f"Expected 1 evidence record for {ticker}, got {len(evs)}. "
            "Concurrent write may have lost data — MAC-TEST-002."
        )


@pytest.mark.asyncio
async def test_concurrent_writes_same_market_are_serialised(tmp_path: Path):
    """Writes to the same market ticker serialise via per-market lock.

    Fires 10 sequential-version updates concurrently for one ticker.
    All must persist; the per-market asyncio.Lock() must prevent races.
    """
    store = EvidenceStore(tmp_path / "evidence.db")
    ticker = "KXSERIAL-TEST"
    await store.update_dossier(_dossier(ticker))

    # Add 10 evidence records to the same market concurrently
    async def write_evidence(idx: int) -> None:
        await store.add_evidence(_evidence(ticker, idx))

    await asyncio.gather(*[write_evidence(i) for i in range(10)])

    evs = await store.get_recent_evidence(ticker, limit=20)
    assert len(evs) == 10, (
        f"Expected 10 evidence records, got {len(evs)}. "
        "Per-market serialisation lock may have dropped writes — MAC-TEST-002."
    )


@pytest.mark.asyncio
async def test_wal_mode_is_active_after_first_write(tmp_path: Path):
    """Confirm WAL mode is set on the connection used for writes.

    If PRAGMA journal_mode=WAL is removed from _connect(), this test fails.
    The WAL journal mode returns 'wal' as the journal mode string.
    """
    import sqlite3 as _sqlite3

    store = EvidenceStore(tmp_path / "evidence.db")
    ticker = "KXWALCHECK"
    await store.update_dossier(_dossier(ticker))

    with _sqlite3.connect(str(store.db_path)) as conn:
        row = conn.execute("PRAGMA journal_mode").fetchone()

    assert row[0] == "wal", (
        f"Expected journal_mode=wal, got {row[0]!r}. "
        "MAC-DB-001 PRAGMA journal_mode=WAL may have been removed — MAC-TEST-002."
    )
