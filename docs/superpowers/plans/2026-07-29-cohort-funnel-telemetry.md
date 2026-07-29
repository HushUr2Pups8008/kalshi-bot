# Cohort Funnel Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attribute fresh paper-cohort funnel outcomes without changing matching, sizing, admission, or live-trading behavior.

**Architecture:** Bind the selected cohort once to `TradeLogger`, reuse it for two Polymarket-only observability records, and add a separate read-only pending-cohort section to botcheck. Lock fresh-pending G7 scope with a startup regression test.

**Tech Stack:** Python 3.14, pytest, SQLite read-only inspection, JSONL logs.

## Global Constraints

- Do not change match scores, candidate limits, market horizons, sizing, paper admission, or live flags.
- `legacy_pending` remains permanently paper-only and all live-transition blocks remain fail-closed.
- Botcheck must not create, migrate, or mutate a database.
- Historical JSONL records without cohort fields remain valid.
- Tests use `BANKROLL=500` and both live flags set to `false`.

---

### Task 1: Bind runtime cohort lineage

**Files:** Modify `utils/logger.py:555-563`, `main.py:984-997`; test `tests/test_log_records.py`.

**Interfaces:** Add `TradeLogger.bind_runtime_context(*, cohort_id: str, cohort_kind: str) -> None`. `_write()` adds these fields only after binding.

- [ ] Write failing tests:
    def test_trade_logger_binds_runtime_cohort_context(tmp_path):
        logger = TradeLogger(tmp_path / "trades.jsonl")
        logger.bind_runtime_context(cohort_id="legacy-pending-20260729", cohort_kind="legacy_pending")
        logger.log_signal(source="Reuters", headline="Example", url="https://example.test", signal_strength=0.5, keywords_matched=["example"])
        record = json.loads((tmp_path / "trades.jsonl").read_text())
        assert record["cohort_id"] == "legacy-pending-20260729"
        assert record["cohort_kind"] == "legacy_pending"
- [ ] Add an unbound logger test asserting both fields are absent.
- [ ] Run `pytest tests/test_log_records.py -k runtime_cohort_context -q`; it must fail before the method exists.
- [ ] Implement `_runtime_context`, `bind_runtime_context`, and `{**self._runtime_context, **record}` before timestamping. Bind `trade_log` immediately after `TradingBot` resolves `self.paper_cohort`.
- [ ] Run `pytest tests/test_log_records.py tests/test_main_startup.py -q`.
- [ ] Commit: `git commit -m "feat(logging): bind runtime paper cohort context"`.

### Task 2: Emit Polymarket no-candidate and cache records

**Files:** Modify `utils/logger.py`, `polymarket/paper_runtime.py:277-301,494-528`; test `tests/polymarket/test_paper_runtime.py` and `tests/test_log_records.py`.

**Interfaces:** Add `log_match_no_candidate(...)` with reasons `no_eligible_markets`, `no_match`, and `market_fetch_failed`. Add `log_polymarket_market_cache(...)` with `raw_fetched`, `cursor_present`, `eligible_30d`, `candidate_within_admission_horizon`, `admission_horizon_days`, and `market_limit`.

- [ ] Write an empty-cache `process_news()` test expecting one `MATCH_NO_CANDIDATE` event with `reason == "no_eligible_markets"`.
- [ ] Write a no-score-match test expecting `reason == "no_match"` and the eligible cache count.
- [ ] Write a warm-cache test with raw=3, cursor present, eligible_30d=2, candidate_within_admission_horizon=1, and admission_horizon_days=14.
- [ ] Run `pytest tests/polymarket/test_paper_runtime.py -k 'no_candidate or cache_refresh' -q`; it must fail before the event methods exist.
- [ ] At the two zero-route returns in `process_news`, append `MATCH_NO_CANDIDATE`; after the existing 30-day filter in `_get_markets`, count the existing admission-horizon predicate and append `POLYMARKET_MARKET_CACHE`.
- [ ] Do not fetch another page, alter `_markets`, or alter any return value.
- [ ] Run `pytest tests/polymarket/test_paper_runtime.py tests/test_log_records.py tests/test_match_quality_diagnostics.py -q`.
- [ ] Commit: `git commit -m "feat(observability): record polymarket funnel exits"`.

### Task 3: Add read-only pending-cohort botcheck

**Files:** Modify `scripts/botcheck.py:69,260-327,1793-1821`; test `tests/test_botcheck.py`.

**Interfaces:** Add `summarize_pending_paper_cohorts(data_dir: Path) -> dict[str, object]` and `print_pending_paper_cohort_section(summary: dict[str, object]) -> None`. Add the two Polymarket event names to `SIGNAL_FLOW_EVENTS`.

- [ ] Write an absent-root test asserting `status == "absent"` and no directory creation.
- [ ] Write a valid manifest test asserting a cohort ID and database path are reported.
- [ ] Write malformed-manifest and symlink-root tests asserting an invalid status without database access.
- [ ] Write a signal-flow test that counts one `MATCH_NO_CANDIDATE` and one `POLYMARKET_MARKET_CACHE` row.
- [ ] Run `pytest tests/test_botcheck.py -k 'pending_cohort or polymarket_terminal' -q`; it must fail before the helpers exist.
- [ ] Implement only `Path.lstat()`, regular-file checks, and JSON parsing. Never instantiate `PaperTrader`, a provisioner, or a SQLite initializer. Print this section after signal flow and retain legacy settlement reporting unchanged.
- [ ] Run `pytest tests/test_botcheck.py -q`.
- [ ] Commit: `git commit -m "feat(botcheck): report pending paper cohorts"`.

### Task 4: Lock cohort-local pending G7

**Files:** Modify `tests/test_main_startup.py:171-224`.

- [ ] Add `test_pending_g7_snapshot_is_scoped_to_pending_runtime_db_not_legacy_baseline` using the existing pending fixture.
- [ ] Assert discovery contains `legacy-pending-baseline` plus the pending cohort, patch `scripts.mark_open_positions.compute_open_position_marks`, and reject every path except `runtime.db_path`.
- [ ] Assert the fresh runtime snapshot has zero drawdown when the pending DB is empty.
- [ ] Run `pytest tests/test_main_startup.py -k pending_g7_snapshot -q` and then `pytest tests/test_main_startup.py tests/test_paper_cohorts.py tests/test_paper_trader.py -q`.
- [ ] Commit: `git commit -m "test(paper): lock pending cohort G7 isolation"`.

### Task 5: Verify and publish

- [ ] Run `ruff check utils/logger.py main.py polymarket/paper_runtime.py scripts/botcheck.py tests/test_log_records.py tests/polymarket/test_paper_runtime.py tests/test_botcheck.py tests/test_main_startup.py`.
- [ ] Run `python -m py_compile utils/logger.py main.py polymarket/paper_runtime.py scripts/botcheck.py` and `git diff --check`.
- [ ] Run `pytest tests/test_log_records.py tests/polymarket/test_paper_runtime.py tests/test_botcheck.py tests/test_main_startup.py tests/test_paper_cohorts.py tests/test_paper_trader.py -q`.
- [ ] Run `pytest -q`; confirm any installed-launchd-template failure also reproduces on unchanged `main`.
- [ ] Push `feat/cohort-funnel-telemetry` and open a draft PR titled `feat(observability): add cohort funnel telemetry`.
