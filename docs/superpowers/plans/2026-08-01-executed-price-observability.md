# Executed Price Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`
> to implement this plan task-by-task.

## Goal

Add a bounded, schema-versioned `executed_price_provenance` object only to
`SKIPPED` records whose reason is `invalid_executed_price`. Preserve the
current invalid-price rejection and paper-admission behavior exactly.

## Constraints

- The invalid-price guard remains `not bool`, `int`, and `1 <= cents <= 99`.
- The implementation performs no quote retry, REST/order-book request, cache
  refresh, provider call, gate relaxation, price fallback, or new path to the
  paper executor.
- Do not modify `main.py`, `config.py`, database schemas, migrations, service
  definitions, runtime flags, or paper/live controls.
- Reuse `MARKET_CACHE_TTL_SECONDS` only as an observational timestamp boundary;
  it must not become a new admission condition.
- Do not emit the new payload through `signal_meta`, `TradeCandidate`,
  `OPPORTUNITY`, `GATE_SUMMARY`, or paper persistence.

## File Map

| File | Responsibility |
| --- | --- |
| `utils/log_records.py` | Own the frozen provenance type, fixed schema, sanitizer, timestamp parser, and pure constructor. |
| `utils/logger.py` | Add a typed optional `executed_price_provenance` argument to `TradeLogger.log_skipped` and serialize only its fixed record. |
| `tasks/blend_task.py` | Build the object only at the existing invalid-price terminal branch and pass it through `_emit_skipped`. |
| `tests/test_log_records.py` | Lock the pure schema, logger serialization, redaction, enum, timestamp, age, and cardinality contract. |
| `tests/test_blend_task.py` | Prove terminal-path behavior, emitted JSONL shape, source-fault distinctions, and no paper admission. |

## Task 1: Define the Fixed Provenance Contract

- Test: `tests/test_log_records.py`
- Consumes: primitive values only; no `KalshiMarket`, client, cache, or logger.
- Produces: `ExecutedPriceSkipProvenance` and
  `build_executed_price_skip_provenance(...)` for the blend and logger tasks.

- [ ] **Step 1: Write failing schema tests.** Add
  `test_build_executed_price_skip_provenance_distinguishes_source_faults` and
  `test_build_executed_price_skip_provenance_redacts_untrusted_values` with
  parameterized cases for:
  - empty selected quote: `executed_price_cents=None`, selected ask `None`,
    `price_available=False`, expected `source_quote_empty`;
  - zero selected quote: raw and selected quote `0`, expected
    `source_quote_zero`;
  - invalid selected quote: boolean or `101`, expected
    `source_quote_invalid` and no raw object serialization;
  - stale selected quote: a valid selected ask with an aware retrieval time
    older than `MARKET_CACHE_TTL_SECONDS`, combined with an invalid handoff,
    expected `source_quote_stale`;
  - a missing, naive, malformed, future, and valid timestamp, with the exact
    expected timestamp state and no incorrect `fresh` classification.
  Assert the exact key set, fixed enum values, no more than four fault codes,
  bounded integer fields, `16`-hex fingerprint prefix behavior, and redaction
  of untrusted strings/objects.

- [ ] **Step 2: Run the red test.**

  Run: `CI=1 .venv/bin/python -m pytest -q tests/test_log_records.py`

  Expected before implementation: import or assertion failure because the
  fixed provenance type and constructor do not exist.

- [ ] **Step 3: Implement the pure record.** In `utils/log_records.py`, add
  `@dataclass(frozen=True) ExecutedPriceSkipProvenance` with an
  `as_log_record() -> dict[str, object]` method that returns exactly the schema
  in the design. Add
  `build_executed_price_skip_provenance(*, executed_price_cents: object,
  requested_side: object, signal_type: object, selected_quote_cents: object,
  price_available: object, price_source: object, price_method: object,
  price_retrieved_at: object, raw_payload_hash: object, observed_at: object,
  stale_after_seconds: int) -> ExecutedPriceSkipProvenance`.

  Make the constructor total: normalize only allowlisted strings, bounded
  integers, UTC-aware timestamps, and validated hashes. Convert every other
  input to a fixed enum/null, never call external code, and never raise for
  malformed input. Implement the precedence and capped-age rules from the
  design exactly.

- [ ] **Step 4: Run the focused green test.**

  Run: `CI=1 .venv/bin/python -m pytest -q tests/test_log_records.py`

- [ ] **Step 5: Commit Task 1.**

  Run: `git add utils/log_records.py tests/test_log_records.py && git commit -m "feat: define executed price skip provenance"`

## Task 2: Add the Typed Logger Boundary

- Test: `tests/test_log_records.py`
- Consumes: `ExecutedPriceSkipProvenance.as_log_record()`.
- Produces: a single additive JSONL member accepted by `TradeLogger.log_skipped`.

- [ ] **Step 1: Write failing logger tests.** Add
  `test_trade_logger_serializes_executed_price_skip_provenance` and
  `test_trade_logger_omits_executed_price_provenance_when_not_supplied`. Create
  a real `TradeLogger` with a temporary JSONL path, call `log_skipped` with a
  constructed provenance record, and assert that the emitted line has the
  exact nested key set. The neighboring non-invalid skip does not pass the
  optional argument and must omit the new key.

- [ ] **Step 2: Run the red test.**

  Run: `CI=1 .venv/bin/python -m pytest -q tests/test_log_records.py`

- [ ] **Step 3: Implement logger serialization.** Change only
  `TradeLogger.log_skipped` in `utils/logger.py` to accept
  `executed_price_provenance: ExecutedPriceSkipProvenance | None = None`.
  When present, set `record["executed_price_provenance"]` to
  `executed_price_provenance.as_log_record()`. Do not accept a raw `dict`,
  mutate `signal_meta`, or change any existing field calculation.

- [ ] **Step 4: Run the focused green test.**

  Run: `CI=1 .venv/bin/python -m pytest -q tests/test_log_records.py`

- [ ] **Step 5: Commit Task 2.**

  Run: `git add utils/logger.py tests/test_log_records.py && git commit -m "feat: log executed price skip provenance"`

## Task 3: Attach Provenance at the Existing Terminal Guard

- Test: `tests/test_blend_task.py`
- Consumes: Task 1 constructor and Task 2 typed logger argument.
- Produces: one provenance member only on the existing invalid-price skip.

- [ ] **Step 1: Write failing terminal-path tests.** Add
  `test_invalid_executed_price_emits_bounded_provenance_without_admission` and
  `test_valid_price_blocked_skip_has_no_executed_price_provenance`, then extend
  the existing invalid-price tests around
  `test_invalid_executed_price_stops_before_blend_readiness_and_g7` and
  `test_invalid_executed_price_persists_skip_without_fabricated_price_fields`.
  For each empty, zero, invalid, and stale fixture, assert:
  - expected `primary_fault`, state fields, source timestamp state, and bounded
    payload;
  - `ready is False`, `candidate is None`, `enqueued is False`, and the queue
    is empty;
  - the fake store, blender, readiness evaluator, execution-liquidity provider,
    pre-queue provider, quarantine sink, and paper-admission handoff are not
    called;
  - `market_price`, `edge`, `min_edge_threshold`, `signed_diff`, and
    `absolute_diff` remain absent from real JSONL;
  - the original `signal_meta` object is unchanged and has no added provenance
    key.
  Add a valid-price blocked-result regression asserting that it has no
  `executed_price_provenance` member.

- [ ] **Step 2: Run the red test.**

  Run: `CI=1 .venv/bin/python -m pytest -q tests/test_blend_task.py -k "invalid_executed_price or blocked_blend"`

- [ ] **Step 3: Implement the narrow handoff.** In
  `tasks/blend_task.py`, collect only the selected ask and documented market
  metadata with guarded direct attribute reads. Build the provenance once in
  `_invalid_executed_price_result` using one local observation time and
  `MARKET_CACHE_TTL_SECONDS`; pass it as an optional argument into
  `_emit_skipped` and then `logger.log_skipped`.

  Do not call `market.is_tradeable()`, a provider, or `main.py`. Keep
  `_has_valid_executed_price_cents`, `BlendTaskResult`, queue behavior, and the
  invalid price branch unchanged apart from the one additive log argument.

- [ ] **Step 4: Run the focused green test.**

  Run: `CI=1 .venv/bin/python -m pytest -q tests/test_blend_task.py -k "invalid_executed_price or blocked_blend"`

- [ ] **Step 5: Commit Task 3.**

  Run: `git add tasks/blend_task.py tests/test_blend_task.py && git commit -m "feat: observe invalid executed price skips"`

## Task 4: Verify the Locked Boundary

- Test: focused suites plus static diff review.
- Consumes: Tasks 1-3.
- Produces: evidence that the only behavior change is the bounded JSONL member.

- [ ] **Step 1: Run the focused regression suite.**

  Run: `CI=1 .venv/bin/python -m pytest -q tests/test_log_records.py tests/test_blend_task.py tests/test_main_pipeline.py`

- [ ] **Step 2: Run static checks.**

  Run: `ruff check utils/log_records.py utils/logger.py tasks/blend_task.py tests/test_log_records.py tests/test_blend_task.py`

  Run: `git diff --check HEAD~3..HEAD`

- [ ] **Step 3: Perform the negative-path review.** Confirm the diff does not
  touch `main.py`, `config.py`, `trading/paper_trader.py`, migrations, launchd
  or service files, and contains no HTTP/client/provider call in the
  provenance constructor or invalid-price path.

- [ ] **Step 4: Record rollout evidence without restart.** Parse a controlled
  temporary JSONL record from the tests. It must contain the new fixed object,
  omit all price-derived decision fields, and have no paper row. Do not inject
  a runtime event, restart a service, enable a setting, or take a live action.

- [ ] **Step 5: Commit only any verification-note update.**

  Run: `git add docs/superpowers/plans/2026-08-01-executed-price-observability.md .superpowers/sdd/2026-08-01-executed-price-observability/progress.md && git commit -m "docs: record executed price observability verification"`

## Later Rollout Gate

This docs-only design authorizes no rollout. A later operator-approved code
deployment may observe naturally occurring invalid-price skips, but it must
not restart, modify configuration, or trigger a manual quote/action merely to
produce a sample. A nonzero runtime sample must be inspected for the fixed
schema and absence of a corresponding paper admission. A zero sample is
reported as no observed event, not as correctness, profit, or trade-readiness
evidence.
