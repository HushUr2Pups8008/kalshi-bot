# scripts/

Operational and diagnostic utilities for kalshi-bot. None of these run inside
the bot process — they are auxiliary tools for development, observation, and
maintenance. Scripts marked *read-only* do not touch DBs, logs, or runtime
state.

## Test / CI tooling

| Script | Purpose |
|---|---|
| `run_tests.sh` | Canonical pytest entry point. Venv-aware; `--detach` runs in background and writes to `logs/tests/`. |
| `show_run_registry.py` | Show recent local test runs from `logs/tests/run_registry.jsonl`. |

## Platform / host setup

| Script | Purpose |
|---|---|
| `botcheck.py` | macOS status report for the Kalshi bot LaunchAgent workflow. |
| `setup_launchd.sh` | macOS-only: installs the bot as a LaunchAgent. |

## Database maintenance

| Script | Purpose |
|---|---|
| `check_sqlite_wal.sh` | Check for SQLite WAL/SHM sidecars; with `--checkpoint`, runs `PRAGMA wal_checkpoint(TRUNCATE)`. Do not run while the bot is active. |

## Data migration

| Script | Purpose |
|---|---|
| `migrate_trade_logs.py` | Split the legacy monolithic trade log into `archive/YYYY/MM/YYYY-MM-DD.jsonl` partitions. |
| `validate_trade_log_cutover.py` | Validate analytics parity between legacy and partitioned trade-log layouts. |

## Diagnostics (read-only)

| Script | Purpose |
|---|---|
| `daily_review.py` | Pipeline-shaped daily review report. |
| `decision_funnel_summary.py` | Concise decision funnel summary for structured trade logs. |
| `freshness_diagnostics.py` | Source freshness / latency diagnostics. |
| `match_quality_diagnostics.py` | Headline-to-market match quality diagnostics. |
| `match_suppression_audit.py` | Audit for `MATCH_SUPPRESSION_CANDIDATE` events. |
| `observability_completeness_review.py` | Observability completeness review for Stage 4.2. |
| `ollama_error_audit.py` | Parse `bot.log` for Ollama HTTP errors and classify by failure bucket. |
| `paper_performance_drilldown.py` | Paper trading performance drilldown from `data/paper_trades.db`. |
| `performance_analysis.py` | Performance analysis over trade history. |
| `pipeline_impact_audit.py` | Before/after audit for recent pipeline-quality changes. |
| `replay_dossier.py` | Dossier replay utility. |
| `signal_edge_diagnostics.py` | Diagnostics for the signal → opportunity → execution boundary. |
| `source_scorecard.py` | Source scorecard for operator review. |
| `trade_log_summary.py` | Summary tool for structured trade logs. |

## Keyword governance

| Script | Purpose |
|---|---|
| `keyword_feedback.py` | Keyword feedback audit for missed keyword-gate events. |
| `keyword_promotion_report.py` | Keyword promotion report — passive governance layer over shadow evaluation. |
| `keyword_shadow_eval.py` | Keyword shadow evaluation — passive offline analysis tool. |

## Harnesses / validation

| Script | Purpose |
|---|---|
| `budget_manager_stress.py` | Synthetic S4.3 stress test for the LLM budget manager. |
| `ollama_test_harness.py` | Standalone harness for validating the Ollama OpenAI-compatible path. |
| `regime_weight_validation.py` | S4.4 — Regime weight validation against historical outcomes. |
| `structured_log_latency_benchmark.py` | Measure durable structured-log append latency. |

## Behavioural simulations (read-only)

End-to-end harnesses that exercise specific pipeline stages against
production code paths and curated event fixtures. Read-only — never
mutate `paper_trades.db`, `evidence_store.db`, or the trade-log archive.
Safe to run while the bot is active.

| Script | Pipeline stage |
|---|---|
| `simulations/threshold_calibration.py` | Readiness-gate G4 + G1 thresholds vs. categorical priors and production `BLEND_DECISION` distribution. |
| `simulations/readiness_gate_events.py` | Readiness gate end-to-end against the 5 canonical LLM-positive events from the PROFIT-EDGE-001 investigation. |
| `simulations/executor_validate.py` | Executor `_validate()` (E1–E12) against the same 5 canonical events, in independent + sequential passes. |
| `simulations/match_score_audit.py` | Match-score gate (`PAPER_MIN_MATCH_SCORE` — pipeline's first kill point) against production headlines + market titles, with threshold sweep + cross-contamination guard. |
| `simulations/blend_task_integration.py` | Full `BlendTask.process_fast_lane_result` integration with seeded slow-lane context for KXTRUMPIRAN; cross-checks the readiness outcome against the no-dossier readiness simulation. |

See [`scripts/simulations/README.md`](simulations/README.md) for detailed
usage. Smoke tests in [`tests/test_simulations_smoke.py`](../tests/test_simulations_smoke.py)
keep the harnesses themselves green under code changes. The plan for
remaining pipeline-stage simulations is in
[`docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md`](../docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md).
