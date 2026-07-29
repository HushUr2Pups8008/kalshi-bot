from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import gc
from types import SimpleNamespace
import sqlite3
import weakref

import pytest

from analysis.research_gate import (
    ResearchEvidence,
    ResearchQuery,
    ResearchStatus,
    ResearchVerdict,
)
from tasks.research_dossier import ResearchDossierStore
from tasks import research_prewarm_task as research_prewarm_task_module
from tasks.research_prewarm_task import (
    ResearchPrewarmError,
    ResearchPrewarmResult,
    ResearchPrewarmTask,
    _ResearchPrewarmProviderAdmission,
    _has_independent_source_path,
    _prewarm_news,
    _write_research_prewarm_result,
)


def _market(ticker: str = "KXRESEARCH-26DEC31", *, status: str = "open"):
    return SimpleNamespace(
        ticker=ticker,
        title="Will the researched event resolve yes?",
        rules_primary="The market resolves from the official report.",
        rules_secondary="Later revisions are ignored.",
        settlement_sources=(),
        contract_terms_url="",
        status=status,
        close_time="2099-12-31T23:59:59Z",
        yes_ask_cents=60,
        no_ask_cents=40,
    )


def _decision_grade_verdict() -> ResearchVerdict:
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ResearchVerdict(
        status=ResearchStatus.DECISION_GRADE_CANDIDATE,
        attempted=True,
        queries=[
            ResearchQuery("official result", "official_resolution", "resolution_source"),
            ResearchQuery("countercase", "disconfirming", "reputable_secondary"),
        ],
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official Agency",
                source_url="https://agency.gov/result",
                title="Official result",
                snippet="Official evidence supports YES.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=retrieved_at,
                contract_fingerprint="fingerprint-1",
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Independent Wire",
                source_url="https://wire.example/context",
                title="Independent context",
                snippet="Independent reporting corroborates the result.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=retrieved_at,
                contract_fingerprint="fingerprint-1",
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Associated Press",
                source_url="https://apnews.com/counter",
                title="Countercase",
                snippet="A weak countercase supports NO.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.2,
                retrieved_at=retrieved_at,
                contract_fingerprint="fingerprint-1",
            ),
        ],
        force_side="yes",
        estimated_probability=0.68,
        confidence=0.82,
        market_price=0.55,
        estimated_edge=0.12,
        decision_grade_reasons=("price_present", "counter_evidence_present"),
        research_contract_fingerprint="fingerprint-1",
    )


def test_prewarm_news_does_not_fabricate_trigger_headline():
    news = _prewarm_news(_market())

    assert news.headline == ""
    assert news.source == "research_prewarm"


def test_prewarm_source_path_requires_source_class_diversity():
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Agency A",
            source_url="https://agency-a.gov/result",
            title="Result",
            snippet="Supports yes.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
        ),
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Agency B",
            source_url="https://agency-b.gov/counter",
            title="Counter",
            snippet="Supports no.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.9,
        ),
    ]

    assert _has_independent_source_path(evidence) is False


@pytest.mark.asyncio
async def test_prewarm_process_market_persists_research_run_and_evidence(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    provider_starts: list[float] = []
    provider_completions = 0
    loop = asyncio.get_running_loop()
    admission = _ResearchPrewarmProviderAdmission(
        min_start_interval_seconds=0.015,
        clock=loop.time,
    )
    monkeypatch.setattr(
        research_prewarm_task_module,
        "_get_research_prewarm_provider_admission",
        lambda: admission,
    )

    async def search_provider(_query):
        nonlocal provider_completions
        provider_starts.append(loop.time())
        await asyncio.sleep(0.006)
        provider_completions += 1
        return [
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official Report",
                source_url="https://official.example.com/report",
                title="Official report",
                snippet="The official report supports yes.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=retrieved_at,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wire",
                source_url="https://wire.example.com/context",
                title="Wire context",
                snippet="Independent context also supports yes.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=retrieved_at,
            ),
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.8,
            "reason": "Prewarmed evidence clears edge.",
        }

    task = ResearchPrewarmTask(
        store=store,
        search_provider=search_provider,
        adjudicator=adjudicator,
        research_timeout_seconds=0.15,
    )

    result = await task.process_market(_market())

    assert result.status == ResearchStatus.NEEDS_RESEARCH.value
    assert result.attempted is True
    assert result.evidence_count == 2
    assert provider_completions == 8
    assert len(provider_starts) == 8
    assert all(
        later - earlier >= 0.014
        for earlier, later in zip(provider_starts, provider_starts[1:])
    )
    # Seven required-intent starts plus the post-adjudication counter query.
    production_margin = 12.0 - (7 * 1.5 + 0.6)
    assert production_margin == pytest.approx(0.9)
    with sqlite3.connect(db_path) as conn:
        dossier = conn.execute(
            """
            SELECT last_verdict_status, last_force_side, contract_question
            FROM research_dossiers
            """
        ).fetchone()
        run_contract_question = conn.execute(
            "SELECT contract_question FROM research_runs"
        ).fetchone()
        evidence_count = conn.execute("SELECT COUNT(*) FROM research_evidence").fetchone()
    assert dossier == (
        ResearchStatus.NEEDS_RESEARCH.value,
        None,
        "Will the researched event resolve yes?",
    )
    assert run_contract_question == ("Will the researched event resolve yes?",)
    assert evidence_count == (2,)


@pytest.mark.asyncio
async def test_prewarm_passes_decision_grade_required_without_live_promotion(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls = []

    async def research_gate(news, market, **kwargs):
        calls.append((news, market, kwargs))
        return _decision_grade_verdict()

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market())

    assert result.status == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert calls[0][2]["live_mode"] is False
    assert calls[0][2]["require_decision_grade"] is True


@pytest.mark.asyncio
async def test_prewarm_run_once_market_failure_does_not_abort_cycle(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(_news, market, **_kwargs):
        if market.ticker == "KXRESEARCH-BAD":
            raise RuntimeError("research failed")
        return SimpleNamespace(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="no_research_hits",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    results = await task.run_once([
        _market("KXRESEARCH-BAD"),
        _market("KXRESEARCH-GOOD"),
    ])

    assert [result.market_ticker for result in results] == ["KXRESEARCH-GOOD"]
    assert results[0].status == ResearchStatus.CONTINUE_RESEARCHING.value


@pytest.mark.asyncio
async def test_prewarm_run_once_emits_market_result_sink_after_result_sink(
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    events: list[tuple[str, str]] = []

    async def research_gate(_news, _market, **_kwargs):
        return _decision_grade_verdict()

    async def result_sink(result: ResearchPrewarmResult) -> None:
        events.append(("result", result.market_ticker))

    async def market_result_sink(
        result: ResearchPrewarmResult,
        market,
    ) -> None:
        events.append(("market", market.ticker))

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        result_sink=result_sink,
        market_result_sink=market_result_sink,
    )

    results = await task.run_once([_market("KXRESEARCH-SINK")])

    assert results[0].status == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert events == [
        ("result", "KXRESEARCH-SINK"),
        ("market", "KXRESEARCH-SINK"),
    ]


@pytest.mark.asyncio
async def test_prewarm_run_once_limits_market_concurrency(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    started: list[str] = []
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def research_gate(_news, market, **_kwargs):
        nonlocal active, max_active
        started.append(market.ticker)
        active += 1
        max_active = max(max_active, active)
        await release.wait()
        active -= 1
        return SimpleNamespace(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="no_research_hits",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        max_concurrency=2,
    )
    run_task = asyncio.create_task(
        task.run_once([_market(f"KXRESEARCH-{i}") for i in range(5)])
    )
    while len(started) < 2:
        await asyncio.sleep(0)

    assert len(started) == 2
    assert set(started) <= {f"KXRESEARCH-{i}" for i in range(5)}
    assert max_active == 2
    release.set()
    results = await run_task

    assert len(results) == 5
    assert max_active == 2


@pytest.mark.asyncio
async def test_prewarm_default_serializes_local_adjudication(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    active = 0
    max_active = 0

    async def research_gate(_news, _market, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="no_research_hits",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
    )

    results = await task.run_once(
        [_market(f"KXRESEARCH-DEFAULT-{i}") for i in range(3)]
    )

    assert len(results) == 3
    assert max_active == 1


@pytest.mark.asyncio
async def test_prewarm_backpressures_query_fanout_before_transport_admission(
    monkeypatch,
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    transport_slots = asyncio.BoundedSemaphore(4)
    provider_started: list[str] = []
    provider_completed: list[str] = []
    provider_active = 0
    provider_peak = 0
    now = 0.0

    async def advance_clock(delay: float) -> None:
        nonlocal now
        now += delay
        await asyncio.sleep(0)

    admission = _ResearchPrewarmProviderAdmission(
        min_start_interval_seconds=1.5,
        clock=lambda: now,
        sleeper=advance_clock,
    )
    monkeypatch.setattr(
        research_prewarm_task_module,
        "_get_research_prewarm_provider_admission",
        lambda: admission,
    )

    async def search_provider(query: ResearchQuery):
        nonlocal provider_active, provider_peak
        provider_started.append(query.query)
        if transport_slots.locked():
            raise TimeoutError("transport deadline expired before DNS")
        async with transport_slots:
            provider_active += 1
            provider_peak = max(provider_peak, provider_active)
            await asyncio.sleep(0)
            provider_active -= 1
        provider_completed.append(query.query)
        return []

    async def research_gate(_news, _market, **kwargs):
        queries = [
            ResearchQuery(
                f"provider-query-{index}",
                "corroboration",
                "reputable_secondary",
            )
            for index in range(6)
        ]
        results = await asyncio.gather(
            *(kwargs["search_provider"](query) for query in queries),
            return_exceptions=True,
        )
        assert not [result for result in results if isinstance(result, BaseException)]
        return _decision_grade_verdict()

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        search_provider=search_provider,
    )

    result = await task.process_market(_market("KXRESEARCH-BACKPRESSURE"))

    assert result.status == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert provider_peak == 1
    assert provider_started == [f"provider-query-{index}" for index in range(6)]
    assert provider_completed == provider_started
    assert now == 7.5


@pytest.mark.asyncio
async def test_prewarm_shares_provider_admission_across_task_instances(
    monkeypatch,
    tmp_path,
):
    transport_slots = asyncio.BoundedSemaphore(4)
    release = asyncio.Event()
    provider_active = 0
    provider_peak = 0
    provider_started: list[tuple[str, float]] = []
    completed: list[str] = []
    now = 0.0

    async def advance_clock(delay: float) -> None:
        nonlocal now
        now += delay
        await asyncio.sleep(0)

    admission = _ResearchPrewarmProviderAdmission(
        min_start_interval_seconds=1.5,
        clock=lambda: now,
        sleeper=advance_clock,
    )
    monkeypatch.setattr(
        research_prewarm_task_module,
        "_get_research_prewarm_provider_admission",
        lambda: admission,
    )

    async def search_provider(query: ResearchQuery):
        nonlocal provider_active, provider_peak
        provider_started.append((query.query, now))
        async with transport_slots:
            provider_active += 1
            provider_peak = max(provider_peak, provider_active)
            await release.wait()
            provider_active -= 1
        completed.append(query.query)
        return []

    first = ResearchPrewarmTask(
        store=ResearchDossierStore(tmp_path / "first.db"),
        search_provider=search_provider,
    )
    second = ResearchPrewarmTask(
        store=ResearchDossierStore(tmp_path / "second.db"),
        search_provider=search_provider,
    )
    queries = [
        ResearchQuery(
            f"provider-query-{index}",
            "corroboration",
            "reputable_secondary",
        )
        for index in range(6)
    ]
    runs = [
        asyncio.create_task(
            (first if index % 2 == 0 else second)._search_provider_with_admission(query)
        )
        for index, query in enumerate(queries)
    ]
    results = []
    try:
        for _ in range(100):
            if len(provider_started) == 6:
                break
            await asyncio.sleep(0)
        assert len(provider_started) == 6
        assert provider_active == 4
        assert provider_peak == 4
        assert sorted(started_at for _, started_at in provider_started) == [
            0.0,
            1.5,
            3.0,
            4.5,
            6.0,
            7.5,
        ]
    finally:
        release.set()
        results = await asyncio.gather(*runs, return_exceptions=True)

    assert len(results) == 6
    assert not [result for result in results if isinstance(result, BaseException)]
    assert len(completed) == 6
    assert len(set(completed)) == 6


@pytest.mark.asyncio
async def test_prewarm_provider_admission_releases_all_slots_on_cancellation(
    monkeypatch,
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    release = asyncio.Event()
    provider_active = 0
    provider_peak = 0
    provider_started: list[str] = []
    spawned: list[asyncio.Task] = []
    admission = _ResearchPrewarmProviderAdmission(min_start_interval_seconds=0.0)
    monkeypatch.setattr(
        research_prewarm_task_module,
        "_get_research_prewarm_provider_admission",
        lambda: admission,
    )

    async def search_provider(query: ResearchQuery):
        nonlocal provider_active, provider_peak
        provider_started.append(query.query)
        provider_active += 1
        provider_peak = max(provider_peak, provider_active)
        try:
            await release.wait()
        finally:
            provider_active -= 1
        return []

    async def research_gate(_news, _market, **kwargs):
        queries = [
            ResearchQuery(
                f"provider-query-{index}",
                "corroboration",
                "reputable_secondary",
            )
            for index in range(8)
        ]
        spawned.extend(
            asyncio.create_task(
                kwargs["search_provider"](query),
                name=query.query,
            )
            for query in queries
        )
        return await asyncio.gather(*spawned)

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        search_provider=search_provider,
    )
    run = asyncio.create_task(
        task.process_market(_market("KXRESEARCH-CANCELLED"))
    )
    while provider_active < 1:
        await asyncio.sleep(0)

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    await asyncio.sleep(0)

    assert 1 <= provider_peak <= len(spawned)
    assert provider_active == 0
    assert provider_started
    assert set(provider_started).issubset(
        {f"provider-query-{index}" for index in range(8)}
    )
    assert all(query_task.done() for query_task in spawned)


@pytest.mark.parametrize("interval", [-1.0, float("inf"), float("nan")])
def test_prewarm_provider_admission_rejects_invalid_intervals(interval):
    with pytest.raises(ValueError, match="finite and non-negative"):
        _ResearchPrewarmProviderAdmission(min_start_interval_seconds=interval)


@pytest.mark.asyncio
async def test_prewarm_provider_admission_is_shared_by_live_loop():
    first = research_prewarm_task_module._get_research_prewarm_provider_admission()
    second = research_prewarm_task_module._get_research_prewarm_provider_admission()

    assert second is first


def test_prewarm_provider_admission_does_not_retain_closed_event_loops(monkeypatch):
    references: list[
        tuple[
            weakref.ReferenceType[asyncio.AbstractEventLoop],
            weakref.ReferenceType[_ResearchPrewarmProviderAdmission],
        ]
    ] = []
    monkeypatch.setattr(
        research_prewarm_task_module,
        "_RESEARCH_PREWARM_PROVIDER_MIN_START_INTERVAL_SECONDS",
        0.0,
    )

    async def contend_for_admission():
        loop = asyncio.get_running_loop()
        admission = (
            research_prewarm_task_module._get_research_prewarm_provider_admission()
        )
        release = asyncio.Event()
        started = asyncio.Event()

        async def provider(query: str):
            if query == "first":
                started.set()
                await release.wait()
            return query

        first = asyncio.create_task(admission.run(provider, "first"))
        await started.wait()
        second = asyncio.create_task(admission.run(provider, "second"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        return weakref.ref(loop), weakref.ref(admission)

    for _ in range(2):
        references.append(asyncio.run(contend_for_admission()))
    gc.collect()

    assert not hasattr(
        research_prewarm_task_module,
        "_RESEARCH_PREWARM_PROVIDER_ADMISSIONS",
    )
    assert all(loop_ref() is None for loop_ref, _ in references)
    assert all(admission_ref() is None for _, admission_ref in references)


@pytest.mark.asyncio
async def test_prewarm_provider_admission_is_fifo_and_spaces_starts_after_errors():
    now = 0.0
    sleeps: list[float] = []
    starts: list[tuple[str, float]] = []

    async def advance_clock(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay
        await asyncio.sleep(0)

    async def provider(query: str):
        starts.append((query, now))
        if query == "query-2":
            raise RuntimeError("isolated provider failure")
        await asyncio.sleep(0)
        return query

    admission = _ResearchPrewarmProviderAdmission(
        min_start_interval_seconds=1.5,
        clock=lambda: now,
        sleeper=advance_clock,
    )

    results = await asyncio.gather(
        *(admission.run(provider, f"query-{index}") for index in range(6)),
        return_exceptions=True,
    )

    assert starts == [
        ("query-0", 0.0),
        ("query-1", 1.5),
        ("query-2", 3.0),
        ("query-3", 4.5),
        ("query-4", 6.0),
        ("query-5", 7.5),
    ]
    assert sleeps == [1.5] * 5
    assert isinstance(results[2], RuntimeError)
    assert [result for result in results if not isinstance(result, BaseException)] == [
        "query-0",
        "query-1",
        "query-3",
        "query-4",
        "query-5",
    ]


@pytest.mark.asyncio
async def test_prewarm_provider_admission_paces_starts_without_serializing_transport():
    now = 0.0
    starts: list[tuple[str, float]] = []
    release = asyncio.Event()
    provider_active = 0
    provider_peak = 0

    async def advance_clock(delay: float) -> None:
        nonlocal now
        now += delay
        await asyncio.sleep(0)

    async def provider(query: str) -> str:
        nonlocal provider_active, provider_peak
        starts.append((query, now))
        provider_active += 1
        provider_peak = max(provider_peak, provider_active)
        try:
            await release.wait()
        finally:
            provider_active -= 1
        return query

    admission = _ResearchPrewarmProviderAdmission(
        min_start_interval_seconds=1.5,
        clock=lambda: now,
        sleeper=advance_clock,
    )
    first = asyncio.create_task(admission.run(provider, "query-0"))
    for _ in range(20):
        if starts:
            break
        await asyncio.sleep(0)
    assert starts == [("query-0", 0.0)]

    second = asyncio.create_task(admission.run(provider, "query-1"))
    try:
        for _ in range(20):
            if len(starts) == 2:
                break
            await asyncio.sleep(0)
        assert starts == [("query-0", 0.0), ("query-1", 1.5)]
        assert provider_peak == 2
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_prewarm_gate_budget_cancels_queued_provider_admissions(
    monkeypatch,
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    provider_started: list[str] = []
    provider_active = 0
    admission = _ResearchPrewarmProviderAdmission(min_start_interval_seconds=1.5)
    monkeypatch.setattr(
        research_prewarm_task_module,
        "_get_research_prewarm_provider_admission",
        lambda: admission,
    )

    async def search_provider(query: ResearchQuery):
        nonlocal provider_active
        provider_started.append(query.query)
        provider_active += 1
        try:
            await asyncio.Event().wait()
        finally:
            provider_active -= 1

    async def research_gate(_news, _market, **kwargs):
        queries = [
            ResearchQuery(
                f"provider-query-{index}",
                "corroboration",
                "reputable_secondary",
            )
            for index in range(6)
        ]
        async with asyncio.timeout(0.01):
            await asyncio.gather(
                *(kwargs["search_provider"](query) for query in queries)
            )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        search_provider=search_provider,
    )

    with pytest.raises(ResearchPrewarmError) as exc_info:
        await task.process_market(_market("KXRESEARCH-BUDGET"))
    await asyncio.sleep(0.02)

    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert provider_started == ["provider-query-0"]
    assert provider_active == 0


@pytest.mark.asyncio
async def test_prewarm_slow_provider_paces_all_queries_and_cleans_up_after_budget(
    monkeypatch,
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    provider_started: list[str] = []
    provider_active = 0
    admission = _ResearchPrewarmProviderAdmission(min_start_interval_seconds=0.015)
    monkeypatch.setattr(
        research_prewarm_task_module,
        "_get_research_prewarm_provider_admission",
        lambda: admission,
    )

    async def search_provider(query: ResearchQuery):
        nonlocal provider_active
        provider_started.append(query.query)
        provider_active += 1
        try:
            await asyncio.sleep(0.05)
        finally:
            provider_active -= 1
        raise TimeoutError("scaled five-second provider timeout")

    async def research_gate(_news, _market, **kwargs):
        queries = [
            ResearchQuery(
                f"provider-query-{index}",
                "corroboration",
                "reputable_secondary",
            )
            for index in range(6)
        ]
        async with asyncio.timeout(0.12):
            await asyncio.gather(
                *(kwargs["search_provider"](query) for query in queries),
                return_exceptions=True,
            )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        search_provider=search_provider,
    )

    with pytest.raises(ResearchPrewarmError) as exc_info:
        await task.process_market(_market("KXRESEARCH-SLOW-PROVIDER"))
    starts_at_budget = tuple(provider_started)
    await asyncio.sleep(0.06)

    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert provider_started == [f"provider-query-{index}" for index in range(6)]
    assert tuple(provider_started) == starts_at_budget
    assert provider_active == 0


@pytest.mark.asyncio
async def test_prewarm_run_once_cools_down_attempted_ticker(
    monkeypatch,
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    now = 10_000.0
    calls: list[str] = []
    emitted: list[ResearchPrewarmResult] = []

    monkeypatch.setattr(
        research_prewarm_task_module.time,
        "monotonic",
        lambda: now,
    )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="no_research_hits",
        )

    async def result_sink(result):
        emitted.append(result)

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        result_sink=result_sink,
        target_cooldown_seconds=1800.0,
    )

    first = await task.run_once([_market("KXRESEARCH-COOLDOWN")])
    second = await task.run_once([_market("KXRESEARCH-COOLDOWN")])

    assert [result.market_ticker for result in first] == ["KXRESEARCH-COOLDOWN"]
    assert second == []
    assert calls == ["KXRESEARCH-COOLDOWN"]
    assert [result.market_ticker for result in emitted] == ["KXRESEARCH-COOLDOWN"]


@pytest.mark.asyncio
async def test_prewarm_revisits_nonterminal_after_cooldown(monkeypatch, tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    now = 10_000.0
    calls: list[str] = []

    monkeypatch.setattr(
        research_prewarm_task_module.time,
        "monotonic",
        lambda: now,
    )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="missing_counter_evidence",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=60.0,
    )

    first = await task.run_once([_market("KXRESEARCH-RETRY")])
    second = await task.run_once([_market("KXRESEARCH-RETRY")])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2000-01-01T00:00:00.000Z',
                cooldown_until_ts = '2000-01-01T00:00:00.000Z'
            WHERE market_ticker = 'KXRESEARCH-RETRY'
            """
        )
    now += 61.0
    third = await task.run_once([_market("KXRESEARCH-RETRY")])

    assert [result.market_ticker for result in first] == ["KXRESEARCH-RETRY"]
    assert second == []
    assert [result.market_ticker for result in third] == ["KXRESEARCH-RETRY"]
    assert calls == ["KXRESEARCH-RETRY", "KXRESEARCH-RETRY"]


@pytest.mark.asyncio
async def test_prewarm_run_once_prioritizes_due_persisted_tasks(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    await store.record_research_run(
        "KXRESEARCH-DUE",
        "run-due",
        trigger_headline="Needs more work",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Evidence needs countercase.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="missing_counter_evidence",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T10:00:00.000Z',
                cooldown_until_ts = '2026-06-30T10:05:00.000Z'
            WHERE market_ticker = 'KXRESEARCH-DUE'
            """
        )
    calls: list[str] = []

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="missing_counter_evidence",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        max_concurrency=1,
    )

    results = await task.run_once(
        [
            _market("KXRESEARCH-FRESH"),
            _market("KXRESEARCH-DUE"),
        ]
    )

    assert [result.market_ticker for result in results] == [
        "KXRESEARCH-DUE",
        "KXRESEARCH-FRESH",
    ]
    assert calls == ["KXRESEARCH-DUE", "KXRESEARCH-FRESH"]


@pytest.mark.asyncio
async def test_prewarm_process_market_respects_persisted_task_cooldown(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []

    await store.record_research_run(
        "KXRESEARCH-PERSISTED-COOLDOWN",
        "run-cooldown",
        trigger_headline="Needs more work",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Evidence needs countercase.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="missing_counter_evidence",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
    )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.DECISION_GRADE_CANDIDATE,
            attempted=True,
            queries=[],
            evidence=[],
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market("KXRESEARCH-PERSISTED-COOLDOWN"))

    assert result.status == "skipped_cooldown"
    assert result.attempted is False
    assert result.skip_reason == "research_task_cooldown"
    assert calls == []


@pytest.mark.asyncio
async def test_prewarm_process_market_respects_configured_cooldown_after_store_backoff(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    calls: list[str] = []

    await store.record_research_run(
        "KXRESEARCH-TARGET-COOLDOWN",
        "run-target-cooldown",
        trigger_headline="Needs more work",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Evidence needs countercase.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="missing_counter_evidence",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET cooldown_until_ts = '2000-01-01T00:00:00.000Z'
            WHERE market_ticker = 'KXRESEARCH-TARGET-COOLDOWN'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.DECISION_GRADE_CANDIDATE,
            attempted=True,
            queries=[],
            evidence=[],
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=1800.0,
    )

    result = await task.process_market(_market("KXRESEARCH-TARGET-COOLDOWN"))

    assert result.status == "skipped_cooldown"
    assert result.attempted is False
    assert result.skip_reason == "research_task_cooldown"
    assert calls == []


@pytest.mark.asyncio
async def test_prewarm_researches_repaired_zero_backoff_task_inside_configured_cooldown(
    tmp_path,
):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    calls: list[str] = []

    await store.record_research_run(
        "KXRESEARCH-REPAIRED-COUNTER",
        "run-repaired-counter",
        trigger_headline="Needs counter evidence",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Candidate was repaired and must be retried immediately.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="missing_counter_evidence",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET cooldown_until_ts = NULL,
                backoff_seconds = 0,
                state = 'needs_counter_evidence'
            WHERE market_ticker = 'KXRESEARCH-REPAIRED-COUNTER'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.UNTRADEABLE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="no_edge",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=1800.0,
    )

    result = await task.process_market(_market("KXRESEARCH-REPAIRED-COUNTER"))

    assert result.status == ResearchStatus.UNTRADEABLE.value
    assert result.attempted is True
    assert result.skip_reason == "no_edge"
    assert calls == ["KXRESEARCH-REPAIRED-COUNTER"]


@pytest.mark.asyncio
async def test_prewarm_retries_repeated_ambiguous_task_after_cooldown(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []

    for attempt in range(3):
        await store.record_research_run(
            "KXRESEARCH-TERMINAL",
            f"run-terminal-{attempt}",
            trigger_headline="Ambiguous",
            trigger_source="research_prewarm",
            attempted=True,
            summary="Evidence remains ambiguous.",
            verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
            skip_reason="ambiguous_direction",
            decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2000-01-01T00:00:00.000Z',
                cooldown_until_ts = '2000-01-01T00:00:00.000Z'
            WHERE market_ticker = 'KXRESEARCH-TERMINAL'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return _decision_grade_verdict()

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market("KXRESEARCH-TERMINAL"))

    assert result.status == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert result.attempted is True
    assert calls == ["KXRESEARCH-TERMINAL"]


@pytest.mark.asyncio
async def test_prewarm_retries_terminal_timeout_task(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []
    await store.record_research_run(
        "KXTIMEOUT-26JUL01",
        "run-timeout-terminal",
        trigger_headline="Timeout",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Timeout is not a trade decision.",
        verdict_status=ResearchStatus.UNTRADEABLE.value,
        skip_reason="research_timeout_exhausted",
        decision_grade_status=ResearchStatus.UNTRADEABLE.value,
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET terminal_reason = 'research_timeout_exhausted',
                last_skip_reason = 'research_timeout',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXTIMEOUT-26JUL01'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="missing_counter_evidence",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market("KXTIMEOUT-26JUL01"))

    assert calls == ["KXTIMEOUT-26JUL01"]
    assert result.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE.value
    assert result.attempted is True
    assert result.skip_reason == "missing_counter_evidence"


@pytest.mark.asyncio
async def test_prewarm_retries_terminal_official_pending_task(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []
    await store.record_research_run(
        "KXOFFICIALPENDING-26JUL01",
        "run-official-terminal",
        trigger_headline="Official data pending",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Official source has not published yet.",
        verdict_status=ResearchStatus.UNTRADEABLE.value,
        skip_reason="official_data_pending",
        decision_grade_status=ResearchStatus.UNTRADEABLE.value,
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET state = 'untradeable',
                terminal_reason = 'official_data_pending',
                last_skip_reason = 'official_data_pending',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXOFFICIALPENDING-26JUL01'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="official_data_pending",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market("KXOFFICIALPENDING-26JUL01"))

    assert calls == ["KXOFFICIALPENDING-26JUL01"]
    assert result.status == ResearchStatus.NEEDS_RESEARCH.value
    assert result.attempted is True
    assert result.skip_reason == "official_data_pending"


@pytest.mark.asyncio
async def test_prewarm_honors_existing_official_pending_cooldown(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []
    await store.record_research_run(
        "KXOFFICIAL-26JUL01",
        "run-official-pending",
        trigger_headline="Official data pending",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Official source has not published yet.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="official_data_pending",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
    )
    updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    overlong_until = datetime.now(timezone.utc) + timedelta(hours=5)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = ?,
                cooldown_until_ts = ?,
                backoff_seconds = 21600
            WHERE market_ticker = 'KXOFFICIAL-26JUL01'
            """,
            (
                updated_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                overlong_until.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            ),
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="official_data_pending",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market("KXOFFICIAL-26JUL01"))

    assert calls == []
    assert result.status == "skipped_cooldown"
    assert result.attempted is False
    assert result.skip_reason == "research_task_cooldown"


@pytest.mark.asyncio
async def test_targeted_refresh_bypasses_nonterminal_persisted_cooldown(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    ticker = "KXOFFICIAL-26JUL01"
    await store.record_research_run(
        ticker,
        "run-official-pending",
        trigger_headline="Official data pending",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Official source has not published yet.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="official_data_pending",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
    )
    calls = []

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return ResearchVerdict(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            skip_reason="official_data_pending",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(
        _market(ticker),
        bypass_persisted_cooldown=True,
    )

    assert calls == [ticker]
    assert result.attempted is True


@pytest.mark.asyncio
async def test_prewarm_skips_terminal_decision_grade_candidate(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []

    await store.record_research_run(
        "KXRESEARCH-DECISION",
        "run-decision-old",
        trigger_headline="Decision-grade candidate",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Candidate was decision-grade but cache may expire.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        skip_reason=None,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official",
                source_url="https://agency.gov/result",
                title="Official result",
                snippet="Official source supports YES.",
                claim_type="settlement",
                supports_direction="yes",
                supports_confidence=0.9,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wire",
                source_url="https://wire.example.com",
                title="Wire corroboration",
                snippet="Wire independently supports YES.",
                claim_type="settlement",
                supports_direction="yes",
                supports_confidence=0.85,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="AP",
                source_url="https://apnews.com/counter",
                title="Countercase",
                snippet="Counter evidence supports NO.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.35,
            ),
        ],
        queries=[
            ResearchQuery(
                query="official result",
                query_intent="official_resolution",
                source_class="resolution_source",
            ),
            ResearchQuery(
                query="countercase",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            ),
        ],
    )
    with sqlite3.connect(tmp_path / "research_dossier.db") as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T10:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXRESEARCH-DECISION'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="official_data_pending",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=60.0,
    )

    result = await task.process_market(_market("KXRESEARCH-DECISION"))

    assert result.status == "skipped_terminal"
    assert result.attempted is False
    assert result.skip_reason == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert calls == []


@pytest.mark.asyncio
async def test_prewarm_emits_terminal_decision_grade_to_market_result_sink(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []
    emitted: list[tuple[str, str, str | None]] = []

    await store.record_research_run(
        "KXRESEARCH-DECISION",
        "run-decision-old",
        trigger_headline="Decision-grade candidate",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Candidate was decision-grade but cache may expire.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        skip_reason=None,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official",
                source_url="https://agency.gov/result",
                title="Official result",
                snippet="Official source supports YES.",
                claim_type="settlement",
                supports_direction="yes",
                supports_confidence=0.9,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wire",
                source_url="https://wire.example.com",
                title="Wire corroboration",
                snippet="Wire independently supports YES.",
                claim_type="settlement",
                supports_direction="yes",
                supports_confidence=0.85,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="AP",
                source_url="https://apnews.com/counter",
                title="Countercase",
                snippet="Counter evidence supports NO.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.35,
            ),
        ],
        queries=[
            ResearchQuery(
                query="official result",
                query_intent="official_resolution",
                source_class="resolution_source",
            ),
            ResearchQuery(
                query="countercase",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            ),
        ],
    )
    with sqlite3.connect(tmp_path / "research_dossier.db") as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2026-06-30T10:00:00.000Z',
                cooldown_until_ts = NULL
            WHERE market_ticker = 'KXRESEARCH-DECISION'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="official_data_pending",
        )

    async def market_result_sink(
        result: ResearchPrewarmResult,
        market,
    ) -> None:
        emitted.append((market.ticker, result.status, result.skip_reason))

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=60.0,
        market_result_sink=market_result_sink,
    )

    results = await task.run_once([_market("KXRESEARCH-DECISION")])

    assert [(result.market_ticker, result.status) for result in results] == [
        ("KXRESEARCH-DECISION", "skipped_terminal")
    ]
    assert results[0].skip_reason == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert emitted == [
        (
            "KXRESEARCH-DECISION",
            "skipped_terminal",
            ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        )
    ]
    assert calls == []


@pytest.mark.asyncio
async def test_prewarm_researches_decision_grade_candidate_with_same_side_counter(
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []

    await store.record_research_run(
        "KXRESEARCH-BAD-DECISION",
        "run-decision-bad",
        trigger_headline="Decision-grade candidate",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Candidate has same-side counter evidence.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        skip_reason=None,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official",
                source_url="https://official.example.com",
                title="Official result",
                snippet="Official source supports YES.",
                claim_type="settlement",
                supports_direction="yes",
                supports_confidence=0.9,
            ),
            ResearchEvidence(
                source_class="official_primary",
                source_name="Official",
                source_url="https://official.example.com/counter",
                title="Same-side counter",
                snippet="Counter search also supports YES.",
                claim_type="disconfirming",
                supports_direction="yes",
                supports_confidence=0.95,
                metric_name="nws_daily_high_temp_f",
            ),
        ],
    )
    with sqlite3.connect(tmp_path / "research_dossier.db") as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET state = 'decision_grade_candidate',
                cooldown_until_ts = NULL,
                backoff_seconds = 0,
                terminal_reason = NULL
            WHERE market_ticker = 'KXRESEARCH-BAD-DECISION'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="missing_counter_evidence",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=60.0,
    )

    result = await task.process_market(_market("KXRESEARCH-BAD-DECISION"))

    assert result.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE.value
    assert result.attempted is True
    assert calls == ["KXRESEARCH-BAD-DECISION"]


@pytest.mark.asyncio
async def test_prewarm_terminalizes_closed_market_even_inside_cooldown(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    await store.record_research_run(
        "KXRESEARCH-CLOSED",
        "run-needs-counter",
        trigger_headline="Needs more research",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Research needs counter evidence.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="missing_counter_evidence",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
    )
    calls: list[str] = []

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="missing_counter_evidence",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=1800.0,
    )

    results = await task.run_once([_market("KXRESEARCH-CLOSED", status="closed")])

    assert len(results) == 1
    assert results[0].status == "skipped_closed"
    assert results[0].skip_reason == "market_closed"
    assert results[0].research_persisted is True
    assert calls == []
    snapshot = await store.get_research_task_snapshot("KXRESEARCH-CLOSED")
    assert snapshot is not None
    assert snapshot.state == "untradeable"
    assert snapshot.terminal_reason == "market_closed"


@pytest.mark.asyncio
async def test_prewarm_researches_decision_grade_candidate_without_counter_query(
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []

    await store.record_research_run(
        "KXRESEARCH-NO-COUNTER-QUERY",
        "run-decision-no-counter-query",
        trigger_headline="Decision-grade candidate",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Candidate has counter evidence but no disconfirming query record.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        skip_reason=None,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        queries=[
            ResearchQuery(
                query="official result",
                query_intent="official_resolution",
                source_class="resolution_source",
            ),
        ],
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official",
                source_url="https://official.example.com/result",
                title="Official result",
                snippet="Official source supports YES.",
                claim_type="settlement",
                supports_direction="yes",
                supports_confidence=0.9,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Independent Wire",
                source_url="https://wire.example.com/counter",
                title="Countercase",
                snippet="Independent counter evidence supports NO.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.75,
            ),
        ],
    )
    with sqlite3.connect(tmp_path / "research_dossier.db") as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET state = 'decision_grade_candidate',
                cooldown_until_ts = NULL,
                backoff_seconds = 0,
                terminal_reason = NULL
            WHERE market_ticker = 'KXRESEARCH-NO-COUNTER-QUERY'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="missing_counter_query",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=60.0,
    )

    result = await task.process_market(_market("KXRESEARCH-NO-COUNTER-QUERY"))

    assert result.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE.value
    assert result.attempted is True
    assert calls == ["KXRESEARCH-NO-COUNTER-QUERY"]
    with sqlite3.connect(tmp_path / "research_dossier.db") as conn:
        dossier = conn.execute(
            """
            SELECT last_verdict_status, last_decision_grade_status, last_skip_reason
            FROM research_dossiers
            WHERE market_ticker = 'KXRESEARCH-NO-COUNTER-QUERY'
            """
        ).fetchone()
    assert dossier == (
        ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        "missing_counter_query",
    )


@pytest.mark.asyncio
async def test_prewarm_researches_decision_grade_candidate_without_independent_sources(
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []

    await store.record_research_run(
        "KXRESEARCH-SAME-SOURCE",
        "run-decision-same-source",
        trigger_headline="Decision-grade candidate",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Candidate has counter evidence copied from the same source path.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        skip_reason=None,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Wire",
                source_url="https://wire.example.com/story",
                title="Official-looking result",
                snippet="Source supports YES.",
                claim_type="settlement",
                supports_direction="yes",
                supports_confidence=0.9,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wire",
                source_url="https://wire.example.com/story",
                title="Countercase rewrite",
                snippet="Same source also carries the NO countercase.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.95,
            ),
        ],
    )
    with sqlite3.connect(tmp_path / "research_dossier.db") as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET state = 'decision_grade_candidate',
                cooldown_until_ts = NULL,
                backoff_seconds = 0,
                terminal_reason = NULL
            WHERE market_ticker = 'KXRESEARCH-SAME-SOURCE'
            """
        )

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return SimpleNamespace(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="no_reliable_source_path",
        )

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        target_cooldown_seconds=60.0,
    )

    result = await task.process_market(_market("KXRESEARCH-SAME-SOURCE"))

    assert result.status == ResearchStatus.NEEDS_RESEARCH.value
    assert result.attempted is True
    assert calls == ["KXRESEARCH-SAME-SOURCE"]


@pytest.mark.asyncio
async def test_prewarm_cooldown_and_terminal_skips_do_not_emit_spend_log(monkeypatch):
    emitted: list[tuple[object, dict]] = []

    async def fake_write_trade_log_async(writer, **fields):
        emitted.append((writer, fields))

    monkeypatch.setattr(
        research_prewarm_task_module,
        "write_trade_log_async",
        fake_write_trade_log_async,
    )

    await _write_research_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-PERSISTED-COOLDOWN",
            status="skipped_cooldown",
            attempted=False,
            skip_reason="research_task_cooldown",
        )
    )
    await _write_research_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-TERMINAL",
            status="skipped_terminal",
            attempted=False,
            skip_reason="contradictory_evidence_unresolved",
        )
    )

    assert emitted == []


@pytest.mark.asyncio
async def test_prewarm_result_writer_emits_timeout_attribution(monkeypatch):
    emitted: list[tuple[object, dict]] = []

    async def fake_write_trade_log_async(writer, **fields):
        emitted.append((writer, fields))

    monkeypatch.setattr(
        research_prewarm_task_module,
        "write_trade_log_async",
        fake_write_trade_log_async,
    )

    await _write_research_prewarm_result(
        ResearchPrewarmResult(
            market_ticker="KXRESEARCH-TIMEOUT",
            status="continue_researching",
            attempted=True,
            research_timeout_stage="provider_fanout",
            research_provider_error_count=3,
            research_provider_error_attributions=(
                "timeout",
                "generic_search_unavailable",
            ),
            research_generic_search_circuit_state="open",
            research_generic_search_failure_classes=(
                "TimeoutError",
                "TimeoutError",
            ),
            research_generic_search_attempt_delta=2,
            research_generic_search_blocked_call_delta=1,
        )
    )

    assert len(emitted) == 1
    _, fields = emitted[0]
    assert fields["research_timeout_stage"] == "provider_fanout"
    assert fields["research_provider_error_count"] == 3
    assert fields["research_provider_error_attributions"] == [
        "timeout",
        "generic_search_unavailable",
    ]
    assert fields["research_generic_search_circuit_state"] == "open"
    assert fields["research_generic_search_failure_classes"] == [
        "TimeoutError",
        "TimeoutError",
    ]
    assert fields["research_generic_search_attempt_delta"] == 2
    assert fields["research_generic_search_blocked_call_delta"] == 1


@pytest.mark.asyncio
async def test_prewarm_run_once_emits_structured_result_events(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    emitted = []

    async def research_gate(_news, market, **_kwargs):
        if market.ticker == "KXRESEARCH-BAD":
            raise RuntimeError("research failed")
        return SimpleNamespace(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            queries=[object(), object()],
            evidence=[object()],
            skip_reason="no_research_hits",
            research_run_id="rr-test-good",
            log_fields=lambda: {
                "research_contract_fingerprint": "contract-test-good",
            },
            research_persisted=True,
            research_persistence_error=None,
            research_direct_fetch_failures=("resolution_source:https://bad.example:boom",),
            research_timeout_stage="provider_fanout",
            research_provider_error_count=3,
            research_provider_error_attributions=("timeout",),
            research_generic_search_circuit_state="open",
            research_generic_search_failure_classes=("TimeoutError", "HTTPError:403"),
            research_generic_search_attempt_delta=4,
            research_generic_search_blocked_call_delta=2,
        )

    async def result_sink(result):
        emitted.append(result)

    task = ResearchPrewarmTask(
        store=store,
        research_gate=research_gate,
        result_sink=result_sink,
    )

    results = await task.run_once(
        [
            _market("KXRESEARCH-BAD"),
            _market("KXRESEARCH-GOOD"),
        ]
    )

    assert [result.market_ticker for result in results] == ["KXRESEARCH-GOOD"]
    assert [(result.market_ticker, result.status) for result in emitted] == [
        ("KXRESEARCH-BAD", "error"),
        ("KXRESEARCH-GOOD", ResearchStatus.CONTINUE_RESEARCHING.value),
    ]
    assert emitted[0].error == "failed research prewarm for KXRESEARCH-BAD"
    assert emitted[1].query_count == 2
    assert emitted[1].evidence_count == 1
    assert emitted[1].research_run_id == "rr-test-good"
    assert emitted[1].research_contract_fingerprint == "contract-test-good"
    assert emitted[1].research_persisted is True
    assert len(emitted[1].research_direct_fetch_failures) == 1
    assert emitted[1].research_timeout_stage == "provider_fanout"
    assert emitted[1].research_provider_error_count == 3
    assert emitted[1].research_provider_error_attributions == ("timeout",)
    assert emitted[1].research_generic_search_circuit_state == "open"
    assert emitted[1].research_generic_search_failure_classes == (
        "TimeoutError",
        "HTTPError:403",
    )
    assert emitted[1].research_generic_search_attempt_delta == 4
    assert emitted[1].research_generic_search_blocked_call_delta == 2


@pytest.mark.asyncio
async def test_prewarm_skips_closed_markets_without_research_call(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise AssertionError("closed markets must not be researched")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market(status="closed"))

    assert result.status == "skipped_closed"
    assert result.attempted is False
    snapshot = await store.get_research_task_snapshot("KXRESEARCH-26DEC31")
    assert snapshot is not None
    assert snapshot.state == ResearchStatus.UNTRADEABLE.value
    assert snapshot.terminal_reason == "market_closed"


@pytest.mark.asyncio
async def test_prewarm_terminalizes_active_market_after_close_time(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise AssertionError("expired markets must not be researched")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)
    market = _market()
    market.close_time = "2026-07-10T23:59:59Z"

    result = await task.process_market(market)

    assert result.status == "skipped_closed"
    assert result.skip_reason == "market_expired"
    snapshot = await store.get_research_task_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.state == ResearchStatus.UNTRADEABLE.value
    assert snapshot.terminal_reason == "market_expired"
    dossier = await store.get_dossier_snapshot(market.ticker)
    assert dossier is not None
    assert dossier.market_status == "open"
    assert dossier.market_close_time == "2026-07-10T23:59:59Z"


@pytest.mark.asyncio
async def test_prewarm_queues_active_market_without_close_time(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise AssertionError("markets with unknown close time must not be researched")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)
    market = _market()
    market.close_time = None

    result = await task.process_market(market)

    assert result.status == "needs_research"
    assert result.skip_reason == "market_close_time_missing"
    snapshot = await store.get_research_task_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.state == ResearchStatus.NEEDS_RESEARCH.value
    assert snapshot.last_skip_reason == "market_close_time_missing"
    dossier = await store.get_dossier_snapshot(market.ticker)
    assert dossier is not None
    assert dossier.market_status == "open"
    assert dossier.market_close_time is None


@pytest.mark.asyncio
async def test_prewarm_terminalizes_finalized_market_without_research_call(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise AssertionError("finalized markets must not be researched")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(
        _market("KXNASDAQ100-26JUN30H1600-T30499.9900", status="finalized")
    )

    assert result.status == "skipped_closed"
    assert result.attempted is False
    assert result.skip_reason == "market_closed"
    snapshot = await store.get_research_task_snapshot(
        "KXNASDAQ100-26JUN30H1600-T30499.9900"
    )
    assert snapshot is not None
    assert snapshot.state == ResearchStatus.UNTRADEABLE.value
    assert snapshot.terminal_reason == "market_closed"


@pytest.mark.asyncio
async def test_prewarm_keeps_unresearchable_open_markets_queued(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise AssertionError("markets without a source path must not be researched")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(
        SimpleNamespace(
            ticker="KXMVESPORTSMULTIGAMEEXTENDED-S2026",
            title="yes Pittsburgh,yes Texas,yes Tampa Bay,yes Detroit",
            rules_primary="",
            rules_secondary="",
            settlement_sources=(),
            contract_terms_url="",
            status="active",
            close_time="2099-12-31T23:59:59Z",
            yes_ask_cents=60,
            no_ask_cents=40,
        )
    )

    assert result.status == ResearchStatus.NEEDS_RESEARCH.value
    assert result.attempted is False
    assert result.skip_reason == "no_reliable_source_path"
    snapshot = await store.get_research_task_snapshot(
        "KXMVESPORTSMULTIGAMEEXTENDED-S2026"
    )
    assert snapshot is not None
    assert snapshot.state == ResearchStatus.NEEDS_RESEARCH.value
    assert snapshot.terminal_reason is None
    assert snapshot.cooldown_until_ts is not None
    assert snapshot.backoff_seconds > 0


@pytest.mark.asyncio
async def test_prewarm_researches_known_direct_source_market_without_hydrated_rules(
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls: list[str] = []

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return ResearchVerdict(
            status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="counter_evidence_required",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(
        SimpleNamespace(
            ticker="KXHIGHNY-26JUL02-B99.5",
            title="Will the high temperature in NYC be below 99.5F on July 2?",
            rules_primary="",
            rules_secondary="",
            settlement_sources=(),
            contract_terms_url="",
            status="active",
            close_time="2099-12-31T23:59:59Z",
            yes_ask_cents=60,
            no_ask_cents=40,
        )
    )

    assert calls == ["KXHIGHNY-26JUL02-B99.5"]
    assert result.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE.value
    assert result.attempted is True
    assert result.skip_reason == "counter_evidence_required"


@pytest.mark.asyncio
async def test_prewarm_terminalizes_repeated_unresearchable_open_market(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise AssertionError("markets without a source path must not be researched")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)
    market = SimpleNamespace(
        ticker="KXMVESPORTSMULTIGAMEEXTENDED-S2026",
        title="yes Pittsburgh,yes Texas,yes Tampa Bay,yes Detroit",
        rules_primary="",
        rules_secondary="",
        settlement_sources=(),
        contract_terms_url="",
        status="active",
        close_time="2099-12-31T23:59:59Z",
        yes_ask_cents=60,
        no_ask_cents=40,
    )

    first = await task.process_market(market)
    second = await task.process_market(market)

    assert first.status == ResearchStatus.NEEDS_RESEARCH.value
    assert second.status == ResearchStatus.UNTRADEABLE.value
    assert second.skip_reason == "no_reliable_source_path"
    snapshot = await store.get_research_task_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.state == ResearchStatus.UNTRADEABLE.value
    assert snapshot.terminal_reason == "no_reliable_source_path"


@pytest.mark.asyncio
async def test_prewarm_revives_terminal_no_source_task_when_source_path_appears(
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def blocked_research_gate(*_args, **_kwargs):
        raise AssertionError("markets without a source path must not be researched")

    terminalizing_task = ResearchPrewarmTask(
        store=store,
        research_gate=blocked_research_gate,
    )
    market = SimpleNamespace(
        ticker="KXRESEARCH-SOURCE-REVIVED",
        title="Will an unspecified event happen?",
        rules_primary="",
        rules_secondary="",
        settlement_sources=(),
        contract_terms_url="",
        status="active",
        close_time="2099-12-31T23:59:59Z",
        yes_ask_cents=60,
        no_ask_cents=40,
    )
    await terminalizing_task.process_market(market)
    await terminalizing_task.process_market(market)

    snapshot = await store.get_research_task_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.state == ResearchStatus.UNTRADEABLE.value
    assert snapshot.terminal_reason == "no_reliable_source_path"

    calls: list[str] = []

    async def research_gate(_news, market, **_kwargs):
        calls.append(market.ticker)
        return ResearchVerdict(
            status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
            attempted=True,
            queries=[],
            evidence=[],
            skip_reason="counter_evidence_required",
        )

    revived_task = ResearchPrewarmTask(store=store, research_gate=research_gate)
    result = await revived_task.process_market(
        SimpleNamespace(
            ticker=market.ticker,
            title="Will the high temperature in NYC be below 99.5F on July 2?",
            rules_primary="",
            rules_secondary="",
            settlement_sources=(),
            contract_terms_url="",
            status="active",
            close_time="2099-12-31T23:59:59Z",
            yes_ask_cents=60,
            no_ask_cents=40,
        )
    )

    assert calls == [market.ticker]
    assert result.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE.value
    assert result.attempted is True
    assert result.skip_reason == "counter_evidence_required"


@pytest.mark.asyncio
async def test_prewarm_processes_active_kalshi_response_markets(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    calls = []

    async def research_gate(news, market, **kwargs):
        calls.append((news, market, kwargs))
        return ResearchVerdict(
            status=ResearchStatus.CONTINUE_RESEARCHING,
            attempted=True,
            evidence=[
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Wire",
                    source_url="https://wire.example.com/context",
                    title="Wire context",
                    snippet="More context is needed.",
                    claim_type="corroboration",
                )
            ],
            skip_reason="insufficient_corroboration",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market(status="active"))

    assert result.status == ResearchStatus.CONTINUE_RESEARCHING.value
    assert result.attempted is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_prewarm_marks_task_researching_before_gate_runs(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    observed_states: list[str | None] = []

    async def research_gate(_news, market, **_kwargs):
        snapshot = await store.get_research_task_snapshot(market.ticker)
        observed_states.append(snapshot.state if snapshot is not None else None)
        return ResearchVerdict(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            skip_reason="no_research_hits",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market("KXRESEARCH-STATE"))
    snapshot = await store.get_research_task_snapshot("KXRESEARCH-STATE")

    assert observed_states == [ResearchStatus.RESEARCHING.value]
    assert result.status == ResearchStatus.NEEDS_RESEARCH.value
    assert snapshot is not None
    assert snapshot.state == ResearchStatus.NEEDS_RESEARCH.value


@pytest.mark.asyncio
async def test_prewarm_process_market_wraps_failures(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def research_gate(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    with pytest.raises(ResearchPrewarmError, match="KXRESEARCH-26DEC31"):
        await task.process_market(_market())


@pytest.mark.asyncio
async def test_prewarm_fallback_persistence_uses_stored_validated_status(tmp_path):
    store = ResearchDossierStore(tmp_path / "research.db")
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Single Wire",
            source_url="https://same.example/support",
            title="Resolution report",
            snippet="A specific report supports YES.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint="fingerprint-1",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Single Wire",
            source_url="https://same.example/counter",
            title="Countercase",
            snippet="A specific but weak countercase was found.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.2,
            retrieved_at=retrieved_at,
            contract_fingerprint="fingerprint-1",
        ),
    ]
    queries = [
        ResearchQuery("official result", "official_resolution", "resolution_source"),
        ResearchQuery("countercase", "disconfirming", "reputable_secondary"),
    ]

    async def research_gate(*_args, **_kwargs):
        return ResearchVerdict(
            status=ResearchStatus.DECISION_GRADE_CANDIDATE,
            attempted=True,
            queries=queries,
            evidence=evidence,
            summary="Specific proof appears to clear edge.",
            force_side="yes",
            estimated_probability=0.8,
            confidence=0.8,
            market_price=0.5,
            estimated_edge=0.29,
            decision_grade_reasons=("price_present", "counter_evidence_present"),
            research_persisted=False,
            research_persistence_error="initial persistence failed",
            research_contract_fingerprint="fingerprint-1",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    result = await task.process_market(_market("KXSAME-SOURCE"))
    snapshot = await store.get_dossier_snapshot("KXSAME-SOURCE")
    with sqlite3.connect(tmp_path / "research.db") as conn:
        stored_reasons = conn.execute(
            "SELECT decision_grade_reasons_json FROM research_runs"
        ).fetchone()[0]

    assert result.status == ResearchStatus.NEEDS_RESEARCH.value
    assert result.skip_reason == "no_reliable_source_path"
    assert result.research_persisted is True
    assert result.research_run_id
    assert result.research_contract_fingerprint == "fingerprint-1"
    assert result.research_persistence_error == "initial persistence failed"
    assert stored_reasons == '["price_present", "counter_evidence_present"]'
    assert snapshot is not None
    assert snapshot.last_research_run_id == result.research_run_id
    assert snapshot.last_verdict_status == ResearchStatus.NEEDS_RESEARCH.value


def _prewarm_persisted_candidate() -> SimpleNamespace:
    return SimpleNamespace(
        status=ResearchStatus.DECISION_GRADE_CANDIDATE,
        attempted=True,
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="Official source",
                source_url="https://agency.gov/result",
                title="Official result",
                snippet="Official evidence supports YES.",
                claim_type="resolution",
                contract_fingerprint="fingerprint-new",
            )
        ],
        force_side="yes",
        decision_grade_reasons=("price_present", "counter_evidence_present"),
        research_run_id="run-new",
        research_persisted=True,
        research_persistence_error="initial persistence warning",
        research_contract_fingerprint="fingerprint-new",
    )


def _prewarm_persisted_snapshot(**overrides):
    values = {
        "market_ticker": "KXPERSIST",
        "last_research_run_id": "run-new",
        "last_contract_fingerprint": "fingerprint-new",
        "last_verdict_status": ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        "last_skip_reason": None,
        "last_force_side": "yes",
        "last_estimated_probability": 0.7,
        "last_confidence": 0.8,
        "last_market_price": 0.5,
        "last_estimated_edge": 0.2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        _prewarm_persisted_snapshot(
            last_research_run_id="run-older",
            last_contract_fingerprint="fingerprint-older",
        ),
        _prewarm_persisted_snapshot(market_ticker="KXOTHER"),
        _prewarm_persisted_snapshot(last_contract_fingerprint="fingerprint-other"),
    ],
    ids=["missing_snapshot", "retained_older_run", "ticker_mismatch", "fingerprint_mismatch"],
)
async def test_prewarm_candidate_reconciliation_fails_closed_for_unverified_identity(
    snapshot,
):
    class Store:
        async def get_dossier_snapshot(self, _ticker):
            return snapshot

        async def get_research_run_evidence(self, _ticker, _run_id):
            return _prewarm_persisted_candidate().evidence

    verdict = _prewarm_persisted_candidate()
    reconciled = await research_prewarm_task_module._reconcile_prewarm_persisted_status(
        verdict,
        store=Store(),
        ticker="KXPERSIST",
    )

    assert reconciled.status == "needs_research"
    assert reconciled.skip_reason == "persistence_status_unverified"
    assert reconciled.force_side is None
    assert reconciled.decision_grade_reasons == verdict.decision_grade_reasons
    assert reconciled.research_run_id == "run-new"
    assert reconciled.research_contract_fingerprint == "fingerprint-new"
    assert reconciled.research_persisted is True
    assert reconciled.research_persistence_error == "initial persistence warning"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_getter", ["snapshot", "run_evidence"])
async def test_prewarm_candidate_reconciliation_requires_both_persistence_getters(
    missing_getter,
):
    async def get_dossier_snapshot(_ticker):
        return _prewarm_persisted_snapshot()

    async def get_research_run_evidence(_ticker, _run_id):
        return _prewarm_persisted_candidate().evidence

    store = SimpleNamespace(
        **{
            name: getter
            for name, getter in {
                "get_dossier_snapshot": get_dossier_snapshot,
                "get_research_run_evidence": get_research_run_evidence,
            }.items()
            if missing_getter not in name
        }
    )

    reconciled = await research_prewarm_task_module._reconcile_prewarm_persisted_status(
        _prewarm_persisted_candidate(),
        store=store,
        ticker="KXPERSIST",
    )

    assert reconciled.status == "needs_research"
    assert reconciled.skip_reason == "persistence_status_unverified"


@pytest.mark.asyncio
async def test_prewarm_nondecision_reconciliation_is_not_spuriously_demoted():
    verdict = SimpleNamespace(
        status=ResearchStatus.NEEDS_RESEARCH,
        attempted=True,
        skip_reason="insufficient_corroboration",
        decision_grade_reasons=("audit_reason",),
    )

    reconciled = await research_prewarm_task_module._reconcile_prewarm_persisted_status(
        verdict,
        store=SimpleNamespace(),
        ticker="KXPERSIST",
    )

    assert reconciled is verdict


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage",
    ["dossier_snapshot", "query_intent", "run_evidence"],
)
async def test_countercase_lookup_exceptions_reopen_research(failure_stage):
    class FailingCountercaseStore:
        async def get_dossier_snapshot(self, _ticker):
            if failure_stage == "dossier_snapshot":
                raise RuntimeError("dossier lookup failed")
            return SimpleNamespace(
                last_research_run_id="run-1",
                last_force_side="yes",
            )

        async def has_research_run_query_intent(self, _run_id, _intents):
            if failure_stage == "query_intent":
                raise RuntimeError("query lookup failed")
            return True

        async def get_research_run_evidence(self, _ticker, _run_id):
            if failure_stage == "run_evidence":
                raise RuntimeError("evidence lookup failed")
            return []

    task = ResearchPrewarmTask(
        store=FailingCountercaseStore(),
        research_gate=lambda *_args, **_kwargs: None,
    )

    assert not await task._decision_grade_task_has_countercase("KXFAIL")


@pytest.mark.asyncio
async def test_prewarm_injects_persisted_open_questions_into_retry(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    ticker = "KXGAP-26JUL12"
    question = "Which official source reports the contract-window result?"
    await store.record_research_run(
        ticker,
        "run-gap-seed",
        trigger_headline="",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Missing settlement-aligned evidence.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="missing_resolution_source",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        open_questions=[question],
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET updated_ts = '2000-01-01T00:00:00.000Z',
                cooldown_until_ts = '2000-01-01T00:00:00.000Z'
            WHERE market_ticker = ?
            """,
            (ticker,),
        )
    observed_questions = None

    async def research_gate(news, _market, **_kwargs):
        nonlocal observed_questions
        observed_questions = news.research_open_questions
        return ResearchVerdict(
            status=ResearchStatus.NEEDS_RESEARCH,
            attempted=True,
            skip_reason="missing_resolution_source",
        )

    task = ResearchPrewarmTask(store=store, research_gate=research_gate)

    await task.process_market(_market(ticker))

    assert observed_questions == (question,)
