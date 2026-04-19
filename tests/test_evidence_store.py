import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from config import DATA_DIR
from tasks.evidence_store import (
    DEFAULT_EVIDENCE_DB_PATH,
    DossierState,
    DossierUpdateRecord,
    DuplicateEvidenceError,
    EvidenceRecord,
    EvidenceStore,
    EvidenceStoreIntegrityError,
)
from analysis.evidence_types import PriorEstimate


TS0 = "2026-04-19T00:00:00+00:00"
TS1 = "2026-04-19T00:01:00+00:00"
TS2 = "2026-04-19T00:02:00+00:00"


def _store(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "evidence_store.db")


def _dossier(market_ticker: str = "KXTEST-26DEC31", version: int = 0) -> DossierState:
    return DossierState(
        market_ticker=market_ticker,
        dossier_version=version,
        current_estimate=0.50,
        confidence=0.10,
        prior_estimate=0.50,
        created_ts=TS0,
        updated_ts=TS0 if version == 0 else TS2,
    )


def _evidence(
    evidence_id: str = "ev-1",
    *,
    market_ticker: str = "KXTEST-26DEC31",
    version_before: int = 0,
    version_after: int = 1,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        market_ticker=market_ticker,
        source="Reuters",
        source_class="wire",
        headline=f"Evidence {evidence_id}",
        ingested_ts=TS1,
        content_hash=f"hash-{evidence_id}",
        update_type="state",
        dossier_version_before=version_before,
        dossier_version_after=version_after,
        published_ts=TS0,
        headline_ngram_fingerprint=f"fingerprint-{evidence_id}",
        quality_score=0.75,
        original_weight=0.80,
    )


def _update_record() -> DossierUpdateRecord:
    return DossierUpdateRecord(
        market_ticker="KXTEST-26DEC31",
        dossier_version=1,
        created_ts=TS2,
        trigger_evidence_id="ev-1",
        prior_estimate=0.50,
        new_estimate=0.56,
        update_delta=0.06,
        confidence_before=0.10,
        confidence_after=0.22,
        update_type="state",
        llm_called=False,
        drift_suspect=False,
        in_recovery=False,
    )


def _structural_prior(
    *,
    market_ticker: str = "KXTEST-26DEC31",
    estimate: float = 0.55,
    confidence: float = 0.25,
    computed_ts: str = TS1,
    input_source_count: int = 1,
    llm_called: bool = False,
) -> PriorEstimate:
    return PriorEstimate(
        market_ticker=market_ticker,
        estimate=estimate,
        confidence=confidence,
        input_source_count=input_source_count,
        llm_called=llm_called,
        computed_ts=computed_ts,
    )


@pytest.mark.asyncio
async def test_get_dossier_returns_none_for_missing_market(tmp_path: Path):
    store = _store(tmp_path)

    assert await store.get_dossier("KXMISSING-26DEC31") is None


@pytest.mark.asyncio
async def test_update_dossier_persists_and_gets_current_state(tmp_path: Path):
    store = _store(tmp_path)

    await store.update_dossier(_dossier(version=0))
    state = await store.get_dossier("KXTEST-26DEC31")

    assert state == _dossier(version=0)


@pytest.mark.asyncio
async def test_add_evidence_inserts_valid_evidence(tmp_path: Path):
    store = _store(tmp_path)
    await store.update_dossier(_dossier())
    await store.add_evidence(_evidence())

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", ("ev-1",)).fetchone()

    assert row["market_ticker"] == "KXTEST-26DEC31"
    assert row["source_class"] == "wire"
    assert row["update_type"] == "state"


@pytest.mark.asyncio
async def test_get_recent_evidence_returns_newest_first(tmp_path: Path):
    store = _store(tmp_path)
    await store.update_dossier(_dossier())
    first = _evidence("ev-1")
    second = EvidenceRecord(
        **{
            **_evidence("ev-2").__dict__,
            "ingested_ts": TS2,
            "dossier_version_before": 1,
            "dossier_version_after": 2,
        }
    )
    await store.add_evidence(first)
    await store.add_evidence(second)

    rows = await store.get_recent_evidence("KXTEST-26DEC31", limit=2)

    assert [row.evidence_id for row in rows] == ["ev-2", "ev-1"]


@pytest.mark.asyncio
async def test_list_dossier_market_tickers_returns_sorted_tickers(tmp_path: Path):
    store = _store(tmp_path)
    await store.update_dossier(_dossier(market_ticker="KXTWO-26DEC31"))
    await store.update_dossier(_dossier(market_ticker="KXONE-26DEC31"))

    assert await store.list_dossier_market_tickers() == [
        "KXONE-26DEC31",
        "KXTWO-26DEC31",
    ]


@pytest.mark.asyncio
async def test_add_evidence_duplicate_event_id_fails_clearly(tmp_path: Path):
    store = _store(tmp_path)
    await store.update_dossier(_dossier())
    await store.add_evidence(_evidence())

    with pytest.raises(DuplicateEvidenceError, match="duplicate evidence_id: ev-1"):
        await store.add_evidence(_evidence())


@pytest.mark.asyncio
async def test_add_evidence_requires_valid_parent_dossier(tmp_path: Path):
    store = _store(tmp_path)

    with pytest.raises(EvidenceStoreIntegrityError, match="FOREIGN KEY"):
        await store.add_evidence(_evidence())


@pytest.mark.asyncio
async def test_update_dossier_appends_update_history_and_contributing_links(tmp_path: Path):
    store = _store(tmp_path)
    await store.update_dossier(_dossier())
    await store.add_evidence(_evidence())
    await store.update_dossier(
        _dossier(version=1),
        update=_update_record(),
        evidence_ids_contributing=["ev-1"],
    )

    state = await store.get_dossier("KXTEST-26DEC31")
    assert state.dossier_version == 1

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        update_row = conn.execute(
            "SELECT * FROM dossier_updates WHERE market_ticker = ? AND dossier_version = ?",
            ("KXTEST-26DEC31", 1),
        ).fetchone()
        link_row = conn.execute(
            "SELECT * FROM dossier_update_evidence WHERE market_ticker = ?",
            ("KXTEST-26DEC31",),
        ).fetchone()

    assert update_row["trigger_evidence_id"] == "ev-1"
    assert link_row["evidence_id"] == "ev-1"


@pytest.mark.asyncio
async def test_structural_prior_upsert_and_get(tmp_path: Path):
    store = _store(tmp_path)
    await store.update_dossier(_dossier())

    await store.update_structural_prior(
        _structural_prior(),
        recompute_trigger="scheduled",
    )
    await store.update_structural_prior(
        _structural_prior(estimate=0.62, confidence=0.40, computed_ts=TS2, input_source_count=3, llm_called=True),
        recompute_trigger="dossier_update",
    )

    prior = await store.get_structural_prior("KXTEST-26DEC31")

    assert prior is not None
    assert prior.market_ticker == "KXTEST-26DEC31"
    assert prior.prior_estimate == pytest.approx(0.62)
    assert prior.confidence == pytest.approx(0.40)
    assert prior.computed_ts == TS2
    assert prior.recompute_trigger == "dossier_update"
    assert prior.input_source_count == 3
    assert prior.llm_called is True

    with sqlite3.connect(store.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM structural_priors").fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_update_dossier_rejects_mismatched_update_market(tmp_path: Path):
    store = _store(tmp_path)
    update = DossierUpdateRecord(
        **{**_update_record().__dict__, "market_ticker": "KXOTHER-26DEC31"}
    )

    with pytest.raises(ValueError, match="update.market_ticker"):
        await store.update_dossier(_dossier(), update=update)


@pytest.mark.asyncio
async def test_concurrent_reads_are_safe(tmp_path: Path):
    store = _store(tmp_path)
    await store.update_dossier(_dossier())

    states = await asyncio.gather(
        *(store.get_dossier("KXTEST-26DEC31") for _ in range(20))
    )

    assert {state.market_ticker for state in states} == {"KXTEST-26DEC31"}


@pytest.mark.asyncio
async def test_same_market_writes_are_serialized(tmp_path: Path):
    store = _store(tmp_path)
    active = 0
    max_active = 0
    guard = threading.Lock()

    def operation() -> None:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with guard:
            active -= 1

    await asyncio.gather(
        store._run_market_write("KXTEST-26DEC31", operation),
        store._run_market_write("KXTEST-26DEC31", operation),
        store._run_market_write("KXTEST-26DEC31", operation),
    )

    assert max_active == 1


@pytest.mark.asyncio
async def test_different_market_writes_do_not_share_market_lock(tmp_path: Path):
    store = _store(tmp_path)
    active = 0
    max_active = 0
    guard = threading.Lock()

    def operation() -> None:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with guard:
            active -= 1

    await asyncio.gather(
        store._run_market_write("KXONE-26DEC31", operation),
        store._run_market_write("KXTWO-26DEC31", operation),
    )

    assert max_active == 2


def test_default_evidence_store_path_is_separate_from_paper_trades_db():
    assert DEFAULT_EVIDENCE_DB_PATH == DATA_DIR / "evidence_store.db"
    assert DEFAULT_EVIDENCE_DB_PATH != DATA_DIR / "paper_trades.db"
