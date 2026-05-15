# `market_yes_price` Alias Caller Inventory

**Date:** 2026-05-13
**Status:** INVENTORY — informs P1 alias-removal program; no behavior change in this report.
**Authority:** v0.30.0 vertical-integration audit recommendation #6.
**Related:** `PROFIT-API-001` (debt log); LD-10 + LD-17 deprecation decision in `docs/_archive/governance/2026-05-11-kalshi-api-drift-pricing-correctness-roadmap.md` §11.

**Workoff update (2026-05-15):** P1-A landed in `bb9cc95`; P1-B and
P1-C landed in `c982ef5` and were activated by the Bounce 2 restart at
`2026-05-15T14:08:20Z`. The live `paper_trades` schema now has
`entry_price_cents` and no `market_yes_price`. Runtime dataclasses, paper
DB writes, hydration, replay dataset generation, resolved-market fetch, and
performance-analysis display paths now use the canonical name while selected
offline readers still dual-emit/read the legacy JSONL key for historical
corpus compatibility. Line-number references below are retained as the
original 2026-05-13 inventory evidence, not current source coordinates.

## Why this inventory exists

LD-10 / LD-17 retain `SignalAnalysis.market_yes_price` and
`OpenPosition.market_yes_price` as **deprecated aliases for
`executed_price_cents`** through v0.30.x. Hard-removal is deferred to
P1. Before that removal can land, every reader of the alias must be
inventoried — silent breakage at P1 cutover would be the largest
single-PR blast radius in the project. This document is the
inventory.

The semantic shift at v0.30.0 is invisible to callers because of
`SignalAnalysis.__post_init__`
(`analysis/__init__.py:42`), which populates
`self.market_yes_price = float(self.executed_price_cents)` whenever
`executed_price_cents` is set. So today every caller reading
`analysis.market_yes_price` is *de facto* reading the executable-side
price. The P1 removal turns "de facto" into "by contract" — and any
caller that still expects the YES-midpoint semantics breaks silently.

## Inventory methodology

Scripted grep across the live tree, excluding `__pycache__`,
`.claude/worktrees/`, and `docs/_archive/`. Counts:

| Pattern | Hit count |
|---|---|
| `market_yes_price` (all files) | **64** |
| `.yes_price` (attribute access) | **18** |
| `yes_prob` | **15** |
| `OpenPosition.market_yes_price` (substring) | covered by the 64 above |

Tally by top-level directory (`market_yes_price`):

| Directory | Hits |
|---|---:|
| `tests/` | 20 |
| `scripts/` | 20 |
| `docs/` | 16 |
| `trading/` | 3 (paper_trader, executor, portfolio) |
| `utils/` | 1 (logger) |
| `tasks/` | 1 (blend_task) |
| `main.py` | 1 |
| `analysis/` | 1 (`__init__.py`) |
| `CHANGELOG.md` | 1 |

## Classified call sites

### Production runtime (P1-must-migrate)

| File:line | Use | P1 recommendation |
|---|---|---|
| `main.py:697` | local var `market_yes_price = market.yes_price` (drift logging input) | replace with `executed_price_cents` from `SignalAnalysis`; or compute drift in cents directly |
| `main.py:728` | log emit arg | rename keyword and update consumers via `utils/logger.py` keyword schema |
| `main.py:850` | construction `market_yes_price=market.yes_price` | switch to `executed_price_cents=...` |
| `main.py:885` | comment | doc-only; update wording when above sites migrate |
| `main.py:1085` | `drift = now_mid - pos.market_yes_price` (drift telemetry) | rename to `entry_price_cents`; semantically unchanged because `OpenPosition.market_yes_price` already stores the executed entry price (LD-17) |
| `main.py:1091` | `entry_price=pos.market_yes_price` log emit | same as 1085 |
| `analysis/__init__.py:42` | `__post_init__` populates alias from `executed_price_cents` | DELETE in P1 with the alias itself |
| `analysis/__init__.py:16-19,37` | field declaration + deprecated comment | DELETE in P1 |
| `tasks/blend_task.py:389` | `market_price = fast_lane_result.market_yes_price` | replace with `executed_price_cents` |
| `tasks/blend_task.py:525` | `market_yes_price=fast_lane_result.market_yes_price` | replace with `executed_price_cents` keyword |
| `tasks/blend_task.py:530-536` | comment + `int(fast_lane_result.market_yes_price)` fallback | DELETE fallback when alias drops; comment ages out |
| `tasks/blend_task.py:95` | docstring | doc-only |
| `trading/executor.py:246,254` | drift gating `abs(pos.market_yes_price - analysis.market_yes_price)` | rename both to executed-cents domain; semantics unchanged (both already are executable price) |
| `trading/executor.py:408` | `analysis.market_yes_price = float(new_executed_cents)` re-fetch hook (P-5 CR-D) | DELETE assignment in P1 (alias no longer exists); re-fetch hook continues to set `executed_price_cents` |
| `trading/portfolio.py:23,37` | docstring + dataclass field | rename field to `entry_price_cents` per LD-17 plan; column rename is a separate migration |
| `trading/portfolio.py:74,81` | SQL `SELECT estimated_prob, market_yes_price, ts ...` | DB column rename = schema migration; see "DB compatibility" below |
| `trading/portfolio.py:103` | `market_yes_price=row["market_yes_price"]` hydration | update when DB column is renamed |
| `trading/paper_trader.py:65` | DDL `market_yes_price REAL NOT NULL` | DB column rename — schema migration (covered separately) |
| `trading/paper_trader.py:698,718` | INSERT path | update when column renamed |
| `trading/paper_trader.py:761` | `OpenPosition(...market_yes_price=...)` instantiation | update when dataclass field renamed |
| `utils/logger.py:555,573,605,623` | structured-log emit kwargs `market_yes_price=...` | rename log-field schema in P1 — emits 4 distinct event types (`OPPORTUNITY`, `BLEND_DECISION`, `PAPER_TRADE`, `SKIPPED`); coordinate with downstream consumers (replay scripts) |
| `utils/logger.py:723` | docstring referencing the alias | doc-only |

### Replay / offline analysis (P1-migrate-after-runtime)

Scripts under `scripts/edge_replay/`, `scripts/simulations/`, and
top-level `scripts/` (20 files): all consume `paper_trades.market_yes_price`
column OR `JSONL.market_yes_price` field from log emits. **Migration
order:** runtime field + DDL must land first; replay scripts then
update to read the new column/field name in a single follow-up PR.
Affected scripts (representative; full list in `docs/_archive/2026-05-09-docs-consolidation/`):

- `scripts/performance_analysis.py` (already silent-50-fixed in MR `!16`)
- `scripts/paper_performance_drilldown.py`
- `scripts/signal_edge_diagnostics.py`
- `scripts/edge_replay/build_replay_dataset.py`
- `scripts/edge_replay/score_counterfactual_pnl.py`
- `scripts/edge_replay/reingest_dossier_updates_post_fix.py` (silent-50 fixed in MR `!15`)
- `scripts/edge_replay/post_fix_new_readiness_status.py` (NEW in MR `!18` — uses `paper_trades.ts` only; **no alias dependency**)
- `scripts/simulations/baseline_vs_multilane.py` (constructs `TradeCandidate(market_yes_price=...)` — will break when P1 removes the kwarg)
- `scripts/daily_review.py` (consumes via section helpers; indirect)
- ~10 other diagnostic / audit scripts

### Test fixtures / assertions (compatibility — leave until alias removed)

Tests under `tests/test_*.py` construct `KalshiMarket(yes_price=...)`,
`SignalAnalysis(market_yes_price=...)`, or `OpenPosition(market_yes_price=...)`
as compatibility fixtures. **Leave unchanged until P1.** When the
alias is removed, these break loudly (constructor signature error),
which is the desired signal. Affected files: ~20 across the test
suite; representative subset:

- `tests/test_executor.py`
- `tests/test_paper_trader.py`
- `tests/test_blend_task.py`
- `tests/test_main_pipeline.py`
- `tests/test_kalshi_normalizer_p0.py`
- `tests/test_kalshi_pricing_p0.py`
- `tests/test_kalshi_pricing_p0_replay.py`

### DB schema / hydration compatibility (schema migration scope)

Two columns + one dataclass field carry the alias name AS the source-of-truth identifier (not just as a callsite):

| Surface | Symbol | P1 action |
|---|---|---|
| `data/paper_trades.db` | column `paper_trades.market_yes_price REAL NOT NULL` | `ALTER TABLE paper_trades RENAME COLUMN market_yes_price TO entry_price_cents;` — or write a `CREATE TABLE new + INSERT SELECT + DROP + RENAME` migration (SQLite RENAME COLUMN requires 3.25+). Pre-P0 rows are already excluded from the POST_FIX_NEW cohort by sentinel filter; column rename is forward-compatible for the value, only the *name* changes. |
| `trading/portfolio.py:37` | `OpenPosition.market_yes_price: float` | rename to `entry_price_cents`; update `__init__` callers (mostly in `paper_trader.py` + tests) |
| `analysis/__init__.py:19` | `SignalAnalysis.market_yes_price: float` | DELETE the field (no rename — the canonical name `executed_price_cents` already exists on the dataclass; alias is the deprecated copy) |

### Docs / comments only (no behavior; update in sweep)

Documentation surfaces that mention the alias name:

| File | Disposition |
|---|---|
| `CHANGELOG.md` `[0.30.0]` entry | historical record — DO NOT REWRITE |
| `docs/profit_path_debt_log.md` `PROFIT-API-001` notes | extends correctly; no edit needed |
| `docs/governance/2026-05-11-...-roadmap.md` §11 LD-10/LD-17 | locked design decisions; no edit needed |
| Comments in `analysis/__init__.py`, `tasks/blend_task.py`, `trading/portfolio.py`, `main.py`, `utils/logger.py` | doc-only; refresh wording in P1 PR alongside the code change |

## Summary counts

| Category | Files | Action class |
|---|---:|---|
| Production runtime (P1-must-migrate) | 7 | replace / rename / delete in P1 |
| Replay / offline scripts | ~20 | migrate after runtime + DDL (single follow-up PR) |
| Test fixtures / assertions | ~20 | leave; let P1 alias removal trip them as designed-failure |
| DB schema / hydration | 2 (1 DDL + 1 hydration) + 1 dataclass field | schema migration in P1 |
| Docs / comments | ~16 | sweep in same P1 PR as runtime |

## P1 program shape (recommended)

Land in this order in 3 PRs to minimize the per-PR blast radius:

1. **P1-A — runtime + dataclass + log schema**
   - Delete `SignalAnalysis.market_yes_price` field + `__post_init__` aliasing.
   - Rename `OpenPosition.market_yes_price` → `entry_price_cents`.
   - Update every production caller listed above to use the canonical name.
   - Rename `utils/logger.py` log-field kwarg `market_yes_price` → `entry_price_cents` for the 4 event types.
   - Tests will FAIL — that's the designed signal. Do not skip; fix them in this PR.

2. **P1-B — DB schema migration**
   - `ALTER TABLE paper_trades RENAME COLUMN market_yes_price TO entry_price_cents`.
   - Update `trading/portfolio.py` SELECT/INSERT to use the new column name.
   - Update `trading/paper_trader.py` DDL.
   - The cohort sentinel `bot_state.p0_price_fix_deployed_ts` is UNAFFECTED by this migration. Pre-P0 rows that already carry the old column name simply get the new name; values are forward-compatible because the v0.30.x semantic shift (`market_yes_price = executed entry price`) already happened in code.

3. **P1-C — replay/offline migration**
   - Update every script under `scripts/` that reads the renamed column or the renamed log-field schema.
   - Update simulation harnesses (`scripts/simulations/baseline_vs_multilane.py` is the canary).

Land sequentially — each PR must be merged before the next branches off.

## Why not in v0.30.x

Per LD-10 / LD-17 + the P0 audit verdict: alias removal is multi-file
cross-layer work that runs concurrent with active paper-trade
accumulation. Deferring removal to P1 minimizes risk during the
POST_FIX_NEW cohort-build window (Cycle-17D resume check 2026-06-14).
This document captures the inventory so the P1 work can land cleanly.
