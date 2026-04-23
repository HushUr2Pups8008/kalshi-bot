# Implementation Roadmap — Multi-Lane Trading Architecture

**Version:** 1.0
**Status:** ACTIVE
**Contract:** [IMPLEMENTATION_CONTRACT.md](IMPLEMENTATION_CONTRACT.md)

This is the shared, authoritative task tracker for Claude, Codex, and Codex.
Status must be updated as work progresses. See contract Section 10 for rules.

**Allowed statuses:** `NOT_STARTED` | `IN_PROGRESS` | `COMPLETE` | `BLOCKED`

---

## Stage 0 — Diagnostics Foundation

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| S0.1 | Token normalization in `_compute_pre_llm_match_meta` | COMPLETE | Claude | Fix mixed-case token bypass of stopword filter | No behavior change | Normalized tokens before stopword filtering |
| S0.2 | Denominator fields in `MATCH_DIAGNOSTIC` | COMPLETE | Claude | Enable ratio debugging | No behavior change | `pre_llm_headline_token_count`, `pre_llm_market_token_count` in events |
| S0.3 | LLM usefulness tracking in `SIGNAL_ANALYSIS_DETAIL` | COMPLETE | Claude | Enable `pre_llm_would_block_and_useful` diagnostic | No behavior change | `llm_useful`, `llm_probability_movement`, `pre_llm_would_block_and_useful` in events |
| S0.4 | Test suite update | COMPLETE | Claude | Keep 140 tests green after signature expansion | Do not weaken assertions | All tests pass |

---

## Stage 1 — Instrumentation and Regime Baseline

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| S1.1 | Define `BLEND_DECISION` log schema | COMPLETE | Shared | Establish telemetry contract before implementation | Must be finalized before S2 blending work | Agreed schema in `utils/logger.py` with all fields from contract Section 8 (including `evidence_ids_contributing`) |
| S1.2 | Define `EVIDENCE_INGESTION` and `DOSSIER_UPDATE` log schemas | COMPLETE | Shared | Telemetry contract for accumulation lane | Must align with evidence store schema (S2.1) | Two new log event types in `utils/logger.py` |
| S1.3 | Implement `regime_classifier.py` in `/analysis` | COMPLETE | Claude | Compute regime weight vector from market metadata | Pure function only; no I/O; no LLM calls | Function `compute_regime_weights(market) -> dict[str, float]` |
| S1.4 | Add regime weights to market discovery path | COMPLETE | Codex | Attach regime vector to market objects at discovery | Extend existing market dataclass; do not change routing logic | Markets carry `regime_weights` field from discovery onward |
| S1.5 | Define `STRUCTURAL_PRIOR_RECOMPUTE` log schema | COMPLETE | Shared | Telemetry contract for structural layer | Must align with structural prior implementation (S3.1) | Schema defined; `utils/logger.py` extended |
| S1.6 | Add `CALIBRATION_CHECK` log event | COMPLETE | Claude | Foundation for cross-lane drift detection | Emitted at resolution time only; no runtime impact | Per-lane prediction error logged at resolution |

**Dependencies:** None. All S1 tasks are independent of each other and of Stage 2.

---

## Stage 2 — Evidence Store and Accumulation Lane

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| S2.1 | Design evidence store schema | COMPLETE | Shared | Define dossier table, evidence table, foreign keys | Must support replay (immutable event IDs); separate DB from `paper_trades.db` | Schema spec (tables, columns, indexes) |
| S2.2 | Implement `evidence_store.py` in `/tasks` | COMPLETE | Codex | Persistence layer with per-market async locking | Serialize writes; concurrent reads; clear interface | `get_dossier`, `update_dossier`, `add_evidence` functions |
| S2.3 | Implement `evidence_scorer.py` in `/analysis` | COMPLETE | Claude | Score evidence quality: source class, corr. discount, dedup | Pure function; no DB access | `score_evidence(evidence, recent_market_evidence) -> EvidenceScore` |
| S2.4 | Implement `dossier_builder.py` in `/analysis` | COMPLETE | Claude | Belief update: state-update vs confidence-update distinction, drift detection, displacement cap, recovery mode | Pure function; no DB; no LLM; implements BSR-1 through BSR-7 from contract | `update_dossier(current_dossier, new_evidence_score, update_type) -> Dossier` |
| S2.5 | Implement `accumulation_task.py` in `/tasks` | COMPLETE | Codex | Wire evidence ingestion: feed → scorer → builder → store | No trading logic; emit `EVIDENCE_INGESTION` and `DOSSIER_UPDATE` events | Running async task; dossiers persisted to `evidence_store.db` |
| S2.6 | Implement forgetting mechanisms | COMPLETE | Claude | Time decay, supersession, resolution clearing | Parameters must be market-type-specific; no hardcoded global TTL | Decay applied in `dossier_builder`; clearing on resolution |
| S2.7 | Implement `budget_manager.py` in `/tasks` | COMPLETE | Codex | Enforce per-market and global LLM call budgets with priority queue | Per-market: 4 calls/hour; global: 60 calls/hour; circuit breaker at 3× depth | `request_llm_call(market_ticker, priority) -> bool`; `BUDGET_PRESSURE` event on circuit break |
| S2.NEW | Implement `trade_readiness_gate.py` in `/tasks` | COMPLETE | Codex | Stateless predicate gate per contract Section 5 | All G1–G6 conditions; dossier vs fast-lane exemptions; no bypass path | `evaluate_readiness(blend_result, regime_confidence) -> ReadinessDecision` |

**Dependencies:** S2.1 before S2.2, S2.3, S2.4. S2.2 + S2.3 + S2.4 + S2.7 before S2.5. S1.1 + S1.2 before S2.5 (log schemas must exist). S2.5 before S2.NEW.

---

## Stage 3 — Structural Prior and Decision Unification

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| S3.1 | Implement `structural_prior.py` in `/analysis` | COMPLETE | Claude | Base-rate + context synthesis boundary | Pure function at synthesis boundary; LLM call happens in `/tasks` | `compute_structural_prior(market, context) -> PriorEstimate` |
| S3.2 | Implement `structural_task.py` in `/tasks` | COMPLETE | Codex | Scheduled structural recompute; emit `STRUCTURAL_PRIOR_RECOMPUTE` | Time-driven only; skip if no new structural data available | Structural priors persisted; log event emitted |
| S3.3 | Implement `decision_blender.py` in `/analysis` | COMPLETE | Claude | Confidence-weighted blend; dominance rule; structural fail-safe tiers | Pure function; regime weights from S1.3; implements DER-1 through DER-4 from contract | `blend(fast, accumulation, structural, regime_weights) -> BlendResult` |
| S3.4 | Implement `blend_task.py` in `/tasks` | COMPLETE | Codex | Read three lane outputs; call blender; evaluate readiness gate; emit `BLEND_DECISION`; produce `TradeCandidate` | No trading logic; blocked candidates logged with `trade_blocked_reason` | Blended candidates in trading queue; all blocked candidates emitting events |
| S3.5 | Extend executor to accept blended candidates | COMPLETE | Shared | Executor receives `signal_meta`; applies `readiness_gate_min_edge_override` only; logs all other fields | All existing safety gates intact; no new decision logic in `/trading`; highest-risk task — explicit confirmation required before starting | Executor handles both candidate types; no behavioral regression |
| S3.6 | Cross-lane calibration monitoring | COMPLETE | Claude | Build `CALIBRATION_CHECK` consumer; detect drift; scale confidence | Read-only; no behavior change unless drift threshold exceeded | Calibration curves per lane; drift alerts; auto-scaling of effective confidence |

**Dependencies:** S1.3 before S3.3. S2.5 before S3.4. S3.3 + S3.2 before S3.4. S3.4 + S2.NEW before S3.5 (gate must exist before executor extension). S1.5 before S3.2. S1.6 before S3.6.

---

## Stage 4 — Hardening and Validation

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| S4.1 | Replay utility for dossier reconstruction | COMPLETE | Codex | Verify dossier auditability from event log | Read-only offline tool | CLI tool that replays belief trajectory for a market from its evidence event chain |
| S4.2 | Observability completeness review | COMPLETE | Shared | Confirm all `BLEND_DECISION` fields populated; traceability chain intact | Run against paper trading; compare event completeness | All required fields non-null for ≥ 90% of events |
| S4.3 | Budget manager stress test | COMPLETE | Codex | Verify circuit breaker fires under synthetic load | Inject synthetic high-volume queue; confirm `BUDGET_PRESSURE` emitted | Circuit breaker fires at 3× depth; no runaway LLM calls |
| S4.4 | Regime weight validation against historical outcomes | COMPLETE | Claude | Check that regime weights improve blended calibration vs unweighted blend | Use `windows_archive` data for backtesting | Calibration improvement documented |
| S4.5a | Section 13 implementation gate | COMPLETE | Shared | Verify all code-correctness criteria in Section 13 by test and static inspection — no runtime required | pytest must pass without weakening assertions; covers criteria 1, 2, 5, 6, 7, 8 | All verifiable Section 13 criteria confirmed; results recorded. Note: most recent comprehensive run was 229 passed (2026-04-20, PROFIT-PERF-001 validation). |
| S4.5b | Runtime wiring verification | COMPLETE | Shared | Confirm all three lanes produce expected event types under real intake in a 2-hour minimum window | No intake modification; production-intended feed config; no code changes to force events; startup probe events excluded | ≥1 each of `EVIDENCE_INGESTION`, `DOSSIER_UPDATE`, `STRUCTURAL_PRIOR_RECOMPUTE`, `BLEND_DECISION` (with `fast_lane_p` non-null); zero unhandled exceptions; pass/fail verdict recorded in Notes. **PASS (2026-04-23)**: over a 47-hour continuous window (2026-04-21T00:09 → 2026-04-22T23:44 UTC, post-2731d9a structural fix deployed 2026-04-21T01:38 UTC), `logs/trades/archive/2026/04/2026-04-{21,22}.jsonl` contain `EVIDENCE_INGESTION ×46`, `DOSSIER_UPDATE ×46`, `BLEND_DECISION ×49` (fast_lane_p non-null on sampled events), `STRUCTURAL_PRIOR_RECOMPUTE ×20` across 6 distinct dossier markets; first structural event at 2026-04-21T16:52:38 UTC; zero unhandled exceptions in `bot.log`. |
| S4.5c | Extended validation window | IN_PROGRESS | Shared | Provide statistical basis for Section 13 completeness and calibration criteria; final gate before Phase 4 | 24-hour early-close window (one-time bounded exception, rationale in Notes); production-intended intake; startup probe events excluded; no config changes during window | Section 13 criteria 3, 4, 5, 6 pass against window output; observability completeness review PASS; ≥3 distinct dossier markets observed; signed Section 13 checklist recorded in Notes. **Window opened 2026-04-23T00:28:31 UTC** (commit `4074e13`). **Original plan:** 72h minimum close at ~2026-04-26T00:28:31 UTC. **Revised plan (2026-04-23, operator-approved):** close early at **2026-04-24T00:28:31 UTC** (24h from open). Window overlaps P2.2 (opened 2026-04-22T12:07:35 UTC per `0f91bf7`); P2.2 continues to its full 72h minimum close at **2026-04-25T12:07:35 UTC** independently and is **not** subject to this S4.5c truncation. If P2.2 fails and `all_required` is reverted, a post-revert S4.5c-prime window must be opened from the revert commit. **Early-close rationale (bounded exception, not a general precedent):** (a) criteria 3-5 already PASS per the S4.5b bridge evidence (47h continuous, zero exceptions, 6 distinct dossier markets — exceeds ≥3 threshold, zero-baseline trade-frequency) supplemented by the ongoing S4.5c observation; (b) criterion 6 is structurally vacuously passing per `PROFIT-CAL-001` — the `CALIBRATION_CHECK` emission site does not exist at runtime, so no amount of additional soak time produces the events the 72h window was designed to evaluate; (c) marginal information gain from hours 25-72 in the current zero-trade, zero-resolution regime is ≈ 0; (d) early close unblocks `PROFIT-CAL-001` execution (the pre-live-trading blocker) by ~2 calendar days. **This exception is bound to the specific conditions above**: a future S4.5c-style window in an *active*-calibration or non-zero-trade regime requires the full 72h minimum and the truncation precedent does not apply. **Post-CAL-001 re-validation (S4.5c-prime):** Once the `PROFIT-CAL-001` emission wiring lands (design in `docs/plans/profit_cal_001_calibration_wiring.md`), open a new **S4.5c-prime** window (24-48h) to re-validate Section 13 criteria under an active calibration loop. This is additional rigor above the original plan — the original 72h run would have validated only the inert state; S4.5c-prime validates the fixed state. **Pre-assessment (2026-04-23) against prior-window evidence (2026-04-21 → 2026-04-22):** "Safe to Run in Paper Mode" criterion 3 PASS (47h, zero exceptions), criterion 4 PASS (`BLEND_DECISION ×49`, `DOSSIER_UPDATE ×46`, `EVIDENCE_INGESTION ×46` across 6 distinct markets — already exceeds ≥3 threshold), criterion 5 PASS (zero-baseline rule: 0 paper trades), criterion 6 PASS (vacuous — see `PROFIT-CAL-001` 2026-04-23 Validation Notes: in addition to zero resolutions in window, an investigation confirmed the emission site from `resolve_market` to `log_calibration_check` does not exist at runtime, so criterion 6 is *silently* vacuously satisfied; the wiring gap is a deferred post-window fix and is not a blocker for S4.5c closure). "Not Done Until" red flags CLEAR: `blend_mode` varies (weighted_blend 67.3% / dominant_lane 32.7%); `trade_blocked_reason` populated on all 49 BLEND_DECISION (all `G1_blended_confidence`, consistent with Phase 0 zero-signal verdict). Traceability chain intact (`evidence_ids_contributing` non-null on all 49). Section 8 required-field audit: 12/16 fields 100% non-null; 4/16 (accumulation/structural p/confidence) are 67.3% non-null — deliberately null in dominant_lane mode per S4.2 accepted interpretation. Final close requires: signed Section 13 checklist + re-run observability completeness review against the new window output. **Post-window sign-off scaffold** (fill at close, target 2026-04-24T00:28:31 UTC per early-close revision; **separately**, a follow-on scaffold will be attached to S4.5c-prime post-CAL-001): `[ ]` criterion 3 — runtime=___h, unhandled exceptions=___; `[ ]` criterion 4 — BLEND_DECISION=___, DOSSIER_UPDATE=___, EVIDENCE_INGESTION=___; `[ ]` criterion 5 — trades=___ vs. fast-lane baseline=___ (multiple=___× or zero-baseline N/A); `[ ]` criterion 6 — CALIBRATION_CHECK events=___, max per-lane error vs. baseline=___× (vacuous if zero resolutions); `[ ]` red flag — `blend_mode` variance confirmed (not always `weighted_blend`); `[ ]` red flag — `trade_blocked_reason` populated on all blocked candidates; `[ ]` Section 8 required-field audit — ___/16 fields ≥ 90% non-null; `[ ]` observability completeness review PASS against window output; `[ ]` ≥ 3 distinct dossier markets observed (S4.5b baseline had 6); signed: ______ (Claude | Codex), date: ______. |

**Dependencies:** All Stage 3 tasks COMPLETE before Stage 4 begins. **S4.5a** is complete on test evidence. **S4.5b** COMPLETE (2026-04-23); structural recompute participation verified post-2731d9a via trade-log evidence. **S4.5c** IN_PROGRESS (window opened 2026-04-23T00:28:31 UTC; **truncated 72h → 24h early-close target 2026-04-24T00:28:31 UTC** per bounded exception documented in the S4.5c row Notes); final integration gate before any Phase 4 (live trading) consideration; pre-assessment against prior-window data shows criteria 3–6 PASS (criterion 6 vacuously). A post-`PROFIT-CAL-001` **S4.5c-prime** window (24-48h) will re-validate Section 13 under an active calibration loop before Phase 4 is authorized. Stage 5 Phases 0–3 may proceed in parallel with S4.5c since they are diagnostics-only.

---

## Notes

- **S3.5** requires explicit confirmation before starting. It is the only task that touches `/trading`.
- **S2.NEW** (`trade_readiness_gate.py`) was added during the final precision refinement pass. It is a prerequisite for S3.4 and S3.5.
- The contract in `IMPLEMENTATION_CONTRACT.md` governs all tasks. Any apparent conflict between a task description here and the contract must be raised before implementation proceeds.
- **S4.5 split rationale:** The original S4.5 was a one-shot binary milestone that could not honestly be satisfied by a single run. It is replaced by S4.5a (implementation gate, code-verifiable), S4.5b (wiring verification, 2-hour window), and S4.5c (extended validation, 72-hour window). Phase 4 authorization requires S4.5c COMPLETE. The Notes column on S4.5b and S4.5c is the authoritative record of observation state and pass/fail verdicts — this is Option A per the S4.5 reevaluation (2026-04-21).

---

## Stage 5 — Pipeline Quality and Signal Recovery

**Version:** 1.1 (appended 2026-04-21)
**Status:** IN_PROGRESS (Phase 0 active)
**Contract:** [IMPLEMENTATION_CONTRACT.md](IMPLEMENTATION_CONTRACT.md) — all invariants and boundary rules remain binding
**Depends on:** Architecture stages 0–4 COMPLETE (S4.5 IN_PROGRESS; Phases 0–2 below may run in parallel since they are diagnostics-only)

### Context

A production audit covering 2026-04-17 to 2026-04-21 found: the multi-lane architecture is wired correctly, but the pipeline produces zero real edge. Every non-probe LLM call across 40+ samples returned exactly `est = 0.5000` (market price). Match quality flags (`single_named_entity_only`, `minimal_overlap`, `near_threshold_score`) appear on 75%+ of matches. The pre-LLM gate is double-disabled. One ingestion source ("Politics") is 100% stale. Startup probe synthetic output contaminates all signal-quality metrics.

**Governing constraints for this stage:**
- Phases 0–2 are diagnostics and observability only. No changes to execution criteria, safety gates, or trading logic.
- Phase 3 changes match and source quality, not execution thresholds.
- Phase 4 (trading readiness) requires explicit written authorization from Codex and a measurable non-zero edge in paper mode before beginning.
- Step ordering within Phase 2 is mandatory; do not collapse or reorder.

---

### Phase 0 — LLM Signal Diagnosis

**Purpose:** Determine whether the LLM is structurally unable to form a directional view from current inputs, or whether the prompt or input is anchoring it to market price. This verdict governs Phase 2 scope.

**Go/no-go checkpoint (P0-GATE):** Before Phase 2 gate enforcement (P2.3+) may proceed, P0.3 must deliver a documented root-cause verdict: (a) prompt anchoring to market price, (b) insufficient input context, (c) market-scope ceiling, or (d) structural LLM limitation. Gate changes without this verdict are premature.

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| P0.1 | Tag startup probe in `SIGNAL_ANALYSIS_DETAIL` events | COMPLETE | Codex | Startup probe uses hardcoded synthetic `(0.38, 0.82)` output; it must be excluded from signal-quality statistics | No behavior change; tagging only | `is_synthetic_probe=true` on probe events; all reports and metrics filter it |
| P0.2 | Log full prompt and raw LLM response for non-probe calls | COMPLETE | Codex | Enable manual inspection of why production LLM returns `0.5000` | DEBUG level only; no prompt change; no behavioral change | Prompt + raw LLM response visible in logs for each real analysis call |
| P0.3 | Manual diagnosis: est vs market_price distribution | COMPLETE | Claude | Review P0.2 output; determine if `est == market_price` is tautological (anchoring) or coincidental | No code change; manual inspection | Written verdict recorded; one of the four root-cause categories confirmed |

**P0.3 Verdict (recorded 2026-04-21):**

*Observations:* 40+ non-probe LLM calls across 2026-04-17 to 2026-04-21 returned exactly `est = market_price` (0.5000) in every case. Match quality flags (`single_named_entity_only`, `minimal_overlap`, `near_threshold_score`) appear on 75%+ of matches. All active matches are Trump/Iran/Tehran named-entity hits on broadly scoped geopolitical markets (e.g., "Will Iran agree to a peace deal this month?") priced near 50c.

*Mechanism confirmed:* `_parse_llm_response` returns `market.yes_prob` unchanged when the LLM outputs `magnitude="none"`. The system prompt explicitly instructs the model that "Most headlines should result in magnitude='none'" and reserves 'moderate' or 'large' for "major unexpected developments." The LLM is not anchored to market price — it does not receive the price in its prompt — it is correctly classifying nearly all incremental Iran/US tension news as already priced into broadly scoped resolution criteria.

*Ruling out other categories:*
- **(a) Prompt anchoring**: Eliminated. The market price is not present in the LLM prompt; `_build_prompt_text` does not include `yes_prob`.
- **(b) Insufficient input context**: Eliminated. Headline + source are present; extended context is unavailable but is not the root cause at this scope.
- **(d) Structural LLM limitation**: Eliminated. The LLM's `magnitude="none"` classification is correct for the inputs. The problem is the input, not the model.

*Classification:* **(c) Market-scope ceiling.** The markets in the current active set are too broadly scoped relative to their resolution criteria for incremental news to produce a non-zero directional signal. The structural mechanism (`magnitude="none"` → passthrough) is working correctly; the inputs feeding it are the problem.

*Phase 2/3 implications:* P0-GATE PASS. Gate enforcement (P2.3+) will suppress low-quality matches but will not resolve the market-scope ceiling on its own. The primary fix path is P3.2 (`market_specificity_score`) and P3.3 (source-market alignment audit). P2 cleans up noise; P3 is where edge recovery begins.

**P0-GATE outcome:**
- PASS → Phase 1 proceeds (may start in parallel with P0); Phase 2 gate changes authorized after verdict
- ESCALATE → LLM returns 0.5000 for inputs with clearly sufficient directional context → prompt engineering or model change required before Phase 2

---

### Phase 1 — Observability Fixes

**Purpose:** Separate synthetic from real metrics so all subsequent reporting is trustworthy. Without this, Phase 2 gate impact is unmeasurable.

**Dependencies:** P0.1 COMPLETE before P1 observability is meaningful.

**Go/no-go checkpoint (P1-GATE):** All three P1 tasks COMPLETE before Phase 2 enforcement steps (P2.3+) begin. Unauditable metrics make gate enforcement unverifiable.

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| P1.1 | Add `publish_ts` and `age_at_match_seconds` to `MATCH_DIAGNOSTIC` | COMPLETE | Claude | Distinguish article publication age from staleness at match time | Diagnostic field only; no behavior change | `publish_ts` and `age_at_match_seconds` present in match events |
| P1.2 | Add per-source freshness waterfall to daily reports | COMPLETE | Claude | Identify which sources are chronically stale vs genuinely fresh | Report generation only; no pipeline change | Daily reports show fresh/stale/drop counts by source |
| P1.3 | Surface pre-LLM gate diagnostic breakdown in reports | COMPLETE | Claude | `pre_llm_would_block` counts are currently global aggregates; need per-source and per-market breakdown | Report generation only | Reports show gate decision distribution by source and market |

**P1-GATE outcome:**
- PASS → Phase 2 may proceed
- FAIL if `publish_ts` unavailable in feed payloads → document gap; Phase 2 proceeds with noted blind spot

---

### Phase 1.5 — Source Hygiene

**Purpose:** Remove or quarantine dead sources consuming queue cycles and inflating ingestion volume metrics.

**Dependencies:** P1.2 COMPLETE (per-source freshness waterfall required to confirm which sources are dead).

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| P1.5.1 | Investigate "Politics" source (100% stale, 0 fresh across audit period) | COMPLETE | Codex | Confirm dead/misconfigured before disabling | Disable only; do not delete config | Verdict: confirmed dead/low-value. Daily reviews show repeated 0-fresh windows (2026-04-17, 2026-04-19, 2026-04-20); archived scorecard shows `Politics` disabled_by=source with 1,276 obs, 0 signals, 0 paper trades; current logs confirm exact-label disabled-source drops. Source was already present in `DISABLED_NEWS_SOURCES`, so no config change was needed. |
| P1.5.2 | Audit Reddit sources: freshness rate and match-to-analysis conversion | NOT_STARTED | Codex | High volume but unclear signal value; confirm worth keeping | Diagnostic only; no changes until findings reviewed | Per-Reddit-source freshness rate and match rate documented |
| P1.5.3 | Disable confirmed-dead sources | NOT_STARTED | Codex | Eliminate noise from ingestion metrics | Only sources confirmed dead in P1.5.1 / P1.5.2 | Config change committed; dead sources disabled |

*No hard gate; Phase 1.5 findings feed into Phase 3 source-market alignment work.*

---

### Phase 2 — Gate and Override Enforcement

**Purpose:** Bring the pre-LLM match gate into meaningful operation. Current state: `enable_pre_llm_match_gate=false` AND `pre_llm_match_gate_diagnostics_only=true`; keyword override mode is `any_hit` (any single keyword match bypasses the gate entirely). All current production matches involve Trump/Iran/Tehran named entities and bypass via `any_hit`.

**Dependencies:** P0-GATE PASS (verdict documented); P1-GATE PASS (clean metrics).

**WARNING — step ordering within this phase is mandatory. P2.1 before P2.2 before P2.3 before P2.4 before P2.5. Do not collapse.**

**Go/no-go checkpoint (P2-GATE):** Before P2.3, confirm from P2.2 diagnostics that the tightened gate would block ≥ 20% of real matches in the trailing 7 days. If < 20%, the gate adds no filtering value at current match volumes — stop at P2.2 and reassess keyword config.

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| P2.1 | Tighten keyword override from `any_hit` to `all_required` | COMPLETE | Claude | `any_hit` makes the gate a near-no-op; this is a prerequisite to gate being meaningful | Adds `all_required` mode to `_should_keyword_override_pre_llm_gate`; gate still `diagnostics_only=true`; no behavioral enforcement | Keyword override requires the article to hit at least one keyword in every `GEOPOLITICAL_SIGNALS` group before bypassing the gate |
| P2.2 | Run gate in diagnostics mode with tightened override for 3 days | IN_PROGRESS | Shared | Observe `pre_llm_would_block` rate before any enforcement | No enforcement; `diagnostics_only=true` throughout | Gate block rate documented; confirm ≥ 20% before P2.3. **Observation window start: 2026-04-22T12:07:35 UTC** (deploy of commit `0f91bf7` — the P2.1 `all_required` keyword-override change this window is measuring). **Earliest close: 2026-04-25T12:07:35 UTC** (start + 72h minimum per Contract §10 Rule 6). **No-change scope during window:** runtime code must not modify any keyword-gate, pre-LLM match, or executor-selectivity code path, or the comparison integrity is lost and the clock restarts from the subsequent deploy. Unrelated runtime changes (e.g., `PROFIT-CAL-001` calibration emission which touches only `resolve_market` + schema) are explicitly out-of-scope for P2.2 and may proceed as long as they preserve the above invariants, documented per change. |
| P2.3 | Enable `ENABLE_LOW_QUALITY_MATCH_SUPPRESSION` | NOT_STARTED | Claude | Suppression criteria (`low_match_quality AND near_threshold_score`) are already correct; flag just disabled | Requires P2.2 COMPLETE and block rate ≥ 20% | Suppression fires; low-quality matches suppressed before LLM call |
| P2.4 | Enable pre-LLM gate in diagnostics mode | NOT_STARTED | Claude | Final observation pass before live enforcement | `diagnostics_only=true`; watch for false-positive rate on confirmed-valid matches; run 3 days | Gate active; no matches blocked; false-positive rate measured |
| P2.5 | Enable pre-LLM gate enforcement | NOT_STARTED | Codex | Live enforcement | Requires P2.4 clean for 3 days with < 5% false-positive rate on confirmed-valid matches | Gate enforced in production; low-quality matches blocked before LLM |

**P2-GATE outcome:**
- PASS → Phase 3 authorized
- ROLLBACK trigger: if P2.1 (`all_required` override) blocks > 50% of Trump/Iran matches that have historically been valid → roll back P2.1; reassess keyword config before continuing

**P2.1 semantic clarification (authored 2026-04-22, Claude, per Contract §9 Claude Rule 1 and §11):**

`all_required` is defined as follows: the keyword override fires only when the article's text contains at least one keyword from *every* group in `config.GEOPOLITICAL_SIGNALS`. This is the literal reading of "all configured keywords" under the current configuration model — there is no separate override keyword list, so "configured keywords" refers to the only configured set (`GEOPOLITICAL_SIGNALS`).

Alternatives considered and rejected:
- *All keywords in any one group must match* — impossibly strict for multi-phrase groups (e.g., the war group has 15 phrases); no real article hits all of them.
- *Count-based threshold (≥N distinct keywords)* — semantically `min_count`, not `all_required`. Cleanest fit for the ROLLBACK trigger's expected partial block rate but does not match the task name.

The literal interpretation will likely block a high fraction of current Trump/Iran-dominated traffic because those articles hit named-entity phrasing only, not conflict/peace/sanctions groups. This is intentional: P2.2 runs the mode in `diagnostics_only=true` precisely so the ROLLBACK trigger can fire on evidence. If it fires, a follow-up task introduces a softer mode (likely count-based); the follow-up is *not* part of P2.1.

---

### Phase 3 — Candidate Quality and Market Specificity

**Purpose:** Improve the quality of matches reaching the LLM so they carry real directional content. Informed by Phase 0 verdict and Phase 2 gate metrics.

**Dependencies:** P2 COMPLETE; P0.3 verdict documented.

**Go/no-go checkpoint (P3-GATE):** After P3 improvements are deployed, paper mode must show ≥ 1 non-zero edge event in the trailing 14 days before Phase 4 is authorized. If zero non-zero edge after P3 — the LLM cannot form directional views from available input, and no further operational change will fix that. Stop and escalate.

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| P3.1 | Measure flag-outcome correlation | NOT_STARTED | Claude | Determine whether `single_named_entity_only`, `minimal_overlap`, `low_token_overlap` flags predict `est == market_price` | Diagnostic only; pure analysis | Correlation table: flag presence vs `est == market_price` rate; confirms or refutes match-quality hypothesis |
| P3.2 | Add `market_specificity_score` to match events | NOT_STARTED | Claude | Broad-scope markets (general Iran/US tension) yield near-zero directional signal; specificity scoring enables prioritization | Pure function in `/analysis`; no behavior change | `market_specificity_score` field on match events; high-specificity markets surface first |
| P3.3 | Source-market alignment audit | NOT_STARTED | Shared | Measure which sources produce matches on which markets; identify high- and low-signal source-market pairings | Diagnostic only; no filtering yet | Source-market alignment matrix documented; low-value pairings flagged |
| P3.4 | Implement source-market alignment filter | NOT_STARTED | Claude | Filter confirmed low-value source-market pairings at match time | Implement only if P3.3 shows ≥ 30% of matches from consistently low-value pairings | Source-market pairs below alignment threshold suppressed; LLM call budget preserved for high-value pairings |

**P3-GATE outcome:**
- PASS (≥ 1 non-zero edge in paper mode, trailing 14 days) → Phase 4 authorized
- FAIL (zero non-zero edge) → escalation required; do not proceed to Phase 4

---

### Phase 4 — Trading Readiness

**Purpose:** Validate that edge is real, stable, and sufficient for live consideration. Requires explicit written authorization from Codex before beginning.

**Dependencies:** P3-GATE PASS; explicit written authorization from Codex; S4.5 COMPLETE; `PROFIT-CAL-001` calibration-emission wiring COMPLETE (see `docs/plans/profit_cal_001_calibration_wiring.md` — structural blocker on P4.2 and therefore on P4.3).

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| P4.1 | 14-day paper mode monitoring with all pipeline improvements active | NOT_STARTED | Shared | Confirm edge is stable, not a one-off artifact | All existing paper-trade safety gates active; no live trading; INV-6 and INV-7 enforced | Edge > 0 on ≥ 3 distinct trade candidates over 14 days |
| P4.2 | Calibration review: est distribution vs resolved outcomes | NOT_STARTED | Claude | Verify LLM estimates are calibrated, not coincidentally correct | Requires ≥ 10 resolved paper trades. **Hard blocker:** `PROFIT-CAL-001` — `CALIBRATION_CHECK` emission is not wired from `resolve_market`; P4.2 cannot produce its calibration-curve output until that fix lands. Design in `docs/plans/profit_cal_001_calibration_wiring.md`; execution scheduled as first post-S4.5c work item. | Calibration curve documented; over/underconfidence measured |
| P4.3 | Live trading authorization | NOT_STARTED | Codex | Explicit written sign-off | Requires P4.1 + P4.2 COMPLETE; INV-6 and INV-7 compliance verified in writing | Authorization recorded; live mode enabled |

**P4-GATE outcome:**
- PASS → Live trading authorized
- FAIL (edge unstable or calibration poor) → return to Phase 3 diagnosis

---

**Stage 5 Phase Dependencies:**
```
P0.1 → P0.2 → P0.3 (P0-GATE)
P0.1 → P1.1 → P1.2 → P1.3 (P1-GATE)
P1.2 → P1.5.1 → P1.5.3
         P1.5.2 → P1.5.3
[P0-GATE + P1-GATE] → P2.1 → P2.2 (P2-GATE) → P2.3 → P2.4 → P2.5
P2 COMPLETE → P3.1 → P3.2 → P3.3 → P3.4 (P3-GATE)
[P3-GATE + S4.5c COMPLETE + Shared authorization] → P4.1 → P4.2 → P4.3
```

---

### What Changed From Prior Assumptions

1. **LLM diagnosis is Phase 0, not Phase 3.** The prior plan treated match quality as the root cause. 40+ real production LLM calls returning exactly `0.5000` across 4+ days is a first-class finding. Gate changes are premature without a root-cause verdict.

2. **Startup probe must be separated immediately.** The synthetic `(0.38, 0.82)` probe output contaminates all "meaningful signal" and "LLM useful" percentages. Current reporting is misleading. P0.1 is the first task.

3. **Gate enforcement step ordering is mandatory.** The prior plan conflated keyword override tightening, suppression enablement, diagnostics mode, and live enforcement into a single step. They are now explicit sequential tasks (P2.1 → P2.5) with a 3-day observation window before live enforcement.

4. **"Politics" source is confirmed dead** across the full audit period (0 fresh passes, 100% stale drop). P1.5.1 is a diagnosis task, but the expected outcome is disable.

5. **Phase 4 (live trading) is explicitly gated on measurable non-zero edge in paper mode.** No timeline. If edge does not form after P3 improvements, escalation is required — not more operational changes.

---

## Appendix A — Post-OT&E News Source Options (investigated 2026-04-22; expanded 2026-04-23)

**Status:** investigation only. No integration until S4.5c COMPLETE and P4-GATE outcome known. Order below is recommended *post-OT&E* integration sequence, smallest-risk-first, each A/B-testable against the paper-mode baseline individually rather than bundled.

**Constraints considered:** zero/low cost, architecturally reliable, low latency, geopolitical-market relevance, fits existing `feeds/` async-generator pattern (INV-4 preserved).

**Comprehensive evaluation:** a fuller analysis of the current `feeds/` architecture, warts worth fixing pre-expansion, Reddit's degraded-permanent state (see `PROFIT-SOURCE-001`), and expanded source candidates lives in `docs/plans/news_sources_evaluation.md`. This appendix is the short-form reference; the evaluation doc is authoritative for architectural context and sequencing rationale.

### Tier 1 — Zero cost, production-reliable, drop-in `rss_monitor.py` additions

| Source | Access | Latency | Signal notes |
|---|---|---|---|
| US State Dept / White House / Treasury OFAC / DoD press feeds | RSS | minutes | Primary sovereign-level signal; sanctions designations move Russia/Iran markets directly |
| UN News / IAEA / IMF / WTO | RSS | minutes | Resolutions, nuclear status, sanctions context |
| Foreign MFA press offices (Iran, Russia, China, Israel) | RSS | minutes | Often *first* signal on reciprocal actions |
| Al Jazeera English, France 24, DW, BBC World, Xinhua/TASS English | RSS | minutes | Regional + multilingual coverage complementing existing Reuters/AP |
| European Council / European Commission press feeds | RSS | minutes | EU-side sanctions and foreign-policy signal; complements US gov feeds for Russia/Ukraine markets |
| Congress.gov daily digest | RSS | daily | Legislative action relevant to Trump-related markets (KXMOCTRUMP*, KXTRUMPENDORSE*, KXPARDONSTRUMP*); slow cadence but unambiguous |
| Kyiv Independent / Ukrainska Pravda (English) | RSS | minutes | Fast Ukraine-specific reporting; directly supplements Guardian/AJ for KXUKR / KXRUSSIA markets |
| Iran International (English) | RSS | minutes | Iran-specific coverage from diaspora press; supplements KXIRAN / KXTRUMPIRAN markets |
| Times of Israel | RSS | minutes | Direct Israel/Gaza/Lebanon coverage for KXMIDEAST markets |
| Defense News / Breaking Defense (replace Defense One) | RSS | minutes | Defense One is already in `DISABLED_NEWS_SOURCES`; these replacements have better signal-to-noise in prior spot checks |
| Google Alerts → RSS (per market) | RSS | minutes | Per-market keyword-targeted; pairs naturally with P3.2 (`market_specificity_score`) once narrow markets are prioritized |
| GDELT extended endpoints (GKG themes, 2.0 Translingual, GDELT Cloud 2026 streaming) | API | 15min–realtime | Extension of existing `feeds/gdelt_monitor.py`; theme monitoring + push alerts add incremental signal without new infra |

### Tier 2 — Good fits, slightly more integration work

| Source | Access | Latency | Signal notes |
|---|---|---|---|
| Institute for the Study of War (ISW) | RSS | daily | Russia/Ukraine analysis; slow cadence, high quality |
| CSIS, CFR, Brookings, RAND | RSS | days | Analytical depth; treat as context/enrichment, not fast lane |
| Bellingcat, Liveuamap | RSS / JSON | hours | OSINT investigations; crowd-sourced conflict mapping |
| Polymarket cross-reference | Public GraphQL / Gamma REST | sub-second poll | Parallel prediction market; price moves can precede news — highest novel-signal leverage of any item in this appendix. **Scope pivot (2026-04-22):** the user has redefined Polymarket as a candidate **second trading venue** (peer to Kalshi), not only a news-signal lane. The venue-integration investigation is the primary design document — see [`docs/plans/polymarket_venue_integration_investigation.md`](plans/polymarket_venue_integration_investigation.md). This appendix row remains valid as the *read-only market-data observer* sub-initiative (Phase 1 of that plan), which is still the right entry point whether or not live trading follows. |
| Metaculus | REST | minutes | Probabilistic forecasts with rationale |
| Bluesky (AT Protocol) | Public API | near-realtime streaming | Open protocol; growing journalist/newswire user base; covers the "social news" gap without X/ToS issues |
| Mastodon (ActivityPub) | Public API + WebSocket stream | near-realtime | Journalist community on journa.host and mstdn.social |

### Tier 3 — Consider carefully

| Source | Access | Latency | Signal notes |
|---|---|---|---|
| Telegram channels | Bot API (free) | near-realtime | Conflict-zone reporters often post first on Telegram, especially Ukraine. Russian-language dominant. Per-channel setup cost. |
| ACLED (Armed Conflict Location & Event Data) | Free academic tier | daily | Structured event data; non-commercial license |
| USGS earthquakes / NOAA / GDACS | Public APIs | minutes | Only relevant if disaster-related Kalshi markets are being traded |
| Wikipedia Current Events portal | RSS | daily | Human-curated validation/context layer |

### Explicitly skipped / deferred

- **X/Twitter via twikit or Playwright/browser automation** — investigated separately. Diagnosis: account-ban risk + compliance ambiguity + unstable internal API + latency during breaking-news captcha spikes. Adding X won't fix the P0.3 "market-scope ceiling" — more news of the same shape gives more magnitude="none" passthroughs. Defer indefinitely; revisit only if P3 leaves a concrete signal-volume gap. **Watch-list note (2026-04-23):** retained on deferred status, but track whether a post-CAL-001 latency gap appears that only X can close (wire services often post to X before updating their own RSS in breaking-news windows). Do not integrate; do monitor for the gap.
- **NewsAPI.org, GNews, Currents, MediaStack, NewsAPI.ai** — all free tiers are 100–2000 req/day and ToS-restricted to "development." Paid tiers land at $50–$500/mo. Not zero-cost for production polling.
- **Paid aggregators (Benzinga, Bloomberg, Feedly Pro, Inoreader)** — outside the zero-cost criterion.

### Suggested post-OT&E integration order (smallest risk / highest leverage first)

1. Government RSS expansion (Tier 1, group 1–3) — weekend-scale work, zero risk, direct primary-source signal.
2. Google Alerts → RSS per high-specificity market — natural pairing with P3.2.
3. Polymarket cross-reference — highest novel signal type in the whole list; well-defined API. **See the separate [venue-integration investigation](plans/polymarket_venue_integration_investigation.md) — this item is now Phase 1 of a larger dual-venue question, not just a news-source add.**
4. Wire service regional expansion (Al Jazeera, France 24, DW, BBC) — breadth.
5. Bluesky journalist-timeline feed — covers the "social news" gap cleanly; also the recommended replacement for Reddit's firsthand / ground-level content (see `PROFIT-SOURCE-001` and `docs/plans/news_sources_evaluation.md` §7).
6. ISW / CSIS / CFR RSS — context layer.
7. GDELT extended endpoints — incremental extension to existing monitor.

Each addition should be evaluated against a stable paper-mode baseline individually, not bundled, so edge attribution stays clean.
