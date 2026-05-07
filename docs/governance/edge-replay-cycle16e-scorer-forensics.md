# Cycle-16E scorer forensics

**Verdict:** `scorer_overadmission_confirmed_no_ic16_slice_after_production_proxy`.

The Cycle-16D operational read is corrected. The raw `237 trades / 2 wins` number is a scorer-overadmission artifact, not a production-like trade count. The corrected production-proxy replay has 12 trades, 0 wins, and 0 IC §16-eligible slices.

## Load-bearing findings

1. Price units are cents-consistent. Captured Kalshi snippets expose `yes_price_dollars`; D3 stored cents via dollars x 100. No 100x unit inversion found.
2. The 50% random-win baseline is invalid for this corpus. Baseline market-implied expected wins are 9.463, not 118.5, because most raw trades are cheap YES longshots.
3. Raw scorer over-admitted trades. Paper price sanity, readiness, same-ticker cooldown, and duplicate-position gates reduce 237 raw trades to 12 production-proxy trades.
4. No IC §16 slice survives corrected replay: 0 slices with `ev_ci_95_lo > 0` and `trades >= 10`.

## Variant comparison

| variant | trades | wins | win_rate | yes/no | market_expected_wins | pnl |
|---|---:|---:|---:|---:|---:|---:|
| `baseline_abs_edge` | 237 | 2 | 0.0084 | 231/6 | 9.4630 | -7.4630 |
| `readiness_only` | 182 | 0 | 0.0000 | 182/0 | 4.1650 | -4.1650 |
| `paper_price_sanity` | 110 | 1 | 0.0091 | 108/2 | 7.7240 | -6.7240 |
| `readiness_plus_price_sanity` | 63 | 0 | 0.0000 | 63/0 | 3.4960 | -3.4960 |
| `readiness_price_signed_edge` | 63 | 0 | 0.0000 | 63/0 | 3.4960 | -3.4960 |
| `production_proxy` | 12 | 0 | 0.0000 | 12/0 | 1.0050 | -1.0050 |

## Production-proxy skip reasons

| reason | rows |
|---|---:|
| `baseline_not_trade` | 35 |
| `paper_duplicate_position` | 43 |
| `paper_price_sanity` | 119 |
| `paper_ticker_cooldown` | 8 |
| `readiness_not_admitted` | 55 |

## Price-unit audit

Unit verdict: `cents_consistent`.

| source | rows | min | max | fractional | <1c |
|---|---:|---:|---:|---:|---:|
| `live_trades` | 3561 | 0.1000 | 99.0000 | 382 | 109 |

Sub-2c prices are real replay inputs, not proof of a unit bug. They are outside the paper executor's `2c..98c` price sanity band and must be excluded from production-like trade counts.

## Production-proxy breakdowns

### By side

| side | trades | wins | win_rate |
|---|---:|---:|---:|
| `yes` | 12 | 0 | 0.0000 |

### By series

| series | trades | wins | win_rate |
|---|---:|---:|---:|
| `KXMOCTRUMP25` | 5 | 0 | 0.0000 |
| `KXPARDONSTRUMP` | 3 | 0 | 0.0000 |
| `KXVANCEPAKISTAN` | 2 | 0 | 0.0000 |
| `KXNEWDEAL` | 1 | 0 | 0.0000 |
| `KXVOTESAVEAMERICA` | 1 | 0 | 0.0000 |

## Re-run command

```bash
.venv/bin/python scripts/edge_replay/scorer_forensics_audit.py \
  --dataset logs/edge_replay/cycle16d/replay_dataset.jsonl \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --endpoint-diagnosis logs/edge_replay/cycle16d/endpoint_diagnosis.json \
  --output logs/edge_replay/cycle16e/scorer_forensics.json \
  --corrected-scores logs/edge_replay/cycle16e/counterfactual_scores_production_proxy.json \
  --report docs/governance/edge-replay-cycle16e-scorer-forensics.md
```

## Operational consequence

Cycle-16D's `information_frontier_holds` label remains not deploy-positive, but the anti-correlation interpretation is withdrawn. The corrected read is narrower: the previous scorer overstated trade count by admitting longshot YES rows that production paper-mode gates would reject or suppress. After correction, no positive-EV deploy slice exists; capital remains PAPER-ONLY.

---

## Claude N6 verdict appendix

**Drafted:** 2026-05-07 post-Codex `c913ffd`.
**Authority:** Cycle-16E task split N6 (`2026-05-07-cycle-16e-task-split.md`); rescope review (`2026-05-07-cycle-16e-claude-rescope-and-review.md`).

### Verdict against locked 4-outcome charter

Per task-split locked outcomes, Cycle-16E result = **`scorer_fixed_no_signal_confirmed`**.

Codex's report label `scorer_overadmission_confirmed_no_ic16_slice_after_production_proxy` is a more verbose synonym; both label outcome 2 in the locked table.

| outcome | match |
|---|---|
| `scorer_fixed_with_positive_ev_slice` | ❌ 0 IC §16 slices |
| **`scorer_fixed_no_signal_confirmed`** | **✓** 0 slices + production-proxy 12 trades / 0 wins is consistent with market-implied expected wins (1.005); no anomaly persists |
| `scorer_fixed_but_anomalous_persists` | ❌ no extreme win rate or persistent bias survives correction |
| `scorer_corrections_incomplete` | ❌ all 5 charter checks satisfied |

### Independent voice — Codex's central insight rescues prior framing

Cycle-16D M6 appendix (Claude, 2026-05-06) invoked "anti-correlated signal" hypothesis based on 2/237 = 0.84% vs assumed 50% coin-flip baseline. **The 50% coin-flip baseline was wrong** for this corpus. Most raw trades were YES at low cents (Kalshi longshot pricing), so market-implied expected wins were 9.463 — not 118.5. Got 2 wins. Underperforms market-implied baseline by 7.463 — not "99.16% wrong-direction."

Codex caught this. Operator caught the scorer-bug hypothesis. Both insights were absent from the M6 appendix. Filed lesson in memory.

### Production-proxy faithfulness

Cycle-16E production-proxy gates ported line-by-line from `executor.py:200-244`. All 7 gate constants match production exactly (price floor 2¢, ceil 98¢, ticker cooldown 14400s, paper-duplicate prob/price deltas 0.07/5.0, same-signal deltas 0.02/2.0). Two gates not modeled (opposing-position block, per-ticker concentration cap) but both would only further reduce the trade count, not raise it. Production-proxy 12 trades is therefore an UPPER BOUND on what production would have generated. **Verdict robust to these gaps.**

### Cycle-17 routing — RESTORED

Per `cycle-17-conditional-charter-skeletons.md`, `scorer_fixed_no_signal_confirmed` routes to **§B (source onboarding) OR §C (strategic redesign) per operator decision**. The §B/§C operator decision deferred 2026-05-07 per cycle-16D operator override is now **UN-DEFERRED**. Returns to the table.

Recommended weighting (un-changed from cycle-16D M6 appendix, BUT now supported by an audited scorer):
- 4 cycles produced no positive-EV slice across audited scorer + restored prices + repaired extraction.
- Operator should weigh §C heavily.
- §B onboarding remains valid path if operator picks; mandatory pre-onboarding re-trace requirement is RELAXED (the 235-loser trace concern was driven by anti-correlation hypothesis which is now withdrawn). §B can proceed against current evidence without per-trade trace, gated only on candidate-source replay validation per IC §16 Rule 4.

### What's RULED OUT (post-Cycle-16E)

- **Anti-correlated signal hypothesis.** Win rate matches market-implied baseline within sampling noise.
- **Scorer-bug hypothesis residual.** Production gates ported faithfully; 5 forensic checks satisfied.
- **Extraction overfit hypothesis.** Cycle-15B Lane B 8/8 + 2/2 ✓ + Cycle-16E sample-size 12 trades is too small to confirm or refute production-text overfit independently of source-coverage.
- **Cycle-16F additional forensics.** Anomaly was scorer artifact + wrong baseline; not extraction overfit + not LLM-path mispathology. §F not triggered.

### What this verdict does NOT settle

- Whether 12 production-proxy trades / 0 wins is "no signal" or "too small to tell." Statistically inconclusive at n=12. Cycle-17 §B onboarding would expand the trade pool; §C redesign would reset the trade pool entirely. Either path acknowledges current sample is uninformative for "edge exists" question.
- Whether LLM-path differentiation would change outcomes. L7.2 deferral remains open. Revisit only if Cycle-17 routes to §B and post-onboarding replay still produces 0 IC §16 slices.

### Capital posture

PAPER-ONLY. Locked. No live-trading flip. Cycle-17 §B/§C operator decision is the next gate; live-trading flip is gated on positive-EV slice surfacing post-Cycle-17, not on Cycle-16E itself.
