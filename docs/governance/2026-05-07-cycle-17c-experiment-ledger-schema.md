# Cycle-17C experiment ledger — schema + initial entries

**Type:** experiment log. ONE row per experiment. Includes reverts, diagnostic-only, and keeps. Failed experiments are information.
**Drafted:** 2026-05-07 cycle-17C charter landing.
**Authority:** `2026-05-07-cycle-17c-charter-single-variable-redesign.md` §"Experiment declaration" + §"Experiment result reporting".
**Maintained by:** Claude (after each experiment verdict); reviewed by Codex + operator.

## Frozen baseline

| field | value |
|---|---|
| **Anchor commit** | `c913ffd` (cycle-16e codex: scorer forensics audit) |
| **Replay dataset** | `logs/edge_replay/cycle16d/replay_dataset.jsonl` (272 dossier rows) |
| **Audited scorer** | `scripts/edge_replay/scorer_forensics_audit.py` production-proxy mode |
| **Replay command** | `bash scripts/edge_replay/run_cycle16d_replay.sh --skip-price-backfill && .venv/bin/python scripts/edge_replay/scorer_forensics_audit.py [args per cycle-16E reproducibility section]` |
| **Baseline metrics** | 12 trades / 0 wins / 1.005 market-implied expected wins / -$1.005 P&L / 0 IC §16 slices |

## Schema

Each experiment row contains the following fields. Pre-replay fields are committed in the criteria-lock commit BEFORE replay runs. Post-replay fields are filled after replay output lands.

### Pre-replay (locked)

| field | description |
|---|---|
| `experiment_id` | Sequential: E1, E2, E3, ... |
| `axis` | One of: `update_rule` / `market_family` / `side_inference` / `readiness_admission` / `extraction_prompt` / `keyword_map` |
| `hypothesis` | Single sentence, falsifiable. E.g. "Replacing additive update with Bayesian update on log-odds will produce ≥1 IC §16 slice on the frozen corpus." |
| `touched_file_line` | `file.py:line_range` |
| `before_pseudocode` | Existing function body or relevant lines (pseudocode OK) |
| `after_pseudocode` | Replacement (pseudocode OK) |
| `expected_directional_effect` | Qualitative + quantitative if predictable. E.g. "Trade count increases ~20-30% (looser admission via tighter probability calibration); win rate vs market-implied baseline closes by ≥5pp." |
| `replay_command` | Exact shell command including dataset path + scorer flags |
| `acceptance_threshold` | IC §16: ≥1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`. OR diagnostic-only criteria stated explicitly. |
| `revert_condition` | What triggers automatic revert. E.g. "If 0 IC §16 slices OR if `ev_ci_95_lo` overall worsens vs baseline by >0.02, revert." |
| `n_disclosure_plan` | How experiment will report resulting n + MDE. E.g. "Report trades count + computed minimum-detectable effect at observed n via 1-sided binomial test at α=0.05." |
| `pre_replay_commit` | Commit hash of criteria-lock commit |

### Post-replay (reported)

| field | description |
|---|---|
| `trades` | Count of counterfactual trades produced |
| `wins` | Count of winning trades |
| `market_implied_expected_wins` | `Σ p_yes_at_decision_time` over trades (or NO-side equivalent) |
| `pnl` | Net P&L in cents (per scorer convention) |
| `ic16_slices` | Count of (source × market_family × signal_type) slices passing both gates |
| `delta_vs_baseline` | Trade-count delta + wins delta + slice-count delta vs frozen baseline |
| `n_observed_mde` | Resulting n + MDE at α=0.05 |
| `decision` | One of: `keep` / `revert` / `diagnostic-only` |
| `decision_rationale` | One paragraph: why this decision matches charter rules |
| `post_replay_commit` | Commit hash of verdict commit (or revert commit if reverted) |
| `notes` | Any caveats, surprises, follow-up flags |

## Ledger entries

### E0 — Frozen baseline (synthetic row, not an experiment)

| field | value |
|---|---|
| experiment_id | E0 (baseline) |
| axis | n/a |
| hypothesis | n/a — baseline anchor |
| touched_file_line | n/a |
| before_pseudocode | n/a |
| after_pseudocode | n/a |
| expected_directional_effect | n/a |
| replay_command | per "Frozen baseline" section above |
| acceptance_threshold | n/a |
| revert_condition | n/a |
| n_disclosure_plan | n/a |
| pre_replay_commit | `c913ffd` |
| trades | 12 |
| wins | 0 |
| market_implied_expected_wins | 1.005 |
| pnl | -1.005 |
| ic16_slices | 0 |
| delta_vs_baseline | n/a (this IS the baseline) |
| n_observed_mde | n=12; MDE for win-rate detection at α=0.05 ≈ 25pp |
| decision | n/a (baseline) |
| decision_rationale | Frozen as baseline per cycle-17C charter; superseded only by `keep`-verdict experiment |
| post_replay_commit | `c913ffd` |
| notes | Cycle-16E `scorer_fixed_no_signal_confirmed` verdict; production-proxy gates ported faithfully line-by-line vs `executor.py:200-244` |

### E1 — Bayesian log-odds update rule (criteria locked)

| field | value |
|---|---|
| experiment_id | E1 |
| axis | `update_rule` |
| hypothesis | Replacing additive weighted-delta updates with Bayesian log-odds updates will convert repaired extraction signal into a differentiated probability distribution and produce ≥1 IC §16 slice on the frozen corpus. |
| touched_file_line | `analysis/dossier_builder.py:154-163` (`update_dossier` state-update estimate math only) |
| before_pseudocode | `if current_estimate is None: new_estimate = implied_probability; else: new_estimate = current_estimate + clamp((implied_probability - current_estimate) * original_weight, -cap, +cap)` |
| after_pseudocode | `current = 0.5 if current_estimate is None else current_estimate; raw_log_lr = original_weight * logit(clamp(implied_probability, 0.001, 0.999)); capped_log_lr = clamp(raw_log_lr, -2.0, +2.0); new_estimate = sigmoid(logit(clamp(current, 0.001, 0.999)) + capped_log_lr)` |
| expected_directional_effect | Materially changes probability distribution and trade admission; pre-replay plausible range remains 5-50 production-proxy trades, so the IC §16 `trades >= 10` gate is reachable but not guaranteed. |
| replay_command | `.venv/bin/python scripts/edge_replay/scorer_forensics_audit.py --dataset logs/edge_replay/cycle16d/replay_dataset.jsonl --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json --endpoint-diagnosis logs/edge_replay/cycle16d/endpoint_diagnosis.json --output logs/edge_replay/cycle17c/e1/scorer_forensics.json --corrected-scores logs/edge_replay/cycle17c/e1/counterfactual_scores_production_proxy.json --report docs/governance/edge-replay-cycle17c-e1-report.md` |
| acceptance_threshold | IC §16: ≥1 slice with `ev_ci_95_lo > 0` AND `trades >= 10`. |
| revert_condition | Revert if 0 IC §16 slices, replay cannot execute against frozen scorer/corpus, behavior changes outside the locked `update_dossier` estimate math, or every slice remains below `trades >= 10`. |
| n_disclosure_plan | Report observed trade count, slice trade counts, market-implied expected wins, P&L, and MDE at observed n. Use market-implied expected wins, not a 50% coin-flip baseline. |
| pre_replay_commit | Criteria-lock commit containing this row: `2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md` |
| trades | 2 |
| wins | 0 |
| market_implied_expected_wins | 0.1500 |
| pnl | -0.1500 |
| ic16_slices | 0 |
| delta_vs_baseline | -10 trades; 0 win delta; +0.855 P&L delta caused by suppressing almost all trades, not by producing an accepted slice |
| n_observed_mde | n=2; below IC §16 `trades >= 10`, so the experiment is under the minimum acceptance sample by construction |
| decision | `revert` |
| decision_rationale | E1 produced 0 IC §16 slices and only 2 production-proxy trades. That triggers the pre-locked revert condition even though aggregate P&L is less negative than baseline, because the result is not deploy-positive and is below the minimum slice sample threshold. |
| post_replay_commit | Verdict/revert commit containing `edge-replay-cycle17c-e1-report.md` and `git revert fa8e15a` |
| notes | Semantic check locked: `EvidenceScore.implied_probability` is dossier-state independent and market-price anchored; E1 uses `original_weight * logit(implied_probability)`, not `logit(implied_probability) - logit(current_estimate)`. Tested implementation commit: `fa8e15a`. Result report: `docs/governance/edge-replay-cycle17c-e1-report.md`. |

## Conventions

- **Sequential numbering.** E1, E2, E3, ... regardless of axis. Axis is recorded per-row, not per-cycle.
- **Reverts logged.** A `revert` decision still produces a complete ledger row. The post-replay-commit hash is the revert commit (which restores to last-keep baseline).
- **Diagnostic-only logged.** `diagnostic-only` produces a complete ledger row. The post-replay-commit hash is the verdict commit; no baseline change occurs.
- **Baseline pointer.** When an experiment is `keep`, update the "Frozen baseline" anchor commit at the top of this doc to the new post-replay commit. The previous baseline anchor stays referenced in the ledger row.
- **No retroactive edits.** Once a verdict is logged, the row is immutable. Corrections go in `notes` of a NEW row referencing the corrected experiment.

## Sample size + MDE table (for hypothesis design)

Reference table for experiment authors. Helps decide whether a hypothesis is testable on the frozen corpus.

| baseline n | observed wins | minimum-detectable change in win rate (α=0.05, 1-sided) |
|---|---|---|
| 12 | 0 | ~25 percentage points |
| 30 | 0 | ~10 pp |
| 100 | 0 | ~3 pp |
| 12 | 1 | detect "wins shift to ≥4" with confidence |
| 12 | 6 | detect "wins shift to ≥10" with confidence |

If a hypothesis predicts a smaller effect than the row corresponding to its expected n, the experiment is underpowered as designed. Either:
- Reframe to a larger-effect prediction, OR
- Pick an axis that produces a larger n (e.g., update_rule that admits more trades), OR
- Mark experiment `diagnostic-only` upfront — it cannot reach IC §16 acceptance regardless of outcome.

## Cross-links

- `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` — charter (rules source).
- `docs/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md` — first-axis info-gain table.
- `docs/governance/edge-replay-cycle16e-scorer-forensics.md` — frozen baseline source.
- `data/dossier_updates_post_fix.db` — frozen corpus.
- `scripts/edge_replay/scorer_forensics_audit.py` — frozen audited scorer.
