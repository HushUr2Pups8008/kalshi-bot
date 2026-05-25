# PROFIT-ALIGN Codex Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Codex's independent review verdict into scoped safety fixes, tests, tracker docs, and a Claude handoff.

**Architecture:** Keep the bot paper-only and avoid runtime/env mutation. Tighten the existing PROFIT-ALIGN surfaces in place: prove floor-clamp provenance before halving, key LLM dedup by prompt and cache verdict fields, make matcher downweights recoverable/compositional, centralize test isolation, and wire the three observability scaffold items.

**Tech Stack:** Python 3, pytest, existing kalshi-bot modules and trade-log writers.

---

## File Structure

- Modify `main.py`: clamp detector signature and position-drift threshold wiring.
- Modify `analysis/signal_analyzer.py`: verdict-field cache integration and probability recomputation helper.
- Modify `analysis/llm_dedup_cache.py`: prompt-hash cache identity.
- Modify `analysis/market_matcher.py`: composition-aware downweight helper.
- Modify `analysis/match_feedback.py` only if provisional seed behavior requires code support beyond existing recovery rules.
- Modify `data/matcher_token_weights.json`: mark audit seeds provisional and unpinned.
- Modify `tasks/blend_task.py`: emit lane-skipped and gate-summary events.
- Modify `utils/logger.py`: add `LANE_SKIPPED` writer.
- Modify tests under `tests/`: regression coverage and isolation fixtures.
- Modify `CHANGELOG.md` and `docs/profit_path_debt_log.md`: canonical repo docs.
- Add `docs/governance/2026-05-25-profit-align-codex-handoff.md`: Claude/operator handoff.

## Tasks

- [x] **Task 1: Floor-clamp provenance**

Write failing tests in `tests/test_main_pipeline.py` showing exact final
`0.05` / `0.95` without raw boundary crossing must not trigger halving.
Implement raw-probability reconstruction in `main._is_floor_clamp_suspected`
using market price, LLM direction, magnitude, and confidence.

- [x] **Task 2: LLM dedup cache safety**

Write failing tests in `tests/test_signal_analyzer.py` for current-price
recompute on cache hit and fresh calls when source/body differ. Change
`analysis/llm_dedup_cache.py` to hash full prompt text and store verdict
fields only. Recompute final probability in `analysis/signal_analyzer.py`.

- [x] **Task 3: Matcher downweight recovery**

Write/update tests in `tests/test_market_matcher.py` and
`tests/test_match_feedback.py` so one generic token no longer min-dominates a
multi-token match and seed weights are provisional. Implement
`_combined_token_downweight`; mark committed seed weights unpinned with
`_seed_status: provisional`.

- [x] **Task 4: Test isolation**

Move LLM cache reset into `tests/conftest.py`. Add shared
`isolated_match_feedback_weights` fixture and opt matcher/simulation baseline
tests into it.

- [x] **Task 5: Scaffold follow-through**

Wire `cfg.position_drift_alert_threshold` into the price-update drift loop.
Add `LANE_SKIPPED` writer and BlendTask emission when lane skipping is enabled.
Call `log_gate_summary` from BlendTask readiness evaluation.

- [x] **Task 6: Documentation and handoff**

Update `CHANGELOG.md`, `docs/profit_path_debt_log.md`, and add the Claude
handoff document. Include explicit review notes and verification command.

- [x] **Task 7: Verification**

Run focused tests, then relevant broader tests and lint. Update this task when
verification evidence is current.

Verification evidence:

- Focused regression set: `26 passed`.
- Affected test files:
  `tests/test_main_pipeline.py tests/test_signal_analyzer.py tests/test_market_matcher.py tests/test_match_feedback.py tests/test_blend_task.py tests/test_align_remaining.py tests/test_simulations_smoke.py`:
  `367 passed, 13 xfailed`.
- Ruff affected Python files: `All checks passed!`.
- Full suite: `2389 passed, 4 skipped, 71 xfailed, 7 warnings`.
