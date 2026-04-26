import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from analysis.evidence_types import PriorEstimate
from kalshi import KalshiMarket
from tasks.evidence_store import DossierState, EvidenceRecord, EvidenceStore
from tasks.structural_task import (
    StructuralComputationError,
    StructuralTask,
    run_once,
)


TS0 = "2026-04-19T00:00:00+00:00"
TS1 = "2026-04-19T00:01:00+00:00"
TS2 = "2026-04-19T00:02:00+00:00"
TS3 = "2026-04-19T00:03:00+00:00"


def _store(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "evidence_store.db")


def _market(ticker: str = "KXSTRUCT-26DEC31") -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title="Test structural market",
        yes_bid=54.0,
        yes_ask=56.0,
        yes_price=55.0,
        volume=1000,
        open_interest=500,
        close_time="2026-12-31T23:59:59Z",
        status="open",
        regime_weights={"fast": 0.1, "interpretation": 0.2, "structural": 0.7},
    )


def _dossier(
    market_ticker: str = "KXSTRUCT-26DEC31",
    *,
    updated_ts: str = TS2,
) -> DossierState:
    return DossierState(
        market_ticker=market_ticker,
        dossier_version=1,
        current_estimate=0.55,
        confidence=0.40,
        prior_estimate=0.55,
        created_ts=TS0,
        updated_ts=updated_ts,
    )


def _evidence(
    evidence_id: str = "ev-1",
    *,
    market_ticker: str = "KXSTRUCT-26DEC31",
    source: str = "Reuters",
    source_class: str = "news",
    ingested_ts: str = TS1,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        market_ticker=market_ticker,
        source=source,
        source_class=source_class,
        headline=f"Evidence {evidence_id}",
        ingested_ts=ingested_ts,
        content_hash=f"hash-{evidence_id}",
        update_type="state",
        dossier_version_before=0,
        dossier_version_after=1,
        original_weight=0.80,
    )


def _prior(
    *,
    market_ticker: str = "KXSTRUCT-26DEC31",
    estimate: float = 0.51,
    confidence: float = 0.30,
    computed_ts: str = TS3,
) -> PriorEstimate:
    return PriorEstimate(
        market_ticker=market_ticker,
        estimate=estimate,
        confidence=confidence,
        input_source_count=0,
        llm_called=False,
        computed_ts=computed_ts,
    )


class SpyLogger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def log_structural_prior_recompute(self, **payload) -> None:
        self.events.append(payload)


class SpyComputer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[KalshiMarket, dict]] = []

    def __call__(self, market: KalshiMarket, context: dict) -> PriorEstimate:
        self.calls.append((market, context))
        if self.fail:
            raise RuntimeError("structural prior failed")
        return PriorEstimate(
            market_ticker=market.ticker,
            estimate=0.62,
            confidence=0.44,
            input_source_count=len(context["evidence_records"]),
            llm_called=False,
            computed_ts=context["now_ts"],
        )


def _task(
    tmp_path: Path,
    *,
    computer: SpyComputer | None = None,
    logger: SpyLogger | None = None,
) -> tuple[StructuralTask, SpyComputer, SpyLogger]:
    computer = computer or SpyComputer()
    logger = logger or SpyLogger()
    return (
        StructuralTask(
            store=_store(tmp_path),
            computer=computer,
            logger=logger,
            now=lambda: datetime.fromisoformat(TS3),
        ),
        computer,
        logger,
    )


@pytest.mark.asyncio
async def test_no_prior_recomputes_persists_and_emits_telemetry(tmp_path: Path):
    task, computer, logger = _task(tmp_path)
    await task.store.update_dossier(_dossier())
    await task.store.add_evidence(_evidence(source="Reuters", source_class="news"))
    await task.store.add_evidence(
        _evidence("ev-2", source="StateDept", source_class="official", ingested_ts=TS2)
    )

    result = await task.process_market(_market())

    assert result.status == "recomputed"
    assert result.recompute_trigger == "scheduled"
    assert result.prior is not None
    assert computer.calls[0][0].ticker == "KXSTRUCT-26DEC31"
    assert computer.calls[0][1]["dossier"].updated_ts == TS2
    assert [record.evidence_id for record in computer.calls[0][1]["evidence_records"]] == [
        "ev-2",
        "ev-1",
    ]
    assert logger.events == [
        {
            "market_ticker": "KXSTRUCT-26DEC31",
            "prior_estimate": 0.0,
            "new_estimate": 0.62,
            "input_sources": ["official:StateDept", "news:Reuters"],
            "llm_called": False,
            "token_count": 0,
        }
    ]

    stored = await task.store.get_structural_prior("KXSTRUCT-26DEC31")
    assert stored is not None
    assert stored.prior_estimate == pytest.approx(0.62)
    assert stored.confidence == pytest.approx(0.44)
    assert stored.computed_ts == TS3
    assert stored.recompute_trigger == "scheduled"
    assert stored.input_source_count == 2
    assert stored.llm_called is False


@pytest.mark.asyncio
async def test_new_dossier_update_recomputes_existing_prior(tmp_path: Path):
    task, computer, logger = _task(tmp_path)
    await task.store.update_dossier(_dossier(updated_ts=TS2))
    await task.store.update_structural_prior(
        _prior(computed_ts=TS1),
        recompute_trigger="scheduled",
    )

    result = await task.process_market(_market())

    assert result.status == "recomputed"
    assert result.recompute_trigger == "dossier_update"
    assert len(computer.calls) == 1
    assert logger.events[0]["prior_estimate"] == pytest.approx(0.51)
    stored = await task.store.get_structural_prior("KXSTRUCT-26DEC31")
    assert stored.recompute_trigger == "dossier_update"


@pytest.mark.asyncio
async def test_no_new_dossier_update_skips_without_telemetry(tmp_path: Path):
    task, computer, logger = _task(tmp_path)
    await task.store.update_dossier(_dossier(updated_ts=TS1))
    await task.store.update_structural_prior(
        _prior(computed_ts=TS2),
        recompute_trigger="scheduled",
    )

    result = await task.process_market(_market())

    assert result.status == "skipped"
    assert computer.calls == []
    assert logger.events == []


@pytest.mark.asyncio
async def test_missing_dossier_skips_without_persistence_or_telemetry(tmp_path: Path):
    task, computer, logger = _task(tmp_path)

    result = await task.process_market(_market())

    assert result.status == "missing_dossier"
    assert computer.calls == []
    assert logger.events == []
    assert await task.store.get_structural_prior("KXSTRUCT-26DEC31") is None


@pytest.mark.asyncio
async def test_run_once_processes_multiple_markets_safely(tmp_path: Path):
    task, computer, logger = _task(tmp_path)
    await task.store.update_dossier(_dossier("KXSTRUCT-A"))
    await task.store.update_dossier(_dossier("KXSTRUCT-B"))

    results = await task.run_once([_market("KXSTRUCT-A"), _market("KXSTRUCT-B")])

    assert sorted(result.market_ticker for result in results) == ["KXSTRUCT-A", "KXSTRUCT-B"]
    assert {result.status for result in results} == {"recomputed"}
    assert len(computer.calls) == 2
    assert len(logger.events) == 2
    assert await task.store.get_structural_prior("KXSTRUCT-A") is not None
    assert await task.store.get_structural_prior("KXSTRUCT-B") is not None


@pytest.mark.asyncio
async def test_module_run_once_uses_supplied_store(tmp_path: Path):
    store = _store(tmp_path)
    await store.update_dossier(_dossier())
    computer = SpyComputer()
    logger = SpyLogger()

    results = await run_once(
        [_market()],
        store=store,
        computer=computer,
        logger=logger,
    )

    assert len(results) == 1
    assert results[0].status == "recomputed"
    assert len(computer.calls) == 1
    assert len(logger.events) == 1


@pytest.mark.asyncio
async def test_computation_failure_is_visible_and_does_not_persist(tmp_path: Path):
    task, _, logger = _task(tmp_path, computer=SpyComputer(fail=True))
    await task.store.update_dossier(_dossier())

    with pytest.raises(StructuralComputationError, match="KXSTRUCT-26DEC31"):
        await task.process_market(_market())

    assert logger.events == []
    assert await task.store.get_structural_prior("KXSTRUCT-26DEC31") is None


@pytest.mark.asyncio
async def test_run_periodic_honors_stop_event(tmp_path: Path):
    task, _, _ = _task(tmp_path)
    await task.store.update_dossier(_dossier())
    stop_event = asyncio.Event()
    calls = 0

    def market_provider():
        nonlocal calls
        calls += 1
        stop_event.set()
        return [_market()]

    await task.run_periodic(
        market_provider,
        interval_seconds=0.01,
        stop_event=stop_event,
    )

    assert calls == 1


@pytest.mark.asyncio
async def test_periodic_scheduler_filters_to_dossier_backed_markets(tmp_path: Path):
    task, _, _ = _task(tmp_path)
    await task.store.update_dossier(_dossier("KXSTRUCT-A"))

    markets = [_market("KXSTRUCT-A"), _market("KXSTRUCT-B")]

    filtered = await task._dossier_backed_markets(markets)

    assert [market.ticker for market in filtered] == ["KXSTRUCT-A"]


@pytest.mark.asyncio
async def test_run_once_market_failure_does_not_propagate(tmp_path: Path):
    """run_once must not raise when individual markets fail.

    With SpyComputer(fail=True) every process_market raises
    StructuralComputationError.  run_once must absorb those exceptions,
    log warnings, and return an empty list — never re-raise.
    """
    task, _, _ = _task(tmp_path, computer=SpyComputer(fail=True))
    await task.store.update_dossier(_dossier("KXSTRUCT-A"))
    await task.store.update_dossier(_dossier("KXSTRUCT-B"))

    results = await task.run_once([_market("KXSTRUCT-A"), _market("KXSTRUCT-B")])

    assert results == [], "all failures → empty result list, no exception raised"


@pytest.mark.asyncio
async def test_run_once_failure_warning_surfaces_underlying_cause(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PROFIT-EDGE-002: structural failure log MUST surface __cause__.

    process_market wraps every exception in StructuralComputationError(...)
    from exc. The original logging used `%s % r`, which only shows the
    wrapper message ("failed structural recompute for X") and discards the
    chained cause — so production failures across many markets were silently
    untraceable. v0.29.57 surfaces the cause explicitly.
    """
    task, _, _ = _task(tmp_path, computer=SpyComputer(fail=True))
    await task.store.update_dossier(_dossier("KXSTRUCT-A"))

    with caplog.at_level("WARNING", logger="tasks.structural_task"):
        await task.run_once([_market("KXSTRUCT-A")])

    failure_records = [
        r for r in caplog.records
        if "per-market recompute failed" in r.getMessage()
    ]
    assert failure_records, "expected a per-market failure WARNING"
    msg = failure_records[0].getMessage()
    # The wrapper text is preserved for backwards compatibility.
    assert "failed structural recompute for KXSTRUCT-A" in msg
    # The cause must be surfaced so failures stop being silent.
    assert "RuntimeError" in msg, (
        f"underlying cause not surfaced in log message: {msg!r}"
    )
    assert "structural prior failed" in msg, (
        f"underlying message not surfaced in log message: {msg!r}"
    )
