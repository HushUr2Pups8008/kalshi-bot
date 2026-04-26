# Profit Path Technical Debt Log

**Single system of record for technical debt that could materially reduce the bot's ability to make money through disciplined, well-educated trades.**

This log supersedes the former `docs/macos_migration_debt.md` tracker. The original Windows -> macOS migration findings are preserved with their `MAC-*` IDs and completion history; the scope is now broader so platform, signal-quality, belief-system, execution-boundary, observability, validation, and documentation risks stay in one durable queue instead of fragmenting across parallel logs. Claude and Codex should continue using this renamed file as the sole technical-debt tracking mechanism going forward.

---

## Header / Metadata

| Field | Value |
|-------|-------|
| Last Updated | 2026-04-25 |
| Audit Source | Expanded profit-path audit — Codex 2026-04-20; incorporates prior migration audit from commit 2315a1d; Claude 2026-04-22 observation-window code-hygiene sweep; Claude 2026-04-23 S4.5b closure and PROFIT-RUNTIME-001 unblock; Claude 2026-04-23 PROFIT-CAL-001 emission-wiring investigation; Claude 2026-04-23 PROFIT-CAL-001 elevation to pre-live-trading blocker; Claude 2026-04-23 news-sources evaluation and PROFIT-SOURCE-001 registration of Reddit degraded-permanent state; Claude 2026-04-25 governance Phase 2 execution-time decision on signal-analyzer LLM unification deferral (PROFIT-LLM-001) |
| Previous Tracker Name | `docs/macos_migration_debt.md` |
| Current Tracker Name | `docs/profit_path_debt_log.md` |
| Total Items | 35 |
| Open — HIGH | 3 |
| Open — MEDIUM | 1 |
| Open — LOW | 2 |
| Items COMPLETE | 28 (MAC-ASYNC-001, MAC-ASYNC-002, MAC-DB-001, MAC-DB-002, MAC-DB-003, MAC-DB-004, MAC-DB-005, MAC-CLI-001, MAC-CLI-002, MAC-DOC-001, MAC-DOC-002, MAC-DOC-003, MAC-FS-001, MAC-LOG-001, MAC-PLAT-001, MAC-TEST-001, MAC-TEST-002, MAC-TEST-003, MAC-TEST-004, PROFIT-TRACE-001, PROFIT-REPLAY-001, PROFIT-EVID-002, PROFIT-EXEC-001, PROFIT-OBS-001, PROFIT-OBS-002, PROFIT-PERF-001, PROFIT-STARTUP-001, PROFIT-STRUCT-001) |

### High-Risk Areas

1. **Multi-lane validation is incomplete** — S4.5 still needs a sustained paper-mode run that proves accumulation, structural priors, blending, readiness, and execution all participate under production-intended intake.
2. **Traceability and replay gaps** — runtime evidence IDs are random, current-signal evidence can miss the matching `BLEND_DECISION`, and replay-critical `implied_probability` is not persisted by the live accumulation path.
3. **Execution-boundary bypass risk** — fade tweet and price-fade paths still call the executor directly rather than the blend/readiness lane meeting point.
4. **Source-class and evidence-quality loss** — live evidence conversion collapses all news into `source_class="news"`, weakening source diversity, scorer quality, readiness G2, and structural context.
5. **Observability under long runs** — app-log rollover policy is time-only and documentation disagrees with code behavior on macOS, making S4.5 and go-live audit trails fragile.
6. **Calibration feedback loop is operationally inert** — `PROFIT-CAL-001` confirmed 2026-04-23 that no runtime code calls `log_calibration_check` or `CalibrationTask.record_calibration_check`, so per-lane drift detection and confidence scaling never fire. Architecturally complete, functionally a no-op. Blocks ROADMAP P4.2 and therefore P4.3. Execution design in [`plans/profit_cal_001_calibration_wiring.md`](plans/profit_cal_001_calibration_wiring.md).

### Recommended Execution Order

1. `PROFIT-RUNTIME-001` (blocked on structural participation evidence; blocks confidence in the whole architecture)
2. `PROFIT-VALID-001` + `PROFIT-OBS-001` (validation and observability hardening)
3. `PROFIT-EVID-001` (blocked pending contract decision on non-trading evidence intake)
4. Remaining MEDIUM items in dependency order

---

## Full Technical Debt Log

---

## Current Open Profit-Path Items

These items were added during the 2026-04-20 expanded audit. They do not replace the completed `MAC-*` migration work below; they extend the same single tracking mechanism to all issues that could impair profitable, safe, auditable trading.

---

### PROFIT-RUNTIME-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-RUNTIME-001 |
| **Title** | S4.5 multi-lane paper validation remains unproven over a meaningful window |
| **Category** | System Validation / Profit-Path Integrity |
| **Severity** | HIGH |
| **Status** | OPEN |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | S4.5c extended validation window |
| **Blocks** | Go-live confidence, S4.5 completion |

**Description**  
The system now wires fast-lane output through `BlendTask`, evidence through `AccumulationTask`, and structural recomputes through `StructuralTask`, but the corrected S4.5 validation still requires a sustained 6-12 hour paper-mode run. Earlier attempts only proved wiring readiness or insufficient runtime, not real multi-lane behavior under production-intended intake.

**Why it matters to profitability / safety / reliability**  
Without this run, the bot can appear architecturally complete while still failing to accumulate useful evidence, recompute priors, emit complete blend telemetry, or preserve the trade-frequency constraint in real flow.

**Evidence / Source**  
- `main.py:709-723` routes keyword-positive fast-lane signals into evidence + blend.
- `main.py:1616-1620` starts accumulation, blend consumer, and structural tasks.
- S4.5 notes in chat: prior runs lacked sufficient post-wiring observation time.

**Proposed Fix**  
Run paper mode under production-intended intake for a meaningful window, capture lane activation timeline, compare fast-lane baseline to multi-lane frequency, and record Section 13 pass/fail evidence.

**Acceptance Criteria**  
- Runtime duration is recorded.
- `EVIDENCE_INGESTION`, `DOSSIER_UPDATE`, `STRUCTURAL_PRIOR_RECOMPUTE`, and `BLEND_DECISION` are observed or a defect is filed.
- Trade-frequency comparison is computed or handled with the zero-baseline rule.
- Section 13 checklist is explicitly PASS / FAIL / N/A.

**Notes**  
Do not modify intake settings to force activity; this is a validation debt item, not a tuning item.

**Validation Notes** (2026-04-20)  
Runtime evidence exists for accumulation and blend participation, but the gate cannot close yet. `logs/trades/live/trades.jsonl` shows `BLEND_DECISION`, `EVIDENCE_INGESTION`, and `DOSSIER_UPDATE` events from 2026-04-20T03:43Z through 2026-04-20T12:50Z for `KXTRUMPIRAN-26MAY01`; `data/evidence_store.db` shows one dossier at version 7 with seven evidence rows. No `STRUCTURAL_PRIOR_RECOMPUTE` events were found, and `structural_priors` is empty. This is now blocked on resolving structural lane participation (see `PROFIT-STRUCT-001`) before S4.5 can be honestly marked complete.

**Validation Notes** (2026-04-23)  
Structural-lane block cleared. Over the 47-hour window 2026-04-21T00:09 → 2026-04-22T23:44 UTC (post-commit `2731d9a` structural crash-loop fix deployed 2026-04-21T01:38 UTC), `logs/trades/archive/2026/04/2026-04-{21,22}.jsonl` contain `EVIDENCE_INGESTION ×46`, `DOSSIER_UPDATE ×46`, `BLEND_DECISION ×49` (with `fast_lane_p` non-null on sampled events), and `STRUCTURAL_PRIOR_RECOMPUTE ×20` across 6 distinct dossier markets (`KXELECTIONEMERGENCY-26MAY01`, `KXMOCTRUMP25-26-APR24`, `KXMOCTRUMP25-26-MAY01`, `KXPARDONSTRUMP-26APR-1`, `KXTRUMPENDORSE-26SEP15-NMOR`, `KXTRUMPIRAN-26MAY01`). First structural event at 2026-04-21T16:52:38 UTC. Zero unhandled exceptions in `bot.log`. Trade-frequency observation: zero paper trades, consistent with Phase 0 verdict (LLM correctly declines directional views on broad-scope markets — not a runtime defect) — zero-baseline rule applies. **S4.5b closed PASS** (see `docs/ROADMAP.md` Stage 4 table). Status advanced `BLOCKED → OPEN`. The only remaining blocker is S4.5c — the 72-hour extended statistical-basis window — which is a soak-time requirement, not an architectural concern. No code or intake changes are required to reach S4.5c; depends only on elapsed observation time after any current configuration changes stabilize.

---

### PROFIT-TRACE-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-TRACE-001 |
| **Title** | Evidence identity and blend traceability are fragile in live runtime |
| **Category** | Auditability / Traceability |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | S2.5, S3.4 |
| **Blocks** | Reliable trade explanation, replay confidence |

**Description**  
`_signal_to_evidence()` creates `evidence_id=str(uuid.uuid4())`, so the same feed item reprocessed after restart receives a new immutable ID. `BlendTask` then emits `evidence_ids_contributing` from already-persisted recent evidence only; because `main.py` enqueues evidence and immediately calls `BlendTask`, the triggering signal's evidence may not yet be persisted and may be missing from the related `BLEND_DECISION`.

**Why it matters to profitability / safety / reliability**  
The contract relies on evidence -> dossier -> blend -> trade traceability. If IDs are nondeterministic or the current signal is absent from blend telemetry, later review cannot reliably explain why a profitable or losing trade happened.

**Evidence / Source**  
- `main.py:333-348` generates random UUID evidence IDs.
- `main.py:709-723` enqueues evidence non-blocking, then immediately blends.
- `tasks/blend_task.py:191` emits `evidence_ids = [record.evidence_id for record in recent_records] if dossier else []`.

**Proposed Fix**  
Define deterministic evidence identity for feed-derived evidence and ensure the triggering evidence ID is included in the blend trace when available, without blocking the fast lane on accumulation policy.

**Acceptance Criteria**  
- Reprocessing the same source/headline/market/time identity produces the same evidence ID or a documented idempotency key.
- `BLEND_DECISION.evidence_ids_contributing` can be joined to the specific evidence chain behind the decision.
- Tests cover same-signal traceability and restart/replay idempotency.

**Notes**  
This is not a request to redesign evidence semantics; it is a traceability contract repair.

**Implementation Notes** (2026-04-20)
Fixed by replacing random runtime evidence IDs with deterministic `ev-<sha256>` IDs derived from ticker, source, URL, headline, and published timestamp. The fast-lane `SignalAnalysis.signal_meta` now carries `trigger_evidence_id`, and `BlendTask` includes that ID in `BLEND_DECISION.evidence_ids_contributing` even before the asynchronous accumulation task has caught up. Recent evidence IDs are de-duplicated against the trigger ID. Validation: `.venv/bin/pytest tests/test_main_pipeline.py tests/test_blend_task.py tests/test_accumulation_task.py tests/test_replay_dossier.py` (80 passed).

---

### PROFIT-REPLAY-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-REPLAY-001 |
| **Title** | Live accumulation path does not persist replay-critical `implied_probability` |
| **Category** | Replay / Auditability |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | Codex |
| **Depends On** | S4.1 |
| **Blocks** | Deterministic dossier reconstruction |

**Description**  
The replay utility intentionally fails if persisted evidence lacks `raw_payload_json.implied_probability`, but `_evidence_record_from_score()` does not populate `raw_payload_json`. The live `Evidence` object carries `implied_probability`, then storage drops it from replay payload.

**Why it matters to profitability / safety / reliability**  
If belief state cannot be reconstructed solely from persisted evidence events, losing trades cannot be audited and profitable patterns cannot be calibrated from a trustworthy history.

**Evidence / Source**  
- `scripts/replay_dossier.py:299-344` requires `raw_payload_json.implied_probability`.
- `tasks/accumulation_task.py:320-333` builds `EvidenceRecord` without `raw_payload_json`.
- `analysis/evidence_types.py:26` defines `implied_probability` as part of `Evidence`.

**Proposed Fix**  
Persist the minimal replay payload required by S4.1 when converting scored evidence to `EvidenceRecord`, preserving schema compatibility.

**Acceptance Criteria**  
- Live-ingested evidence rows contain replay-critical probability data.
- `scripts.replay_dossier` can replay a paper-run market without synthetic test-only payloads.
- Existing raw payload fields remain backward compatible for old rows.

**Notes**  
Do not patch replay to guess missing probabilities; missing data should stay visible for old records.

**Implementation Notes** (2026-04-20)
`AccumulationTask` now persists a minimal `raw_payload_json` payload containing `implied_probability`, `evidence_id`, and `content_hash` when converting live `Evidence` into `EvidenceRecord`. This preserves backward compatibility for old rows while making new rows replayable by `scripts/replay_dossier.py`. Validation: `.venv/bin/pytest tests/test_main_pipeline.py tests/test_blend_task.py tests/test_accumulation_task.py tests/test_replay_dossier.py` (80 passed).

---

### PROFIT-EVID-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EVID-001 |
| **Title** | Accumulation lane only learns from keyword-positive fast-lane survivors |
| **Category** | Signal Quality / Evidence Coverage |
| **Severity** | HIGH |
| **Status** | BLOCKED |
| **Priority** | HIGH |
| **Owner** | Shared |
| **Depends On** | S2.5 |
| **Blocks** | Dossier completeness, structural prior quality |

**Description**  
`_process_candidate()` returns immediately on `if not keywords`, before creating `SignalAnalysis` or evidence. That means weak/no-keyword but potentially informative LLM outcomes and rejected candidates do not enter the accumulation lane.

**Why it matters to profitability / safety / reliability**  
The dossier can become a biased memory of only signal-positive events, weakening calibration around non-events, false positives, and contextual evidence that should reduce confidence.

**Evidence / Source**  
- `main.py:553-568` logs `no_keywords` and returns.
- Evidence creation begins only at `main.py:709`.

**Proposed Fix**  
Decide, at the contract level, which rejected or low-signal observations should become non-trading evidence, then route only those approved classes into accumulation with explicit update types.

**Acceptance Criteria**  
- Evidence intake policy documents which fast-lane outcomes become dossier evidence.
- Tests verify no unintended trade path is opened by ingesting non-trading evidence.
- Dossier updates can represent confidence/state-neutral evidence where intended.

**Notes**  
This is not permission to loosen trading thresholds.

**Blocker Notes** (2026-04-20)  
Implementation intentionally paused. Ingesting no-keyword or low-signal rejected candidates would alter dossier state and future blend/readiness inputs, which is a decision-policy change. The proposed fix requires a contract-level decision on which rejected observations should become non-trading evidence and how those observations should update state versus confidence. Do not implement until `IMPLEMENTATION_CONTRACT.md` defines the allowed rejected-evidence classes and update semantics.

---

### PROFIT-EVID-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EVID-002 |
| **Title** | Runtime evidence conversion collapses all sources into `source_class="news"` |
| **Category** | Signal Quality / Source Classification |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | HIGH |
| **Owner** | Codex |
| **Depends On** | S2.5, S3.4 |
| **Blocks** | Readiness G2 fidelity, source-quality scoring |

**Description**  
`_signal_to_evidence()` hardcodes every feed-derived item to `source_class="news"`, even when source families include publisher RSS, Reddit/social, direct feeds, search-derived items, or official-style sources.

**Why it matters to profitability / safety / reliability**  
The evidence scorer, dossier builder, structural task, and readiness gate use source class for quality, correlation, diversity, and source-class requirements. Collapsing classes hides evidence concentration and can over-block or over-trust trades.

**Evidence / Source**  
- `main.py:342` sets `source_class="news"`.
- `analysis/evidence_scorer.py:58-118` uses `source_class` for quality and independence.
- `tasks/trade_readiness_gate.py:95-97` enforces source-class diversity for dossier candidates.

**Proposed Fix**  
Add a narrow source-family-to-source-class mapper for evidence metadata, reusing existing source-family classification where possible.

**Acceptance Criteria**  
- RSS, Reddit/social, search, direct, official/market-like sources map deterministically to documented classes.
- Existing trading behavior remains unchanged except for evidence metadata.
- Tests cover readiness G2 and scorer effects with real runtime source examples.

**Notes**  
Keep mapping conservative; unknown sources should remain `other` or a documented fallback.

**Implementation Notes** (2026-04-20)  
Added conservative runtime source-class mapping in `main.py`: Reddit-style sources map to `social`, official/government-style labels map to `official`, known publisher/search/direct news sources map to `news`, and unknown sources fall back to `other`. Evidence conversion now uses this mapper instead of hardcoding `news`. Validation: `.venv/bin/pytest tests/test_main_pipeline.py tests/test_blend_task.py tests/test_accumulation_task.py tests/test_replay_dossier.py` (80 passed).

---

### PROFIT-OBS-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-OBS-001 |
| **Title** | `BLEND_DECISION` completeness rules conflict with nullable lane semantics |
| **Category** | Observability / Contract Clarity |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | HIGH |
| **Owner** | Shared |
| **Depends On** | S4.2 |
| **Blocks** | Reliable observability pass/fail interpretation |

**Description**  
The contract requires all `BLEND_DECISION` fields to be non-null for at least 90% of paper events, but early/fast-only cases naturally have absent accumulation or structural lane values, and approved trades use `trade_blocked_reason=None`.

**Why it matters to profitability / safety / reliability**  
If observability validation treats semantically optional fields as failed instrumentation, operators may chase false defects or miss real telemetry gaps.

**Evidence / Source**  
- `docs/IMPLEMENTATION_CONTRACT.md:597` defines the 90% non-null criterion.
- `tasks/blend_task.py:316-331` emits accumulation/structural fields directly from lane availability.
- `tests/test_blend_decision_schema.py:79` expects `trade_blocked_reason is None` for non-blocked cases.

**Proposed Fix**  
Clarify completeness validation into required-presence versus semantically nullable fields, while preserving exact schema keys.

**Acceptance Criteria**  
- S4.2 tooling distinguishes absent keys, malformed values, and allowed nulls.
- Contract wording documents which fields may be null and when.
- Tests cover both approved and blocked candidate telemetry.

**Notes**  
Do not remove fields from the schema.

**Implementation Notes** (2026-04-20)  
Updated `scripts/observability_completeness_review.py` to distinguish required-valid completeness from strict non-null completeness. Semantically nullable `BLEND_DECISION` fields (`accumulation_*`, `structural_*`, and approved-candidate `trade_blocked_reason=None`) now count as valid when the schema key is present, while absent or malformed fields still fail. Blocked-reason gaps now flag explicit empty strings rather than approved candidates. Validation: `.venv/bin/pytest tests/test_observability_completeness_review.py` (5 passed).

---

### PROFIT-OBS-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-OBS-002 |
| **Title** | App-log rollover policy and documentation are misaligned for long macOS runs |
| **Category** | Observability / Runtime Operations |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | HIGH |
| **Owner** | Codex |
| **Depends On** | — |
| **Blocks** | S4.5 long-run audit confidence |

**Description**  
`utils/logger.py` uses a time-based `_DailyRotatingFileHandler` with copy+truncate rotation for app logs, while `PLATFORMS.md` claims macOS/Linux use atomic rename. App logs have no size cap, so a high-volume same-day S4.5 run can produce large active files until midnight or manual rotation.

**Why it matters to profitability / safety / reliability**  
Long-run paper validation depends on trustworthy, bounded logs. Misunderstood rollover behavior can lose audit context, create operator confusion, or cause disk pressure during the exact runs meant to prove safety.

**Evidence / Source**  
- `utils/logger.py:146-231` implements copy+truncate daily rotation.
- `PLATFORMS.md:14` documents atomic rename for macOS/Linux.
- git history (v0.6.6–v0.6.7 commits) documents the switch to copy+truncate daily rotation and the singleton-handler fix for duplicate rotation.

**Proposed Fix**  
Audit and either correct the documentation to match intentional daily copy+truncate behavior or implement the intended platform-specific rotation policy with tests for forced rollover and sustained logging.

**Acceptance Criteria**  
- `bot.log` and `errors.log` rollover behavior is explicitly documented and tested on macOS.
- Active app logs have an intentional size or time policy.
- No duplicate file handlers write to the same app log.

**Notes**  
This item captures the logging rollover concern inside the unified debt log instead of creating a separate logging tracker.

**Implementation Notes** (2026-04-20)  
Validated and completed the macOS stale-rollover repair. `utils.logger._maybe_rotate_stale()` now checks the first non-comment log timestamp when mtime alone makes a prior-period active log look current; this catches the long-run/restart case without changing the intended daily copy+truncate policy. `PLATFORMS.md` already documents copy+truncate for macOS/Linux. Added a regression test for current mtime plus prior-period first log timestamp. Validation: `.venv/bin/pytest tests/test_logger_rotation.py tests/test_app_log_reader.py tests/test_log_isolation.py tests/test_trade_log_store.py` (29 passed, 1 skipped).

---

### PROFIT-PERF-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-PERF-001 |
| **Title** | Synchronous structured-log fsyncs can stall async hot paths |
| **Category** | Performance / Timeliness |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | MEDIUM |
| **Owner** | Shared |
| **Depends On** | — |
| **Blocks** | High-volume intake reliability |

**Description**  
`TradeLogStore._append_line()` writes and fsyncs every structured JSONL event synchronously. Many trade-log calls happen inside async feed, analysis, accumulation, and blend paths.

**Why it matters to profitability / safety / reliability**  
During news bursts, synchronous fsyncs can delay candidate processing, evidence ingestion, and decision telemetry, causing stale decisions or missed opportunities.

**Evidence / Source**  
- `utils/logger.py:401-409` writes, flushes, and `os.fsync()`s every structured record.
- `main.py`, `tasks/accumulation_task.py`, and `tasks/blend_task.py` call `trade_log` from async workflows.

**Proposed Fix**  
Measure structured-log write latency under load, then decide whether to batch, queue, or thread off fsyncs without weakening audit durability.

**Acceptance Criteria**  
- Benchmark or stress test quantifies worst-case logging latency.
- Any changed logging path preserves event ordering and durability guarantees.
- Async hot-path tests guard against blocking regression.

**Notes**  
Do not remove fsync without an explicit audit-durability decision.

**Implementation Notes** (2026-04-20)
Preserved per-record flush/fsync durability but moved async hot-path structured-log writes through `utils.logger.write_trade_log_async(...)`, which awaits a thread offload so the event loop is not blocked by durable JSONL appends. Updated fast-lane intake, signal-analysis detail, matcher diagnostics, accumulation, blend, structural, and executor structured writes that run inside async workflows. Added `scripts/structured_log_latency_benchmark.py` to quantify the exact `TradeLogStore` fsync path; local macOS run with 100 synthetic records measured mean `0.0812 ms`, p99 `0.1570 ms`, max `0.2249 ms`. Added regression coverage that a deliberately slow structured writer runs off the event-loop thread. Validation: `.venv/bin/pytest tests/test_trade_log_store.py::test_write_trade_log_async_offloads_blocking_writer_from_event_loop tests/test_structured_log_latency_benchmark.py tests/test_main_pipeline.py tests/test_signal_analyzer.py tests/test_market_matcher.py tests/test_accumulation_task.py tests/test_blend_task.py tests/test_structural_task.py tests/test_executor.py` (229 passed).

---

### PROFIT-VALID-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-VALID-001 |
| **Title** | No first-class harness for fast-lane baseline versus multi-lane S4.5 comparison |
| **Category** | Validation / Testing |
| **Severity** | HIGH |
| **Status** | OPEN |
| **Priority** | HIGH |
| **Owner** | Codex |
| **Depends On** | PROFIT-RUNTIME-001 |
| **Blocks** | Reproducible trade-frequency constraint proof |

**Description**  
S4.5 requires comparing fast-lane-only baseline metrics against multi-lane metrics, but there is no dedicated run mode or measurement harness that cleanly disables blend routing while preserving comparable intake.

**Why it matters to profitability / safety / reliability**  
The 2x trade-frequency constraint is a core anti-overtrading guard. If baseline measurement is ad hoc, future validations may be inconsistent or impossible to reproduce.

**Evidence / Source**  
- `main.py:719-723` always routes normal news candidates through `BlendTask`.
- No config or script was found that captures both baseline and multi-lane metrics under identical intake windows.

**Proposed Fix**  
Add an explicit offline/paper validation harness or documented run flag for baseline measurement that cannot be confused with production trading behavior.

**Acceptance Criteria**  
- Baseline and multi-lane metrics can be generated reproducibly.
- Validation artifacts include evaluated candidates, paper trades, frequency, acceptance rate, and blend pass/block counts.
- The harness is paper/offline only and cannot enable live bypass behavior.

**Notes**  
This should be validation tooling, not a production bypass.

---

### PROFIT-STRUCT-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-STRUCT-001 |
| **Title** | Structural prior recompute may lag initial market-cache availability |
| **Category** | Structural Lane / Timeliness |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | MEDIUM |
| **Owner** | Shared |
| **Depends On** | S3.2 |
| **Blocks** | Early-session structural participation confidence |

**Description**  
`_structural_recompute_task()` runs `StructuralTask.run_periodic()` against `self.matcher._cache._markets`. If the first run occurs before cache warmup has populated active markets, structural participation may be delayed until the next hourly interval.

**Why it matters to profitability / safety / reliability**  
Early-session decisions could be evaluated without structural context even when dossiers become available soon after startup, weakening the multi-lane architecture during the first hour.

**Evidence / Source**  
- `main.py:1555-1560` passes the current market cache directly.
- `tasks/structural_task.py:139-147` sleeps for `interval_seconds` after each run.
- Runtime logs have shown market cache warmup taking minutes under some sessions.

**Proposed Fix**  
Measure first-run timing and, if confirmed, trigger an additional recompute after market-cache warmup or after first dossier activity without creating slow-lane-only trades.

**Acceptance Criteria**  
- Test or runtime trace shows structural task handles empty initial market cache without waiting a full interval after cache population.
- No structural recompute changes trading decisions directly.
- Telemetry documents recompute trigger timing.

**Notes**  
Keep this in orchestration; do not move structural logic into `main.py`.

**Implementation Notes** (2026-04-20)  
Fixed the initial empty-cache lag by making `_structural_recompute_task()` wait until the market cache is non-empty before starting the hourly `StructuralTask.run_periodic()` loop. This prevents an empty startup pass from delaying the first useful structural recompute by a full interval. The provider now returns a defensive list copy of the live cache. Validation: `.venv/bin/pytest tests/test_main_pipeline.py::test_structural_recompute_waits_for_non_empty_market_cache` (passed).

---

### PROFIT-CAL-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-CAL-001 |
| **Title** | Calibration outcome feedback is not proven end-to-end |
| **Category** | Calibration / Belief Quality |
| **Severity** | HIGH |
| **Status** | COMPLETE (2026-04-24, v0.29.47) |
| **Priority** | NOW (post-window) |
| **Owner** | Shared |
| **Depends On** | S4.5c close (no runtime changes during active window) |
| **Blocks** | ROADMAP P4.2 (calibration review, structurally blocked — no `CALIBRATION_CHECK` events can exist without this fix); transitively P4.3 (live trading authorization) |

**Description**  
`CalibrationTask` is constructed and passed to `BlendTask` for scaling, but the expanded audit did not confirm a complete feedback loop from resolved outcomes into calibration updates and `CALIBRATION_CHECK`-style validation events.

**Why it matters to profitability / safety / reliability**  
Without verified calibration feedback, confidence values can drift, causing the bot to over-trust weak lanes or under-trade genuinely profitable signals.

**Evidence / Source**  
- `main.py:371-376` constructs `CalibrationTask` and injects it into `BlendTask`.
- Contract Section 13 includes calibration and readiness validation criteria.
- No live S4.5 evidence has yet proven calibration participation over resolved outcomes.

**Proposed Fix**  
Trace resolved paper outcomes through calibration and add validation output if the feedback loop is incomplete.

**Acceptance Criteria**  
- Resolved paper trades or blocked outcomes can be associated with lane predictions for calibration review.
- Calibration events/metrics are observable during validation windows.
- Tests cover at least one calibration update path or explicitly document why no update is expected yet.

**Notes**  
Do not tune scaling factors as part of this debt item.

**Priority Elevation Note** (2026-04-23)  
Severity raised MEDIUM → HIGH and Priority raised MEDIUM → NOW (post-window) after follow-on analysis of ROADMAP Phase 4 dependencies. ROADMAP P4.2 ("Calibration review: est distribution vs resolved outcomes") has an expected outcome of "Calibration curve documented; over/underconfidence measured" — an output that requires `CALIBRATION_CHECK` events to exist in the first place. Without this fix, P4.2 cannot complete, which means P4.3 (live trading authorization) cannot be reached by transitive dependency. This elevates PROFIT-CAL-001 from "observation gap" to "pre-live-trading blocker." Execution is sequenced as the first action after S4.5c closes (earliest 2026-04-26); full implementation design in [`docs/plans/profit_cal_001_calibration_wiring.md`](plans/profit_cal_001_calibration_wiring.md) (Path A: schema migration + emission at resolve time). Header Open-counts updated: HIGH 3 → 4, MEDIUM 1 → 0.

**Validation Notes** (2026-04-23)  
End-to-end trace confirms the feedback loop is **wired but incomplete**. Satisfies the acceptance criterion's "*explicitly document why no update is expected yet*" branch. Components that exist: `log_calibration_check` (`utils/logger.py:1242`), `CalibrationTask.record_calibration_check` (`tasks/calibration_task.py`), pure-function Brier/drift/scaling state machine (`analysis/calibration_monitor.py`), schema tests (`tests/test_calibration_check_schema.py`), `CalibrationTask` construction + injection into `BlendTask` (`main.py:435-438`), and `BlendTask` consuming `get_scaling_factor(lane)`. Missing glue: **zero runtime callers of `log_calibration_check` or `CalibrationTask.record_calibration_check`**. Repo-wide grep (2026-04-23) returns only definitions, schema tests, and a single script comment — no runtime call site from any resolution or trade path. Specifically, `trading/paper_trader.py:resolve_market` calls `log_paper_resolution` but never `log_calibration_check`. Consequence: `CalibrationTask._state` is permanently empty, `get_scaling_factor(lane)` always returns `1.0` (no-op scaling), and `BlendTask` consumes a neutral factor regardless of how many markets resolve. Contract Section 13 item 6 is therefore *silently* unverifiable — vacuously satisfied not because of "zero resolutions in window" (prior pre-assessment framing) but because the emission site does not exist. This is observation-unsafe to fix; the runtime wire-up (`resolve_market` → `log_calibration_check` → `CalibrationTask.record_calibration_check`) must wait until after S4.5c closes. Deferred action item: add emission at the resolution boundary, backfill one end-to-end test asserting `CALIBRATION_CHECK` is written and consumed, and cross-link to S4.5c criterion 6 once verified.

**Resolution Notes** (2026-04-24, v0.29.47 — commits `186b495`, `74649c6`)
Feedback loop now complete end-to-end per the Path A design in [`docs/plans/profit_cal_001_calibration_wiring.md`](plans/profit_cal_001_calibration_wiring.md). Implementation delivered across two commits:

- **Zone 1 + 2** (`186b495`): six nullable columns added to the `paper_trades` table via the existing `_migrate_db` pattern (`fast_lane_p`, `fast_lane_confidence`, `accumulation_p`, `accumulation_confidence`, `structural_p`, `structural_confidence`). Per-lane estimates are already propagated through `analysis.signal_meta` from `BlendTask`'s `TradeCandidate` construction (`tasks/blend_task.py:425-440`) — no `/trading` adapter extension required; `record_trade` reads directly from `signal_meta`. This supersedes the design note's §3.2 Zone 2 sub-step and reduces the overall change footprint. INV-4 purity preserved (no `/analysis` changes). A `_lane_float` helper guards against non-dict `signal_meta` and non-numeric values so legacy or test-mock signal paths don't crash the INSERT.

- **Zone 3 + 4 + 5** (`74649c6`): `PaperTrader.resolve_market` is now async and fans out one `CALIBRATION_CHECK` event per populated lane to both emission paths (`trade_log.log_calibration_check` + `CalibrationTask.record_calibration_check`). Blocking DB work is factored into `_resolve_market_sync`, dispatched via `asyncio.to_thread` so the MAC-ASYNC-002 off-loop invariant is preserved. `CalibrationTask` is now injected into `PaperTrader` via a constructor kwarg and the same instance is wired into `BlendTask`. `CalibrationTask` errors during emission are logged and swallowed — the DB resolution has already committed before the calibration path runs, so a calibration-side failure cannot corrupt resolved-trade state. Historical rows (pre-v0.29.47, null lane columns) emit zero `CALIBRATION_CHECK` events, preserving backward compatibility.

- **Test coverage** (`tests/test_paper_trader.py::TestCalibrationEmission`, 7 new tests): column population from `signal_meta`; NULL handling for historical rows; three-lane fan-out; `CalibrationTask._state.lanes` update per lane; swallowed-error survival (resolution still committed); migration idempotence. 13 existing `resolve_market` test call sites updated for the async API. Full suite: 1059 passed, 1 skipped, 0 failures.

Acceptance criteria from the design note §4: (1) status moved OPEN → COMPLETE with validation notes; (2) Zone 5 test added and passing; (3) full test suite clean; (4) ROADMAP P4.2 blocker-cross-reference can be struck; (5) `CalibrationTask.get_calibration_summary()` now returns non-empty per-lane stats once resolved paper trades with populated `signal_meta` land — this is already the case in the `TestCalibrationEmission` unit test; the live-traffic observation (≥1 `CALIBRATION_CHECK` in `logs/trades/live/trades.jsonl` after the first paper-trade resolution post-fix) remains a production-soak observation, tracked in the v0.29.47 `CHANGELOG` entry. Fix status: **closed against acceptance criteria**; production-soak observation pending natural paper-trade resolutions (blocked behind P0-GATE LLM market-anchoring, which is a separate debt item). Contract Section 13 item 6 is now substantively verifiable rather than vacuously satisfied.

---

### PROFIT-EXEC-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EXEC-001 |
| **Title** | Fade tweet and price-fade paths still call executor directly |
| **Category** | Execution Boundary / Safety |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | S3.4, S3.5 |
| **Blocks** | No-bypass assurance |

**Description**  
Normal news candidates route through `BlendTask`, but `_on_fade_tweet()` and `_process_price_fade()` still call `self.executor.execute(analysis)` directly. If these are trade-producing paths, they bypass `BLEND_DECISION` and readiness enforcement.

**Why it matters to profitability / safety / reliability**  
The architecture says fast lane triggers blend evaluation and candidates must pass readiness before executor submission. Direct trade-like paths risk unobservable, unblended paper/live behavior.

**Evidence / Source**  
- `main.py:802` executes fade-tweet analysis directly.
- `main.py:991` executes price-fade analysis directly.
- `docs/IMPLEMENTATION_CONTRACT.md:280` requires readiness-gate validity before executor submission.

**Proposed Fix**  
Classify fade paths as disabled diagnostics, non-trading signals, or route them through the same blend/readiness meeting point with explicit contract approval.

**Acceptance Criteria**  
- No trade-producing runtime path reaches executor without either a `BLEND_DECISION` or documented contract exemption.
- Tests assert normal, fade-tweet, and price-fade paths cannot silently bypass readiness if enabled.
- Existing executor safety gates remain intact.

**Notes**  
This is a boundary integrity issue, not a request to remove useful fade diagnostics.

**Implementation Notes** (2026-04-20)  
Resolved direct executor bypasses by routing fade-tweet and price-fade `SignalAnalysis` objects through the same `BlendTask`/readiness path used by normal news candidates. The shared routing helper attaches trigger evidence metadata, emits `BLEND_DECISION` through `BlendTask`, and only lets the existing trading-queue consumer call the executor for approved candidates. Price-fade evidence now uses the `market` source class. Validation: `.venv/bin/pytest tests/test_main_pipeline.py tests/test_executor.py tests/test_blend_task.py` (125 passed).

---

### PROFIT-STARTUP-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-STARTUP-001 |
| **Title** | Startup warmup and cache-empty periods reduce validation and trading uptime |
| **Category** | Runtime Reliability / Timeliness |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | MEDIUM |
| **Owner** | Shared |
| **Depends On** | — |
| **Blocks** | Reliable long-run validation windows |

**Description**  
Runtime logs show discovery and structural workflows can encounter empty market caches during startup. Long cache warmups reduce the effective observation window and can make early feed events ineligible for full multi-lane processing.

**Why it matters to profitability / safety / reliability**  
If the bot spends a meaningful fraction of a validation or trading session without active market context, it may miss opportunities or understate lane participation.

**Evidence / Source**  
- `main.py:1138-1144` market refresh sleeps after each cycle.
- `main.py:1339` subreddit discovery waits for market cache warmup.
- Recent logs included `[DISCOVERY] Market cache empty, skipping discovery pass` during startup.

**Proposed Fix**  
Measure startup warmup duration and cache-empty rates, then add observability or startup gating if the delay materially affects trade windows.

**Acceptance Criteria**  
- Startup report includes market-cache warmup duration and first non-empty cache timestamp.
- S4.5 metrics distinguish wall-clock runtime from effective multi-lane runtime.
- No decision logic changes are made without separate approval.

**Notes**  
This is especially relevant to short paper-validation windows.

**Implementation Notes** (2026-04-20)
Added startup observability that records the first non-empty market cache timestamp, wall-clock seconds since boot, and an explicit `effective_multi_lane_runtime_start=true` marker. Discovery passes that still see an empty cache now include a monotonically increasing empty-cache count, seconds since startup, and whether effective multi-lane runtime has started. This is instrumentation only; no routing, analysis, or trading logic changed. Validation: `.venv/bin/pytest tests/test_main_pipeline.py::test_refresh_market_cache_once_logs_startup_warmup_duration tests/test_main_pipeline.py::test_structural_recompute_waits_for_non_empty_market_cache` (2 passed).

---

### PROFIT-CFG-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-CFG-001 |
| **Title** | Remove deprecated `KALSHI_GEOPOLITICAL_SERIES` dead-code allowlist from `config.py` |
| **Category** | Code Hygiene / Config Cleanup |
| **Severity** | LOW |
| **Status** | OPEN |
| **Priority** | LATER |
| **Owner** | Claude |
| **Depends On** | Stage 5 Phase 2 (P2.2) 72-hour paper-mode observation window closing |
| **Blocks** | — |

**Description**  
`config.py:475-489` defines `KALSHI_GEOPOLITICAL_SERIES`, a Kalshi series-ticker allowlist (`KXUKR`, `KXINTL`, `KXMIDEAST`, etc.). The list was deprecated in commit `60cb30c5` (2026-03-11) when the market cache switched to series-title keyword discovery via `_GEO_SERIES_KEYWORDS` in `analysis/market_matcher.py`. The list has been dead code for ~42 days but remains defined with a self-documenting `NOTE` comment block (`config.py:475-478`).

**Why it matters to profitability / safety / reliability**  
Low direct impact. Pure dead code; no runtime path reads the symbol. The residual risk is that a future contributor mistakes the list for a live filter and resurrects it, silently narrowing market discovery. The `CLAUDE.md:52` gotcha entry exists specifically to prevent that. Deleting the dead definition removes the source of the confusion at its origin.

**Evidence / Source**  
- `config.py:475-489` — `NOTE` comment block + dead list definition.
- Git history:
  - `60cb30c5` (2026-03-11) — deprecated consumer, added the `NOTE` comment.
  - `e3da7962` (2026-04-11) — dead-write: added `KXPRESGELECT`, `KXNK`, `KXNATO` to the list 31 days *after* the consumer was removed.
- Repo-wide scan (2026-04-22): zero imports, zero attribute access on `cfg` / `config`, zero `from config import *` wildcards, zero `.env` references, zero dynamic `getattr` / `hasattr` lookups. Only self-reference in `config.py` + a warning in `CLAUDE.md`.
- Replacement: `_GEO_SERIES_KEYWORDS` in `analysis/market_matcher.py`; the separate `MARKET_SERIES_BLOCKLIST_PREFIXES` list in `config.py` is actively used (imported by `feeds/search_news_monitor.py:26`) and must be preserved.

**Proposed Fix**  
Delete `config.py:475-489` (the `NOTE` comment block and the `KALSHI_GEOPOLITICAL_SERIES = [...]` list). Preserve the `CLAUDE.md:52` warning entry — it remains useful guidance for future agents even with the symbol gone.

**Acceptance Criteria**  
- `config.py` no longer defines `KALSHI_GEOPOLITICAL_SERIES`.
- `make lint` (ruff) passes.
- `pytest tests/` passes with no new failures vs. pre-removal baseline.
- `CLAUDE.md:52` warning text is preserved verbatim.

**Notes**  
Deferred until the Stage 5 Phase 2 (P2.2) 72-hour paper-mode observation window closes (opened 2026-04-22 per commit `b86c624`). Rationale: the change is provably safe (no runtime path reads the symbol), but editing `config.py` during an active observation window is avoided as a general rule even when the specific diff is inert. Execute after the window closes.

---

### PROFIT-SOURCE-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-SOURCE-001 |
| **Title** | Reddit intake is degraded-permanent; Reddit OAuth is externally blocked |
| **Category** | Intake / Source Availability |
| **Severity** | MEDIUM |
| **Status** | OPEN |
| **Priority** | NOW (mitigation is cheap; unblock is externally gated) |
| **Owner** | Shared |
| **Depends On** | Reddit Responsible Builder Policy review (externally blocked; app submitted, no response) |
| **Blocks** | — (Reddit-unique signal assessed as thin per `docs/plans/news_sources_evaluation.md` §7) |

**Description**  
Reddit intake runs in public-JSON mode because OAuth credentials are not available — the operator submitted an application per Reddit's Responsible Builder Policy and has received no response. Public-JSON polling is rate-limited per-IP and triggers structural 403 storms; on 2026-04-22 the 403 storm tripped `reddit_monitor.py`'s global circuit breaker within ~11 seconds of startup ("100% of subreddits failed (1/1), suspending all Reddit polling for 30m"). The circuit-breaker behavior is the *intended* response to 403 storms, not a bug — but it means Reddit contributes effectively zero signal whenever the circuit is open, which is most of the time during cold polling cycles.

**Why it matters to profitability / safety / reliability**  
Reddit contributes a small but non-zero share of the signal mix when it works. Losing it permanently is a coverage reduction, not a correctness or safety issue. The full evaluation in `docs/plans/news_sources_evaluation.md` §7 concludes Reddit-unique content is thin (most is wire-service repost; analytical content is slower than ISW/CSIS RSS; firsthand-witness content is replaceable by Bluesky/Mastodon/Telegram when those integrations are authorized). The residual risk is that downstream diagnostics (`source_scorecard`, feedback loops that attribute signal to Reddit posts) silently report a distorted source mix if Reddit is treated as "active" while it's actually degraded.

**Evidence / Source**  
- `logs/app/bot.log` 2026-04-22T11:16:43 UTC — "Reddit access denied for r/ArmedConflicts (403) -- backing off 120s", followed by 30+ similar lines across all 20 polled subreddits within 10 seconds, followed by "Reddit global circuit open -- suspending all Reddit polling for 30m".
- `feeds/reddit_monitor.py:19` starts with the log message "Reddit monitor started (public JSON -- degraded, expect rate limits). Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env for OAuth2" — the module itself acknowledges the degraded state at every startup.
- Operator confirmation (2026-04-23): Reddit app submitted via Responsible Builder Policy intake; no response. Treat as permanently blocked for planning purposes.

**Proposed Fix**  
Two-track strategy per `docs/plans/news_sources_evaluation.md` §7.2:

*Track A — mitigation (cheap, post-S4.5c close):*
1. Trim `REDDIT_SUBREDDITS` polling pool from 20 to a curated 2-3 (candidates: `r/ArmedConflicts`, `r/CredibleDefense`, plus 1-2 region-specific rotated in by `subreddit_selector.py`) to reduce the 403-storm attack surface.
2. Downgrade Reddit-related log lines at startup from INFO to DEBUG where they're not actionable, to stop polluting the `bot.log` signal.
3. Ensure `SOURCE_HEALTH` telemetry (planned in the same evaluation) distinguishes "Reddit circuit open (expected)" from "Reddit circuit open (unexpected)" — only the latter should alert.

*Track B — unblock (gated externally):*
1. Do not spend further engineering effort on Reddit OAuth until Reddit responds to the pending app.
2. If Reddit approves the app, migrate `reddit_monitor.py` to OAuth2 — that's a config-credential change plus a small code change to the auth flow, not a re-architecture.
3. If Reddit denies or stays silent past a configurable patience window (suggest 90 days post-submission), formally deprecate Reddit intake and repurpose the polling loop for a replacement (Bluesky journalist timeline is the leading candidate per Appendix A Tier 2).

**Acceptance Criteria**  
- Track A mitigations deployed post-S4.5c close and observed in `bot.log` (circuit-open events drop significantly in volume; no unexpected-outage false positives in `SOURCE_HEALTH` emissions).
- This debt-log entry transitions to COMPLETE under either (a) Track B succeeds and OAuth is active, OR (b) Reddit formally deprecated and replacement source integrated.
- `docs/plans/news_sources_evaluation.md` §7.2 steps 1-5 are executed or consciously re-deferred.

**Notes**  
Do not treat this as a go-live blocker. Per `docs/plans/news_sources_evaluation.md` §6, the operator-confirmed priority is correctness over velocity, and the Reddit-unique signal is thin enough that going live without Reddit is acceptable provided the source mix is honestly reported. The go-live blocker is `PROFIT-CAL-001`, not this item.

---

### PROFIT-LLM-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-LLM-001 |
| **Title** | Signal-analyzer / governance-agent LLM unification deferred until after Phase 2 lands |
| **Category** | LLM Layer / Operational Reliability |
| **Severity** | LOW |
| **Status** | OPEN |
| **Priority** | AFTER (post governance Phase 2 close) |
| **Owner** | Operator |
| **Depends On** | Governance Phase 2 implementation closing (Task 27 — VERSION/CHANGELOG bump) |
| **Blocks** | — (no downstream item; Phase 2 ships fine without unification) |

**Description**
Two distinct Ollama model strings are configured side-by-side after governance Phase 2:

- Signal analyzer (existing trading): `OLLAMA_MODEL` defaults to `qwen2.5:7b` per `config.py:1077`.
- Governance agent (Phase 2): `GOVERNANCE_LLM_MODEL` set to `qwen3:8b` on MacBook (via the launchd plist landing in Task 25), `qwen3:14b` on Mac Studio.

Ollama runs single-model by default — a request for a model that isn't loaded unloads the previous one and loads the new one (5 min idle TTL). So in steady state at most one model is resident; the runtime cost is a ~5-10s cold-load each time governance and signal analysis fire close in time. RAM is safe on the MacBook's 18GB.

The deferred decision is whether to bump `OLLAMA_MODEL=qwen3:8b` so signal analysis upgrades to the same model the governance agent already uses, eliminating swap latency and unifying calibration. The agent decided on 2026-04-25 to **defer this past Phase 2** rather than bundle it into the governance ship, after operator pushback against the original "wait for Mac Studio" recommendation made the actual risks explicit.

**Why it matters to profitability / safety / reliability**
A unilateral model swap on the signal-analyzer path carries three concrete risks that need observation, not just a unit-test pass:

1. **JSON-parse reliability.** Different LLMs wrap JSON output in different preamble styles. The existing parser uses `JSONDecoder.raw_decode()` scanning each `{` (per CLAUDE.md "Critical Gotchas / Signal analysis") because some prior model emitted preambles like `Sure, here is the analysis: {...}`. qwen3:8b may emit a different preamble or new failure modes. A regression here looks fine in unit tests but breaks in production when the parser silently drops a malformed response.
2. **Probability calibration drift.** qwen2.5:7b's "0.42" doesn't necessarily mean the same thing as qwen3:8b's "0.42". The bot's edge-threshold gating, Kelly sizing, and same-signal guards are all calibrated against what 2.5:7b currently produces. A more-confident model pushes more bets through the gate; a less-confident one starves it. Either drift would only show in cross-week comparisons of EV-gate pass-rate and decision distribution — not in any single test.
3. **Prompt-template fit.** The existing signal-analyzer prompt (in `analysis/signal_analyzer.py`) was written with one model's reasoning style in mind. qwen3 is a different generation — it could refuse, over-explain, ignore an instruction, or hit token limits differently.

These risks are observable but not catchable by tests alone. They need a baseline-comparison observation window in paper mode.

**Why deferral is the right call (not "wait for Mac Studio")**
The original agent recommendation was "wait until Mac Studio arrives" and was wrong-by-default rather than reasoned. The honest reason to defer past Phase 2:

- Bundling a model swap into Phase 2 makes any regression hard to attribute — was it the new governance code, or the new signal model?
- Phase 2's risk surface is already large enough (new agent, new prompt, new audit log, new launchd plists). Keeping the signal-analyzer path frozen during Phase 2 cutover means any issue surfaces against a known baseline.
- Once Phase 2 is closed and observed quiet for a few cycles, swapping `OLLAMA_MODEL=qwen3:8b` is a one-keystroke change with a one-keystroke revert. The cost of waiting is small (a few governance/signal coordination cold-loads per day). The cost of bundling is harder root-cause analysis if something goes wrong.

Hardware is not the gate. Both models fit comfortably on the 18GB MacBook (4.7GB on disk each, ~5-6GB resident); only `qwen3:14b` is too tight for MacBook (8-9GB on disk, 10-12GB resident with context — that's the Mac Studio model).

**Evidence / Source**
- `config.py:1077` — `ollama_model` field default factory `os.getenv("OLLAMA_MODEL", "qwen2.5:7b")`.
- `.env:84` — commented `OLLAMA_MODEL=qwen2.5:7b` line.
- Governance Phase 2 plan (`docs/superpowers/plans/2026-04-25-governance-agent-phase-2-plan.md`) Task 15 — `LocalQwenLLM` constructor default `qwen3:14b` with env-var override `GOVERNANCE_LLM_MODEL`; comment block flags `qwen3:8b` as the MacBook model and `qwen3:14b` as the Mac Studio model.
- CLAUDE.md "Critical Gotchas / Signal analysis" — JSON-extraction workaround documents past brittleness when changing model output behavior.
- Operator-pulled models on MacBook as of 2026-04-25: `qwen2.5:7b` (active for signal analysis), `qwen3.5:9b` (idle), `qwen3:8b` (newly pulled, will be used by governance agent).

**Proposed Fix**
After governance Phase 2 closes (PROFIT-LLM-001 transitions to `IN_PROGRESS` only after Task 27's VERSION/CHANGELOG commit lands):

1. Single-line `.env` change: set `OLLAMA_MODEL=qwen3:8b` (uncomment + update the existing line at `.env:84`).
2. Restart the bot and let it run paper mode for at least one full diurnal cycle (~24h) of news ingestion.
3. Compare the post-change cycle against the prior week's `bot.log` and trade-funnel diagnostics on:
   - JSON-parse error count in `signal_analyzer` log lines (must be ≤ baseline; ideally zero).
   - Distribution of `estimated_probability` outputs (should be roughly the same shape; gross shifts indicate calibration drift).
   - EV-gate pass-rate (rate of analyses that produce a candidate trade decision; should be within ±20% of baseline).
   - Bet-size distribution from any paper trades produced (gross shifts indicate confidence-calibration drift).
4. **Revert criteria — set `OLLAMA_MODEL=qwen2.5:7b` and re-open this item** if any of:
   - Any new JSON-parse error class shows up in logs that wasn't there pre-change.
   - EV-gate pass-rate moves outside ±20% of the pre-change weekly baseline.
   - Manual review of 5 random paper trades shows reasoning quality clearly worse than baseline.
5. **Promote criteria — mark COMPLETE** if all of:
   - One full 24h paper cycle produces zero new parse errors.
   - Probability-output distribution and EV-gate pass-rate stay within tolerance.
   - Manual review of 5 random paper trades shows reasoning quality at least equivalent.

**Acceptance Criteria**
- One of: (a) `.env` carries `OLLAMA_MODEL=qwen3:8b` with a commit referencing this item ID and at least 24h of paper-mode observation logged, OR (b) the operator consciously re-defers past 2026-Q3 with rationale in this item's Notes section, OR (c) the operator decides the unification is not worth doing (e.g. Mac Studio brings `qwen3:14b` and a different model topology).

**Notes**
This item is decision-track, not bug-track. It exists because the reasoning behind the deferral matters as much as the deferral itself — a future agent reading the file structure could otherwise see two model strings configured and treat it as an oversight instead of an intentional ordering decision. The operator-confirmed priority on 2026-04-25 was: ship Phase 2 cleanly first; unify second; never bundle.

If `PROFIT-CAL-001`'s calibration-feedback wiring lands before this item moves to `IN_PROGRESS`, the per-lane confidence scaling will detect calibration drift automatically — that strengthens the safety net for this swap and tightens the revert criteria above.

---

### MAC-ASYNC-001

| Field | Value |
|-------|-------|
| **ID** | MAC-ASYNC-001 |
| **Title** | `paper_trader.record_trade()` blocks event loop from async executor |
| **Category** | Async / Queue / Scheduling |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | MAC-TEST-001 |

**Implementation Notes** (2026-04-19)
`get_notional_bankroll()` (SQLite SELECT) was also called synchronously in the same `log.info()` line. Both calls were batched in a single `asyncio.to_thread(_record)` closure to avoid two separate thread dispatches and eliminate any race window between the write and the bankroll read. Fixed in `executor.py`. MAC-TEST-001 regression guard added in `test_executor.py:TestPaperExecutionAsync` — verifies `record_trade` is called from a non-event-loop thread. Committed as v0.29.21.

**Description**  
`executor.py:361` calls `self._paper.record_trade(analysis)` directly inside `async def _execute_paper()` without `asyncio.to_thread()`. `PaperTrader.record_trade()` is a synchronous method that executes an SQLite `INSERT`. This blocks the asyncio event loop for the duration of the DB write on every paper trade.

**Why This Is Platform-Sensitive**  
On Windows under NSSM, the service ran as a single-process loop with low concurrency pressure. On macOS as a developer process running under asyncio with concurrent task runners, event-loop stalls are more visible and have a wider blast radius (delayed news processing, missed price updates during stall window).

**Evidence / Source**  
- Audit findings R-1, A-1, A-2
- `trading/executor.py:361` — `_execute_paper()`
- `trading/paper_trader.py:462` — `record_trade()` signature (no `async`)

**Proposed Fix**  
```python
# executor.py _execute_paper()
trade_id = await asyncio.to_thread(self._paper.record_trade, analysis)
```

**Acceptance Criteria**  
- `_execute_paper()` wraps `record_trade()` in `asyncio.to_thread()`
- No event-loop blocking call remains in `_execute_paper()`
- Existing paper-trade tests continue to pass
- `MAC-TEST-001` test passes

**Notes**  
`PaperTrader` has `check_same_thread=False` on its connection, so it is safe to call from a thread-pool thread via `to_thread()`. No connection changes needed.

---

### MAC-ASYNC-002

| Field | Value |
|-------|-------|
| **ID** | MAC-ASYNC-002 |
| **Title** | `paper_trader` nightly/resolve calls block event loop from async task functions |
| **Category** | Async / Queue / Scheduling |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | MAC-TEST-001 |

**Description**  
Three additional synchronous paper trader calls are made directly from async methods in `main.py`:
- `main.py:998` — `self.paper.daily_summary()` inside `async def _daily_report_task()`
- `main.py:999` — `self.paper.generate_report()` inside `async def _daily_report_task()`
- `main.py:1076` — `self.paper.resolve_market(ticker, resolved_yes)` inside `async def _check_and_resolve()`

`generate_report()` performs a full table scan and string-builds hundreds of lines — the longest-running of the three, and the one most likely to cause a visible stall as the trade history grows.

**Why This Is Platform-Sensitive**  
Same as MAC-ASYNC-001. Windows NSSM single-process model masked this; macOS asyncio multi-task model exposes it.

**Evidence / Source**  
- Audit findings R-1, A-1, A-2
- `main.py:994–1000` — `_daily_report_task()`
- `main.py:1045–1079` — `_check_and_resolve()`

**Proposed Fix**  
```python
# _daily_report_task()
await asyncio.to_thread(self.paper.daily_summary)
report = await asyncio.to_thread(self.paper.generate_report)

# _check_and_resolve()
await asyncio.to_thread(self.paper.resolve_market, ticker, resolved_yes)
```

**Acceptance Criteria**  
- All three call sites wrapped in `asyncio.to_thread()`
- No synchronous paper trader method called from any async context without `to_thread()`
- `_daily_report_task` and `_check_and_resolve` tests pass

**Implementation Notes** (2026-04-19)  
Fixed in v0.29.22. Five blocking calls wrapped in `asyncio.to_thread()` in `main.py`:
- `_daily_report_task()`: `daily_summary()`, `generate_report()`, `report_path.write_text()` (file I/O)
- `_check_and_resolve()`: `_conn.execute(...).fetchall()` (direct DB query in lambda), `resolve_market()`, post-loop `get_notional_bankroll()`

`TestMainAsyncBlocking` in `tests/test_main_pipeline.py` adds 5 regression guard tests verifying each call is dispatched off the event loop thread.

**Notes**  
Assess whether `generate_report()` at scale (>1000 trades) creates a thread-pool saturation risk. If so, a dedicated thread executor should be considered — but that is a future item, not part of this fix.

---

### MAC-DB-001

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-001 |
| **Title** | `evidence_store._connect()` missing WAL journal mode |
| **Category** | Persistence / DB |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | MAC-TEST-002, MAC-DB-005 |

**Description**  
`tasks/evidence_store.py:221–225`: `_connect()` creates a fresh connection per DB operation with `PRAGMA foreign_keys = ON` but no `PRAGMA journal_mode=WAL`. With the default DELETE journal mode, a single writer blocks all other connections (readers and writers). `AccumulationTask` dispatches writes to different markets concurrently via `asyncio.to_thread()`; all of those threads compete for SQLite's global write lock at the OS level. Under a busy multi-market session (common during news events), writes serialize and can hit the 30-second timeout.

**Why This Is Platform-Sensitive**  
macOS APFS I/O patterns and asyncio task scheduling tend to produce more concurrent DB access than the Windows NSSM pattern (where one task ran at a time). The issue exists on both platforms but surfaces more readily on macOS.

**Evidence / Source**  
- Audit findings R-2, D-1
- `tasks/evidence_store.py:221–225` — `_connect()`
- `tasks/accumulation_task.py:208–211` — concurrent `to_thread()` dispatches

**Proposed Fix**  
```python
def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(str(self.db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
```

**Acceptance Criteria**  
- `_connect()` sets `journal_mode=WAL` and `synchronous=NORMAL`
- A `-wal` file appears next to the DB after the first write
- Concurrent write test (`MAC-TEST-002`) passes without `OperationalError`
- No existing DB schema or migration is broken

**Implementation Notes** (2026-04-19)  
Fixed in v0.29.23. Added `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` to `tasks/evidence_store.py:_connect()`. All 958 tests pass.

**Notes**  
WAL mode persists in the DB file after first write; subsequent connections inherit it. `synchronous=NORMAL` is safe with WAL (crash-safe with slightly relaxed fsync), and meaningfully faster than the default FULL.

---

### MAC-DB-002

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-002 |
| **Title** | `paper_trader` SQLite connection missing explicit timeout |
| **Category** | Persistence / DB |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`trading/paper_trader.py:189`: `sqlite3.connect(str(db_path), check_same_thread=False)` uses SQLite's default 5-second lock timeout. `evidence_store._connect()` uses `timeout=30.0`. If a background admin script or test holds the paper trade DB open during a resolve or report cycle, the paper trader will fail with `OperationalError` after 5 seconds while evidence_store would wait 30. The inconsistency makes failure behavior unpredictable.

**Why This Is Platform-Sensitive**  
macOS users are more likely to run `sqlite3 paper_trades.db` interactively to inspect trades. Windows users typically accessed the DB through the NSSM service logs only. Interactive access increases the probability of a live lock contention scenario.

**Evidence / Source**  
- Audit finding R-3, D-2
- `trading/paper_trader.py:189`

**Proposed Fix**  
```python
self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
```

**Acceptance Criteria**  
- `paper_trader` connection uses `timeout=30.0`
- Timeout is consistent with `evidence_store._connect()` timeout

**Implementation Notes** (2026-04-19)  
Fixed in v0.29.23. Added `timeout=30.0` to `sqlite3.connect()` call in `trading/paper_trader.py:189`. All 958 tests pass.

---

### MAC-DB-003

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-003 |
| **Title** | `paper_trader` connection has unnecessary `check_same_thread=False` |
| **Category** | Persistence / DB |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-ASYNC-001, MAC-ASYNC-002 |
| **Blocks** | — |

**Description**  
`trading/paper_trader.py:189`: `check_same_thread=False` disables SQLite's thread-safety guard. All current paper trader methods are synchronous and, after MAC-ASYNC-001/002 are fixed, will be invoked from `asyncio.to_thread()` worker threads. Since each `to_thread()` call dispatches to its own thread, a single shared connection with `check_same_thread=False` would then be legitimately accessed from different threads — which is actually the case that requires the flag.

Re-evaluate after MAC-ASYNC-001/002: if `to_thread` is used, the flag is required; if a per-call connection pattern is adopted, the flag can be removed. Do not remove this flag until the async usage pattern is finalized.

**Why This Is Platform-Sensitive**  
Flag was likely set during Windows development where threading model was different. Intent is now unclear.

**Evidence / Source**  
- Audit finding D-3
- `trading/paper_trader.py:189`

**Proposed Fix**  
After MAC-ASYNC-001/002: audit whether `_conn` is ever accessed from multiple threads simultaneously. If yes (via `to_thread()`), the flag is correct and this item closes as "no change needed." If no (single-threaded access), remove the flag to restore the safety guard.

**Acceptance Criteria**  
- Decision documented (flag needed or not) with rationale
- If removed: no `ProgrammingError` in any test

**Implementation Notes** (2026-04-20)  
Decision: `check_same_thread=False` is **required and correct**. After MAC-ASYNC-001/002, all paper trader method calls go through `asyncio.to_thread()`, which dispatches to arbitrary thread-pool worker threads. The single shared `_conn` is therefore legitimately accessed from different threads across calls. Removing the flag would cause `ProgrammingError` on the first `to_thread`-dispatched call. No code change needed. Closed as documented decision.

---

### MAC-DB-004

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-004 |
| **Title** | `paper_trader._migrate_db()` uses `executescript()` without explicit transaction guard |
| **Category** | Persistence / DB |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`trading/paper_trader.py:206` calls `self._conn.executescript(_DDL)`. `executescript()` implicitly commits any open transaction before running, which is documented Python behavior. If the DDL partially fails (e.g., disk full mid-migration), the DB may be left in a partially migrated state. On macOS this is unlikely but would be hard to diagnose.

**Why This Is Platform-Sensitive**  
On Windows under NSSM, DB initialization happened in a controlled startup environment. On macOS as a developer process, interrupted startups (Ctrl+C during init) are more common.

**Evidence / Source**  
- Audit finding D-4
- `trading/paper_trader.py:206`

**Proposed Fix**  
Replace `executescript()` with individual `execute()` calls inside an explicit `BEGIN`/`COMMIT` block, or use a context manager: `with self._conn: self._conn.execute(ddl_statement)`.

**Acceptance Criteria**  
- DDL is wrapped in an explicit transaction
- A simulated mid-migration failure leaves the DB in a recoverable state

**Implementation Notes** (2026-04-19)  
Replaced `self._conn.executescript(_DDL)` / `self._conn.commit()` in `initialize()` with a `with self._conn:` block that splits `_DDL` on `;` and calls `self._conn.execute()` for each non-empty statement. The context-manager form issues a single `BEGIN`/`COMMIT` around all CREATE TABLE statements, so a mid-DDL failure rolls back cleanly. 961 tests passed.

---

### MAC-DB-005

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-005 |
| **Title** | No WAL checkpoint task — WAL files grow unbounded |
| **Category** | Persistence / DB |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-DB-001 |
| **Blocks** | — |

**Description**  
Once WAL mode is enabled (MAC-DB-001), SQLite writes go to a `-wal` file that is periodically checkpointed back to the main DB file. Without an explicit checkpoint task, the WAL file can grow large if the bot runs continuously without a restart (common on macOS dev machine that stays running). This increases startup time and can hit filesystem quotas on large datasets.

**Why This Is Platform-Sensitive**  
Windows NSSM service would restart the bot at least daily (after the scheduled task). macOS dev machine may run the bot continuously for weeks without restart.

**Evidence / Source**  
- Audit finding D-5

**Proposed Fix**  
Add a periodic checkpoint to `_log_maintenance_task()` or a dedicated DB maintenance task:
```python
conn.execute("PRAGMA wal_checkpoint(RESTART)")
```
Run at most once per day, after the nightly report cycle.

**Acceptance Criteria**  
- WAL checkpoint runs at least once per 24-hour period
- `-wal` file size remains bounded during continuous operation

**Implementation Notes** (2026-04-19)  
Added `PRAGMA wal_checkpoint(RESTART)` block to `_log_maintenance_task()` in [main.py](../main.py), just before the summary log. Opens a short-lived connection to `data/paper_trades.db`, runs the checkpoint, then closes it. Failures are caught and logged at WARNING so they never abort the broader maintenance sweep. Runs once per 24-hour maintenance cycle. Added `import sqlite3` to top-level imports.

---

### MAC-CLI-001

| Field | Value |
|-------|-------|
| **ID** | MAC-CLI-001 |
| **Title** | No macOS automation equivalent for `setup_daily_task.ps1` |
| **Category** | Shell / CLI / Environment |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`scripts/setup_daily_task.ps1` registers a Windows Scheduled Task using `Register-ScheduledTask` (Windows PowerShell API). There is no equivalent script for macOS. If the user expects the daily review to run on a schedule on macOS (as it did under Windows), it silently never fires. No error, no log, no alert.

**Why This Is Platform-Sensitive**  
Windows Scheduled Tasks are a Windows-only feature. macOS uses launchd (for persistent agents) or cron (for simple schedules). Neither is configured.

**Evidence / Source**  
- Audit findings M-1, S-1
- `scripts/setup_daily_task.ps1`

**Proposed Fix**  
Create `scripts/setup_launchd.sh` that:
1. Generates `~/Library/LaunchAgents/com.kalshibot.dailyreview.plist` using the repo's `.venv/bin/python` and `scripts/daily_review.py`
2. Calls `launchctl load` to activate it
3. Accepts a `--time HH:MM` parameter (default 09:00)

Alternatively, document the manual `crontab -e` one-liner in `README.md` as the minimum.

**Acceptance Criteria**  
- Running `bash scripts/setup_launchd.sh` (or `setup_launchd.sh --time 09:00`) on macOS installs and activates a launchd agent
- `launchctl list | grep kalshibot` confirms the agent is registered
- OR: README documents an explicit manual scheduling step for macOS users

**Implementation Notes** (2026-04-20)  
Created `scripts/setup_launchd.sh`. Script:
- Accepts `--time HH:MM` (default 09:00) and `--uninstall` flags
- Generates `~/Library/LaunchAgents/com.kalshibot.dailyreview.plist` using `StartCalendarInterval` with the specified hour/minute
- Uses `.venv/bin/python` from the repo root
- Calls `launchctl load` to activate immediately
- Logs stdout/stderr to `logs/launchd_daily_review*.log`
- Verified: `launchctl list | grep kalshibot` returns the agent.

---

### MAC-CLI-002

| Field | Value |
|-------|-------|
| **ID** | MAC-CLI-002 |
| **Title** | `daily_review.ps1` hardcodes Windows `.venv\Scripts\python.exe` path |
| **Category** | Shell / CLI / Environment |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-CLI-001 |
| **Blocks** | — |

**Description**  
`scripts/daily_review.ps1:20` constructs `$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"`. This path is Windows-specific. On macOS the virtualenv binary is at `.venv/bin/python`. If the PS1 is ever invoked on macOS (e.g., via PowerShell for Mac), it won't find the virtualenv and silently falls back to system `python`. `daily_review.py` is the portable implementation; the PS1 is only a launcher shim.

**Why This Is Platform-Sensitive**  
Windows virtualenv structure (`Scripts/`) differs from POSIX (`bin/`). Path separator is also Windows-specific (`\`).

**Evidence / Source**  
- Audit findings M-3, S-2
- `scripts/daily_review.ps1:20`

**Proposed Fix**  
After MAC-CLI-001 is done, add a note to `daily_review.ps1` header: "Windows only. macOS users: use `scripts/daily_review.sh` or run `python scripts/daily_review.py` directly." No code change required in the PS1 itself.

**Acceptance Criteria**  
- `daily_review.ps1` has a clear Windows-only header comment
- macOS users can find the correct invocation without reading the PS1 body

**Implementation Notes** (2026-04-20)  
Added `# PLATFORM: Windows only.` header with macOS reference to both `scripts/daily_review.ps1` and `scripts/setup_daily_task.ps1`. MAC-DOC-002 also resolved by this change.

---

### MAC-FS-001

| Field | Value |
|-------|-------|
| **ID** | MAC-FS-001 |
| **Title** | NSSM service log cleanup code in `_log_maintenance_task()` is dead on macOS |
| **Category** | Filesystem / Paths |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | MAC-DOC-001 |

**Description**  
`main.py:1193–1210`: The maintenance task globs for `logs/service/service_stderr-*.log`, `service_stdout-*.log`, `ollama_stderr-*.log`, `ollama_stdout-*.log` and applies 30-day retention. These paths were created by the Windows NSSM service runner. On macOS none of these files exist; the globs return empty and no cleanup occurs. The code is dead but still runs on every maintenance cycle, creating a false impression that NSSM log management is active.

**Why This Is Platform-Sensitive**  
NSSM (Non-Sucking Service Manager) is Windows-only. The bot no longer runs under NSSM on macOS.

**Evidence / Source**  
- Audit findings M-2, Doc-1
- `main.py:1153–1155, 1193–1210`

**Proposed Fix**  
Wrap the NSSM block in a platform guard:
```python
if sys.platform == "win32":
    # NSSM service log retention (Windows only)
    ...
```
Or delete the block entirely with a comment in the commit message noting it was Windows NSSM-specific.

**Acceptance Criteria**  
- NSSM cleanup block does not execute on macOS
- macOS maintenance task logs do not reference `service_*` paths
- If kept under `sys.platform == "win32"`, code is covered by a comment explaining NSSM context

**Implementation Notes** (2026-04-20)  
Wrapped the NSSM service log archive block in `if sys.platform == "win32":` with an inline comment explaining the Windows-only context. Also updated the docstring retention table to annotate all three NSSM entries as `(Windows only)`. MAC-DOC-001 is also resolved by this change.

---

### MAC-LOG-001

| Field | Value |
|-------|-------|
| **ID** | MAC-LOG-001 |
| **Title** | `TradeLogStore._rotate_live_to_archive()` silently falls back on `PermissionError` on macOS |
| **Category** | Logging / Runtime Lifecycle |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`utils/logger.py:413–427`: `_rotate_live_to_archive()` tries `os.replace()` first. On `PermissionError` it falls back to `shutil.copyfileobj()` + truncate. On Windows, `PermissionError` during a rename is expected (file held open), so the fallback is load-bearing. On macOS, `PermissionError` indicates a real access control problem (filesystem permissions, sandboxing, or a bug). The fallback silently succeeds, masking the real error, and the trade log may be in an ambiguous state (partially copied).

**Why This Is Platform-Sensitive**  
Windows PermissionError on rename = file locked (expected). macOS PermissionError on rename = actual permission denied (should be fatal).

**Evidence / Source**  
- Audit finding L-6, finding 2.5
- `utils/logger.py:413–427`

**Proposed Fix**  
```python
try:
    os.replace(str(src), str(dst))
except PermissionError:
    if sys.platform == "win32":
        # Windows: file may be held open by another process; copy+truncate instead
        _copy_truncate(src, dst)
    else:
        log.error("TradeLogStore: permission denied rotating %s → %s", src, dst)
        raise
```

**Acceptance Criteria**  
- On macOS, `PermissionError` during trade log rotation raises and logs the error
- On Windows, the copy+truncate fallback is preserved
- Trade log is never silently left in a partial state

**Implementation Notes** (2026-04-20)  
Added `sys` import to `utils/logger.py`. `_rotate_live_to_archive()` now checks `sys.platform != "win32"` before taking the copy+truncate fallback path: on macOS/Linux the `PermissionError` is re-raised; on Windows the fallback is preserved.

---

### MAC-PLAT-001

| Field | Value |
|-------|-------|
| **ID** | MAC-PLAT-001 |
| **Title** | `_RuntimeInstanceGuard` uses `os.name == "nt"` instead of `sys.platform == "win32"` |
| **Category** | Python / Platform Interaction |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`main.py:271, 279`: Platform detection uses `os.name == "nt"`. This works correctly in practice (both are True on Windows only), but `sys.platform == "win32"` is the idiomatic, more explicit, and more widely recognized check in the Python ecosystem. `os.name == "nt"` is also True on Cygwin/MinGW, which are edge cases.

**Why This Is Platform-Sensitive**  
Style/convention item. Not a runtime bug, but inconsistent with the portability rules in the project guidelines.

**Evidence / Source**  
- Audit finding P-2
- `main.py:271, 279`

**Proposed Fix**  
Replace `os.name == "nt"` with `sys.platform == "win32"` at both call sites.

**Acceptance Criteria**  
- Both `os.name == "nt"` guards replaced with `sys.platform == "win32"`
- Instance guard tests pass on macOS

**Implementation Notes** (2026-04-20)  
Both `os.name == "nt"` guards in `_lock_handle()` and `_unlock_handle()` replaced with `sys.platform == "win32"` using `replace_all`. `sys` was already imported in main.py.

---

### MAC-TEST-001

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-001 |
| **Title** | No test verifies paper trader calls are non-blocking from async context |
| **Category** | Tests |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-ASYNC-001, MAC-ASYNC-002 |
| **Blocks** | — |

**Implementation Notes** (2026-04-19)
Written as part of MAC-ASYNC-001 fix. `TestPaperExecutionAsync` in `tests/test_executor.py` contains two tests: `test_record_trade_called_off_event_loop_thread` (thread-name check that fails if `record_trade` reverts to a direct call) and `test_execute_paper_returns_correct_trade_id_and_logs` (end-to-end functional check). MAC-ASYNC-002 is now also COMPLETE (v0.29.22); `TestMainAsyncBlocking` in `tests/test_main_pipeline.py` provides the MAC-ASYNC-002 regression guard (5 tests).

**Description**  
After MAC-ASYNC-001/002 are fixed, there is no regression guard to prevent a future developer from accidentally reverting to a direct synchronous call. Without a test, the fix is invisible to CI. The event-loop blocking bug would be reintroduced silently.

**Why This Is Platform-Sensitive**  
Tests were written under the Windows NSSM model where all tasks ran synchronously in sequence; async event-loop blocking was not a concern.

**Evidence / Source**  
- Audit finding T-1

**Proposed Fix**  
Add a test in `tests/test_executor.py` (or a new `tests/test_paper_trader_async.py`) that:
1. Instruments the event loop with a timing probe
2. Calls `_execute_paper()` via `asyncio.run()`
3. Asserts that the event loop was not blocked for more than a small threshold (e.g., 50ms)

Alternatively: assert via mock that `asyncio.to_thread` was called with `paper.record_trade` as the argument.

**Acceptance Criteria**  
- Test exists that would fail if `record_trade` is called without `to_thread()`
- Test passes after MAC-ASYNC-001 fix

---

### MAC-TEST-002

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-002 |
| **Title** | No integration test for `evidence_store` concurrent multi-market writes |
| **Category** | Tests |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-DB-001 |
| **Blocks** | — |

**Description**  
After MAC-DB-001 adds WAL mode, there is no test that exercises concurrent writes and would catch a regression (e.g., someone removing the PRAGMA or adding a lock that serializes everything). There is also no test that would have caught the pre-fix contention issue.

**Why This Is Platform-Sensitive**  
macOS asyncio scheduling is more aggressive about parallelism than the Windows NSSM single-process model.

**Evidence / Source**  
- Audit finding T-2

**Proposed Fix**  
Add `tests/test_evidence_store_concurrency.py`:
1. Create an `EvidenceStore` backed by a temp DB
2. Fire 20 concurrent `asyncio.gather()` writes to 20 different market tickers
3. Assert all 20 writes succeed (no `OperationalError: database is locked`)
4. Assert each dossier is readable after the writes

**Acceptance Criteria**  
- 20 concurrent writes complete without `OperationalError`
- Test fails if WAL mode is removed (verify by temporarily removing the PRAGMA and confirming failure)

**Implementation Notes** (2026-04-20)  
Added `tests/test_evidence_store_concurrency.py` with three tests:
- `test_concurrent_writes_to_20_markets_no_operational_error`: 20 concurrent `asyncio.gather()` writes to distinct tickers; asserts all 20 dossiers are readable and each has 1 evidence record.
- `test_concurrent_writes_same_market_are_serialised`: 10 concurrent writes to the same ticker; asserts per-market lock serialises them correctly (all 10 persist).
- `test_wal_mode_is_active_after_first_write`: reads `PRAGMA journal_mode` directly and asserts `wal`; fails if MAC-DB-001 PRAGMA is removed.

---

### MAC-TEST-003

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-003 |
| **Title** | No test for non-clean shutdown followed by restart with stale lock file |
| **Category** | Tests |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`_RuntimeInstanceGuard` uses a lock file (`instance.lock`) to prevent duplicate bot instances. If the bot is killed with SIGKILL (common on macOS during forced Finder quit or OOM kill), the lock file may not be cleaned up. On subsequent restart, the guard must detect that the lock is stale (the previous PID is gone) and allow the new instance to proceed. There is no test that simulates this scenario.

**Why This Is Platform-Sensitive**  
On Windows under NSSM, the service manager controlled process lifecycle and SIGKILL was rare. On macOS as a developer process, SIGKILL (via Activity Monitor) or OOM termination is more common.

**Evidence / Source**  
- Audit finding T-4

**Proposed Fix**  
Add `tests/test_instance_guard.py`:
1. Create a lock file with a PID that does not exist
2. Instantiate `_RuntimeInstanceGuard`
3. Assert it acquires the lock successfully (stale lock is released)
4. Clean up

**Acceptance Criteria**  
- Guard correctly detects and clears a stale lock file (dead PID)
- Test passes on macOS

**Implementation Notes** (2026-04-19)  
Added `tests/test_instance_guard.py` with 3 tests:
- `test_guard_acquires_when_lock_file_absent`: baseline — no prior lock file.
- `test_guard_acquires_over_stale_pid_content`: writes a lock file with PID 999999999 (guaranteed dead), asserts `acquire()` returns `True` and overwrites the file with the current PID. This is the core MAC-TEST-003 scenario.
- `test_guard_describe_owner_returns_stale_info_before_acquire`: asserts `describe_owner()` surfaces the stale PID before any acquire.
All 3 pass on macOS.

---

### MAC-TEST-004

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-004 |
| **Title** | `_maybe_rotate_stale()` period-boundary edge case untested |
| **Category** | Tests |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`utils/logger.py` `_maybe_rotate_stale()` is tested for the case where `mtime` is well before the period start. The edge case where `mtime` is exactly equal to `period_start` (or within a few milliseconds) is not covered. The current implementation uses strict `<` so a file written exactly at the period boundary is not rotated, which is correct — but this is not asserted by any test.

**Why This Is Platform-Sensitive**  
macOS filesystem timestamps (APFS) have nanosecond resolution; a file written during a near-midnight rotation sequence could land exactly at the boundary epoch second.

**Evidence / Source**  
- Audit finding T-5
- `utils/logger.py:242–258`

**Proposed Fix**  
Add two cases to `tests/test_logger_rotation.py`:
1. `mtime == period_start` → no rotation (file is current period)
2. `mtime == period_start - 1` → rotation triggered (file is previous period)

**Acceptance Criteria**  
- Both edge cases pass
- Behavior at boundary is documented in the test

**Implementation Notes** (2026-04-19)  
Added two tests to `tests/test_logger_rotation.py`:
- `test_maybe_rotate_stale_does_not_rotate_at_exact_period_boundary`: sets mtime == period_start, asserts no archive is created (strict `<` keeps the file).
- `test_maybe_rotate_stale_rotates_when_mtime_is_one_second_before_period_boundary`: sets mtime == period_start - 1, asserts archive is created and content preserved.
Both pass on macOS.

---

### MAC-DOC-001

| Field | Value |
|-------|-------|
| **ID** | MAC-DOC-001 |
| **Title** | NSSM references in `main.py` comments lack Windows-only annotation |
| **Category** | Documentation / Prompt Drift |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-FS-001 |
| **Blocks** | — |

**Description**  
`main.py:1153–1155` lists NSSM log paths in a comment block without noting they are Windows-only. An AI assistant (Claude/Codex) or future maintainer reading this may incorrectly infer the bot is still deployed under NSSM.

**Evidence / Source**  
- Audit finding Doc-1
- `main.py:1153–1155`

**Proposed Fix**  
After MAC-FS-001 (NSSM code guarded or removed), update the comment to read:
```
logs/service/  -- Windows NSSM service logs (Windows deployment only; unused on macOS)
```
Or remove the comment entirely if the code block is deleted.

**Acceptance Criteria**  
- No unqualified NSSM references remain in `main.py` comments

---

### MAC-DOC-002

| Field | Value |
|-------|-------|
| **ID** | MAC-DOC-002 |
| **Title** | `setup_daily_task.ps1` and `daily_review.ps1` lack Windows-only headers |
| **Category** | Documentation / Prompt Drift |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-CLI-001 |
| **Blocks** | — |

**Description**  
Both PS1 scripts have usage comments but no explicit "Windows only" warning. A developer on macOS attempting to run these will get a cryptic `command not found: powershell` error with no guidance on the macOS alternative.

**Evidence / Source**  
- Audit findings Doc-2, S-1, S-2
- `scripts/setup_daily_task.ps1`, `scripts/daily_review.ps1`

**Proposed Fix**  
Add to the top of both PS1 scripts:
```
# PLATFORM: Windows only.
# macOS / Linux: see scripts/setup_launchd.sh (MAC-CLI-001) or run scripts/daily_review.py directly.
```

**Acceptance Criteria**  
- Both PS1 files have a Windows-only platform notice
- macOS alternative is referenced

**Implementation Notes** (2026-04-20)  
Resolved as part of MAC-CLI-001/MAC-CLI-002. Both PS1 headers now read "PLATFORM: Windows only. macOS / Linux: use scripts/setup_launchd.sh or daily_review.py directly."

---

### MAC-DOC-003

| Field | Value |
|-------|-------|
| **ID** | MAC-DOC-003 |
| **Title** | No platform support matrix documenting Windows-only vs cross-platform items |
| **Category** | Documentation / Prompt Drift |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-CLI-001, MAC-CLI-002, MAC-DOC-001, MAC-DOC-002 |
| **Blocks** | — |

**Description**  
There is no document that explicitly states which parts of the codebase are Windows-only, macOS-current, or cross-platform. After the migration, several scripts and code paths are platform-specific without any system-level documentation to that effect. Future maintainers and AI assistants have no canonical reference.

**Evidence / Source**  
- Audit finding Doc-3

**Proposed Fix**  
Add a `PLATFORMS.md` at the repo root (or a section to `README.md`) with a table:

| Component | Windows | macOS | Linux |
|-----------|---------|-------|-------|
| Runtime | deprecated (NSSM) | primary | untested |
| Automation | `setup_daily_task.ps1` | `setup_launchd.sh` (MAC-CLI-001) | cron (undocumented) |
| Daily review launcher | `daily_review.ps1` | `daily_review.py` direct | `daily_review.py` direct |
| DB / logs | ✅ | ✅ | ✅ |

**Acceptance Criteria**  
- A platform matrix exists and is linked from README or CLAUDE.md
- All Windows-only scripts are listed with their macOS equivalents or "N/A"

**Implementation Notes** (2026-04-19)  
Created `PLATFORMS.md` at repo root with four sections: Runtime/Process Management, Automation/Scheduling, Scripts, and Data/Persistence. All Windows-only scripts listed with macOS equivalents. Added a link to `PLATFORMS.md` from `README.md`.

---

## Execution Views

---

### A. Current Profit-Path Fix Queue

Open or blocked items, ordered for safe sequential execution:

| Order | ID | Title | Why First |
|-------|----|-------|-----------|
| 1 | PROFIT-RUNTIME-001 | S4.5 multi-lane paper validation remains unproven | Re-run after structural participation fix and sufficient wall-clock runtime |
| 2 | PROFIT-EVID-001 | Accumulation only learns from keyword-positive survivors | Blocked on contract decision for rejected-evidence intake semantics |
| 3 | PROFIT-VALID-001 | No first-class baseline-vs-multi-lane harness | The 2x trade-frequency constraint must be reproducible |
| 4 | PROFIT-CAL-001 | Calibration outcome feedback is not proven end-to-end | Depends on S4.5/resolved outcomes but remains visible |

**Execution note:** Do not bundle these into broad rewrites. Each item touches a different safety boundary and should close with focused tests and evidence.

---

### B. Expanded Pre-Go-Live Gate

Items that must be COMPLETE before live trading:

| ID | Title | Rationale |
|----|-------|-----------|
| All `MAC-*` items | Migration reliability set | Already COMPLETE; preserve as historical live-readiness foundation |
| PROFIT-RUNTIME-001 | S4.5 validation | Multi-lane architecture must be proven in paper mode before live exposure |
| PROFIT-TRACE-001 | Evidence/blend traceability | Every trade must remain explainable |
| PROFIT-REPLAY-001 | Replay-critical probability persistence | Dossiers must be reconstructable from persisted evidence |
| PROFIT-EVID-002 | Source-class fidelity | Source diversity and quality gates need accurate metadata |
| PROFIT-OBS-002 | Log rollover clarity | Long validation and live runs need reliable audit logs |
| PROFIT-EXEC-001 | Direct executor bypass risk | No trade-producing path should bypass readiness without explicit contract exemption |
| PROFIT-VALID-001 | Baseline vs multi-lane harness | The 2x trade-frequency constraint must be reproducible |

**Gate rule:** All open HIGH items in this table must be STATUS = COMPLETE before live mode is enabled. `OPEN` HIGH items outside this table require an explicit risk review before live mode.

---

### C. Current Parallelizable Work Streams

Items are grouped into independent streams with no inter-stream dependencies. Work within each stream is sequential; streams can proceed simultaneously.

#### Stream 1 — End-to-End Validation
Items: `PROFIT-RUNTIME-001`, `PROFIT-VALID-001`, `PROFIT-STARTUP-001`
Files likely touched: validation scripts, reporting docs, possibly paper-only instrumentation.

#### Stream 2 — Traceability / Replay
Items: `PROFIT-TRACE-001`, `PROFIT-REPLAY-001`, `PROFIT-OBS-001`
Files likely touched: `main.py`, `tasks/accumulation_task.py`, `tasks/blend_task.py`, `scripts/replay_dossier.py`, contract/docs.

#### Stream 3 — Evidence Quality
Items: `PROFIT-EVID-001`, `PROFIT-EVID-002`
Files likely touched: `main.py`, evidence metadata helpers, tests.

#### Stream 4 — Execution Boundary
Items: `PROFIT-EXEC-001`
Files likely touched: `main.py`, `tasks/blend_task.py`, tests. Contract review required before changing behavior.

#### Stream 5 — Observability / Performance
Items: `PROFIT-OBS-002`, `PROFIT-PERF-001`
Files likely touched: `utils/logger.py`, `PLATFORMS.md`, logging tests.

#### Stream 6 — Structural / Calibration
Items: `PROFIT-STRUCT-001`, `PROFIT-CAL-001`
Files likely touched: `tasks/structural_task.py`, `tasks/calibration_task.py`, validation scripts.

---

### D. Legacy Migration Work Streams

The following migration-specific stream plan is retained for historical continuity. These items are currently COMPLETE.

Items are grouped into independent streams with no inter-stream dependencies. Work within each stream is sequential; streams can proceed simultaneously.

#### Stream 1 — Async Blocking (MAC-ASYNC-001, MAC-ASYNC-002, MAC-TEST-001)
Files: `trading/executor.py`, `main.py`, `tests/test_executor.py`
No overlap with other streams.

#### Stream 2 — SQLite Reliability (MAC-DB-001, MAC-DB-002, MAC-DB-005, MAC-TEST-002)
Files: `tasks/evidence_store.py`, `trading/paper_trader.py`, `tests/test_evidence_store_concurrency.py`
MAC-DB-005 depends on MAC-DB-001 (needs WAL enabled first).
MAC-DB-002 is independent of MAC-DB-001 (different file, different connection).

#### Stream 3 — macOS Automation (MAC-CLI-001, MAC-CLI-002, MAC-DOC-002)
Files: `scripts/` only. No runtime code touched.
MAC-CLI-002 and MAC-DOC-002 depend on MAC-CLI-001 (reference the new script).

#### Stream 4 — Dead Code / Platform Guards (MAC-FS-001, MAC-PLAT-001, MAC-LOG-001, MAC-DOC-001)
Files: `main.py`, `utils/logger.py`
MAC-DOC-001 depends on MAC-FS-001 (comment update follows code removal).
All others are independent of all streams.

#### Stream 5 — Test Gaps (MAC-TEST-003, MAC-TEST-004)
Files: `tests/` only. No runtime code touched. Fully independent.

#### Stream 6 — Documentation (MAC-DOC-003, MAC-DB-003, MAC-DB-004)
Files: `PLATFORMS.md` (new), `trading/paper_trader.py`
MAC-DOC-003 depends on MAC-CLI-001 (needs launchd script to reference).
MAC-DB-003 depends on MAC-ASYNC-001/002 (re-evaluate after async pattern is set).

---

## Dependency Map

### Current Profit-Path Dependencies

```
PROFIT-RUNTIME-001 ─────────────────────────► PROFIT-VALID-001
PROFIT-RUNTIME-001 ─────────────────────────► PROFIT-CAL-001

PROFIT-TRACE-001 ─┐
PROFIT-REPLAY-001 ├────────────────────────► S4.5 observability confidence
PROFIT-OBS-001 ───┘

PROFIT-EVID-002 ────────────────────────────► Readiness G2 fidelity
PROFIT-EVID-001 ────────────────────────────► Dossier completeness review

PROFIT-EXEC-001 ────────────────────────────► No-bypass live readiness
PROFIT-OBS-002 ─────────────────────────────► Long-run validation safety
```

### Legacy Migration Dependencies

```
MAC-ASYNC-001 ──────────────────────────────► MAC-TEST-001
MAC-ASYNC-002 ──────────────────────────────► MAC-TEST-001
MAC-ASYNC-001 ──┐
MAC-ASYNC-002 ──┘──► MAC-DB-003 (re-evaluate flag after async pattern set)

MAC-DB-001 ─────────────────────────────────► MAC-TEST-002
MAC-DB-001 ─────────────────────────────────► MAC-DB-005

MAC-CLI-001 ────────────────────────────────► MAC-CLI-002
MAC-CLI-001 ────────────────────────────────► MAC-DOC-002
MAC-CLI-001 ────────────────────────────────► MAC-DOC-003

MAC-FS-001 ─────────────────────────────────► MAC-DOC-001

MAC-DOC-001 ─┐
MAC-DOC-002 ─┤
MAC-CLI-001 ─┘──► MAC-DOC-003
```

---

## Operating Rules

These rules govern how this log is used during remediation work.

### R-1 — Status Updates Are Mandatory
No item may remain at `OPEN` after work begins. Change to `IN_PROGRESS` on first edit to any file in scope.

### R-2 — COMPLETE Requires Acceptance Criteria
An item may not be set to `COMPLETE` unless every acceptance criterion listed for it is satisfied. Partial fixes stay `IN_PROGRESS`.

### R-3 — New Discoveries Must Be Logged
If a fix or audit uncovers a new issue that could impair trading quality, safety, selectivity, auditability, calibration, timeliness, or operational reliability, that issue must be added here before the fix commit is closed. Do not silently absorb discoveries into code or create a parallel tracker.

### R-4 — No Silent Fixes
Every fix that closes an item in this log must be traceable to a commit. The commit message should reference the item ID (e.g., `fix(async): wrap paper trader calls in to_thread (MAC-ASYNC-001, MAC-ASYNC-002)`).

### R-5 — Pre-Go-Live Gate Is a Hard Block
The Expanded Pre-Go-Live Gate items must all be COMPLETE before live mode (`ENABLE_LIVE_TRADING=true`) is set. This gate cannot be waived without an explicit contract/risk-review update.

### R-6 — Dependencies Must Be Respected
No item may move to `IN_PROGRESS` if its `Depends On` items are not yet COMPLETE, unless the dependency is explicitly re-evaluated and documented under Notes.

### R-7 — Last Updated Must Stay Current
Update the `Last Updated` timestamp in the metadata header whenever any item changes status or a new item is added.

### R-8 — False Positives Are Documented, Not Deleted
The audit identified several items that turned out to be false positives after code inspection (mtime timezone concern, PEM key handling, signal handler lambda). These are not in this log. If any item is later determined to be a false positive, mark it `COMPLETE` with Notes explaining the determination — do not delete it.

### R-9 — Single Tracker Rule
This file is the only technical-debt tracking mechanism for profit-path, reliability, safety, observability, and platform debt. Do not create a second debt log for macOS, S4.5, logging, calibration, or execution-boundary findings; add them here with a new ID or update an existing item.
