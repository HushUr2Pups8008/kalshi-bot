# Generic Search Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound repeated RSS/DuckDuckGo availability failures without turning provider outages into no-hit research results or changing any admission, evidence-quality, sizing, or execution rule.

**Architecture:** Put a process-local, generation-owned state machine in a new dependency-free module. Route only the generic RSS-primary/DuckDuckGo-fallback pair through it. Keep structured and official providers outside the circuit. Default to observational `shadow`; require a later operator decision before `enforce`.

**Tech Stack:** Python 3.12, `asyncio`, `urllib`, frozen dataclasses, pytest, Ruff, existing botcheck/config/logging infrastructure.

## Global Constraints

- Approved design: `docs/superpowers/specs/2026-07-12-generic-search-circuit-breaker-design.md`.
- Never convert a double availability failure into `[]`; raise `GenericSearchUnavailable` so the gate retains `research_provider_error` semantics.
- Circuit-eligible failures are timeout, DNS/connection, HTTP 429, and HTTP 5xx only. Eligibility controls circuit mutation, not fallback admission: RSS exceptions still call DDG in every mode. Parser, assertion, programming, and other HTTP 4xx failures never open the circuit; if the fallback also fails, the provider exception still reaches the research gate.
- Only the RSS/DDG generic pair is guarded. Structured and official providers remain callable while the circuit is open.
- Admission thresholds, dossier state, pricing, sizing, executor, and paper/live transport are out of scope.
- Default mode is `shadow`; deployment must not set `enforce` without a separate operator decision based on shadow telemetry.
- Runtime artifacts `data/matcher_token_weights.json`, `logs/backups/`, and `logs/state/` remain unstaged and unchanged.

---

## Task 1: Specify Failure Taxonomy And Sequential Mode Behavior

**Files:**
- Create: `tests/test_generic_search_circuit.py`
- Create: `analysis/generic_search_circuit.py`

- [ ] Write RED parameterized tests for `is_provider_availability_failure()` covering `TimeoutError`, `asyncio.TimeoutError`, `socket.gaierror`, `ConnectionError`, reason-sensitive `urllib.error.URLError`, HTTP 429, HTTP 500/503, HTTP 400/401/403/404, `ValueError`, `AssertionError`, and malformed-parser exceptions. A `URLError` is eligible only when `.reason` is a timeout or network/DNS/connection `OSError`; strings and programming exceptions are ineligible.
- [ ] Write RED tests proving `off`, `shadow`, and closed `enforce` call RSS first, call DDG after every RSS exception, and return DDG success unchanged.
- [ ] Write RED tests proving a non-eligible RSS exception still calls DDG, DDG success is returned, and the circuit remains closed.
- [ ] Write RED tests proving two eligible failures raise `GenericSearchUnavailable` with sanitized failure classes, never provider query text, URLs, or exception payloads. A mixed or double non-eligible failure propagates a provider exception without opening the circuit.
- [ ] Implement these public types exactly:

```python
CircuitMode = Literal["off", "shadow", "enforce"]
CircuitState = Literal["closed", "open", "half_open"]

@dataclass(frozen=True)
class GenericSearchCircuitEvent:
    kind: str
    mode: CircuitMode
    state: CircuitState
    generation: int
    failure_classes: tuple[str, ...]
    cooldown_seconds: float
    remaining_cooldown_seconds: float

@dataclass(frozen=True)
class GenericSearchCircuitSnapshot:
    mode: CircuitMode
    state: CircuitState
    generation: int
    open_until: float
    last_failure_classes: tuple[str, ...]
    total_attempts: int
    double_availability_failures: int
    open_transitions: int
    would_open_transitions: int
    blocked_calls: int
    would_block_calls: int
    probe_successes: int
    probe_failures: int

class GenericSearchUnavailable(RuntimeError):
    pass

class GenericSearchCircuit:
    def __init__(
        self,
        *,
        mode: CircuitMode,
        cooldown_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
        event_sink: Callable[[GenericSearchCircuitEvent], None] | None = None,
    ) -> None: ...

    async def run(
        self,
        primary: Callable[[], Awaitable[T]],
        fallback: Callable[[], Awaitable[T]],
    ) -> T: ...

    def snapshot(self) -> GenericSearchCircuitSnapshot: ...

def is_provider_availability_failure(exc: BaseException) -> bool: ...
```

- [ ] Keep exception classification pure. Extract only stable class/status information for events and `GenericSearchUnavailable`.
- [ ] Run `.venv/bin/python -m pytest tests/test_generic_search_circuit.py -q`; expect all Task 1 tests PASS.
- [ ] Run `.venv/bin/ruff check analysis/generic_search_circuit.py tests/test_generic_search_circuit.py`; expect PASS.
- [ ] Commit only Task 1 paths: `git commit -m "feat: add generic search circuit core"`.

## Task 2: Prove Generation Ownership And Concurrency

**Files:**
- Modify: `tests/test_generic_search_circuit.py`
- Modify: `analysis/generic_search_circuit.py`

- [ ] Add a deterministic monotonic clock and controlled futures to the tests.
- [ ] Write RED tests proving a double eligible failure increments the generation and opens until `clock() + 120.0`.
- [ ] Write RED tests proving `enforce` callers fail fast during cooldown without calling either provider.
- [ ] Write RED tests proving cooldown expiry admits exactly one half-open probe and concurrent followers fail fast.
- [ ] Write RED tests proving a successful generation-owned half-open probe closes the circuit, while a failed probe opens a new generation with a fresh cooldown.
- [ ] Write RED half-open terminal tests: a mixed or non-availability owner failure closes the logical availability circuit and propagates the provider exception; owner cancellation restores the same generation to expired-open and releases ownership so the next caller can probe immediately. No terminal path may leave `half_open` orphaned.
- [ ] Write the ordered-completion regression: start a generation-N attempt, open generation N+1 through another attempt, then complete the old attempt successfully; assert the stale success cannot close generation N+1.
- [ ] Write the stale-failure regression: complete a generation-N double failure after another request opened generation N+1; assert it cannot increment generation, extend cooldown, replace last-failure metadata, or emit another warning.
- [ ] Write a second ordered-completion regression proving only the admitted owner for the current half-open generation can close it.
- [ ] Write RED tests proving `shadow` records would-open/would-block/half-open events but continues making RSS/DDG calls. Exactly one logical post-expiry probe owns the shadow generation; logical followers continue provider calls but cannot mutate that generation, so telemetry predicts `enforce` behavior.
- [ ] Write RED tests proving at most one open warning event is emitted per generation and snapshots expose blocked/probe counters.
- [ ] Write RED tests for `urllib.error.URLError` wrapping eligible DNS/timeout/connection reasons and a non-eligible reason. Classify `HTTPError` status before generic `URLError` because `HTTPError` is its subclass.
- [ ] Write RED test proving a throwing event sink cannot change provider results or circuit state.
- [ ] Implement transitions under one `asyncio.Lock`; capture admission generation before releasing the lock; never hold the lock while awaiting network callables or invoking the event sink. Catch sink failures and report them through a non-recursive sanitized logger path.
- [ ] Keep `off` free of state-based blocking and preserve primary/fallback behavior.
- [ ] Run `.venv/bin/python -m pytest tests/test_generic_search_circuit.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check analysis/generic_search_circuit.py tests/test_generic_search_circuit.py`; expect PASS.
- [ ] Commit: `git commit -m "test: prove generic search circuit concurrency"`.

## Task 3: Add Fail-Closed Configuration

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `tests/test_generic_search_circuit.py`

- [ ] Write RED tests proving an absent `GENERIC_SEARCH_CIRCUIT_MODE` yields `shadow`, and exact accepted values are `off`, `shadow`, and `enforce`.
- [ ] Write RED tests proving unknown or case-mutated values fail `BotConfig` construction rather than silently selecting a mode.
- [ ] Add one typed `BotConfig` source; do not add a second import-time module constant:

```python
generic_search_circuit_mode: str = field(
    default_factory=lambda: os.getenv(
        "GENERIC_SEARCH_CIRCUIT_MODE", "shadow"
    ).strip()
)
```

- [ ] Validate membership in `{"off", "shadow", "enforce"}` in `BotConfig.__post_init__` using the existing configuration-error pattern.
- [ ] Document `GENERIC_SEARCH_CIRCUIT_MODE=shadow` in `.env.example`, including one short comment that `enforce` requires separate operator approval.
- [ ] Run `.venv/bin/python -m pytest tests/test_generic_search_circuit.py tests/test_polymarket_config.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check config.py tests/test_generic_search_circuit.py`; expect PASS.
- [ ] Commit: `git commit -m "feat: configure generic search circuit mode"`.

## Task 4: Integrate Only The Generic Provider Pair

**Files:**
- Modify: `analysis/research_gate.py`
- Modify: `tests/test_research_gate.py`
- Modify: `tests/test_generic_search_circuit.py`

- [ ] Write RED integration tests proving RSS success returns normally, eligible RSS failure falls back to DDG, and two eligible failures become `GenericSearchUnavailable`.
- [ ] Write RED gate regression proving that exception remains a provider error and produces `RESEARCH_PROVIDER_ERROR` when settlement evidence is absent; it must not become `no_hits`.
- [ ] Write RED regression proving structured evidence is still returned while the generic circuit is enforced-open.
- [ ] Write RED regression proving parser/programming exceptions do not mutate the circuit state.
- [ ] Add a lazy process singleton and reset helper for tests:

```python
_GENERIC_SEARCH_CIRCUIT: GenericSearchCircuit | None = None

def _get_generic_search_circuit() -> GenericSearchCircuit: ...

async def _run_generic_search(query: ResearchQuery) -> list[ResearchEvidence]:
    circuit = _get_generic_search_circuit()
    return await circuit.run(
        lambda: asyncio.to_thread(_rss_search, query),
        lambda: asyncio.to_thread(_duckduckgo_lite_search, query),
    )
```

- [ ] Add explicit autouse reset fixtures in both `tests/test_generic_search_circuit.py` and `tests/test_research_gate.py`, the two modules allowed to instantiate the singleton. Add a regression proving an open generation from one test cannot leak into the next.

- [ ] Replace only the current RSS `try` / DDG `except` block in `default_search_provider()` with `_run_generic_search(query)`; preserve pending structured evidence concatenation.
- [ ] Connect the event sink to the repository logger. Log stable event kind, mode, state, generation, failure classes, cooldown, and remaining cooldown only. Never log raw query text, provider URL, response body, or exception string.
- [ ] Run focused tests:

```bash
.venv/bin/python -m pytest \
  tests/test_generic_search_circuit.py \
  tests/test_research_gate.py::test_default_search_provider_falls_back_to_duckduckgo_lite \
  tests/test_research_gate.py::test_run_research_gate_reports_provider_exception -q
```

- [ ] Run `.venv/bin/python -m pytest tests/test_research_gate.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check analysis/research_gate.py analysis/generic_search_circuit.py tests/test_research_gate.py`; expect PASS.
- [ ] Commit: `git commit -m "feat: guard generic research search with circuit"`.

## Task 5: Add Operator Visibility Without Runtime Enablement

**Files:**
- Modify: `analysis/generic_search_circuit.py`
- Modify: `analysis/research_gate.py`
- Modify: `scripts/botcheck.py`
- Create: `scripts/generic_search_circuit_report.py`
- Modify: `tests/test_botcheck.py`
- Create: `tests/test_generic_search_circuit_report.py`

- [ ] Write RED botcheck tests for default, `.env`, and process-environment precedence.
- [ ] Display only the effective configured value in `print_research_gate_section()` as `search_cb : shadow (<source>)` using existing source-attribution formatting. Runtime state is derived from sanitized structured circuit log events, not by importing process-local state into botcheck or adding a cross-process state file.
- [ ] Emit one-line app-log records with prefix `[GENERIC_SEARCH_CIRCUIT] ` followed by canonical JSON schema version 1. Fields include `observed_at`, `pid`, event kind, mode/state/generation, sanitized failure classes, cooldown/remaining cooldown, and current counters. Emit double-failure events even when stale and non-transitioning.
- [ ] Implement `scripts/generic_search_circuit_report.py --since-hours 24 --log-dir logs/app` as a read-only parser over active and rotated app logs. It ignores malformed/other-version lines with counted warnings and outputs JSON totals for attempts, double availability failures, open/would-open, blocked/would-block, probes, provider-error events, latest PID/state/generation, and reporting bounds.
- [ ] Test schema serialization, PID/timestamp presence, sanitization, rotated-log aggregation, window filtering, malformed lines, and a zero-event report.
- [ ] Do not expose provider queries, failure payloads, or a control that mutates the mode.
- [ ] Run `.venv/bin/python -m pytest tests/test_botcheck.py tests/test_generic_search_circuit_report.py tests/test_generic_search_circuit.py tests/test_research_gate.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check analysis/generic_search_circuit.py analysis/research_gate.py scripts/botcheck.py scripts/generic_search_circuit_report.py tests/test_botcheck.py tests/test_generic_search_circuit_report.py`; expect PASS.
- [ ] Commit: `git commit -m "chore: expose generic search circuit status"`.

## Task 6: Independent Review And Protected Rollout

**Files:**
- Review all changed circuit paths; do not modify runtime artifacts.

- [ ] Ask an independent reviewer to verify failure taxonomy, generation ownership, stale completion, sanitized once-per-generation telemetry, structured-provider isolation, and unchanged gate semantics.
- [ ] Fix findings with RED regressions first and rerun focused tests.
- [ ] Run `make lint`; expect PASS.
- [ ] Run `scripts/run_tests.sh`; expect PASS with no failures.
- [ ] Inspect `git diff --check` and `git status --short`; confirm only intended code/tests/docs are staged and runtime artifacts remain unstaged.
- [ ] Push the branch and open a protected PR dedicated to the circuit breaker.
- [ ] Wait for required CI. Use the user's approved override only if the repository policy/check state requires it and report the exact overridden check.
- [ ] Merge under the user's recorded authorization, sync local `main`, restart through the existing protected launchd workflow, and run `.venv/bin/python scripts/botcheck.py`.
- [ ] Prove deployed default is `shadow`, the process is healthy, and generic-provider failures still surface as provider errors.
- [ ] Collect at least 24 hours with `.venv/bin/python scripts/generic_search_circuit_report.py --since-hours 24 --log-dir logs/app`. Report total generic attempts, double availability failures, would-open transitions, would-block calls, probe results, provider-error verdicts, and whether any structured provider was affected. Do not set `enforce` in this rollout; if traffic is too sparse to evaluate, remain in `shadow`.
- [ ] Record the later operator decision: keep `shadow`, set `off` for rollback, or explicitly approve `enforce` based on telemetry.
