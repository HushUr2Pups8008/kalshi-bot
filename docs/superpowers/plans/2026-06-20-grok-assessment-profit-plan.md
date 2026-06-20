# Grok Assessment Profit Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Grok's broad profit recommendations into a current-state, evidence-first implementation path that improves replay/paper expectancy proof, throughput diagnostics, and sizing-risk validation without loosening money gates or enabling live trading.

**Architecture:** Keep this as a read-only/reporting-first slice. Add one consolidated profit-evidence report, fold missing throughput metrics into existing operator artifacts, and add a sizing-policy replay matrix. Defer multi-model blending and arbitrage execution until replay/paper evidence proves those are the limiting bottlenecks.

**Tech Stack:** Python scripts under `scripts/`, existing JSONL trade logs under `logs/trades/`, SQLite paper DB snapshots under `data/` or `logs/backups/db_snapshots/`, existing report builders in `scripts/daily_review.py` and `scripts/performance_analysis.py`, pytest.

---

## Current Evidence

Grok recommendations with merit:

- High merit: replay/paper evidence loop. The repo has `scripts/edge_replay/score_counterfactual_pnl.py`, `scripts/edge_replay/replay_gate.py`, `scripts/paper_performance_drilldown.py`, and `scripts/performance_analysis.py`, but no single fresh report combines replay EV, paper expectancy, hit rate, realized edge, and readiness verdict.
- High merit: prove positive expectancy before live. Current artifacts do not prove it. `logs/reports/performance/analysis_20260620_1100.txt` shows POST-P0 `Resolved 10`, `Win rate 50.0%`, `Net P&L +$3.65`, but readiness still fails on sample size, win rate, and `Drawdown 74.7% / 20% max`.
- Medium merit: throughput diagnostics. Daily/performance reports already show funnel, freshness, keyword misses, and skip categories, but not explicit `opportunities/day`, `opportunity age`, `SKIPPED rate`, or per-ticker trades/day in the operator path.
- Medium merit: Kelly/exposure validation. Kelly, cooldown, per-prefix exposure, concentration, and live-loss gates already exist. Missing piece is a parameter-sweep replay harness that shows EV/skip/drawdown tradeoffs before any sizing change.
- Low current merit: local multi-model blending. Existing `BlendTask` blends lanes, not models. Do not add model-vote architecture until replay evidence proves single-model inference is the limiting defect.
- Low current merit: arbitrage execution. Polymarket paper and settlement hooks exist, but cross-venue arbitrage is still observer/research scope. Do not add order-path arbitrage.

Safety constraints:

- Do not change live sizing, live gates, live PM enablement, or service restarts in this plan.
- Do not mutate `data/match_token_fp_counters.db`, `data/matcher_token_weights.json`, paper DBs, or runtime logs.
- Keep dirty runtime artifacts out of commits: `data/matcher_token_weights.json`, `logs/backups/`, `logs/state/`.

## File Structure

- Create `scripts/profit_evidence_report.py`: read-only consolidated CLI for replay + paper + readiness evidence.
- Create `tests/test_profit_evidence_report.py`: unit and rendering tests for the consolidated evidence report.
- Create `scripts/throughput_operator_metrics.py`: reusable metric helpers for opportunity/day, opportunity age, skipped rate, and per-ticker trades/day.
- Modify `scripts/daily_review.py`: render the new throughput metrics in the existing daily operator artifact.
- Modify `scripts/performance_analysis.py`: include the same metrics in the performance artifact when trade-log inputs are available.
- Modify `scripts/bothealth.sh`: add pointers to the new daily/performance metric sections, not a new heavy scan.
- Create `tests/test_throughput_operator_metrics.py`: helper tests for metric calculations.
- Update `tests/test_daily_review.py`: artifact rendering expectations.
- Update `tests/test_performance_analysis_p0_cohorts.py`: performance artifact expectations where applicable.
- Create `scripts/simulations/sizing_policy_replay.py`: read-only parameter sweep over existing paper/replay evidence for Kelly/exposure/cooldown settings.
- Create `tests/test_sizing_policy_replay.py`: parameter-grid and output-contract tests.
- Optional later task only after evidence: create `scripts/polymarket_settlement_feedback_audit.py` or extend an existing since-restart report if the consolidated report proves PM settlement feedback is still opaque.

---

### Task 1: Consolidated Profit Evidence Report

**Files:**
- Create: `scripts/profit_evidence_report.py`
- Create: `tests/test_profit_evidence_report.py`
- Read-only inputs: `logs/edge_replay/`, `logs/trades/`, `data/paper_trades.db`, `logs/backups/db_snapshots/*/paper_trades.db`, `logs/reports/performance/analysis_*.txt`

- [ ] **Step 1: Write tests for paper expectancy aggregation**

Create fixtures with resolved and open trades. Assert the report computes:

- total trades
- resolved/open counts
- win rate
- net P&L
- expectancy as average P&L per resolved trade
- average stored edge
- realized edge by coarse edge buckets

Run:

```bash
.venv/bin/pytest tests/test_profit_evidence_report.py::test_paper_expectancy_summary -q
```

Expected before implementation: fail because `scripts.profit_evidence_report` does not exist.

- [ ] **Step 2: Implement paper summary**

Implement a small dataclass model and pure functions:

```python
@dataclass(frozen=True)
class PaperExpectationSummary:
    total_trades: int
    resolved_trades: int
    open_trades: int
    wins: int
    losses: int
    net_pnl: float
    expectancy_per_resolved_trade: float | None
    avg_edge: float | None
    by_venue: dict[str, "PaperExpectationSummary"]
```

Read SQLite with `sqlite3`, accept a `--paper-db` path, and never write to DB.

- [ ] **Step 3: Write tests for replay artifact aggregation**

Fixture replay score JSON with `trade_count`, `win_rate`, `realized_pnl`, `per_trade_ev`, `avg_pnl_per_trade`, and CI fields. Assert empty/null `ci_runs/HEAD` artifacts are reported as `insufficient_corpus`, not treated as proof.

Run:

```bash
.venv/bin/pytest tests/test_profit_evidence_report.py::test_replay_artifacts_distinguish_scored_from_insufficient_corpus -q
```

- [ ] **Step 4: Implement replay summary**

Scan candidate files:

- `logs/edge_replay/**/counterfactual_scores*.json`
- `logs/edge_replay/ci_runs/*/verdict.json`
- `logs/edge_replay/ci_runs/*/rule4_table.json`

Normalize the fields into:

```python
@dataclass(frozen=True)
class ReplayEvidenceSummary:
    source: str
    status: Literal["scored", "insufficient_corpus", "missing", "unknown"]
    trade_count: int | None
    win_rate: float | None
    realized_pnl: float | None
    per_trade_ev: float | None
    ev_ci_95_lo: float | None
    ev_ci_95_hi: float | None
```

- [ ] **Step 5: Write tests for final verdict logic**

Assert current rule:

- `ready=false` if resolved sample is below threshold.
- `ready=false` if win rate, drawdown, or replay EV evidence fails/missing.
- `ready=true` only when paper sample, paper expectancy, drawdown, and replay evidence all pass.

Run:

```bash
.venv/bin/pytest tests/test_profit_evidence_report.py::test_verdict_requires_paper_and_replay_proof -q
```

- [ ] **Step 6: Implement JSON and text renderers**

CLI contract:

```bash
.venv/bin/python scripts/profit_evidence_report.py \
  --paper-db data/paper_trades.db \
  --edge-replay-root logs/edge_replay \
  --json
```

Text output must include:

- `PAPER EXPECTANCY`
- `REPLAY EVIDENCE`
- `READINESS VERDICT`
- `not live-ready` with reasons when any gate fails

- [ ] **Step 7: Verify against current data**

Run:

```bash
.venv/bin/python scripts/profit_evidence_report.py --paper-db logs/backups/db_snapshots/2026-06-20T1200Z/paper_trades.db --edge-replay-root logs/edge_replay --json
.venv/bin/pytest tests/test_profit_evidence_report.py -q
```

Expected current-world verdict: not live-ready because latest paper/replay evidence does not prove positive expectancy with enough sample and drawdown discipline.

- [ ] **Step 8: Commit**

```bash
git add scripts/profit_evidence_report.py tests/test_profit_evidence_report.py
git commit -m "Add consolidated profit evidence report"
```

---

### Task 2: Operator Throughput Metrics

**Files:**
- Create: `scripts/throughput_operator_metrics.py`
- Create: `tests/test_throughput_operator_metrics.py`
- Modify: `scripts/daily_review.py`
- Modify: `scripts/performance_analysis.py`
- Modify: `scripts/bothealth.sh`
- Modify: `tests/test_daily_review.py`
- Modify: `tests/test_performance_analysis_p0_cohorts.py`

- [ ] **Step 1: Write helper tests**

Use small synthetic event rows for `OPPORTUNITY`, `SKIPPED`, `PAPER_TRADE`, and timestamped ticker fields. Assert:

- opportunities/day
- median and p90 opportunity age if event age is present
- skipped rate = skipped / opportunities
- per-ticker trades/day

Run:

```bash
.venv/bin/pytest tests/test_throughput_operator_metrics.py -q
```

Expected before implementation: import failure.

- [ ] **Step 2: Implement streaming helpers**

Implement:

```python
def summarize_operator_throughput(events: Iterable[dict[str, Any]], *, window_start: datetime, window_end: datetime) -> ThroughputOperatorSummary:
    ...
```

Do not use `Path.read_text().splitlines()` for production log scans. Iterate line by line.

- [ ] **Step 3: Add daily review section**

In `scripts/daily_review.py`, render a compact section named `OPERATOR THROUGHPUT LEADING INDICATORS` with:

- opportunities/day
- skipped/opportunity ratio
- top tickers by trades/day
- opportunity age p50/p90, or `unavailable` when source fields are absent

- [ ] **Step 4: Add performance artifact section**

In `scripts/performance_analysis.py`, render the same metrics when trade-log inputs exist. Keep DB-only runs functional by showing `trade-log metrics unavailable`.

- [ ] **Step 5: Add bothealth pointer**

In `scripts/bothealth.sh`, add a one-line pointer to the daily/performance sections. Do not add a second full scan of `logs/trades`.

- [ ] **Step 6: Verify**

Run:

```bash
.venv/bin/pytest tests/test_throughput_operator_metrics.py tests/test_daily_review.py tests/test_performance_analysis_p0_cohorts.py -q
.venv/bin/ruff check scripts/throughput_operator_metrics.py scripts/daily_review.py scripts/performance_analysis.py tests/test_throughput_operator_metrics.py tests/test_daily_review.py tests/test_performance_analysis_p0_cohorts.py
```

- [ ] **Step 7: Commit**

```bash
git add scripts/throughput_operator_metrics.py scripts/daily_review.py scripts/performance_analysis.py scripts/bothealth.sh tests/test_throughput_operator_metrics.py tests/test_daily_review.py tests/test_performance_analysis_p0_cohorts.py
git commit -m "Add operator throughput leading indicators"
```

---

### Task 3: Sizing Policy Replay Matrix

**Files:**
- Create: `scripts/simulations/sizing_policy_replay.py`
- Create: `tests/test_sizing_policy_replay.py`
- Read: `analysis/kelly.py`, `trading/executor.py`, `config.py`

- [ ] **Step 1: Write matrix tests**

Fixture resolved paper trades with stored probability, price, edge, source class, and P&L. Assert the script can evaluate a grid:

- `kelly_fraction`
- `floor_clamp_kelly_multiplier`
- `max_ticker_exposure_pct`
- `paper_ticker_cooldown`
- per-prefix cap

Expected output fields:

- simulated trades
- skipped by category
- net P&L
- expectancy
- max drawdown
- venue split
- concentration hits

- [ ] **Step 2: Implement read-only replay**

Use existing `analysis.kelly.kelly_bet()` and executor category semantics where possible. Do not import live executor state or write cooldowns. Treat this as an offline policy simulator.

- [ ] **Step 3: Add guardrails**

The script must print:

```text
read_only=true
no_live_policy_change=true
```

JSON output must include:

```json
{
  "read_only": true,
  "no_live_policy_change": true
}
```

- [ ] **Step 4: Verify**

Run:

```bash
.venv/bin/pytest tests/test_sizing_policy_replay.py tests/test_kelly.py tests/test_executor.py -q
.venv/bin/ruff check scripts/simulations/sizing_policy_replay.py tests/test_sizing_policy_replay.py
```

- [ ] **Step 5: Commit**

```bash
git add scripts/simulations/sizing_policy_replay.py tests/test_sizing_policy_replay.py
git commit -m "Add sizing policy replay matrix"
```

---

### Task 4: Polymarket Settlement Feedback Proof

**Files:**
- Prefer modifying existing `scripts/since_restart_money_path.py` or `scripts/since_restart_counterfactual_review.py` before creating another script.
- If a separate script is justified, create `scripts/polymarket_settlement_feedback_audit.py`.
- Create or update tests beside the chosen script.

- [ ] **Step 1: Write proof requirements**

A PM settlement proof row must connect:

- Polymarket paper trade row
- settlement reconciler outcome
- resolved `pnl_dollars`
- feedback/review event if present
- report artifact section

- [ ] **Step 2: Implement read-only audit**

The audit should target the currently small PM resolved sample and say `insufficient_sample` when only two PM rows exist.

- [ ] **Step 3: Verify**

Run:

```bash
.venv/bin/pytest tests/test_since_restart_money_path.py tests/test_since_restart_counterfactual_review.py -q
```

Add a new focused test only if existing report tests cannot express the proof chain.

- [ ] **Step 4: Commit**

```bash
git add scripts/since_restart_money_path.py scripts/since_restart_counterfactual_review.py tests/test_since_restart_money_path.py tests/test_since_restart_counterfactual_review.py
git commit -m "Add Polymarket settlement feedback proof"
```

---

### Task 5: Defer or Reject Non-Merited Work

**Files:**
- Modify: `docs/profit_path_debt_log.md` or the active tracker file used for this lane.

- [ ] **Step 1: Record explicit non-goals**

Document:

- no live trading enablement from this assessment
- no local multi-model/agent ensemble until replay evidence identifies model inference as the bottleneck
- no arbitrage execution until observer-mode divergence and fee-model evidence exist
- no sizing/gate changes until `profit_evidence_report.py` and `sizing_policy_replay.py` show a better policy

- [ ] **Step 2: Commit**

```bash
git add docs/profit_path_debt_log.md
git commit -m "Record Grok assessment non-goals"
```

---

## Multi-Agent Execution Team

- Agent A, Replay/Paper Evidence: owns Task 1. No runtime mutation. Required output is a JSON/text report and tests.
- Agent B, Operator Throughput: owns Task 2. Writes only throughput helpers, report renderers, and report tests.
- Agent C, Risk/Sizing: owns Task 3. Writes only offline replay simulation and tests.
- Agent D, Polymarket Proof: owns Task 4 after Task 1 lands, because it should reuse the consolidated evidence schema if possible.
- Main coordinator: reviews each agent patch, runs focused tests, prevents scope creep into live gates, service restarts, DB mutation, or arbitrage.

## Completion Criteria

This plan is complete only when:

- The consolidated profit report can be run on current artifacts and emits a not-live-ready verdict with concrete reasons.
- Daily/performance/bothealth artifacts expose the missing leading indicators.
- Sizing policy replay can compare parameter sets without mutating runtime state.
- PM settlement feedback proof is either visible in existing reports or explicitly marked insufficient sample with evidence.
- Tests and ruff pass for every modified slice.
- Runtime dirty artifacts remain uncommitted unless the operator explicitly approves persisted-state mutation.
