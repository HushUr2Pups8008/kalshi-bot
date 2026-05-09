# OQ1 / P2-07 — `analysis/` → `tasks/stats/` rename design

**Status:** Proposed
**Date:** 2026-05-09
**Phase:** Phase-3 housekeeping, Stage 3a-3
**Decision:** Move all three DB-backed tracker modules to a new `tasks/stats/` sub-package; leave one-release shim at the old paths.
**Alternatives considered:** Flat under `tasks/` vs. new `tasks/stats/` sub-package — `tasks/stats/` chosen (see §Proposed destination layout).

---

## Context

INV-4, stated in `docs/housekeeping/2026-05-08/architecture.md §5a`:

> `analysis/source_credibility.py`, `analysis/source_stats.py`, and `analysis/keyword_stats.py` perform direct SQLite reads/writes against `data/paper_trades.db`. The `IMPLEMENTATION_CONTRACT.md §2` (`/analysis`) states the layer must be pure (no I/O, no DB access). These three modules are operational trackers that happened to be placed in `analysis/` rather than `tasks/`.

The architecture doc further lists them explicitly in the "Boundary Observations" section with DDL line citations:

- `source_credibility:line 37` — `CREATE TABLE IF NOT EXISTS source_credibility …`
- `source_stats:line 48` — `CREATE TABLE IF NOT EXISTS source_stats …`
- `keyword_stats:line 23` — reads `keyword_outcomes` table

All three modules maintain per-run operational state (win/loss tracking, signal rate counters, per-keyword accuracy cache), flush to SQLite, and seed downstream Kelly calculations. That is orchestration behavior, matching the `tasks/` domain constraint.

---

## Current state

### `analysis/source_credibility.py`

- **Path:** `analysis/source_credibility.py`
- **Line count:** 232
- **SQLite tables owned:**
  - `source_credibility` — DDL at line 36; columns: `source`, `wins`, `losses`, `total`, `accuracy`, `multiplier`, `last_updated`
  - Reads joined table `paper_trades` (owned by `trading/paper_trader.py`) at line 108 — read-only cross-table join, not an ownership claim
- **Public API surface:**
  - `class SourceCredibility` — the only exported name; no `__all__`
  - Methods: `get_multiplier(source)`, `get_all()`, `get_stats(source)`, `record_outcome(source, was_correct)`, `format_table()`

### `analysis/source_stats.py`

- **Path:** `analysis/source_stats.py`
- **Line count:** 267
- **SQLite tables owned:**
  - `source_stats` — DDL at line 47; columns: `source`, `posts_seen`, `signals`, `opportunities`, `trades`, `last_signal`, `last_updated`
- **Public API surface:**
  - Module-level helper: `_is_core(source)` (private by convention)
  - `class SourceStats` — the only exported name; no `__all__`
  - Methods: `increment_posts`, `increment_signals`, `increment_opportunities`, `increment_trades`, `is_suppressed`, `is_low_quality`, `get_signal_rate`, `ranking_score`, `flush`, `get_all`

### `analysis/keyword_stats.py`

- **Path:** `analysis/keyword_stats.py`
- **Line count:** 150
- **SQLite tables owned:**
  - Reads `keyword_outcomes` (owned by `trading/paper_trader.py`) — no DDL here, read-only consumer
  - No `CREATE TABLE` in this file
- **Public API surface:**
  - `class KeywordStats` — the only exported name; no `__all__`
  - Methods: `get_multiplier(keyword, series_ticker)`, `refresh()`, `summary()`

---

## Callsite inventory

### `analysis/source_credibility`

| Caller (file:line) | Imports what | Frequency (uses) | Layer |
|---|---|---|---|
| `trading/paper_trader.py:31` | `SourceCredibility` (class used at line 202, 223) | 2 uses | trading |
| `tests/test_paper_trader.py:124,350,366,372,388,394,410,416,434,442,462,483,489,526,692,757` | patches `trading.paper_trader.SourceCredibility` (no direct import of the module) | 16 patch calls | tests |
| `tests/test_source_credibility.py:15` | `SourceCredibility` (class instantiated); `analysis.source_credibility.sqlite3.connect` patched at line 35 | 1 import + 1 string patch | tests |
| `tests/test_simulations_smoke.py:325,328` | Comments only — references `SourceCredibility` in docstring prose | 0 live imports | tests |

**Summary:** 1 production import (`trading/paper_trader.py`), 1 test-module direct import, 16 test patch strings targeting `trading.paper_trader.SourceCredibility` (those patches survive the move unchanged because the shim re-exports into `trading/paper_trader.py`'s namespace via the existing import there).

### `analysis/source_stats`

| Caller (file:line) | Imports what | Frequency (uses) | Layer |
|---|---|---|---|
| `main.py:71` | `SourceStats` (instantiated at line 470) | 1 import, 1 instantiation | orchestration |
| `tests/test_main_pipeline.py:44,45,169,192–195,229–231,408` | `bot.source_stats` assigned as `MagicMock()` — no direct module import | 8 assertion/usage lines | tests |

**Summary:** 1 production import (`main.py`), 0 test-module direct imports of `analysis.source_stats` (tests inject a mock at the attribute level; no string patches).

### `analysis/keyword_stats`

| Caller (file:line) | Imports what | Frequency (uses) | Layer |
|---|---|---|---|
| `main.py:68` | `KeywordStats` (instantiated at line 471) | 1 import, 1 instantiation | orchestration |
| `tests/test_main_startup.py:208–209,277–278` | `bot.keyword_stats` assigned as `MagicMock()` — no direct module import | 4 usage lines | tests |
| `tests/test_signal_analyzer.py:237,250` | `stats = MagicMock()` used as `keyword_stats` param — no module import | 2 usage lines | tests |

**Summary:** 1 production import (`main.py`), 0 test-module direct imports of `analysis.keyword_stats`.

---

## Proposed destination layout

**Choice: B — New sub-package `tasks/stats/`**

```
tasks/stats/__init__.py
tasks/stats/source_credibility.py
tasks/stats/source_stats.py
tasks/stats/keyword_stats.py
```

**Justification:**

1. `tasks/` has no `__init__.py` today — it is a flat namespace package. The eight existing files (`accumulation_task.py`, `blend_task.py`, `budget_manager.py`, `calibration_task.py`, `evidence_store.py`, `runtime_overrides_task.py`, `structural_task.py`, `trade_readiness_gate.py`) are all flat with no sub-grouping.

2. The three incoming modules are a cohesive cluster — all three are per-entity stat trackers backed by the same `paper_trades.db`. Grouping them under `tasks/stats/` makes the domain boundary visible in the filesystem without disrupting the existing flat layout of task orchestrators.

3. Future extensibility: at least one additional operational tracker is plausible (e.g., per-market hit-rate tracker). A `stats/` sub-package gives a clean home without forcing a decision on which flat-task file it belongs near.

4. Flat Option A would be correct if the modules were as varied as the existing task files; they are not — they are three instances of the same pattern.

---

## Import-shim strategy

None of the three modules defines `__all__`. With no `__all__`, `from module import *` exports everything not prefixed with `_`. The relevant private names per module are:

- `source_credibility.py` — `_DDL` (str constant), `DB_PATH` (Path constant), `log` (logger). These are internal; re-exporting them is harmless but noisy.
- `source_stats.py` — `_DDL`, `_CORE_BARE`, `_is_core()`, `DB_PATH`, `FLUSH_INTERVAL`, `log`. `_is_core` is name-prefixed private; `import *` will NOT re-export it. `FLUSH_INTERVAL` and `DB_PATH` will be re-exported.
- `keyword_stats.py` — `MIN_SAMPLES`, `REFRESH_SECS`, `log`. All re-exported by `import *`.

**Recommendation:** Use **explicit re-exports** in the shim rather than `import *`. This is the conservative option: it re-exports only the single public class each caller actually imports, and avoids accidentally leaking `_DDL` or `log` into the old module's namespace.

Shim shape (one for each module):

```python
# analysis/source_credibility.py
# SHIM: this module moved to tasks/stats/source_credibility.py.
# TODO: delete after one release cycle (next housekeeping pass).
from tasks.stats.source_credibility import SourceCredibility  # noqa: F401
```

Same pattern for `source_stats` and `keyword_stats`.

**Critical note on `test_source_credibility.py:35`:**
```python
with patch("analysis.source_credibility.sqlite3.connect", side_effect=connect):
```
This patches `sqlite3` on the `analysis.source_credibility` namespace. After the shim, `analysis.source_credibility` only re-exports `SourceCredibility`; `sqlite3` is no longer a name in that namespace. This test **must be updated** to patch `tasks.stats.source_credibility.sqlite3.connect` instead. It cannot be left on the shim. This is the one mandatory test fix.

---

## Step-by-step rename order

**Do them serially** — each fully shimmed and pytest-verified before the next. Rationale: the modules are independent (none imports the others), so there is no forced coupling order, but serializing means any pytest regression is attributable to the change just made.

**Recommended order: `source_stats` → `keyword_stats` → `source_credibility`**

Rationale: `source_credibility` has the only mandatory test fix (the `sqlite3.connect` patch). Do it last so the simpler two moves establish confidence in the shim pattern first.

### For each module (repeat ×3 in the order above):

1. Create `tasks/stats/__init__.py` (empty, first module only).
2. Create `tasks/stats/<module>.py` with full contents copied verbatim from `analysis/<module>.py`.
3. Replace `analysis/<module>.py` with the explicit-re-export shim.
4. Run `.venv/bin/pytest -q` — must show same test count, zero new failures.
5. Update primary production caller to use the new path:
   - `source_stats`: update `main.py:71` → `from tasks.stats.source_stats import SourceStats`
   - `keyword_stats`: update `main.py:68` → `from tasks.stats.keyword_stats import KeywordStats`
   - `source_credibility`: update `trading/paper_trader.py:31` → `from tasks.stats.source_credibility import SourceCredibility`
6. For `source_credibility` only: update `tests/test_source_credibility.py:15` and line 35 to use `tasks.stats.source_credibility` paths (mandatory — shim does not carry `sqlite3` namespace).
7. Run `.venv/bin/pytest -q` again — must still be clean.
8. Leave remaining callers (all mock-based tests, `test_simulations_smoke.py` comment references) on the shim.

---

## Test impact

| Test file | How it references the modules | Action required |
|---|---|---|
| `tests/test_source_credibility.py` | Direct import `from analysis.source_credibility import SourceCredibility` (line 15); string patch `"analysis.source_credibility.sqlite3.connect"` (line 35) | **Must update** line 35 to `"tasks.stats.source_credibility.sqlite3.connect"`. Line 15 will work via shim but update it too for cleanliness. |
| `tests/test_paper_trader.py` | 16 uses of `patch("trading.paper_trader.SourceCredibility")` | No change needed — these patch the name in `trading.paper_trader`'s namespace, which is populated by `from analysis.source_credibility import SourceCredibility` (still works via shim) or the updated `from tasks.stats.source_credibility import SourceCredibility` after step 5. Either way the patch target string does not change. |
| `tests/test_main_pipeline.py` | `bot.source_stats = MagicMock()`, `bot.keyword_stats = MagicMock()` — attribute injection only | No change needed. |
| `tests/test_main_startup.py` | `bot.keyword_stats = MagicMock()` — attribute injection only | No change needed. |
| `tests/test_signal_analyzer.py` | `stats = MagicMock()` — no module import | No change needed. |
| `tests/test_simulations_smoke.py` | Prose docstring reference only | No change needed. |

---

## INV-4 verification

After all three moves are complete, run:

```bash
grep -rn "CREATE TABLE\|sqlite3\.\|sqlite\.connect\|\.execute(" analysis/ --include="*.py"
```

**Expected result (pass criteria):**

- The three shim files contain only the explicit re-export line; no `CREATE TABLE`, no `sqlite3`, no `.execute(` calls appear in them.
- The remaining `analysis/` modules (`signal_analyzer.py`, `market_matcher.py`, `kelly.py`, `decision_blender.py`, `dossier_builder.py`, `evidence_scorer.py`, etc.) show no SQLite calls.
- If `analysis/dossier_builder.py` or `analysis/calibration_monitor.py` appear in the grep — inspect before declaring failure. The architecture audit confirmed these are "pure, no I/O"; if they appear it is a false positive (e.g., a comment or type annotation). Confirm by reading the matched line.

**Explicit pass:** `grep` returns zero `.execute(` or `CREATE TABLE` matches in any non-shim `analysis/` file.

**Explicit fail:** Any non-shim `analysis/*.py` file shows a live `sqlite3.connect(`, `CREATE TABLE`, or `cursor.execute(` call — escalate as a new INV-4 violation to `docs/profit_path_debt_log.md` before closing this ticket.

---

## Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| `test_source_credibility.py:35` patches `analysis.source_credibility.sqlite3.connect`, which is absent from the shim | **High** | Mandatory test fix in step 6 of the `source_credibility` rename. Run pytest immediately after shim placement to catch this before moving on. |
| Circular import: `tasks/stats/X.py` imports from `analysis/Y.py` which imports shim that re-imports `tasks/stats/X.py` | High | Verify now: `source_credibility.py` imports only `config`, `utils.logger`. `source_stats.py` imports only `config`, `utils.logger`, `threading`, `sqlite3`. `keyword_stats.py` imports only `utils.logger`, `sqlite3`, `threading`, `time`. None imports from `analysis/`. No circular risk. |
| Downstream `scripts/` access the DB tables by raw SQL name | Low | Table names (`source_credibility`, `source_stats`, `keyword_outcomes`) are unchanged by this move. No risk. |
| `tasks/` has no `__init__.py`; adding `tasks/stats/__init__.py` makes `tasks/stats` a regular package while `tasks/` remains a namespace package | Low | Python resolves both correctly. `tasks.stats.source_credibility` is importable. Verify with one `python -c "from tasks.stats.source_credibility import SourceCredibility; print('ok')"` after step 2 of the first move. |
| `__all__` missing → shim `import *` re-exports private names | Low | Mitigated by using explicit re-exports (single-class shims) rather than `import *`. |
| `scripts/performance_analysis.py:730` references `SOURCE_STATS_ZERO_SIGNAL_POSTS` constant — pulled from `config`, not from the module | None | No import of the module; reads config constants directly. Unaffected. |

---

## Open questions for Stage 3b implementer

1. **`tasks/stats/__init__.py` content:** Empty file is sufficient. If the implementer wants to re-export all three classes at the `tasks.stats` level (i.e., `from tasks.stats import SourceCredibility`), that is optional convenience — not required for the rename to be correct. Recommend leaving `__init__.py` empty to keep the sub-package minimal.

2. **Shim lifetime:** The shims are marked "TODO: delete after one release cycle (next housekeeping pass)." Define concretely: shims should be removed in the Phase-3 Stage 3b cleanup commit, or at the start of Phase-4 housekeeping — whichever comes first. The implementer should add a `PROFIT-DEBT-OQ1-SHIM` debt-log entry in `docs/profit_path_debt_log.md` with a target-removal date to prevent shims from persisting indefinitely.

3. **`analysis/__init__.py` re-export check:** Verify that `analysis/__init__.py` does not currently re-export any of the three moved names (a quick `grep "source_credibility\|source_stats\|keyword_stats" analysis/__init__.py`). If it does, the shim in `analysis/__init__.py` must also be updated.

4. **`paper_trader.py` import after update (step 5):** After updating `trading/paper_trader.py:31` to import from `tasks.stats.source_credibility`, verify the domain constraint: `trading/` is allowed to import from `tasks/` (both are operational layers). No constraint violation.
