# Dynamic Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a rolling feedback loop that keeps matcher, freshness, funnel, and market-mix evidence current without silently changing trading gates.

**Architecture:** Emit structured matcher-weight telemetry at decision time, then add read-only rolling reports that turn trade logs and feedback DB state into repeatable summaries. Behavioral adaptation remains bounded to existing provisional matcher-weight recovery; no readiness, sizing, launchd, or paper/live changes happen in this plan.

**Tech Stack:** Python, pytest, JSONL trade logs, SQLite matcher feedback DB, existing `utils.logger.TradeLogger`, existing `analysis.match_feedback` aggregation.

---

### Task 1: Matcher-Weight Telemetry

**Files:**
- Modify: `analysis/market_matcher.py`
- Modify: `utils/logger.py`
- Test: `tests/test_market_matcher.py`

- [ ] Write a failing test that patches `analysis.market_matcher.trade_log.log_match_weight_applied`, supplies weights for a multi-token overlap, runs `MarketMatcher.find_candidates`, and asserts one event contains `tokens`, `token_weights`, `composition_rule="mean"`, `final_multiplier`, and `weight_status`.
- [ ] Run: `pytest tests/test_market_matcher.py -k match_weight -q`; expected: fail because `log_match_weight_applied` is missing.
- [ ] Add `TradeLogger.log_match_weight_applied(**fields)` that writes a `MATCH_WEIGHT_APPLIED` event.
- [ ] Add a helper near `_combined_token_downweight` that returns both the mean multiplier and per-token details, and call the logger whenever overlap tokens exist.
- [ ] Run: `pytest tests/test_market_matcher.py -k match_weight -q`; expected: pass.
- [ ] Commit: `feat(match): emit matcher weight telemetry`.

### Task 2: Feedback Weight Status Summary

**Files:**
- Modify: `analysis/match_feedback.py`
- Test: `tests/test_match_feedback.py`

- [ ] Write failing tests for a pure function that summarizes weight entries into counts for `provisional`, `pinned`, `automatic`, and `recovered`.
- [ ] Run: `pytest tests/test_match_feedback.py -k weight_status -q`; expected: fail because summary helper is missing.
- [ ] Implement `summarize_weight_status(weights)` and ensure it treats missing metadata as `automatic`.
- [ ] Run: `pytest tests/test_match_feedback.py -k weight_status -q`; expected: pass.
- [ ] Commit: `feat(match): summarize feedback weight status`.

### Task 3: Daily Conversion Funnel Report

**Files:**
- Create: `scripts/pipeline_feedback_report.py`
- Test: `tests/test_pipeline_feedback_report.py`

- [ ] Write failing tests that feed temp JSONL logs and assert stage counts, top reasons, and per-ticker counts.
- [ ] Run: `pytest tests/test_pipeline_feedback_report.py -q`; expected: fail because script module is missing.
- [ ] Implement reusable functions `summarize_events(paths)` and `main(argv)` with JSON output.
- [ ] Run: `pytest tests/test_pipeline_feedback_report.py -q`; expected: pass.
- [ ] Commit: `feat(feedback): add pipeline funnel report`.

### Task 4: Source Freshness Audit

**Files:**
- Modify: `scripts/pipeline_feedback_report.py`
- Test: `tests/test_pipeline_feedback_report.py`

- [ ] Add failing tests asserting `EARLY_STALE_DROP` and `EARLY_FRESH_PASS` are grouped by source/source_class and reason.
- [ ] Run: `pytest tests/test_pipeline_feedback_report.py -k freshness -q`; expected: fail.
- [ ] Extend the report with a `freshness` section and CLI mode/filter.
- [ ] Run: `pytest tests/test_pipeline_feedback_report.py -k freshness -q`; expected: pass.
- [ ] Commit: `feat(feedback): add source freshness audit`.

### Task 5: Market-Mix / LLM-Neutral Audit

**Files:**
- Modify: `scripts/pipeline_feedback_report.py`
- Test: `tests/test_pipeline_feedback_report.py`

- [ ] Add failing tests that aggregate `MATCH_DIAGNOSTIC`, `MATCH_LLM_REVIEW`, `SIGNAL_ANALYSIS_DETAIL`, `SIGNAL`, and `OPPORTUNITY` by market prefix and source class.
- [ ] Run: `pytest tests/test_pipeline_feedback_report.py -k market_mix -q`; expected: fail.
- [ ] Extend the report with a `market_mix` section including neutral review counts and signal/opportunity conversion counts.
- [ ] Run: `pytest tests/test_pipeline_feedback_report.py -k market_mix -q`; expected: pass.
- [ ] Commit: `feat(feedback): add market mix audit`.

### Task 6: Planning Tracker Closure

**Files:**
- Modify: `docs/profit_path_debt_log.md`

- [ ] Update `PROFIT-PIPELINE-001` to record implemented feedback-loop surfaces and commit SHAs.
- [ ] Run: `git diff --check`.
- [ ] Commit: `docs(debt): update dynamic feedback loop tracker`.

### Task 7: Final Verification and Operations

**Files:**
- No source edits expected.

- [ ] Run targeted pytest for changed areas.
- [ ] Run ruff for changed Python files.
- [ ] Run `git status --short` and confirm only intentional changes are staged/committed.
- [ ] Push branch.
- [ ] Merge to `main`, sync local `main`, and restart the bot per operator approval.
