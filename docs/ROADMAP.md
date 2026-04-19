# Implementation Roadmap — Multi-Lane Trading Architecture

**Version:** 1.0
**Status:** ACTIVE
**Contract:** [IMPLEMENTATION_CONTRACT.md](IMPLEMENTATION_CONTRACT.md)

This is the shared, authoritative task tracker for Claude, Codex, and Jake.
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
| S1.4 | Add regime weights to market discovery path | NOT_STARTED | Codex | Attach regime vector to market objects at discovery | Extend existing market dataclass; do not change routing logic | Markets carry `regime_weights` field from discovery onward |
| S1.5 | Define `STRUCTURAL_PRIOR_RECOMPUTE` log schema | NOT_STARTED | Shared | Telemetry contract for structural layer | Must align with structural prior implementation (S3.1) | Schema defined; `utils/logger.py` extended |
| S1.6 | Add `CALIBRATION_CHECK` log event | NOT_STARTED | Claude | Foundation for cross-lane drift detection | Emitted at resolution time only; no runtime impact | Per-lane prediction error logged at resolution |

**Dependencies:** None. All S1 tasks are independent of each other and of Stage 2.

---

## Stage 2 — Evidence Store and Accumulation Lane

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| S2.1 | Design evidence store schema | NOT_STARTED | Shared | Define dossier table, evidence table, foreign keys | Must support replay (immutable event IDs); separate DB from `paper_trades.db` | Schema spec (tables, columns, indexes) |
| S2.2 | Implement `evidence_store.py` in `/tasks` | NOT_STARTED | Codex | Persistence layer with per-market async locking | Serialize writes; concurrent reads; clear interface | `get_dossier`, `update_dossier`, `add_evidence` functions |
| S2.3 | Implement `evidence_scorer.py` in `/analysis` | NOT_STARTED | Claude | Score evidence quality: source class, corr. discount, dedup | Pure function; no DB access | `score_evidence(evidence, recent_market_evidence) -> EvidenceScore` |
| S2.4 | Implement `dossier_builder.py` in `/analysis` | NOT_STARTED | Claude | Belief update: state-update vs confidence-update distinction, drift detection, displacement cap, recovery mode | Pure function; no DB; no LLM; implements BSR-1 through BSR-7 from contract | `update_dossier(current_dossier, new_evidence_score, update_type) -> Dossier` |
| S2.5 | Implement `accumulation_task.py` in `/tasks` | NOT_STARTED | Codex | Wire evidence ingestion: feed → scorer → builder → store | No trading logic; emit `EVIDENCE_INGESTION` and `DOSSIER_UPDATE` events | Running async task; dossiers persisted to `evidence_store.db` |
| S2.6 | Implement forgetting mechanisms | NOT_STARTED | Claude | Time decay, supersession, resolution clearing | Parameters must be market-type-specific; no hardcoded global TTL | Decay applied in `dossier_builder`; clearing on resolution |
| S2.7 | Implement `budget_manager.py` in `/tasks` | NOT_STARTED | Codex | Enforce per-market and global LLM call budgets with priority queue | Per-market: 4 calls/hour; global: 60 calls/hour; circuit breaker at 3× depth | `request_llm_call(market_ticker, priority) -> bool`; `BUDGET_PRESSURE` event on circuit break |
| S2.NEW | Implement `trade_readiness_gate.py` in `/tasks` | NOT_STARTED | Codex | Stateless predicate gate per contract Section 5 | All G1–G6 conditions; dossier vs fast-lane exemptions; no bypass path | `evaluate_readiness(blend_result, regime_confidence) -> ReadinessDecision` |

**Dependencies:** S2.1 before S2.2, S2.3, S2.4. S2.2 + S2.3 + S2.4 + S2.7 before S2.5. S1.1 + S1.2 before S2.5 (log schemas must exist). S2.5 before S2.NEW.

---

## Stage 3 — Structural Prior and Decision Unification

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| S3.1 | Implement `structural_prior.py` in `/analysis` | NOT_STARTED | Claude | Base-rate + context synthesis boundary | Pure function at synthesis boundary; LLM call happens in `/tasks` | `compute_structural_prior(market, context) -> PriorEstimate` |
| S3.2 | Implement `structural_task.py` in `/tasks` | NOT_STARTED | Codex | Scheduled structural recompute; emit `STRUCTURAL_PRIOR_RECOMPUTE` | Time-driven only; skip if no new structural data available | Structural priors persisted; log event emitted |
| S3.3 | Implement `decision_blender.py` in `/analysis` | NOT_STARTED | Claude | Confidence-weighted blend; dominance rule; structural fail-safe tiers | Pure function; regime weights from S1.3; implements DER-1 through DER-4 from contract | `blend(fast, accumulation, structural, regime_weights) -> BlendResult` |
| S3.4 | Implement `blend_task.py` in `/tasks` | NOT_STARTED | Codex | Read three lane outputs; call blender; evaluate readiness gate; emit `BLEND_DECISION`; produce `TradeCandidate` | No trading logic; blocked candidates logged with `trade_blocked_reason` | Blended candidates in trading queue; all blocked candidates emitting events |
| S3.5 | Extend executor to accept blended candidates | NOT_STARTED | Shared | Executor receives `signal_meta`; applies `readiness_gate_min_edge_override` only; logs all other fields | All existing safety gates intact; no new decision logic in `/trading`; highest-risk task — explicit confirmation required before starting | Executor handles both candidate types; no behavioral regression |
| S3.6 | Cross-lane calibration monitoring | NOT_STARTED | Claude | Build `CALIBRATION_CHECK` consumer; detect drift; scale confidence | Read-only; no behavior change unless drift threshold exceeded | Calibration curves per lane; drift alerts; auto-scaling of effective confidence |

**Dependencies:** S1.3 before S3.3. S2.5 before S3.4. S3.3 + S3.2 before S3.4. S3.4 + S2.NEW before S3.5 (gate must exist before executor extension). S1.5 before S3.2. S1.6 before S3.6.

---

## Stage 4 — Hardening and Validation

| ID | Task | Status | Owner | Purpose | Constraints | Expected Outcome |
|----|------|--------|-------|---------|-------------|-----------------|
| S4.1 | Replay utility for dossier reconstruction | NOT_STARTED | Codex | Verify dossier auditability from event log | Read-only offline tool | CLI tool that replays belief trajectory for a market from its evidence event chain |
| S4.2 | Observability completeness review | NOT_STARTED | Shared | Confirm all `BLEND_DECISION` fields populated; traceability chain intact | Run against paper trading; compare event completeness | All required fields non-null for ≥ 90% of events |
| S4.3 | Budget manager stress test | NOT_STARTED | Codex | Verify circuit breaker fires under synthetic load | Inject synthetic high-volume queue; confirm `BUDGET_PRESSURE` emitted | Circuit breaker fires at 3× depth; no runaway LLM calls |
| S4.4 | Regime weight validation against historical outcomes | NOT_STARTED | Claude | Check that regime weights improve blended calibration vs unweighted blend | Use `windows_archive` data for backtesting | Calibration improvement documented |
| S4.5 | End-to-end paper trading test: multi-lane | NOT_STARTED | Shared | Run system in paper mode with all three lanes active | All existing paper-trade safety gates must pass; trade frequency must not exceed 2× fast-lane-only baseline | Bot operates in paper mode; all three lanes emit events; no execution errors; Definition of Done criteria from contract Section 13 satisfied |

**Dependencies:** All Stage 3 tasks COMPLETE before Stage 4 begins. S4.5 is the final integration gate before any live trading consideration.

---

## Notes

- **S3.5** requires explicit confirmation before starting. It is the only task that touches `/trading`.
- **S2.NEW** (`trade_readiness_gate.py`) was added during the final precision refinement pass. It is a prerequisite for S3.4 and S3.5.
- The contract in `IMPLEMENTATION_CONTRACT.md` governs all tasks. Any apparent conflict between a task description here and the contract must be raised before implementation proceeds.
