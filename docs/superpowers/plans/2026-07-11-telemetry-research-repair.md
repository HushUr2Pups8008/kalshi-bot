# Telemetry And Research Admission Repair Plan

**Goal:** Make decision-grade paper admission at-most-once, active/unexpired,
source-diverse, truthfully attributed, and traceable through every money-path event.

**Safety boundary:** Paper/research-shadow only. No sizing, live-order, threshold,
credential, or runtime-artifact changes. SQLite migrations are additive. Runtime
artifacts under `data/matcher_token_weights.json`, `logs/backups/`, and `logs/state/`
remain outside commits.

## Task 1: Shared Fail-Closed Policies

**Files:**
- Create `utils/research_market_eligibility.py`
- Create `utils/research_evidence_quality.py`
- Create `tests/test_research_market_eligibility.py`
- Create `tests/test_research_evidence_quality.py`

1. Add RED tests for status/close-time normalization and source identity/class rules.
2. Implement active/open plus strictly future close-time eligibility.
3. Implement two identities, two classes, and one official class, preserving only
   the existing structured CPI/GDPNow/NWS exception.
4. Run focused pytest and Ruff; commit this isolated slice.

## Task 2: Durable Admission Ownership

**Files:**
- Modify `tasks/research_dossier.py`
- Modify `tests/test_research_dossier.py`

1. Add RED tests for additive market status/close columns and admission receipts.
2. Add `research_paper_admissions` with composite primary key over ticker, run, and
   fingerprint.
3. Implement atomic insert-or-ignore claim plus completed/failed outcome update.
4. Preserve `_connection()` transaction and unconditional-close semantics.
5. Prove sequential, concurrent, and reopened-store dedup with a temporary DB.
6. Run focused pytest/Ruff and independent review; commit this slice.

## Task 3: Prewarm And Persistence Eligibility

**Files:**
- Modify `analysis/research_gate.py`
- Modify `tasks/research_prewarm_task.py`
- Modify `main.py`
- Modify their focused tests

1. Add RED tests for active-but-expired markets, missing close time, and terminal
   task persistence.
2. Replace status-only research checks with the shared eligibility helper.
3. Persist current market status/close time on every research observation without
   demoting retained decision-grade snapshots.
4. Keep explicitly terminal due tasks reachable once for terminalization.
5. Run focused pytest/Ruff; commit only the owned paths.

## Task 4: Atomic Bridge Admission And Attribution

**Files:**
- Modify `tasks/research_paper_admission.py`
- Modify `tests/test_research_paper_admission.py`

1. Add RED tests for live eligibility, run/fingerprint coherence, sequential and
   concurrent dedup, crash/failed receipt behavior, and source attribution.
2. Validate current market and current price before claiming.
3. Require exact result/snapshot run and fingerprint coherence, then atomically
   claim before opportunity emission or routing.
4. Complete the receipt with route outcome; preserve failed/claimed rows as
   non-retryable, operator-auditable records.
5. Derive trigger evidence ID and settlement-source match from the same evidence;
   remove hardcoded truth.
6. Explicitly close the fallback read-only SQLite connection.
7. Run focused pytest/Ruff and independent review; commit this slice.

## Task 5: Validation And Rollout Eligibility

**Files:**
- Modify `scripts/research_profit_validation_loop.py`
- Modify `scripts/research_rollout_gate.py`
- Modify related tests and report assertions

1. Add RED tests for expired, inactive, missing-schema, and source-class failures.
2. Join proof rows to the exact latest dossier run and apply shared eligibility at
   the injected evaluation time.
3. Add an explicit market-ineligible blocker metric; never count blocked rows as
   decision-grade or live-cache eligible.
4. Remove permissive rollout fallback when eligibility metadata is unavailable.
5. Run focused pytest/Ruff; commit this slice.

## Task 6: Lifecycle Telemetry Continuity

**Files:**
- Create `utils/lifecycle.py`
- Modify Kalshi matcher/main, Polymarket paper runtime, blend, logger, executor/paper
  transport surfaces, research admission, and focused tests

1. Add RED tests for deterministic lifecycle IDs and `bool | None` settlement
   attribution across origin, blend, skipped, paper, and live records.
2. Generate one stable ID per venue/news/ticker lineage and transport it only via
   explicit metadata fields.
3. Emit missing Polymarket match/opportunity events without routing through Kalshi
   assumptions.
4. Promote lifecycle and attribution fields to top-level JSONL while keeping
   nested metadata backward-compatible.
5. Add a cross-layer assertion that all six event types share exactly one ID.
6. Run focused pytest/Ruff and independent review; commit this slice.

## Task 7: Integration And Runtime Proof

1. Run all dirty-surface tests, full pytest, Ruff, and diff checks.
2. Run a temporary-DB concurrent admission probe and descriptor sample.
3. Obtain independent review of the complete diff and remediate Important findings.
4. Create a scoped PR without runtime artifacts; require CI and replay-gate evidence.
5. Merge, fast-forward main, run the authorized `restartbot`, and observe at least
   one full prewarm interval.
6. Require zero SQLite/descriptor errors, zero expired/inactive admissions, and no
   duplicate claim routing. Keep the broader profitability goal open until organic
   decision-grade and paper conversion evidence exists.
