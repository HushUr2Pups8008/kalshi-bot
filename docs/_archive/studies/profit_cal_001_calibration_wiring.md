# PROFIT-CAL-001 — Calibration Emission Wiring (Design Note)

| Field | Value |
|-------|-------|
| Status | DESIGN — ready for execution post-S4.5c |
| Execution window | Earliest 2026-04-26 (after S4.5c close) |
| Author | Claude |
| Date drafted | 2026-04-23 |
| Supersedes | — |
| Tracks | `PROFIT-CAL-001` in `docs/profit_path_debt_log.md` |
| Blocks | ROADMAP P4.2 (calibration review), P4.3 (live trading authorization) |

## 1. Context

The calibration feedback loop in kalshi-bot is architecturally complete but
operationally inert. All components exist — `log_calibration_check` at
[utils/logger.py:1242](../../utils/logger.py#L1242), the
`CalibrationTask.record_calibration_check` consumer at
[tasks/calibration_task.py](../../tasks/calibration_task.py), the pure-function
state machine in [analysis/calibration_monitor.py](../../analysis/calibration_monitor.py),
construction and injection into `BlendTask` at
[main.py:435-438](../../main.py#L435) — but **no runtime call site** emits
`CALIBRATION_CHECK` events.

Consequence: `CalibrationTask._state` is permanently empty,
`get_scaling_factor(lane)` always returns `1.0` (no-op scaling), and Contract
[IMPLEMENTATION_CONTRACT.md](../IMPLEMENTATION_CONTRACT.md) Section 13 item 6
is vacuously satisfied because the events do not exist.

This design note specifies the fix.

## 2. Why this is a go-live blocker

ROADMAP P4.2 ("Calibration review: est distribution vs resolved outcomes")
requires `≥ 10 resolved paper trades` and produces a `calibration curve` as its
expected outcome. Both inputs and outputs require the `CALIBRATION_CHECK`
event stream to exist. Without the emission wiring, P4.2 is structurally
unable to complete, and P4.3 (live trading authorization) cannot proceed by
transitive dependency. This is not a matter of contract interpretation —
there is no path from the current state to P4.3 without the fix.

## 3. Chosen path: Path A (schema migration + emission at resolve time)

### 3.1 Path comparison

| Criterion | Path A: persist per-lane estimates on `paper_trades` | Path B: join BLEND_DECISION events at resolve time |
|---|---|---|
| Data durability | ✅ lane estimates survive log rotation, archival, DB-only queries | ❌ depends on intact trade-log event chain |
| Auditability | ✅ single SQL query reconstructs any resolved trade's calibration inputs | ❌ requires ordered log walk + timestamp matching |
| Implementation cost | ~40 lines + migration (6 nullable columns) | ~80 lines + log-walk helper + ambiguity handling |
| Failure modes | ✅ null column → skip that lane's CALIBRATION_CHECK (clean) | ❌ missing/duplicate BLEND_DECISION → silent drop or spurious emission |
| Fits existing pattern | ✅ matches the `_migrate_db` schema-evolution pattern already in [trading/paper_trader.py](../../trading/paper_trader.py) | ❌ new pattern; introduces log-as-database coupling |
| Consistency with BSR-7 | ✅ evidence identity approximation holds: lane estimate identity = `(trade_id, lane)` | ❌ relies on timestamp proximity, not identity |

**Verdict: Path A.** Path B is rejected as brittle and pattern-breaking.

### 3.2 Path A scope

Three code-change zones, one test zone, one schema migration. All post-S4.5c.

#### Zone 1 — Schema migration ([trading/paper_trader.py](../../trading/paper_trader.py))

Add nullable columns to the `paper_trades` table via the existing
`_migrate_db` pattern. Each column captures the per-lane estimate known at
trade time:

```
fast_lane_p            REAL   -- nullable
fast_lane_confidence   REAL   -- nullable
accumulation_p         REAL   -- nullable
accumulation_confidence REAL  -- nullable
structural_p           REAL   -- nullable
structural_confidence  REAL   -- nullable
```

All six columns are nullable so historical rows (pre-migration) do not need
backfill. Any null lane is skipped at emission time — no spurious events.

#### Zone 2 — `record_trade` population

At trade record time, the originating `BLEND_DECISION` is already in scope
(the `TradeCandidate` in [trading/executor.py](../../trading/executor.py)
carries the lane estimates). Route those values into the new columns:

```python
# trading/paper_trader.py record_trade
# At existing INSERT INTO paper_trades, add:
fast_lane_p            = getattr(analysis, "fast_lane_p", None)
fast_lane_confidence   = getattr(analysis, "fast_lane_confidence", None)
accumulation_p         = getattr(analysis, "accumulation_p", None)
accumulation_confidence= getattr(analysis, "accumulation_confidence", None)
structural_p           = getattr(analysis, "structural_p", None)
structural_confidence  = getattr(analysis, "structural_confidence", None)
```

This requires a small extension to the `TradeCandidate` → `SignalAnalysis`
adapter in `trading/executor.py::_analysis_from_candidate()` to carry
`fast_lane_p`, `accumulation_p`, `structural_p` through from the candidate.
Today the adapter preserves `fast_lane_analysis`, `blended_probability`, and
`side`; it needs to also preserve the per-lane inputs.

**Purity check (INV-4):** no `/analysis` function changes. All edits are in
`/trading`.

#### Zone 3 — `resolve_market` emission

In [trading/paper_trader.py](../../trading/paper_trader.py) `resolve_market`,
after the `UPDATE` loop but before the log emission block, iterate lanes
per resolved trade and emit one `CALIBRATION_CHECK` per populated lane:

```python
# Pseudocode inside resolve_market, inside the existing `for t, won, payout, pnl in outcomes:` loop
lanes = [
    ("fast",         t["fast_lane_p"]),
    ("accumulation", t["accumulation_p"]),
    ("structural",   t["structural_p"]),
]
final_resolution = 1.0 if resolved_yes else 0.0
for lane_name, lane_estimate in lanes:
    if lane_estimate is None:
        continue
    error = abs(lane_estimate - final_resolution)
    trade_log.log_calibration_check(
        market_ticker=ticker,
        lane=lane_name,
        lane_estimate=lane_estimate,
        final_resolution=final_resolution,
        error=error,
    )
    await self._calibration_task.record_calibration_check(
        market_ticker=ticker, lane=lane_name,
        lane_estimate=lane_estimate, final_resolution=final_resolution,
        error=error,
    )
```

Two emission paths:
- `trade_log.log_calibration_check(...)` writes to the structured trade log
  (observability, post-hoc audit, S1.6 schema contract).
- `CalibrationTask.record_calibration_check(...)` updates the in-process
  state machine that `BlendTask.get_scaling_factor()` consumes.

Both are needed. Neither is optional. The design note from S1.6
(`tests/test_calibration_check_schema.py`) is already in place; the only
gap is the call site.

**Async note:** `record_calibration_check` is async. `resolve_market` is
currently sync. Follow the existing pattern for async-from-sync in
[trading/paper_trader.py](../../trading/paper_trader.py) — use
`asyncio.run_coroutine_threadsafe` if called from a thread that has a loop,
or accept that `resolve_market` becomes async (matches MAC-ASYNC-001/002
direction of travel). Decision deferred to implementation time; both paths
are viable.

#### Zone 4 — `main.py` reference injection

`resolve_market` currently does not have a reference to `CalibrationTask`.
The `PaperTrader` instance is constructed in `main.py` alongside the
`CalibrationTask` — wire a constructor argument or setter so `PaperTrader`
can reach `self._calibration_task` from within `resolve_market`.

Preferred form: constructor injection. `PaperTrader(db_path=..., startup_context=..., calibration_task=...)`. Default to `None` for tests, and
skip the `record_calibration_check` call when the reference is missing.
This preserves the existing test fixtures and lets us advance incrementally.

#### Zone 5 — Tests

Add a single end-to-end test in `tests/test_paper_trader.py`:

- Construct `PaperTrader` with a real `CalibrationTask` instance.
- `record_trade` with a synthetic `SignalAnalysis` that has all three lane
  fields populated.
- `resolve_market` with a known outcome.
- Assert that `trade_log.log_calibration_check` was called three times (once per lane).
- Assert that `calibration_task._state.lanes` has three entries after the call.
- Assert fields on each emitted event match Section 8 schema.

Also add a negative test: `record_trade` without lane fields → `resolve_market`
emits zero `CALIBRATION_CHECK` events (historical-row compatibility).

## 4. Acceptance criteria

This fix is considered complete when:

1. [PROFIT-CAL-001](../profit_path_debt_log.md) moves from OPEN to COMPLETE,
   with a Validation Notes entry citing the executing commit and paper-mode
   evidence (≥ 1 `CALIBRATION_CHECK` observed in `logs/trades/live/trades.jsonl`
   after the first paper-trade resolution post-fix).
2. Test added per Zone 5 above passes.
3. `make lint` clean; no existing test regressions.
4. [docs/ROADMAP.md](../ROADMAP.md) P4.2 row's blocker cross-reference to
   `PROFIT-CAL-001` can be struck.
5. `CalibrationTask.get_calibration_summary()` returns non-empty per-lane
   stats in a paper run with ≥ 1 resolved trade per lane.

## 5. Rollback

If the emission produces unexpected calibration scaling at runtime (drift
detection firing on a too-small sample), the `scaling_factor` floor is
already `_SCALING_FLOOR = 0.2` in [analysis/calibration_monitor.py](../../analysis/calibration_monitor.py), preventing a runaway
suppression. Additionally, `_MIN_LANE_SAMPLES = 5` gates drift detection —
the first 4 samples per lane cannot trigger scaling at all.

If a stronger rollback is required: revert the wiring in `main.py` Zone 4
(drop the `calibration_task=...` injection). The schema columns remain,
`resolve_market` sees `self._calibration_task is None`, and emission
short-circuits. This is a one-line revert; the schema migration is durable.

## 6. Out of scope for this design note

- Tuning `_DRIFT_THRESHOLD`, `_WINDOW_SIZE`, `_MIN_BASELINE_SAMPLES`, or
  `_SCALING_FLOOR`. Those were set under S1.3/S3.6 and are not up for
  revision as part of wiring the emission.
- Populating historical `paper_trades` rows with lane estimates. Historical
  rows simply emit no `CALIBRATION_CHECK` at resolution time. Backfilling
  would require joining historical `BLEND_DECISION` events and is a separate
  task if ever needed.
- Live-mode (`live_orders`) calibration emission. Live trading is gated on
  P4.3 which itself gates on this fix. Once live trading is authorized, a
  separate task mirrors this emission pattern in the live execution path.
