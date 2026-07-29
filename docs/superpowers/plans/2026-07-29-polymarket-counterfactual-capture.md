# Polymarket Counterfactual Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist bounded, shadow-only counterfactual evidence for rejected
post-admission Polymarket candidates.

**Architecture:** The existing matcher accumulates atomic rejected-candidate
facts during its single scoring pass. `process_news` attaches the optional
snapshot to the existing no-candidate event only after stage classification.
Logger and reports treat the nested schema as additive and fail closed.

**Tech Stack:** Python, JSONL telemetry, pytest, Ruff.

## Global Constraints

- No matching, threshold, weight, admission, execution, or live-mode change.
- No second market fetch, matcher pass, or weight-log call.
- Snapshot is optional and bounded to four candidates. It retains a
  `matched_token_count`, never raw matched-token values.
- No article body, URL, query string, market description, public comments, or
  raw news/body-derived token values. Candidate titles come only from sanitized
  `market.title`, never composite match text.
- Invalid snapshots must not invalidate existing no-candidate aggregates.

---

### Task 1: Capture Atomic Rejected Candidates

**Files:**
- Modify: `polymarket/paper_runtime.py`
- Test: `tests/polymarket/test_paper_runtime.py`

**Interfaces:**
- Consumes: `_match_polymarket_markets_with_rejection_telemetry()` inputs.
- Produces: `_PostAdmissionRejectionTelemetry.as_log_fields()` with optional
  `post_admission_counterfactual_shadow`.

- [x] **Step 1: Write failing runtime tests**

Assert no-overlap candidates include identity/optional title and zero
matched-token count; empty-match-text candidates are explicitly classified;
low-score and weight-demoted candidates include atomic pre/post scores; the
local `candidate_count_total` partitions into captured and omitted counts;
`max_candidates=0` emits no snapshot.

- [x] **Step 2: Run the focused tests to verify they fail**

Run: `/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/polymarket/test_paper_runtime.py -q`

Expected: failures for the absent nested snapshot.

- [x] **Step 3: Implement the single-pass accumulator**

Build candidate summaries inside the existing admitted-market loop. Preserve
all score math and return behavior. Sort/cap summaries only after the loop;
emit them only when `qualifying_match_count == 0`.

- [x] **Step 4: Run focused runtime tests**

Run the command from Step 2. Expected: pass.

### Task 2: Persist and Aggregate the Optional Schema

**Files:**
- Modify: `utils/logger.py`
- Modify: `scripts/decision_funnel_summary.py`
- Modify: `scripts/daily_review.py`
- Test: `tests/test_log_records.py`
- Test: `tests/test_decision_funnel_summary.py`
- Test: `tests/test_daily_review.py`

**Interfaces:**
- Consumes: optional `post_admission_counterfactual_shadow` dictionary.
- Produces: additive JSONL field and aggregate snapshot coverage metrics.

- [x] **Step 1: Write failing schema/report tests**

Test optional logger persistence, valid coverage aggregation, legacy absence,
and malformed nested snapshots that become unavailable without corrupting the
flat rejection aggregate.

- [x] **Step 2: Run tests to verify they fail**

Run: `/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_log_records.py tests/test_decision_funnel_summary.py tests/test_daily_review.py -q`

Expected: failures for absent optional field and coverage metrics.

- [x] **Step 3: Implement additive serialization and fail-closed parser**

Serialize the field only when supplied. Use a strict allowlist, ASCII
canonicalization, payload-size limit, and finite numeric validation. Validate
version, cap, local counts, candidate uniqueness, reason/score consistency,
and truncation. Whitelist the four fixed rejection reasons; require the
snapshot total to match the top-level within-horizon denominator. Add coverage
only to funnel and daily reports; do not render raw candidates.

- [x] **Step 4: Run the Task 2 suite**

Run the command from Step 2. Expected: pass.

### Task 3: Verify and Deliver

**Files:**
- Test: `tests/test_report_snapshots.py`

- [x] **Step 1: Update snapshot expectation if report output changes**

Use the generated report output as the only fixture source.

- [x] **Step 2: Run affected verification**

Run: `/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/polymarket/test_paper_runtime.py tests/test_log_records.py tests/test_decision_funnel_summary.py tests/test_daily_review.py tests/test_report_snapshots.py -q`

Expected: all pass.

- [x] **Step 3: Run static checks**

Run: `/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check polymarket/paper_runtime.py utils/logger.py scripts/decision_funnel_summary.py scripts/daily_review.py tests/polymarket/test_paper_runtime.py tests/test_log_records.py tests/test_decision_funnel_summary.py tests/test_daily_review.py`

Expected: `All checks passed!`

- [x] **Step 4: Commit the scoped change**

Run `git diff --check`, stage only the listed source/tests/docs, and commit
with `feat(polymarket): capture rejected candidate evidence`.
