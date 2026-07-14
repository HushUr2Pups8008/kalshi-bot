# Profit Accounting and Capital-Guard Shadow Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce venue-complete, fee-versioned paper accounting and a disabled-by-default shadow ledger that measures G7-only counterfactual trades without changing capital, sizing, admission, or order behavior.

**Architecture:** Land four bounded PRs in dependency order. First fix venue-qualified settlement identity and persisted-position reconciliation. Then add pure official fee primitives and net executable liquidation reporting. Next migrate new paper rows to fee/net settlement accounting with a durable feedback outbox. Finally add isolated G7-only shadow capture and stateful settlement replay. G7 remains unchanged throughout; any later policy change requires a separate evidence-gated design.

**Tech Stack:** Python 3.14, `Decimal`, dataclasses, SQLite, pytest, Ruff, existing Kalshi REST/series metadata and Polymarket US public settlement clients.

## Global Constraints

- Preserve `G7_MAX_OPEN_EXPOSURE_DRAWDOWN_PCT=0.20`; no threshold or failure-order changes.
- Preserve paper mode, bankroll, sizing, cooldown, exposure, and live-order behavior.
- Never delete, reset, rewrite, or backfill `data/paper_trades.db` with assumed fees.
- Legacy fee state is `unknown`, never zero.
- Use only Kalshi official APIs and the Polymarket US gateway/auth surfaces already present; no Global CLOB.
- Use exact `Decimal`/fixed-point arithmetic for new money and quantity fields.
- Unknown venue, identity, book, fee schedule, or settlement fails closed.
- Runtime capture remains disabled until a separate reviewed restart.
- Runtime artifacts remain outside commits: `data/matcher_token_weights.json`, `data/*.db`, `logs/backups/`, and `logs/state/`.
- Every behavioral or financial-path PR receives independent review; T3 work must satisfy IC section 16 and explicit operator approval before runtime activation.

---

## PR 1: Venue-Qualified Settlement Safety

### Task 1: Introduce Canonical Market Identity

**Files:**
- Modify: `trading/venue.py`
- Modify: `trading/portfolio.py`
- Test: `tests/test_portfolio.py`

**Interfaces:**
- Produces: `MarketRef(venue: Venue, venue_market_id: str, alias: str)`.
- Produces: `Portfolio.resolve(market_ref: MarketRef) -> list[Position]`.
- Preserves: ticker-based lookup helpers for non-settlement reads during this PR.

- [ ] **Step 1: Write failing identity and collision tests**

```python
def test_market_ref_rejects_empty_market_id():
    with pytest.raises(ValueError, match="venue_market_id"):
        MarketRef(Venue.KALSHI, "", "KX-SHARED")


def test_resolve_closes_only_matching_venue_market_identity(tmp_path):
    portfolio = portfolio_with_same_alias_on_both_venues(tmp_path, "shared-id")

    closed = portfolio.resolve(MarketRef(Venue.POLYMARKET_US, "shared-id", "shared-id"))

    assert {row.venue for row in closed} == {Venue.POLYMARKET_US.value}
    assert len(portfolio.open_positions("shared-id")) == 1
    assert portfolio.open_positions("shared-id")[0].venue == Venue.KALSHI.value
```

- [ ] **Step 2: Verify RED**

Run:

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_portfolio.py -q
```

Expected: `MarketRef` is missing and `Portfolio.resolve()` cannot distinguish venues.

- [ ] **Step 3: Implement the minimal identity type and qualified resolve**

```python
@dataclass(frozen=True)
class MarketRef:
    venue: Venue
    venue_market_id: str
    alias: str

    def __post_init__(self) -> None:
        if not self.venue_market_id.strip():
            raise ValueError("venue_market_id is required")
```

Key the mutation by normalized venue plus canonical ID. Do not change exposure,
prefix, or duplicate-read semantics in this task.

- [ ] **Step 4: Verify GREEN and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_portfolio.py -q
git add trading/venue.py trading/portfolio.py tests/test_portfolio.py
git commit -m "fix: qualify portfolio resolution by venue"
```

### Task 2: Resolve Paper Rows by Venue and Market ID

**Files:**
- Modify: `trading/paper_trader.py`
- Modify: `polymarket/settlement_reconciler.py`
- Test: `tests/test_paper_trader_venue.py`
- Test: `tests/polymarket/test_settlement_reconciler.py`

**Interfaces:**
- Consumes: `MarketRef` from Task 1.
- Produces: `PaperTrader._resolve_market_sync(market_ref, resolved_yes)`.
- Produces: `SettlementResolver._resolve_market_sync(market_ref, resolved_yes)` protocol.

- [ ] **Step 1: Write failing cross-venue database regression**

Create two unresolved rows with the same ticker and different venues. Resolve
only `MarketRef(Venue.POLYMARKET_US, ticker, ticker)`. Assert the Polymarket row
resolves, the Kalshi row remains open, bankroll credits exactly once, and only
the Polymarket `Position` closes.

- [ ] **Step 2: Update reconciler protocol tests before implementation**

```python
assert resolver.resolved == [
    (MarketRef(Venue.POLYMARKET_US, "will-example-fail-2026", "will-example-fail-2026"), False)
]
```

- [ ] **Step 3: Verify RED**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_paper_trader_venue.py \
  tests/polymarket/test_settlement_reconciler.py -q
```

Expected: current SQL `WHERE ticker=? AND resolved=0` resolves both venue rows.

- [ ] **Step 4: Implement venue-qualified SQL and propagation**

Use:

```sql
SELECT ... FROM paper_trades
WHERE venue = ? AND ticker = ? AND resolved = 0
```

Pass `market_ref` to `Portfolio.resolve()`. Preserve transactional bankroll
credit and existing lane-event return shape.

- [ ] **Step 5: Verify GREEN and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_paper_trader_venue.py \
  tests/polymarket/test_settlement_reconciler.py -q
git add trading/paper_trader.py polymarket/settlement_reconciler.py \
  tests/test_paper_trader_venue.py tests/polymarket/test_settlement_reconciler.py
git commit -m "fix: isolate settlement identity by venue"
```

### Task 3: Reconcile Persisted Positions Independent of Entry Flags

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_pipeline.py`

**Interfaces:**
- Consumes: persisted open venue set from `paper_trades`.
- Preserves: `POLYMARKET_US_ENABLED=false` as a hard block on discovery and new entries.
- Produces: Polymarket settlement reconciliation whenever an unresolved Polymarket row exists.

- [ ] **Step 1: Add failing runtime routing test**

Extend the existing auto-resolve fixture with `polymarket_us_enabled=False` and
one persisted unresolved `venue='polymarket_us'` row. Assert the reconciler is
constructed and called while the Polymarket paper runtime/discovery path stays
disabled.

- [ ] **Step 2: Verify RED**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_main_pipeline.py -k 'auto_resolve and polymarket' -q
```

Expected: `has_polymarket and cfg.polymarket_us_enabled` skips reconciliation.

- [ ] **Step 3: Remove the entry-flag condition only from settlement routing**

Use persisted venue presence as the settlement condition. Do not alter startup
probe, candidate adapter, market discovery, or execution feature-flag checks.

- [ ] **Step 4: Verify focused PR 1 suite and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_portfolio.py \
  tests/test_paper_trader_venue.py \
  tests/polymarket/test_settlement_reconciler.py \
  tests/test_main_pipeline.py -k 'portfolio or venue or settlement or auto_resolve' -q
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check \
  trading/venue.py trading/portfolio.py trading/paper_trader.py \
  polymarket/settlement_reconciler.py main.py \
  tests/test_portfolio.py tests/test_paper_trader_venue.py \
  tests/polymarket/test_settlement_reconciler.py tests/test_main_pipeline.py
git diff --check
git add main.py tests/test_main_pipeline.py
git commit -m "fix: reconcile persisted Polymarket positions"
```

---

## PR 2: Official Fees and Executable Liquidation Reporting

### Task 4: Add Versioned Pure Fee Calculators

**Files:**
- Create: `trading/fees.py`
- Create: `tests/test_fees.py`

**Interfaces:**
- Produces: `FeeScheduleId` and `FeeQuote` frozen dataclasses.
- Produces: `kalshi_taker_fee(contracts, price, multiplier, schedule_at)`.
- Produces: `polymarket_us_taker_fee(contracts, price, schedule_at)`.
- Produces: `quote_taker_fee(venue, ...)` exhaustive dispatcher.

- [ ] **Step 1: Write fee-matrix tests**

Cover price `$0.01`, `$0.10`, `$0.50`, `$0.90`, `$0.99`; one and 1,000
contracts; Kalshi multipliers; Polymarket banker's-round boundaries `$0.025`
and `$0.035`; effective-date boundaries; invalid/non-finite inputs; unknown
venue and unsupported Kalshi fee type.

```python
def test_polymarket_us_taker_fee_at_midpoint():
    quote = polymarket_us_taker_fee(Decimal("1000"), Decimal("0.50"), AT_JULY_2026)
    assert quote.amount == Decimal("15.00")


def test_kalshi_general_taker_fee_uses_series_multiplier():
    quote = kalshi_taker_fee(Decimal("100"), Decimal("0.50"), Decimal("1"), AT_JULY_2026)
    assert quote.amount == Decimal("1.75")
```

- [ ] **Step 2: Verify RED, implement with `Decimal`, verify GREEN, commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_fees.py -q
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_fees.py -q
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check trading/fees.py tests/test_fees.py
git add trading/fees.py tests/test_fees.py
git commit -m "feat: add versioned venue fee calculators"
```

### Task 5: Use Net Executable Bid Liquidation and Exhaustive Venue Dispatch

**Files:**
- Modify: `scripts/mark_open_positions.py`
- Modify: `kalshi/series_metadata.py` only if stricter fee parsing is required.
- Test: `tests/test_mark_open_positions.py`
- Test: `tests/test_series_metadata.py`

**Interfaces:**
- Consumes: Task 4 fee calculator and existing `Venue` enum.
- Produces: `compute_open_position_marks()` keys `gross_marked_value`, `estimated_exit_fees`, `marked_value`, `unknown_cost`, `unscorable_reasons`, `as_of`, and `fee_schedule_versions`.
- Preserves: `marked_value` as the G7-compatible net liquidation value key.

- [ ] **Step 1: Write RED tests for conservative liquidation**

Prove held YES uses YES bid, held NO uses NO bid, midpoint/ask/last are not G7
marks, exit taker fees reduce value, same-market lots share one snapshot, API
fetches deduplicate, and an unsupported venue is unpriced rather than routed to
Polymarket.

- [ ] **Step 2: Verify RED**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_mark_open_positions.py tests/test_series_metadata.py -q
```

- [ ] **Step 3: Implement adapter mapping and net mark result**

Normalize `row['venue']` with `normalize_venue`; use an explicit mapping keyed
by every `Venue`. Fetch each market and Kalshi series once. Treat missing
series multiplier, bid, fee version, or identity as unscorable with zero value.

- [ ] **Step 4: Verify G7 remains fail-closed**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_mark_open_positions.py \
  tests/test_main_startup.py \
  tests/test_blend_task.py \
  tests/test_trade_readiness_gate.py -q
```

- [ ] **Step 5: Commit**

```bash
git add scripts/mark_open_positions.py kalshi/series_metadata.py \
  tests/test_mark_open_positions.py tests/test_series_metadata.py
git commit -m "fix: value exposure at fee-net executable bids"
```

### Task 6: Route Daily Review Through Canonical Marks

**Files:**
- Modify: `scripts/paper_performance_drilldown.py`
- Modify: `scripts/daily_review.py`
- Test: `tests/test_paper_performance_drilldown.py`
- Test: `tests/test_daily_review.py`

**Interfaces:**
- Consumes: injectable `mark_provider(db_path)` returning Task 5 result.
- Produces: venue-neutral `open_mark` reporting fields.
- Preserves: old Kalshi-only keys for one compatibility cycle as deprecated derived values.

- [ ] **Step 1: Replace tests that codify Polymarket as unknown**

Inject a mixed-venue canonical mark result and assert daily review renders open
cost, gross value, exit fees, net value, unrealized net P&L, unknown cost, and
schedule/as-of provenance. Add provider-failure coverage that renders explicit
unavailable status without breaking the report.

- [ ] **Step 2: Verify RED, implement, and verify GREEN**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_paper_performance_drilldown.py tests/test_daily_review.py -q
```

- [ ] **Step 3: Run PR 2 verification and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_fees.py tests/test_series_metadata.py \
  tests/test_mark_open_positions.py tests/test_main_startup.py \
  tests/test_blend_task.py tests/test_trade_readiness_gate.py \
  tests/test_paper_performance_drilldown.py tests/test_daily_review.py -q
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check \
  trading/fees.py scripts/mark_open_positions.py \
  scripts/paper_performance_drilldown.py scripts/daily_review.py tests/test_fees.py \
  tests/test_mark_open_positions.py tests/test_paper_performance_drilldown.py \
  tests/test_daily_review.py
git diff --check
git add scripts/paper_performance_drilldown.py scripts/daily_review.py \
  tests/test_paper_performance_drilldown.py tests/test_daily_review.py
git commit -m "fix: share venue-complete exposure reporting"
```

---

## PR 3: Fee-Net Settlement and Durable Feedback

### Task 7: Preserve Contract and Fee Identity on New Trades

**Files:**
- Modify: `polymarket/models.py`
- Modify: `polymarket/normalizer.py`
- Modify: `trading/paper_trader.py`
- Test: `tests/test_polymarket_normalizer.py`
- Test: `tests/polymarket/test_paper_trader.py`
- Test: `tests/test_paper_trader.py`

**Interfaces:**
- Produces: canonical venue market ID, side IDs, fee coefficient, minimum quantity, tick size, and contract snapshot provenance on normalized markets.
- Produces nullable paper columns for entry fee, fee schedule, gross/net accounting, refund, terminal state, and settlement observation hash.
- Legacy rows receive `fee_status='unknown'` and no calculated net P&L.

- [ ] **Step 1: Write schema and normalization RED tests**
- [ ] **Step 2: Implement additive fields and idempotent migration**
- [ ] **Step 3: Prove legacy rows are unchanged and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_polymarket_normalizer.py tests/polymarket/test_paper_trader.py \
  tests/test_paper_trader.py -q
git add polymarket/models.py polymarket/normalizer.py trading/paper_trader.py \
  tests/test_polymarket_normalizer.py tests/polymarket/test_paper_trader.py \
  tests/test_paper_trader.py
git commit -m "feat: preserve paper fee and contract identity"
```

### Task 8: Commit Net Settlement and Feedback Outbox Atomically

**Files:**
- Create: `trading/settlement_accounting.py`
- Modify: `trading/paper_trader.py`
- Modify: `polymarket/settlement_reconciler.py`
- Create: `tests/test_settlement_accounting.py`
- Modify: `tests/polymarket/test_settlement_reconciler.py`
- Modify: `tests/test_paper_trader.py`

**Interfaces:**
- Produces: terminal `won|lost|void` accounting result with gross payout, fees/rebates/refund, and net P&L.
- Produces: append-only `paper_feedback_outbox` rows committed with financial state.
- Produces: idempotent outbox drain called after commit and on startup.

- [ ] **Step 1: Write RED conservation, void, duplicate, and crash tests**

Inject failures before and after each write. Assert rollback before commit,
exactly-once bankroll credit after retry, durable outbox presence after a
post-commit consumer crash, and idempotent feedback replay.

- [ ] **Step 2: Implement pure accounting result and one transaction**
- [ ] **Step 3: Implement idempotent outbox drain without changing financial state**
- [ ] **Step 4: Run complete paper/Polymarket suite and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_settlement_accounting.py tests/test_paper_trader.py \
  tests/polymarket tests/test_polymarket*.py -q
git add trading/settlement_accounting.py trading/paper_trader.py \
  polymarket/settlement_reconciler.py tests/test_settlement_accounting.py \
  tests/test_paper_trader.py tests/polymarket/test_settlement_reconciler.py
git commit -m "fix: settle paper positions net of fees atomically"
```

---

## PR 4: Disabled Capital-Guard Shadow Evidence

### Task 9: Add Append-Only Shadow Store and Disabled Configuration

**Files:**
- Create: `trading/capital_guard_shadow.py`
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `scripts/botcheck.py`
- Create: `tests/test_capital_guard_shadow.py`
- Modify: `tests/test_botcheck.py`

**Interfaces:**
- Produces: `ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE` default `false`.
- Produces: `CapitalGuardShadowStore` at `data/capital_guard_shadow.db`.
- Produces append-only candidate, conflict, settlement, and evaluation tables.

- [ ] **Step 1: Write disabled/no-create and append-only RED tests**
- [ ] **Step 2: Implement exact schema, constraints, triggers, and idempotent capture key**
- [ ] **Step 3: Add bounded read-only botcheck status and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_capital_guard_shadow.py tests/test_botcheck.py -q
git add trading/capital_guard_shadow.py config.py .env.example \
  scripts/botcheck.py tests/test_capital_guard_shadow.py tests/test_botcheck.py
git commit -m "feat: add disabled capital guard shadow store"
```

### Task 10: Capture Only Fully Specified G7-Only Candidates

**Files:**
- Modify: `tasks/blend_task.py`
- Modify: `main.py`
- Modify: `utils/logger.py`
- Modify: `tests/test_blend_task.py`
- Modify: `tests/test_lifecycle_telemetry.py`

**Interfaces:**
- Consumes: complete readiness failure set, canonical executed-side edge, depth, fee and sizing provenance.
- Produces: append-only eligible or unscorable shadow candidate without enqueueing.

- [ ] **Step 1: Write RED matrix**

Cover flag off, G7-only, G7 plus zero liquidity, missing side, missing depth,
missing fee, duplicate lifecycle, store failure, and canonical NO-side edge.
Assert no queue mutation and no canonical DB mutation in every case.

- [ ] **Step 2: Implement capture after readiness evaluation and before blocked return**
- [ ] **Step 3: Verify isolation and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_capital_guard_shadow.py tests/test_blend_task.py \
  tests/test_lifecycle_telemetry.py tests/test_main_pipeline.py -q
git add tasks/blend_task.py main.py utils/logger.py \
  tests/test_blend_task.py tests/test_lifecycle_telemetry.py
git commit -m "feat: capture G7-only shadow candidates"
```

### Task 11: Add Stateful Fee-Net Shadow Replay

**Files:**
- Create: `scripts/capital_guard_shadow_replay.py`
- Create: `tests/test_capital_guard_shadow_replay.py`
- Modify: `scripts/README.md`

**Interfaces:**
- Produces: chronological stateful replay over a preregistered window.
- Produces: JSON report with classification/coverage, gross/net P&L, fees, risk, unresolved worst case, family/day block-bootstrap CI, and no-promotion verdicts.

- [ ] **Step 1: Write deterministic replay RED fixtures**

Include duplicate lifecycle, same-family burst, depth-limited fill, cooldown,
exposure cap, fee version change, void, unresolved worst case, and another-gate
failure. Prove independent-row summation would differ from the expected
chronological result.

- [ ] **Step 2: Implement read-only replay and strict coverage gates**
- [ ] **Step 3: Verify deterministic output and commit**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_capital_guard_shadow_replay.py -q
git add scripts/capital_guard_shadow_replay.py \
  tests/test_capital_guard_shadow_replay.py scripts/README.md
git commit -m "feat: replay capital guard shadow outcomes"
```

---

### Task 12: Review, PR, and Runtime Gates

**Files:**
- Modify: `CHANGELOG.md` in each implementation PR.
- Modify: `docs/profit_path_debt_log.md` only when a bounded item closes or changes status.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: independent review evidence, protected PRs, CI, and separate disabled/enabled runtime gates.

- [ ] **Step 1: Run full local verification per PR**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest -q
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check .
git diff --check
```

- [ ] **Step 2: Run tier classification and replay requirements**

Classify each PR independently. Treat settlement/accounting or G7-input changes
as T3 unless the classifier and independent reviewer prove a stricter safety-
fix exemption. Never combine a negative-evidence override with a relaxation.

- [ ] **Step 3: Obtain independent financial-path review**

Review venue exhaustiveness, exact money conservation, fee effective dates,
legacy unknown handling, collision isolation, feature-flag independence,
transaction fault injection, and absence of order/queue paths.

- [ ] **Step 4: Publish protected PRs in dependency order**

Merge only after CI and review. Sync `main`; do not enable shadow capture.

- [ ] **Step 5: Separate activation gate**

After explicit operator approval, set only
`ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE=true`, protected restart, and verify:

- stable PID and healthy P0;
- canonical DB hashes/counts unchanged by a synthetic capture test;
- exact schema and append-only triggers;
- natural G7-only capture with complete provenance;
- zero unexpected errors;
- immediate rollback to `false` on any failure.

- [ ] **Step 6: Evidence gate before any G7 proposal**

Require 30 independent resolved families, 10 per affected venue, 95% scorable
coverage by rows and stake, positive lower 95% confidence bound on fee-net
incremental P&L, and stressed drawdown no greater than 20%. Otherwise retain
G7 unchanged and record the hypothesis as rejected or still insufficient.
