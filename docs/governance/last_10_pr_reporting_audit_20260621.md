# Last 10 PR Reporting Carry-Through Audit - 2026-06-21

Scope: merged PRs #156 through #147 on `main`, audited against daily-report
metrics and versioning rules after the 2026-06-21 reporting/replay work.

## Verdict

- Daily reporting now carries the operational effects from the recent runtime
  and reporting PRs in meaningful operator-facing metrics.
- PR #156 is harness-only and has no trading/reporting metric requirement.
- Versioning was incomplete for the current reporting/replay work because
  `VERSION` and the README badge were still at `0.33.19`; this audit is
  versioned as `0.33.20`.

## PR Coverage Matrix

| PR | Merge date | Theme | Daily/reporting carry-through |
|---|---:|---|---|
| #156 | 2026-06-21 | ECC harness agent-sort install | No trading metric required. Harness-only change; documented here so it is not mistaken for a missing reporting surface. |
| #155 | 2026-06-21 | Normalize legacy Polymarket report series | Covered by `scripts/paper_performance_drilldown.py` and `scripts/performance_analysis.py`; daily review consumes paper-performance drilldowns and open exposure buckets. |
| #154 | 2026-06-20 | Grok profit evidence workoff | Covered by daily review throughput, since-restart money path, performance-analysis hooks, and operator throughput metrics. |
| #153 | 2026-06-20 | Money-path followups | Covered by LLM value-add, routing-skip, edge-audit, and operator-throughput sections. Runtime venue/domain-key changes are visible through venue/prefix and opportunity/skip attribution. |
| #152 | 2026-06-20 | Restart reporting followups | Directly covered by daily review, decision-funnel, keyword-feedback, paper-performance, and signal-edge sections. |
| #151 | 2026-06-18 | Restart money-path audit tooling | Covered by the `SINCE-RESTART MONEY PATH` daily-review section, including Polymarket settlement feedback and candidate terminal reasons. |
| #150 | 2026-06-17 | PM settlement reach + marking snapshot fallback | Covered by Polymarket settlement feedback/proof rows and paper-performance/open-exposure reporting. |
| #149 | 2026-06-16 | PM settlement isolation, blend prefix, MTM drawdown gate | Covered by Polymarket settlement feedback, venue/prefix attribution, skip/liquidity/duplicate metrics, and open exposure horizon. Go-live remains gated separately; daily reporting does not mark live readiness as ready. |
| #148 | 2026-06-15 | Polymarket per-prefix exposure-cap granularity | Covered by match-weight prefix reporting, skip attribution, and Polymarket open-exposure reporting; per-contest prefix behavior is also covered by executor tests. |
| #147 | 2026-06-13 | Venue-parity wave 2 | Covered by Polymarket normalized model fields, venue/prefix attribution, settlement-source match attribution, and Polymarket discovery/context tests. |

## Replay Evidence

Commands run from repo root:

```bash
DAILY_REVIEW_COUNTERFACTUAL_HYDRATE_KALSHI_MARKETS=true \
  .venv/bin/python scripts/daily_review.py \
  --path logs/trades/live/trades.jsonl \
  --since 2026-06-21 \
  --top 5
```

Observed daily-report carry-through:

- Fresh-pass conversion: `352 fresh -> 44 signal rows -> 40 LLM attempts -> 4 opportunities -> 0 paper trades`.
- Match weight applications: `5524` with aggregate `score_delta=-51.0738`.
- Polymarket settlement feedback: `insufficient_sample (0/10 resolved)`.
- Counterfactual LLM eval with Kalshi-detail hydration: `neutral_none_no_keywords=19 context_ready=19 missing_contract_context=0`.
- Execution section: paper trades `0`, duplicate skips `0`, liquidity/near-limit skips `2`.
- Open exposure by resolution horizon is present for paper-performance visibility.
- Source/settlement attribution drilldowns are present for opportunity and skip paths.

Counterfactual model replay:

```bash
.venv/bin/python scripts/counterfactual_llm_eval.py \
  logs/trades/live/trades.jsonl \
  --since 2026-06-21T00:00:00Z \
  --hydrate-kalshi-market-details \
  --eval-ollama-model qwen2.5:7b \
  --eval-ollama-model qwen3:14b \
  --ollama-timeout-seconds 120 \
  --json
```

Result:

- `neutral_none_no_keywords=19`
- `hydrated_contract_context=19`
- `evaluated_context_ready=19`
- `missing_contract_context=0`
- `qwen2.5:7b`: 19 evaluated, 1 paper-candidate-positive, 0 errors
- `qwen3:14b`: 19 evaluated, 0 paper-candidate-positive, 0 errors

Artifacts:

- `logs/reports/counterfactual_llm_eval_20260621.json`
- `logs/reports/counterfactual_llm_eval_20260621.txt`

## Versioning

Repo standard:

- `VERSION` is the source of truth.
- `scripts/sync_readme_version.py --check` enforces README badge/current-through
  synchronization in CI.
- Feature/fix work requires a `VERSION` bump plus `CHANGELOG.md` entry.

Correction:

- Bumped `VERSION` from `0.33.19` to `0.33.20`.
- Synced README via `scripts/sync_readme_version.py --write`.
- Added the `0.33.20` changelog entry documenting this audit and the reporting
  carry-through changes.
