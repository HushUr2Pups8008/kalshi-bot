# Strategic redirect — edge replay over speculative feed onboarding

**Type:** strategic decision document. Operator + Codex + Claude alignment.
**Drafted:** 2026-05-06 (cycle 11.5).
**Authority:** operator decision, post-conversation between Codex and operator (2026-05-05/06).
**Status:** APPROVED.

## TL;DR

Bot has zero proof of edge: 3 lifetime paper trades, 0/3 wins, -$7.50 P&L, 1 source dependence (VitalLaw via Google News), 1 correlated series (KXFISAEXTEND FISA legal-niche). 89% of OBS-003 SKIPPED records show `edge +0.0000 below min_edge 0.02` — bot's model produces market-equivalent probabilities most of the time.

Cycles 7-11 work was deployment safety, observability, and operator control. Necessary but insufficient. **Wave-2 (feed onboarding) and Wave-3 (Lever B/C) are speculation absent replay evidence.** Continuing to pre-stage those deploys is "progress toward operating the bot cleanly," not "progress toward making money."

**Decision:**
1. **Wave-1 shipped 2026-05-09 in de-scoped form (`OBS-003` only)**; the original 2026-05-08 cleanup bundle did not ship as planned.
2. **HALT Wave-2 + Wave-3 deploy track** until replay evidence shows positive expected value for the candidate lever.
3. **Cycle-12 = replay harness construction** (Codex). Output: per-source × market-family × signal-type counterfactual P&L table with confidence intervals.
4. **New IC §16: Replayed-EV gate for behavioral deploys.** Behavioral changes (intake, classifier, blender, gates, sizing) deploy only after replayed-EV evidence shows positive expected value. Safety / observability / governance fixes are exempt.
5. **Reassess Wave-2/3 only after replay.** If replay finds positive-EV feature slice → that becomes the Wave-2 candidate (NOT speculative legal-niche feeds). If replay finds none → strategic pivot question (calibration / sample-size / information-frontier diagnosis).

## The smoking gun (data)

```
Source: data/paper_trades.db
Total trades:       3
Resolved:           3
Wins:               0
Lifetime P&L:      -$7.50
Source diversity:   1 (VitalLaw.com)
Series diversity:   1 (KXFISAEXTEND, FISA legal niche)
Direction:          all NO
Edge calc:          all +0.068 (above min_edge 0.02)
Outcome:            all 3 resolved YES (FISA was extended)
```

```
Source: cycle-10 OBS-003 attribution refresh
SKIPPED records:    9
Reasons:
  edge +0.0000 below min_edge 0.02:           8/9 (89%)
  paper cooldown: last trade 0.0h ago:        1/9
```

When bot evaluates a candidate, 89% of the time it has zero informational advantage. The 1 lever-tightening proposal (Wave-3 Lever B G1=0.04) would mostly convert that "zero edge" volume into more sub-margin trades — counterindicated absent replay.

## Wave-1 shipped (historical rationale)

- All 6 specs prepped; cycle-9/10/11 deploy-safety work is done. Cost to ship ≈ 1h operator-time.
- **OBS-005 cooldown sentinel fix** has a real mechanical hypothesis: pre-fix `0.0` default for never-traded tickers indistinguishable from "cooldown just expired"; bot may be silently blocking fresh tickers from cooling down. Post-deploy production data is the test. If trade rate ticks up vs pre-deploy baseline on never-traded tickers, hypothesis confirmed; if not, rule it out and move on.
- **OBS-003 SKIPPED-emission** improves attribution. We NEED this to do replay analysis honestly (G1-G6 reasons must surface, not just executor-level reasons).
- **MATCH-001 (B′)** + **GOV-003** + **EXEC-002** + **Lever A.1** are defensive / governance / classifier-prereq. Don't create edge but don't block it either; cheap to ship.
- Wave-1 ultimately landed as an `OBS-003`-only cleanup/observability ship; trade-rate uplift remained a hypothesis, not a delivered claim.

## Wave-2 + Wave-3 are HALTED pending replay

| lever | original Wave | speculation? | replay-gated? |
|---|---|---|---|
| Lever A.1+ feed onboarding (legal/geopolitics specialists) | Wave-2 | YES | YES |
| Lever B G1 0.05→0.04 calibration | Wave-3 | YES (Codex: "may convert no-edge into more low-quality trades") | YES |
| Lever C cross-series headline guard | Wave-3 | NO (suppression) — but value is risk control, not alpha | safety-track exempt under §16 below |

Wave-3 Lever C remains safety-relevant if trading resumes (correlated-burst suppression). Lever B is COUNTERINDICATED until replay shows tighter G1 admittance produces positive replayed EV.

## Cycle-12 Codex assignment — replay harness construction

**Owner:** Codex. **Type:** prod-code, not docs.
**Success criterion:** identify at least one (source × market-family × signal-type) slice with positive replayed EV at 95% CI, or explicitly report none found across the full evidence-store window.

**Artifacts (Codex authors):**

1. `scripts/edge_replay/fetch_resolved_markets.py` — Kalshi API puller for resolved markets in the last 30-60 days. Outputs per-market metadata: ticker, title, resolution timestamp, resolution outcome (yes/no), price history if accessible.
2. `scripts/edge_replay/build_replay_dataset.py` — for each resolved market, walk `data/evidence_store.db` snapshots and bot evidence streams over the market's lifetime; emit per-(market, decision-time) record: model_prob, market_yes_price, edge, signal_source, signal_type, news_class, regime_class.
3. `scripts/edge_replay/score_counterfactual_pnl.py` — score each candidate-decision-point as would-have-traded (apply current G1-G6 readiness gate + cooldown + sizing), would-have-won (against resolution outcome), and per-trade EV. Aggregate into the table:

   | source | market_family | signal_type | trades | wins | win_rate | pnl | ev_per_trade | sharpe | ci_95 |

4. `tests/test_edge_replay_*.py` — at minimum: dataset-build determinism test, P&L sign test on synthetic input, source-aggregation test.
5. `docs/governance/edge-replay-cycle12-report.md` — the table + interpretation. Either ranks profitable slices OR explicitly says "no slice has positive replayed EV at our sample size."

**Constraints:**
- Evidence-store data starts 2026-04-23 (~13 days). 30-60 resolved markets in that window may be sample-limited; report confidence intervals honestly.
- Pure read-only against archived data; no live API trading. No new feeds onboarded.
- Replay must apply the CURRENT pipeline state (pre-Wave-1) to be a fair "what would the bot as-deployed have done?" measurement. Optional: a second pass that simulates Wave-1 as deployed (post-OBS-005, post-MATCH-001, etc.) for forward-looking projection. Frame as "current state" vs "Wave-1-ready state" replays.

**Out of scope:**
- Wave-2 / Wave-3 deploy work.
- Speculative new-feed onboarding.
- Lever B G1 tightening (counterindicated absent replay evidence).
- More doc / governance / observability work that doesn't directly support the harness.

## New IC §16 — Replayed-EV Gate for Behavioral Deploys

To be added to `docs/IMPLEMENTATION_CONTRACT.md` in this same cycle. Summary: behavioral changes (intake, classifier, blender, gates, sizing) deploy only after replayed-EV evidence shows positive expected value on the relevant feature, with operator-stated confidence threshold. Safety / observability / governance fixes are exempt (their value is mechanical, not edge-based). Stops the "deploying hope" cycle exposed in this redirect.

Cross-references:
- §11 (Change Control) — extended with replay-evidence requirement
- §1 INV-7 (Selectivity) — strengthens its enforcement
- This redirect doc — incident origin

## Operator + Codex + Claude alignment

- **Operator:** approved the redirect.
- **Codex:** authored the harness scope + endorsed the framing. Owns Cycle-12 implementation.
- **Claude:** files this doc + IC §16 amendment. Reviews Codex's deliverables. Does NOT build the replay harness (out of scope per IC §9 — Codex owns precise implementation).

## What this means for the immediate cycle

- `docs/_archive/governance/wave-2-wave-3-changelog-entries-prestaged.md` (ARCHIVED Stream G R53; was: STAYS as documentation; HALT was on EXECUTING those deploys, not on prestaged content. Archived after Cycle-17 confirmed Wave-2/3 indefinitely halted.)
- `docs/_archive/governance/wave-1-changelog-entry-prestaged.md` (ARCHIVED Stream G R49) + `docs/_archive/governance/wave-1-commit-messages-prestaged.md` (ARCHIVED Stream G R46) — both archived post-Wave-1-deploy.
- `wave-3-deploy-hunks/lever-b.patch` STAYS but is FROZEN pending replay evidence.
- `tests/test_wave2_branch_a_passive_observation.py` STAYS xfail-strict; Branch A is no longer auto-active post-Wave-1.
- Pre-Wave-1 deploy artifacts are historical references only unless explicitly reused for a future close/deploy rehearsal.

## Failure mode this redirect prevents

Without this redirect, the project's working pattern was: Cycle N → propose 10+10 tasks → execute → ship → repeat. Most tasks were "make the next deploy cleaner / more observable / more rollback-safe." The pattern doesn't terminate — there's always more deploy safety to add. But none of it answers the load-bearing question: **does the bot have edge?**

Answer to date: **no proof.** 3 trades, 0 wins. The replay harness either finds an edge or surfaces that there isn't one. Either answer redirects the project usefully. The CURRENT pattern's only termination condition is "operator runs out of patience" — which is the conversation that triggered this redirect.

## Cross-links

- `data/paper_trades.db` — 3-trade history, 0/3 wins
- `docs/governance/2026-05-06-post-obs003-skipped-attribution-audit-refresh.md` — 89% zero-edge SKIPPED finding
- `docs/IMPLEMENTATION_CONTRACT.md` §1 INV-7 — selectivity invariant (preserved)
- `docs/IMPLEMENTATION_CONTRACT.md` §11 — Change Control (will be amended)
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — to be added in same cycle
- `docs/_archive/governance/wave-1-changelog-entry-prestaged.md` — Wave-1 shipped (ARCHIVED Stream G R49)
- `docs/_archive/governance/wave-2-wave-3-changelog-entries-prestaged.md` — HALT pending replay (ARCHIVED Stream G R53)
- Future: `docs/governance/edge-replay-cycle12-report.md` — Cycle-12 deliverable
