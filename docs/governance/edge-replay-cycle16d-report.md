# Cycle-16D replay report

Generated: 2026-05-07

## Verdict

**IC §16 acceptance: FAIL.**

Verdict label: `extraction_fixed_but_information_frontier_holds`.

No source x market-family x signal-type slice met both locked criteria:

- `ev_ci_95_lo > 0`
- `trades >= 10`

One raw positive-EV scorer group exists, but it has `trades = 1`; it is not an IC §16-accepted slice.

## Inputs

- Post-fix DB: `data/dossier_updates_post_fix.db`
- Resolved markets: `logs/edge_replay/cycle13_live/resolved_markets_full.json`
- Historical prices: `logs/edge_replay/cycle16d/historical_prices_cycle16d.json`
- Dataset: `logs/edge_replay/cycle16d/replay_dataset.jsonl`
- Scores: `logs/edge_replay/cycle16d/counterfactual_scores.json`
- Coverage audit: `logs/edge_replay/cycle16d/coverage_audit.json`

## D6 summary

| Metric | Value |
|---|---:|
| Replay rows | 272 |
| Rows with decision-time executable price | 271 |
| Coverage | 99.63% |
| Counterfactual trades | 237 |
| Counterfactual wins | 2 |
| Counterfactual P&L | -7.4630 |
| Overall ev_ci_95_lo | -0.0382 |
| Raw positive-EV scorer groups | 1 |
| IC §16 accepted slices | 0 |
| Brier score (latest model per market, n=24) | 0.2517 |

Brier is supporting evidence only: n=24 markets gives wide uncertainty. IC §16 routing remains governed by slice EV and trade-count criteria.

## Slice table

| slice | candidates | trades | wins | win_rate | pnl | ev_ci_95_lo |
|---|---:|---:|---:|---:|---:|---:|
| `paper-trade-roundtrip` / `KXTRUMPIRAN` / `SKIPPED` / `unknown` | 1 | 1 | 1 | 1.0000 | 0.0010 | 0.0010 |
| `unknown` / `KXTRUMPIRAN` / `PAPER_RESOLUTION` / `unknown` | 2 | 0 | 0 | n/a | 0 | 0.0000 |
| `NYT > World News` / `KXVANCEPAKISTAN` / `MATCH_DIAGNOSTIC` / `unknown` | 4 | 0 | 0 | n/a | 0 | 0.0000 |
| `NYT > World News` / `KXVANCEPAKISTAN` / `ANALYSIS_REJECTED` / `unknown` | 4 | 0 | 0 | n/a | 0 | 0.0000 |
| `Al Jazeera – Breaking News, World News and Video from Al Jazeera` / `KXVANCEPAKISTAN` / `MATCH_DIAGNOSTIC` / `unknown` | 1 | 0 | 0 | n/a | 0 | 0.0000 |
| `Al Jazeera – Breaking News, World News and Video from Al Jazeera` / `KXVANCEPAKISTAN` / `ANALYSIS_REJECTED` / `unknown` | 1 | 0 | 0 | n/a | 0 | 0.0000 |
| `NYT > World News` / `KXVANCEPAKISTAN` / `SIGNAL_ANALYSIS_DETAIL` / `unknown` | 1 | 0 | 0 | n/a | 0 | 0.0000 |
| `AP News` / `KXFISAEXTEND` / `SIGNAL_ANALYSIS_DETAIL` / `unknown` | 4 | 0 | 0 | n/a | 0 | 0.0000 |

## Coverage and exclusions

Coverage passed at 271/272 rows.

The only missing executable-price row is explicitly excluded from P&L scoring:

| ticker | rows | covered | reason |
|---|---:|---:|---|
| `KXPARDONSTRUMP-26APR-22` | 1 | 0 | `no_price_series_for_ticker=1` |

## D9 POST_FIX_REBUILT sentinel

- Sentinel status: `pass`
- Cohort flag: `post_fix_rebuilt`
- `cycle_15b_c7_deploy_commit`: `2222227`
- `cycle_15b_c7_deploy_ts`: `2026-05-07T00:00:00+00:00`

D6 used `data/dossier_updates_post_fix.db`; pre-fix paper-trade DB and trade-log inputs were pointed at empty `/private/tmp` paths.

## Reproducibility

```bash
bash scripts/edge_replay/run_cycle16d_replay.sh --skip-price-backfill
```

Live price refresh path:

```bash
bash scripts/edge_replay/run_cycle16d_replay.sh --live-price-backfill
```

Equivalent command sequence:

```bash
.venv/bin/python scripts/edge_replay/price_coverage_audit.py \
  --dataset logs/edge_replay/cycle15b/replay_dataset.jsonl \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --post-fix-db data/dossier_updates_post_fix.db \
  --output logs/edge_replay/cycle16d/coverage_audit.json

.venv/bin/python scripts/edge_replay/build_replay_dataset.py \
  --markets logs/edge_replay/cycle13_live/resolved_markets_full.json \
  --paper-trades-db /private/tmp/kalshi_cycle16d_no_paper_trades.db \
  --trade-log '/private/tmp/kalshi_cycle16d_no_trade_logs/*.jsonl' \
  --evidence-store-db data/dossier_updates_post_fix.db \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --output logs/edge_replay/cycle16d/replay_dataset.jsonl

.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py \
  --dataset logs/edge_replay/cycle16d/replay_dataset.jsonl \
  --readiness-confidence 0.05 \
  --output logs/edge_replay/cycle16d/counterfactual_scores.json
```

## Interpretation

Cycle-16D removes the prior scorer-blocked state: prices are now available for 99.6324% of rows. With prices verified, the post-fix replay still has no deployable IC §16 slice. Overall counterfactual P&L is negative, and the only raw positive group is a one-trade artifact below the locked trade-count floor.

Routing: no Wave-2/3/D behavioral deploy. Capital posture remains PAPER-ONLY pending Claude M6/M10 verdict consumption.

---

## Claude appendix — verdict consumption + Cycle-17 routing

**Drafted:** 2026-05-07 post-Codex D6/D8/D9/D10 commit `e59f4c3`.
**Authority:** Cycle-16D charter §"Cycle-16D success criterion"; cycle-16D post-verdict checklist (`cycle-16d-post-verdict-action-checklist.md`); Cycle-17 skeletons (`cycle-17-conditional-charter-skeletons.md`).

### Verdict

Per Cycle-16D charter: **`extraction_fixed_but_information_frontier_holds`** (D5 coverage 99.6324% ≥ 90% ✓ AND D8 0 IC §16 slices with `ev_ci_95_lo > 0` AND `trades ≥ 10`).

Verdict derived strictly from locked charter mapping. No operator improvisation.

### Independent voice — anomalously low win rate

The verdict label "information_frontier_holds" carries an implicit assumption: the bot's source mix does not carry decisive signal. Cycle-15B Claude appendix flagged that Cycle-15B's "frontier holds" was qualified — scorer-blocked, not signal-absent. Cycle-16D restores the price layer, so this read is unambiguous.

**However, the win rate is anomalously low.** Of 237 counterfactual trades:
- 2 wins / 237 = **0.84% win rate**
- 235 losses
- Overall `ev_ci_95_lo = -0.0382` (significantly negative at 95% CI)

For binary prediction markets where the bot bets YES or NO:
- Pure no-signal random extraction would produce ~50% win rate.
- Pure no-signal at threshold-admitted trades would still center near coin-flip absent systematic miscalibration.
- 0.84% means bot bet **wrong side** in 99.16% of trades — anti-correlated with outcomes, not orthogonal.

Two readings consistent with this evidence:

1. **Signal is anti-correlated.** The bot's extraction emits directional confidence that is consistently inverse to actual resolution outcomes. This would require systematic sign-error somewhere downstream of the keyword-path repair (cycle-15B C7) — possibly in side derivation, executed-edge sign, OR the readiness-admission criterion gating wrong-direction conviction.
2. **Extraction fix overfit synthetic Lane B fixtures.** Codex C7 added fixture-specific phrases ("fisa section 702 reauthorization signed into law", "trump issues pardons", etc.) that pattern-match production evidence in WRONG context. E.g., a news article discussing historical FISA reauthorization for context could match the keyword on a current market where FISA has NOT yet been reauthorized — producing a YES signal where actual resolution is NO. The L4 secondary concern (overfitting risk) is now consistent with C10 evidence.

Both readings produce the same observed anti-correlation. Distinguishing them requires either:
- A site-by-site re-trace of the 235 losing trades to find systematic patterns (Cycle-15B-extension scope), OR
- Source onboarding with new vocabulary that bypasses the overfit-keyword surface (Cycle-17B scope).

### Three-failed-fix architectural rule

Per `superpowers:systematic-debugging` "if 3+ fixes failed → question architecture":

| cycle | verdict | architectural relevance |
|---|---|---|
| Cycle-13 | 0 positive-EV slices, 0 left-on-table winners | scope expansion, no fix attempted |
| Cycle-14 | `extraction_broken` (Lane B 0.000) | diagnosis only, no fix attempted |
| Cycle-15B | `extraction_fixed_but_ic_§16_scorer_blocked` | C7 keyword-map fix; Lane B 8/8 + 2/2 ✓ at synthetic; IC §16 unevaluable due to price gap |
| Cycle-16D | `extraction_fixed_but_information_frontier_holds` | price layer restored; 237 trades; 0.84% win rate; 0 IC §16 slices |

The fix-attempt count formally is **one** (Cycle-15B C7 keyword-map). Cycle-13 was scope expansion; Cycle-14 was diagnosis; Cycle-16D was harness restoration. Strict rule application says architectural conversation does not yet fire.

**However**, the cumulative evidence is harder to dismiss. Four cycles of work, each landing its locked criterion, have produced:
- Diagnostic infrastructure ✓
- Extraction repair ✓
- Price reconstruction ✓
- **Zero positive-EV slices.**
- 99.16% wrong-direction trade rate at 237-trade sample.

The 0.84% win rate is the hardest data point to reconcile with "extraction now works correctly." Either Lane B fixtures don't represent production evidence, or production evidence is systematically anti-correlated with outcomes at this trader's information set, or both.

### Cycle-17 routing recommendation

Per `cycle-17-conditional-charter-skeletons.md` verdict-to-skeleton map: `extraction_fixed_but_information_frontier_holds` → §B (source onboarding) OR §C (strategic redesign). Operator picks.

**Claude recommends operator weigh §C heavily.** Rationale:

- §B source onboarding assumes the problem is the source mix, not the extraction. The 0.84% win rate suggests extraction may be over-fitting OR signal at the bot's information set is anti-correlated. Onboarding new sources without first re-tracing the wrong-direction pattern risks burning 2-4 weeks rebuilding the same anti-correlation pattern through new vocabulary.
- §C strategic redesign acknowledges that 4 cycles + 1 fix have not produced positive-EV evidence. Continuing without redesign repeats the cycle-12 "deploy hope" pattern that IC §16 was added to prevent.
- §C menu (a)/(b)/(c) — pause / fundamental redesign / paper-only research — leaves the bot's 3 lifetime live trades + -$7.50 P&L as the empirical edge result. No further operator time burned without changed plan.

If operator picks §B regardless, recommend §B include a **mandatory pre-onboarding re-trace** of the 235 wrong-direction trades to identify whether the failure pattern is overfit-keyword or anti-correlated-source. Without that, §B is operating blind on the same data that produced the wrong-direction trades.

### What's RULED OUT

- **`extraction_fixed_with_positive_ev_slice`** — 0 IC §16 slices. Not eligible.
- **`cycle_16d_extension_needed`** — coverage 99.6324% ≥ 90%. Not coverage-blocked.
- **`escalation_required`** — coverage well above 70%. Not evaluation-failed.
- **Scorer-blocked / price-blocked state** — restored: 237 trades materialized.
- **Coverage-blocked state** — 271/272 priced.
- **Cycle-15B C7 keyword-map fix as the load-bearing problem** — Lane B 8/8 + 2/2 ✓ on synthetic fixtures. The fix did its synthetic-acceptance job; production behavior is the new question.

### What this verdict does NOT settle

- Whether the 0.84% win rate is anti-correlated signal vs overfit keyword vs anti-correlated information set. Distinguishing requires per-trade trace of the 235 losers — not a Cycle-16D deliverable.
- Whether re-running Cycle-13 against POST_FIX_NEW (production-runtime) data would produce different results vs the POST_FIX_REBUILT (re-ingested) corpus. The L7.2 finding (C9 keyword-only re-ingestion; LLM path bypassed) means LLM-path differentiation might or might not change the outcome at scale.
- Whether Wave-1's OBS-005 cooldown unblock (post-2026-05-08 deploy) widens the wrong-direction exposure on production runtime. Paper-mode lock holds; live-trading flag stays False; this is a forward concern only.

### Capital posture

PAPER-ONLY. Locked. No live-trading flip authorized regardless of operator's §B vs §C pick. If operator picks §B and a positive-EV slice surfaces post-onboarding, live-trading flip remains a separate operator action with replay-report citation per IC §16 Rule 4.
