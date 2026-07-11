# Research Dossier Connection Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `ResearchDossierStore` SQLite transaction close its connection deterministically so research prewarm cannot exhaust process file descriptors.

**Architecture:** Add one private context manager that composes SQLite transaction handling with unconditional close, then route every dossier schema/read/write method through it. Preserve the existing global write lock, per-market locks, queries, schema, and research concurrency.

**Tech Stack:** Python 3.14, `sqlite3`, `contextlib.contextmanager`, pytest, Ruff, macOS `lsof` for the operational descriptor probe.

## Global Constraints

- Database schema and SQL queries remain unchanged.
- Transaction commit and rollback semantics remain explicit.
- Connections close on success, query failure, and transaction failure.
- Tests and descriptor probes use temporary databases only.
- Research prewarm concurrency remains `3`.
- Paper mode, research shadow mode, and trade gates remain unchanged.
- Runtime artifacts under `data/`, `logs/backups/`, and `logs/state/` stay out of commits.

---

### Task 1: Close Every Dossier Connection

**Files:**
- Modify: `tasks/research_dossier.py:1-980`
- Test: `tests/test_research_dossier.py`

**Interfaces:**
- Consumes: `ResearchDossierStore._connect() -> sqlite3.Connection`.
- Produces: `ResearchDossierStore._connection() -> Iterator[sqlite3.Connection]`, a private synchronous context manager with transaction and close ownership.

- [ ] **Step 1: Write failing success and exception ownership tests**

Add a tracking connection double and two tests:

```python
class _TrackingConnection:
    def __init__(self) -> None:
        self.entered = 0
        self.exit_args = None
        self.closed = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exit_args = (exc_type, exc, traceback)
        return False

    def close(self) -> None:
        self.closed += 1


def test_connection_context_commits_and_closes(monkeypatch, tmp_path):
    store = ResearchDossierStore(tmp_path / "research.db")
    connection = _TrackingConnection()
    monkeypatch.setattr(store, "_connect", lambda: connection)

    with store._connection() as yielded:
        assert yielded is connection

    assert connection.entered == 1
    assert connection.exit_args == (None, None, None)
    assert connection.closed == 1


def test_connection_context_rolls_back_and_closes(monkeypatch, tmp_path):
    store = ResearchDossierStore(tmp_path / "research.db")
    connection = _TrackingConnection()
    monkeypatch.setattr(store, "_connect", lambda: connection)

    with pytest.raises(RuntimeError, match="boom"):
        with store._connection():
            raise RuntimeError("boom")

    assert connection.exit_args[0] is RuntimeError
    assert connection.closed == 1
```

- [ ] **Step 2: Run the ownership tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_research_dossier.py::test_connection_context_commits_and_closes \
  tests/test_research_dossier.py::test_connection_context_rolls_back_and_closes -q
```

Expected: both fail because `_connection` does not exist.

- [ ] **Step 3: Add the close-safe transaction context**

Add imports and the helper:

```python
from collections.abc import Callable, Iterator
from contextlib import contextmanager

@contextmanager
def _connection(self) -> Iterator[sqlite3.Connection]:
    conn = self._connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()
```

Keep `_connect()` responsible only for parent-directory creation, connection
creation, and row factory configuration.

- [ ] **Step 4: Migrate all nine connection call sites**

Replace every occurrence of:

```python
with self._connect() as conn:
```

with:

```python
with self._connection() as conn:
```

Verify no legacy context remains:

```bash
rg -n "with self\._connect\(\) as conn" tasks/research_dossier.py
```

Expected: no matches.

- [ ] **Step 5: Run focused tests and static checks**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_research_dossier.py \
  tests/test_research_prewarm_task.py -q
.venv/bin/ruff check tasks/research_dossier.py tests/test_research_dossier.py
git diff --check
```

Expected: all tests pass; Ruff and diff checks report no findings.

- [ ] **Step 6: Commit the descriptor repair only**

```bash
git add tasks/research_dossier.py tests/test_research_dossier.py
git commit -m "fix: close research dossier connections"
```

---

### Task 2: Prove Descriptor Bound And Restart Stability

**Files:**
- Read: `tasks/research_dossier.py`
- Read: `logs/app/launchd.stderr.log`
- No source edits unless Task 1 verification exposes a defect.

**Interfaces:**
- Consumes: the Task 1 `_connection()` ownership boundary.
- Produces: descriptor-peak evidence and one complete post-restart research cycle without `EMFILE`, `unable to open database file`, or a main-task traceback.

- [ ] **Step 1: Run the 25-market temporary-database probe**

Spawn this command while sampling numeric descriptors with `lsof -nP -p <pid>`:

```bash
.venv/bin/python scripts/research_prewarm.py \
  --max-markets 25 \
  --max-pages 5 \
  --db-path /private/tmp/research_fd_probe_after.db \
  --max-queries 6 \
  --timeout-seconds 12 \
  --json \
  --no-trade-log
```

Acceptance:

- exit code `0`;
- dossier DB descriptors peak at `12` or fewer;
- total numeric descriptors peak at `40` or fewer;
- no `too many open files` or `unable to open database file` output.

- [ ] **Step 2: Request independent review**

Review must confirm transaction semantics, unconditional close, complete call-site migration, focused test coverage, and that no dirty runtime artifact entered the commit.

- [ ] **Step 3: Restart through the authorized shell function**

Run:

```bash
zsh -ic restartbot
```

Do not use the nonexistent `botrestart` name.

- [ ] **Step 4: Verify one full research prewarm interval**

Observe at least six minutes after the boot marker. Require:

- LaunchAgent and Python PID remain running;
- `RESEARCH_PREWARM_RESULT` advances after boot;
- dossier/evidence timestamps advance;
- no `OSError: [Errno 24]`, `unable to open database file`, main traceback, or executor shutdown warning;
- paper mode and research shadow mode remain active;
- no live order.

- [ ] **Step 5: Recompute money-path state**

Run the read-only open-position mark script. Record bankroll, marked value,
equity, and drawdown. Confirm the corrected drawdown remains available to G7.

- [ ] **Step 6: Record task completion**

Append the test, descriptor, restart, and runtime evidence to the task report.
Do not mark the broader multi-issue goal complete; telemetry and research
admission defects remain separate slices.
