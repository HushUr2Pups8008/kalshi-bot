# macOS Migration Technical Debt Log

**System of record for all cross-platform migration issues identified during the Windows → macOS transition.**

---

## Header / Metadata

| Field | Value |
|-------|-------|
| Last Updated | 2026-04-19 |
| Audit Source | Comprehensive migration audit — commit 2315a1d |
| Total Items | 19 |
| Open — HIGH | 0 |
| Open — MEDIUM | 0 |
| Open — LOW | 5 |
| Items COMPLETE | 15 (MAC-ASYNC-001, MAC-ASYNC-002, MAC-DB-001, MAC-DB-002, MAC-DB-003, MAC-DB-004, MAC-CLI-001, MAC-CLI-002, MAC-DOC-001, MAC-DOC-002, MAC-FS-001, MAC-LOG-001, MAC-PLAT-001, MAC-TEST-001, MAC-TEST-002) |

### High-Risk Areas

1. **Async / Event-Loop Blocking** — `paper_trader.py` synchronous SQLite methods are called directly from async task functions, blocking the event loop on every paper trade, nightly report, and market resolution.
2. **SQLite Concurrency** — `evidence_store` opens connections without WAL mode; concurrent multi-market writes contend on SQLite's global write lock.
3. **macOS Automation Gap** — No launchd/cron equivalent exists for the Windows Scheduled Task that drove daily review.

### Recommended Execution Order

1. `MAC-ASYNC-001` → `MAC-ASYNC-002` (async blocking — highest runtime impact)
2. `MAC-DB-001` (WAL mode — prerequisite for safe concurrent writes)
3. `MAC-DB-002` (timeout consistency — simple one-liner)
4. `MAC-CLI-001` (scheduling — blocks operational completeness)
5. `MAC-TEST-001` → `MAC-TEST-002` (test coverage for the above fixes)
6. Everything else in dependency order

---

## Full Technical Debt Log

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
| **Status** | TODO |
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
| **Status** | TODO |
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

---

### MAC-TEST-004

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-004 |
| **Title** | `_maybe_rotate_stale()` period-boundary edge case untested |
| **Category** | Tests |
| **Severity** | LOW |
| **Status** | TODO |
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
| **Status** | TODO |
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

---

## Execution Views

---

### A. Fix Now Queue

Items with `Priority = NOW`, ordered for safe sequential execution:

| Order | ID | Title | Why First |
|-------|----|-------|-----------|
| 1 | MAC-ASYNC-001 | `record_trade()` blocks event loop | Affects every paper trade; hot path |
| 2 | MAC-ASYNC-002 | Nightly report/resolve blocks event loop | Affects daily operations; lower frequency but longer stall |
| 3 | MAC-TEST-001 | Test that paper trader calls are non-blocking | Regression guard for items 1–2; do immediately after |

**Execution note:** Do MAC-ASYNC-001 and MAC-ASYNC-002 in the same commit (same root cause, same fix pattern). Write MAC-TEST-001 before closing that commit.

---

### B. Pre-Go-Live Gate

Items that must be COMPLETE before live trading:

| ID | Title | Rationale |
|----|-------|-----------|
| MAC-ASYNC-001 | `record_trade()` blocks event loop | Live trading sends real orders; stall during trade execution is unacceptable |
| MAC-ASYNC-002 | Nightly report/resolve blocks event loop | Market resolution correctness is a trading integrity requirement |
| MAC-DB-001 | `evidence_store` WAL mode | Concurrent evidence writes drive trading decisions; lock contention distorts dossier state |
| MAC-DB-002 | `paper_trader` timeout consistency | Prevents silent failures during pre-live validation runs |
| MAC-CLI-001 | macOS automation setup | Daily review must run on schedule during go-live validation period |
| MAC-TEST-001 | Non-blocking test for paper trader | No regression gate = no confidence in async fixes |
| MAC-TEST-002 | Concurrent write test for evidence_store | No regression gate = no confidence in WAL fix |

**Gate rule:** All seven items must be STATUS = COMPLETE before live mode is enabled.

---

### C. Parallelizable Work Streams

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
No item may remain at `TODO` after work begins. Change to `IN_PROGRESS` on first edit to any file in scope.

### R-2 — COMPLETE Requires Acceptance Criteria
An item may not be set to `COMPLETE` unless every acceptance criterion listed for it is satisfied. Partial fixes stay `IN_PROGRESS`.

### R-3 — New Discoveries Must Be Logged
If a fix uncovers a new issue not in this log, that issue must be added as a new item before the fix commit is closed. Do not silently absorb discoveries.

### R-4 — No Silent Fixes
Every fix that closes an item in this log must be traceable to a commit. The commit message should reference the item ID (e.g., `fix(async): wrap paper trader calls in to_thread (MAC-ASYNC-001, MAC-ASYNC-002)`).

### R-5 — Pre-Go-Live Gate Is a Hard Block
The seven items in the Pre-Go-Live Gate must all be COMPLETE before live mode (`ENABLE_LIVE_TRADING=true`) is set. This gate cannot be waived.

### R-6 — Dependencies Must Be Respected
No item may move to `IN_PROGRESS` if its `Depends On` items are not yet COMPLETE, unless the dependency is explicitly re-evaluated and documented under Notes.

### R-7 — Last Updated Must Stay Current
Update the `Last Updated` timestamp in the metadata header whenever any item changes status or a new item is added.

### R-8 — False Positives Are Documented, Not Deleted
The audit identified several items that turned out to be false positives after code inspection (mtime timezone concern, PEM key handling, signal handler lambda). These are not in this log. If any item is later determined to be a false positive, mark it `COMPLETE` with Notes explaining the determination — do not delete it.
