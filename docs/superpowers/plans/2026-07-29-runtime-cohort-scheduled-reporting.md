# Runtime Cohort Scheduled Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure scheduled reporting never presents legacy or aggregate paper results as the current daemon's cohort. The scheduled path must either use verified runtime cohort provenance for both paper DB and JSONL metrics, or explicitly withhold the unsafe report.

**Architecture:** Extend the existing process-held `data/bot_runtime.lock` after `TradingBot` resolves its cohort, with a nested, versioned lineage record bound to the original PID and boot timestamp. `bothealth.sh` validates that provenance against the live launchd process and derives the DB path from the validated ID/kind; it never reads a path from the lock or config. `daily_review.py` receives the canonical lineage pair and derived DB, materializes a temporary pair-filtered log for all report readers, and deletes it after rendering. Missing, malformed, unlocked, or mismatched provenance skips the scheduled daily report. `performance_analysis.py` remains withheld from the scheduled path until it supports the same DB/log scope.

**Tech Stack:** Python 3.14, Bash, JSON, pytest, existing shell fixture tests.

## Global Constraints

- No changes to trading gates, thresholds, sizing, databases, daemon configuration, or launchd state.
- Never derive scheduled-report scope from `.env` or `cfg`; only process-held runtime-lock provenance is authoritative.
- Do not fall back to config or an aggregate daily/performance report when runtime provenance cannot be verified.
- Keep the `runtime_paper_cohort_id` / `runtime_paper_cohort_kind` pair exact and fail closed on missing or malformed log provenance.
- Preserve root-DB P0/readiness checks only as explicitly labeled global historical gates; do not use them as runtime-cohort P&L.

---

### Task 1: Bind cohort provenance to the held runtime lock

**Files:**
- Modify: `main.py:585-650`, `main.py:4297-4318`
- Test: `tests/test_main_startup.py:516-570`, `tests/test_main_startup.py:1446-1475`

**Interfaces:**
- Produces: `_RuntimeInstanceGuard.bind_runtime_paper_cohort(cohort_id: str, cohort_kind: str) -> None`
- Produces: nested lock JSON `runtime_paper_cohort={schema_version, cohort_id, cohort_kind, owner_pid, boot_started_utc}`
- Consumes: `TradingBot.paper_cohort.cohort_id` and `_configured_paper_cohort_kind()` after successful bot construction.

- [x] **Step 1: Write the failing lock-metadata test**

```python
guard = _RuntimeInstanceGuard(lock_path)
assert guard.acquire() is True
guard.bind_runtime_paper_cohort("legacy-pending-20260729", "legacy_pending")
payload = json.loads(lock_path.read_text(encoding="utf-8"))
assert payload["runtime_paper_cohort"] == {
    "schema_version": 1,
    "cohort_id": "legacy-pending-20260729",
    "cohort_kind": "legacy_pending",
    "owner_pid": payload["pid"],
    "boot_started_utc": payload["started_utc"],
}
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main_startup.py::test_runtime_instance_guard_binds_runtime_paper_cohort -q`

Expected: FAIL because the guard has no binding method.

- [x] **Step 3: Implement the minimal lock update and boot call**

```python
runtime_guard.bind_runtime_paper_cohort(
    bot.paper_cohort.cohort_id,
    _configured_paper_cohort_kind(),
)
```

The method must only rewrite metadata while the guard owns the lock and retain its original `pid`, `cwd`, `started_utc`, and `argv` fields.

- [x] **Step 4: Run focused startup tests**

Run: `python -m pytest tests/test_main_startup.py::test_runtime_instance_guard_binds_runtime_paper_cohort tests/test_main_startup.py::test_async_main_runtime_path_uses_runtime_context_for_boot_logging -q`

Expected: PASS.

### Task 2: Scope scheduled daily review from verified lock provenance

**Files:**
- Modify: `scripts/bothealth.sh:145-152`, `scripts/bothealth.sh:203-242`, `scripts/bothealth.sh:357-429`, `scripts/bothealth.sh:481-612`
- Test: `tests/shell/test_bothealth_verdict.sh`

**Interfaces:**
- Consumes: the held lock's root PID/cwd/boot fields and nested runtime-cohort provenance.
- Produces: `daily_review.py --runtime-paper-cohort-id <id> --runtime-paper-cohort-kind <kind> --paper-db <derived-path>` only when the lock is held, PID equals launchd, and metadata is canonical.
- Produces: runtime-money sections from the selected DB only; leaves global root-DB P0/readiness checks explicitly labeled.
- Produces: bothealth report line `daily_review cohort_scope=skipped reason=<reason>` on any validation failure.
- Produces: `performance_analysis=skipped reason=unscoped_db_and_log` until that script accepts the same scope contract.

- [x] **Step 1: Write failing shell fixtures**

```bash
cat >"$fixture/data/bot_runtime.lock" <<'EOF'
{"pid":123,"cwd":"$fixture","started_utc":"2026-07-29T10:37:05+00:00","argv":["main.py"],"runtime_paper_cohort":{"schema_version":1,"cohort_id":"legacy-pending-20260729","cohort_kind":"legacy_pending","owner_pid":123,"boot_started_utc":"2026-07-29T10:37:05+00:00}}
EOF
run_bothealth "$fixture" --daily-review
assert_contains "$(cat "$fixture/daily_review.args")" "--runtime-paper-cohort-id"
assert_contains "$(cat "$fixture/daily_review.args")" "--runtime-paper-cohort-kind"
assert_contains "$(cat "$fixture/daily_review.args")" "--paper-db"
```

Use a real Python child holding the lock in the fixture. Add mismatched-PID, invalid lineage, and unlocked-lock fixtures that assert no daily artifact and a stable skipped reason.

- [x] **Step 2: Run shell test to verify it fails**

Run: `bash tests/shell/test_bothealth_verdict.sh`

Expected: FAIL because bothealth does not inspect runtime cohort metadata or forward the flag.

- [x] **Step 3: Implement one-pass lock parsing and fail-closed invocation**

Use the existing `python_bin` helper to parse and non-blockingly validate the held lock. Validate exact PID, `cwd`, argv, boot binding, cohort ID `[a-z0-9][a-z0-9-]{0,63}`, kind `legacy|active|legacy_pending`, pair consistency, and a regular non-symlink derived DB path. Build a Bash argument array only for a verified result.

- [x] **Step 4: Run shell verification**

Run: `bash tests/shell/test_bothealth_verdict.sh`

Expected: PASS, including verified and unavailable paths.

### Task 3: Scope every daily-review data source to the canonical lineage pair

**Files:**
- Modify: `scripts/decision_funnel_summary.py:124-162`, `scripts/decision_funnel_summary.py:918-1040`, `scripts/daily_review.py:101-140`, `scripts/daily_review.py:888-945`, `scripts/daily_review.py:1184-1310`
- Test: `tests/test_decision_funnel_summary.py`, `tests/test_daily_review.py`, `tests/test_reporting_window_hardening.py`

**Interfaces:**
- Accepts a canonical runtime cohort ID/kind pair and its exact deterministic DB path only.
- Rejects lone, blank, whitespace-padded, invalid, or mismatched lineage values before report generation.
- Uses a 0600 temporary filtered JSONL for all log-backed daily readers, then deletes it in `finally`.

- [x] **Step 1: Write failing padded-input tests**

```python
monkeypatch.setattr(sys, "argv", ["daily_review.py", "--runtime-paper-cohort-id", " legacy-pending-20260729 ", "--runtime-paper-cohort-kind", "legacy_pending"])
with pytest.raises(SystemExit, match="canonical"):
    daily_review.parse_args()
```

- [x] **Step 2: Implement shared canonical validation behavior**

Use one strict lineage validator in report code; do not silently normalize values before exact log comparison. Filter by both logger fields so an ID reused under another kind cannot contaminate attribution.

- [x] **Step 3: Run report-focused tests**

Run: `python -m pytest tests/test_decision_funnel_summary.py tests/test_daily_review.py -q`

Expected: PASS.

### Task 4: Verify real runtime attribution and review scope

**Files:**
- Verify only: `logs/trades/live/trades.jsonl`, `data/bot_runtime.lock`, generated scheduled report

- [x] **Step 1: Run static validation**

Run: `ruff check main.py scripts/bothealth.sh scripts/decision_funnel_summary.py scripts/daily_review.py tests/test_main_startup.py tests/test_decision_funnel_summary.py tests/test_daily_review.py && git diff --check`

Expected: no lint or whitespace errors.

- [x] **Step 2: Run focused regression suites**

Run: `pytest tests/test_main_startup.py tests/test_decision_funnel_summary.py tests/test_daily_review.py -q && bash tests/shell/test_bothealth_verdict.sh`

Expected: all selected tests pass.

- [ ] **Step 3: Verify live-log report behavior without changing runtime**

Run: `python scripts/decision_funnel_summary.py --path logs/trades/live/trades.jsonl --runtime-paper-cohort-id <verified-lock-id> --runtime-paper-cohort-kind <verified-lock-kind>`

Expected: selected scope retains only exact-lineage records for cohort metrics, explicitly counts excluded historical untagged or wrong-kind rows, and scheduled daily review uses only the verified paper DB and filtered log.
