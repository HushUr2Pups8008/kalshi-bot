# G7 Skip Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

## Constraints

- Work only in this isolated worktree.
- Preserve `LIVE_TRADING_ENABLED=false`; do not alter G7 thresholds, admission,
  sizing, settlement, feedback, or execution behavior.
- Capture is default-off and append-only. Do not create candidates or reuse the
  drawdown-only capital-guard shadow schema.
- Use tests first for each behavioral change.

## Task 1: Add Isolated Append-Only G7 Receipts

**Files:** new `trading/g7_skip_evidence.py`, new tests.

1. Implement strict receipt dataclasses, canonical JSON/SHA identity, and
   `observed`/`unavailable`/`not_queried` validation.
2. Implement an isolated SQLite v1 schema, exact schema integrity checks,
   append-only triggers, exact idempotency, and conflict rejection.
3. Test invalid timestamps, hashes, side/metadata mismatches, trigger immutability,
   duplicate replay, and malformed rows.

## Task 2: Capture After Final G7 Decision

**Files:** new `tasks/g7_skip_evidence_capture.py`, `config.py`, `main.py`,
`tasks/blend_task.py`, capture/main tests.

1. Add a default-false capture flag and isolated sink construction.
2. Add an optional sink to `BlendTask`; call it only after a final blocked
   decision with at least one G7 failure other than sole open-exposure drawdown.
3. Build receipts solely from final readiness facts and the existing
   `g7_execution_liquidity` metadata. Missing data becomes typed
   unavailable/not-queried evidence; do not refetch or synthesize a book.
4. Ensure errors are non-blocking and cannot change the final gate decision.
5. Test zero liquidity, momentum-not-queried, unavailable reader, disabled
   wiring, and no-capture non-G7 blocks.

## Task 3: Add Read-Only Classification and Status

**Files:** new `scripts/g7_skip_evidence_replay.py`, `scripts/botcheck.py`,
new report/botcheck tests.

1. Emit a validated JSON/text summary that classifies the four evidence states.
2. Add botcheck’s read-only flag/store integrity and count output.
3. Test no store, corrupt store, valid observed receipts, and no profit/readiness
   inference.

## Task 4: Verification and Review

1. Run all new and affected blend, main, botcheck, store, and replay tests
   with `CI=1`.
2. Run Ruff on changed modules and `git diff --check`.
3. Obtain independent review focused on persistence isolation, runtime default,
   G7 non-interference, and inference safety.
4. Version, changelog, commit, PR, and merge only after the evidence boundary is
   proven. Do not restart or enable capture as part of this plan.
