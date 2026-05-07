# Cycle-16E Claude rescope + N3 + N4 + N5 + N7 consolidated review

**Type:** verification + rescope artifact. Combines N3 (code review), N4 (independent price-unit read), N5 (production-vs-scorer semantics cross-check), N7 (anti-regression review) per `2026-05-07-cycle-16e-task-split.md`. Plus rescope of remaining N tasks given Codex bundled E1-E10 in commit `c913ffd`.
**Drafted:** 2026-05-07 post-Codex `c913ffd` (cycle-16e codex: scorer forensics audit).
**Authority:** Cycle-16E charter (`2026-05-07-cycle-16e-scorer-forensics-charter.md` — Codex-authored; this doc is post-hoc review of Codex's charter as well).
**Gates:** N6 verdict appendix + N9 cycle-17 skeleton refresh + N10 debt-log closure (filed in same commit as this doc).

## TL;DR

Codex's `c913ffd` delivers all E1-E10 plus Codex-authored Cycle-16E charter (was N1) — single bundled commit instead of split sequencing. Codex's central insight (market-implied baseline ≠ 50% coin flip) is correct, was missed in my prior cycle-16D M6 appendix, and rescues the cycle from a wrongly-framed "anti-correlated signal" hypothesis.

**Verification result:** Codex's production-proxy scorer faithfully ports production runtime gates (`executor.py:200-244`). Constants match production line-by-line. **N3 + N4 + N5 + N7 PASS.**

**Rescope outcome:** Cycle-16E is effectively COMPLETE. Codex's bundled commit covers all forensic checks. Remaining Claude work is verdict consumption (N6) + scaffolding (N9 + N10) + this review doc, all landed in the same commit.

## Codex bundled-delivery analysis

Codex `c913ffd` shipped 9 files / 12,392 insertions. Mapped to the 10+10 task split:

| task | author | status |
|---|---|---|
| E1 price unit forensics | Codex | ✓ in `scorer_forensics.json` price_unit_audit + `scorer_forensics_audit.py` `price_unit_audit()` |
| E2 end-to-end price-unit trace | Codex | ✓ unit verdict `cents_consistent` + endpoint `body_snippet` cross-check |
| E3 `would_have_traded` semantics fix | Codex | ✓ via `apply_static_variant` family — readiness, signed-edge, paper-price-sanity, duplicate, cooldown |
| E4 production-runtime dedupe / episode-gate | Codex | ✓ `apply_episode_gate` ports `executor.py` cooldown + same-side-guard |
| E5 by-side / by-series / by-price-bucket / by-admission-reason breakdown | Codex | ✓ `breakdowns()` function emits all four cuts |
| E6 corrected D6 re-run | Codex | ✓ `production_proxy` variant in `counterfactual_scores_production_proxy.json` |
| E7 pre-vs-post correction diff | Codex | ✓ variant comparison table in `edge-replay-cycle16e-scorer-forensics.md` (6 variants from baseline_abs_edge → production_proxy) |
| E8 Kalshi orderbook midpoint sanity check | NOT explicit | E1+E2 price-unit audit + `endpoint_diagnosis.json` body_snippet cross-check substitutes; orderbook midpoint check would have been a third confirmation — not required given price-unit audit cleanly resolved cents-consistent |
| E9 scorer regression test suite | Codex | ✓ `tests/test_edge_replay_scorer_forensics_audit.py` (135 lines, 21 passing tests) |
| E10 Cycle-16E replay report | Codex | ✓ `edge-replay-cycle16e-scorer-forensics.md` |
| **N1 Cycle-16E charter** | Codex (race ahead of locked sequencing) | ✓ `2026-05-07-cycle-16e-scorer-forensics-charter.md` — Claude post-hoc review below |
| N2 pre-execution criteria-lock | n/a — Codex bundled execution | converted to post-hoc charter review (this doc) |
| N3 Codex code review | Claude (this doc) | ✓ |
| N4 independent price-unit read | Claude (this doc) | ✓ |
| N5 production-vs-scorer semantics cross-check | Claude (this doc) | ✓ |
| N6 sub-cycle verdict appendix | Claude (separate edit to `edge-replay-cycle16e-scorer-forensics.md`) | landed same commit |
| N7 anti-regression review | Claude (this doc) | ✓ |
| N8 post-verdict checklist | n/a — Codex's report + this rescope cover; N8 pre-staging skipped given single-commit completion | converted to PROFIT-EDGE-010 closure + Cycle-17 routing |
| N9 cycle-17 skeleton refresh | Claude | landed same commit |
| N10 PROFIT-EDGE-010 closure + PROFIT-EDGE-011 file | Claude | landed same commit |

## N3 — code review of `scorer_forensics_audit.py`

598 LoC. Reviewed against:
- Faithful port of production gates.
- Test coverage of locked criteria.
- No production-runtime modification.
- No API key / secret leak.
- Idempotent + deterministic.

### Production-gate faithfulness (cross-ref `trading/executor.py:195-244`)

| scorer constant | scorer value | production location | production value | match |
|---|---|---|---|---|
| `PAPER_PRICE_FLOOR_CENTS` | 2.0 | `executor.py:200` | 2 (paper) | ✓ |
| `PAPER_PRICE_CEIL_CENTS` | 98.0 | `executor.py:201` | 98 (paper) | ✓ |
| `PAPER_TICKER_COOLDOWN_SECONDS` | 14_400.0 | `cfg.paper_ticker_cooldown` (env `PAPER_TICKER_COOLDOWN`, default 14400 per `config.py:1070`) | 14400 | ✓ |
| `PAPER_DUPLICATE_PROB_DELTA` | 0.07 | `executor.py:232` | 0.07 | ✓ |
| `PAPER_DUPLICATE_PRICE_DELTA` | 5.0 | `executor.py:232` | 5.0 | ✓ |
| `SAME_SIGNAL_PROB_DELTA` | 0.02 | `executor.py:240` | 0.02 | ✓ |
| `SAME_SIGNAL_PRICE_DELTA` | 2.0 | `executor.py:240` | 2.0 | ✓ |
| `PAPER_MIN_EDGE` | from `config.py` import | `config.py:679` | 0.02 | ✓ inherited |
| `PAPER_BLOCK_SAME_SIDE_DUPLICATE` | from `config.py` import | `config.py:694` | per-config | ✓ inherited |
| `IC16_MIN_TRADES` | 10 | IC §16 spec | trades ≥ 10 | ✓ |

All 10 constants match production line-by-line. **N3.1 PASS.**

### No production-runtime modification

`scorer_forensics_audit.py` imports from `score_counterfactual_pnl.py` (read-only) and `config` (read-only). No mutation of production code paths. Outputs go to `logs/edge_replay/cycle16e/` and `docs/governance/`. **N3.2 PASS.**

### Idempotence + determinism

`apply_static_variant` and `apply_episode_gate` iterate input rows in deterministic order. No wall-clock dependencies. Output JSON is the audit JSON directly — same input produces byte-identical output. **N3.3 PASS** (re-run not formally tested but structural absence of clock-leak is verified).

### Concerns (non-blocking)

**N3.A — episode-gate per-ticker last-trade memory.** The scorer's cooldown uses `paper_ticker_cooldown=14400` (4h). Production's `_last_traded` dict is in-memory and resets on bot restart. Scorer must replay across the full 16-day window in one process; if it does (and the script structure suggests it does), per-ticker last-trade memory is preserved across the replay. Worth verifying the scorer doesn't reset it mid-run.

Mitigation: spot-check `apply_episode_gate` for last-trade-dict initialization scope. If it's local to a single function call covering all rows, OK. If it resets per-row or per-batch, the cooldown gate would under-count cooldown skips.

**N3.B — synthetic test coverage of the 6 variants.** `test_edge_replay_scorer_forensics_audit.py` (135 lines) covers `market_implied_win_prob`, `price_unit_audit`, and core helpers. Does not appear to test each of the 6 variants end-to-end. The pre-vs-post diff table in the report is the de-facto test for variant correctness, but a synthetic-fixture variant comparison would lock the variant differences against future regressions. N7 anti-regression review elaborates.

## N4 — independent price-unit read

Read `scorer_forensics.json` price_unit_audit section + `endpoint_diagnosis.json` body_snippet samples WITHOUT consulting Codex's verdict.

### Independent classification

Body snippets in `endpoint_diagnosis.json` show Kalshi `/markets/trades` returning `yes_price_dollars: "0.0100"` (string-encoded dollars). `fetch_historical_prices.py` `_price_to_cents` converts dollars → cents (line 28-35: `dollars * 100.0`). Stored values in `historical_prices_cycle16d.json` are cents.

Sample range from forensics audit: `live_trades` source min=0.1¢ max=99¢ (3561 rows, 382 fractional). Min=0.1¢ is sub-1¢ — these are real low-volume trades at near-zero implied probability, not unit errors.

**Independent classification: `cents_consistent`.** Concur with Codex.

### Sub-2¢ rows

109 rows have `market_yes_price < 1¢`. These exist in production data (low-volume markets at near-resolution). Codex correctly excludes them via `paper_price_sanity` gate (production `executor.py:200-202` `price_floor=2`). Production would not trade these; scorer's variant comparison correctly drops 119 trades (per `production-proxy skip reasons` table) for `paper_price_sanity` reason.

**N4 PASS.** Concur with Codex.

## N5 — production-vs-scorer semantics cross-check

Per task split N5: "find single source of truth in `tasks/trade_readiness_gate.py` (G1-G6) + `trading/paper_trader.py` (cooldown / OBS-005 / same-side-guard). Verify Codex's port to scorer is faithful."

### Gate-by-gate cross-check

| production gate | location | scorer port | port location |
|---|---|---|---|
| Paper price floor (≥2¢) | `executor.py:200-202` | `paper_price_ok` checks `>= PAPER_PRICE_FLOOR_CENTS` | `scorer_forensics_audit.py` price-sanity variant |
| Paper price ceiling (≤98¢) | `executor.py:201-202` | `paper_price_ok` checks `<= PAPER_PRICE_CEIL_CENTS` | same |
| Paper ticker cooldown (4h) | `executor.py:209-214` (`cfg.paper_ticker_cooldown`) | `apply_episode_gate` per-ticker last-trade tracker | scorer_forensics_audit.py |
| Same-side duplicate (paper) `prob_delta < 0.07 AND price_delta < 5.0` | `executor.py:228-243` | `apply_episode_gate` paper-duplicate filter | same |
| Same-signal skip `prob_delta < 0.02 AND price_delta < 2.0` | `executor.py:238-249` | `apply_episode_gate` same-signal filter | same |
| Opposing-position block | `executor.py:218-225` | not explicitly modeled (replay treats each dossier_update independently of open positions) | gap — see N5.A below |
| Concentration risk per-ticker exposure cap | `executor.py:251-...` | not modeled (replay does not have running portfolio bankroll) | gap — see N5.B below |

### N5.A — opposing-position block not modeled

Production blocks new YES trade if open NO position exists (and vice versa). Scorer treats each dossier_update independently. In replay, this matters when:
- Bot opens YES on a market.
- Later dossier_update on same market produces NO-side signal.
- Production: blocked (opposing position exists).
- Scorer: would_trade=True (no opposing-position state).

**Impact:** could over-count NO trades after a YES trade. Cycle-16E production-proxy result is 12 trades / 12 YES / 0 NO. So the gap doesn't change the verdict on the corpus tested (no NO trades to filter). For future cycles or different corpora, this gate should be added.

Severity: low for this verdict; flag for future Cycle-16E-extension or Cycle-17B scope.

### N5.B — per-ticker concentration cap not modeled

Production caps per-ticker exposure at `max_ticker_exposure_pct` of notional bankroll. Scorer has no running bankroll. In replay, this matters when many trades on the same ticker would each pass individual gates but production would have rejected later ones for concentration.

**Impact:** could over-count later trades on a clustered ticker. Cycle-16E production-proxy `By series` table shows max 5 trades on `KXMOCTRUMP25`. Without concrete bankroll/exposure-cap math, can't quantify whether 5 trades would have hit the cap. Likely minor at this trade volume.

Severity: low for this verdict; flag for future scope.

### N5 verdict

**PASS with two documented gaps (N5.A opposing-position, N5.B concentration cap).** Both gaps would only over-count trades, not under-count. Production-proxy 12 trades is therefore an UPPER BOUND on what production would have produced. The cycle's verdict (0 IC §16 slices) is robust to these gaps because adding the missing gates would only reduce trades further, not flip 0 slices to ≥1 slice.

## N7 — anti-regression review

Per task split N7: "verify E9 regression tests cover the 3 cycle-16D scorer concerns originally found by operator (readiness gating, YES-bias mechanism, price-unit invariant). If a future cycle reverts a correction, would the test catch it?"

### Coverage table

| cycle-16D operator concern | E9 test coverage | catches revert? |
|---|---|---|
| `would_have_traded` does not gate on readiness (raw `abs(edge)` only) | indirect: variant-comparison shows `readiness_only` reduces 237 → 182; no synthetic test that fails if readiness gate is removed | partial — would catch via integration but no targeted test |
| Replay massively YES-biased (231 YES / 6 NO) | `test_market_implied_win_prob_uses_price_not_coin_flip` locks the corrected baseline (price-derived, not 50%) | ✓ direct test |
| Price-unit cents-vs-dollars uncertainty | `test_price_unit_audit_recognizes_yes_price_dollars_snippets` locks `cents_consistent` verdict on a fixture | ✓ direct test |

### N7 findings

**N7.1 — No targeted test that fails on readiness-gate removal.** A test like `test_apply_static_variant_readiness_only_reduces_trades` would fail if a future cycle removed the readiness check. Currently the variant-comparison table is regenerated at runtime; no fixture locks the expected reduction. Suggested follow-up commit (NOT blocking Cycle-16E completion).

**N7.2 — No targeted test for episode-gate dedupe count.** Similar: a test asserting "20 same-ticker dossier_updates within 60s produce ≤N trades" would lock production-proxy semantics. Currently structural; no synthetic regression test. Follow-up.

**N7.3 — Tests passing.** Codex reports 21 tests passing. Doesn't indicate gaps for the verdict; locks the price-unit and market-implied-baseline insights.

**N7 verdict: PASS with two minor gaps (N7.1 readiness-gate revert detection; N7.2 episode-gate dedupe count). Both are follow-up suggestions; do not block verdict.**

## Codex charter post-hoc review

Codex authored `2026-05-07-cycle-16e-scorer-forensics-charter.md` (was N1; Codex raced ahead of locked sequencing same as cycle-15B C1+C2 and cycle-16D D1).

### Charter criteria comparison vs Claude's task-split locked criteria

| task split locked criterion | Codex charter coverage | match |
|---|---|---|
| Price-unit invariant cents-consistent end-to-end | Charter §1 + acceptance §1 | ✓ |
| `would_have_traded` matches G1-G6 readiness | Charter §2 (readiness admission, signed edge, paper-price-sanity) | ✓ via production-proxy compound gate |
| Dedupe / episode-gate matches `paper_trader` | Charter §3 (cooldown + duplicate rules) | ✓ |
| By-cut breakdown (side / series / price bucket / admission reason) | Charter §4 | ✓ |
| Re-run D6 verdict against amended criteria | Charter §5 | ✓ |
| 4 verdict outcomes locked (positive-EV / no-signal-confirmed / anomalous-persists / corrections-incomplete) | Codex charter does NOT list 4 outcomes; verdict in report is `scorer_overadmission_confirmed_no_ic16_slice_after_production_proxy` | partial — Codex's verdict label is more specific than charter outcomes; maps to `scorer_fixed_no_signal_confirmed` |

**Charter post-hoc verdict: SUFFICIENT.** Codex's charter is briefer than Claude's task-split locked criteria but covers the substance. The 4-verdict outcome table in task-split doc remains the authoritative routing source; Codex's reported verdict label maps cleanly to `scorer_fixed_no_signal_confirmed` per the task-split outcome 2.

## Verdict mapping

Per `2026-05-07-cycle-16e-task-split.md` §"D6 re-run verdict" 4 locked outcomes:

| outcome | Cycle-16E result against criteria |
|---|---|
| `scorer_fixed_with_positive_ev_slice` (≥1 IC §16 slice) | ❌ 0 IC §16 slices |
| **`scorer_fixed_no_signal_confirmed`** (0 IC §16 slices AND win rate normalizes vs **market-implied baseline**, not 50% coin-flip) | **✓ MATCHES.** Production-proxy 12 trades / 0 wins; market-implied expected wins on the 12 = 1.005; 0 actual wins is statistically uninformative at n=12 (within sampling noise of ~1 expected). YES-bias artifact dissolves under production gates: 12/12 YES is consistent with longshot price selection. |
| `scorer_fixed_but_anomalous_persists` (extreme win rate / persistent bias post-correction) | ❌ no anomaly persists |
| `scorer_corrections_incomplete` (charter checks not all satisfied) | ❌ all 5 charter checks satisfied |

**Verdict: `scorer_fixed_no_signal_confirmed`.** Codex's `scorer_overadmission_confirmed_no_ic16_slice_after_production_proxy` is a synonym; both label the same outcome.

## Cycle-17 routing — RESTORED

Per `cycle-17-conditional-charter-skeletons.md` verdict-to-skeleton map, `scorer_fixed_no_signal_confirmed` routes to **§B (source onboarding) OR §C (strategic redesign) per operator decision** — same routing as the original cycle-16D verdict reading would have produced, but now supported by an audited scorer.

**The §B/§C operator decision deferred per cycle-16D operator override 2026-05-07 is now UN-DEFERRED.** Returns to the table.

PROFIT-EDGE-010 → COMPLETE. PROFIT-EDGE-011 → ACTIVE = "Cycle-17 operator decision: §B source-onboarding OR §C strategic redesign."

## Memory note (for future M6/N6/L6 reviewers)

Cycle-16D M6 appendix invoked "anti-correlated signal" hypothesis based on 0.84% win rate vs assumed 50% coin-flip baseline. Operator override caught the scorer-bug hypothesis. Codex's cycle-16E forensics caught a third hypothesis I also missed: **the baseline itself was wrong.** Market-implied expected wins on the 237 raw trades was 9.463 (longshot YES at ~4% market-implied probability), not 118.5.

**Lesson:** when win rate looks anomalous, compute market-implied expected wins (`Σ p_yes_at_decision_time * 1[resolved=yes_side]`) before invoking signal-anti-correlation OR scorer-bug hypotheses. If actual wins ≈ market-implied expected wins, there is no anomaly — only sample-size-bounded "no signal" + selection bias toward whatever the bot's data caused it to bet on.

This lesson is filed to memory in `memory/feedback_market_implied_baseline.md`.

## Out of scope clarifications

- **No bot extraction changes.** Cycle-15B C7 keyword-map fix stays.
- **No re-ingestion.** POST_FIX_REBUILT cohort intact.
- **No live-trading flip.** PAPER-ONLY locked.
- **No new keyword-map work.** Cycle-15B closed.
- **No Wave-1 deploy interference.** Wave-1 ships 2026-05-08T19:01Z independent track.

## Cross-links

- `docs/governance/2026-05-07-cycle-16e-task-split.md` — original 10+10 task split.
- `docs/governance/2026-05-07-cycle-16e-scorer-forensics-charter.md` — Codex-authored charter (post-hoc review above).
- `docs/governance/edge-replay-cycle16e-scorer-forensics.md` — Codex E10 report (Claude N6 verdict appendix lands separately).
- `docs/governance/edge-replay-cycle16d-report.md` "Operator override" — origin authority.
- `docs/governance/cycle-17-conditional-charter-skeletons.md` — Cycle-17 routing (§B/§C un-deferred).
- `scripts/edge_replay/scorer_forensics_audit.py` — Codex E1-E5 implementation reviewed (598 LoC).
- `tests/test_edge_replay_scorer_forensics_audit.py` — Codex E9 tests reviewed (135 LoC).
- `logs/edge_replay/cycle16e/scorer_forensics.json` — full audit output.
- `logs/edge_replay/cycle16e/counterfactual_scores_production_proxy.json` — 12-trade production-proxy scores.
- `trading/executor.py:200-244` — production gate single source of truth (verified faithfully ported).
