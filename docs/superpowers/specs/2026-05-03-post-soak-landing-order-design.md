# Post-PROFIT-PHASE2-001 landing order — OBS-005 / MATCH-001 (B') / OBS-003 / EXEC-002

**Status:** design (operationalized once `PROFIT-PHASE2-001` closes; earliest start 2026-05-09 organic, hard ceiling 2026-05-16)
**Tracker:** umbrella note, no new debt-log entry; references `PROFIT-OBS-003`, `PROFIT-OBS-005`, `PROFIT-MATCH-001`, `PROFIT-EXEC-002`
**Owner:** Claude (sequencing) + Codex (per-spec adversarial review remains in scope)
**Drafted:** 2026-05-03
**Last refresh:** 2026-05-03 (post-Lever-E closure + Lever-D demotion + MATCH-001 tokenization gotcha + Lever-C empirics)

## 0. Current state header (refreshed 2026-05-03)

This spec was drafted before several empirics landed. The **base-stack landing order is unchanged** (OBS-005 → MATCH-001 → OBS-003 → EXEC-002 in Wave 1) — but downstream EDGE-004 work and per-spec implementation details have shifted. Quick map of what changed and where:

| change | impact on Wave-1 | spec section |
|---|---|---|
| **MATCH-001 (B') tokenization gotcha** (commit `cdbf6ef` + Codex audit `e5b7213`) — spec §2 set-difference vs `_tokenize` produces 0 keys; substring containment is the correct semantic. | Step 2 implementation must use option (a) substring containment; option (b) hyphen-splitting tokenizer documented as alternative in `2026-05-03-match-001-tokenization-option-b-design.md`. | Step 2 acceptance + restart trigger unchanged |
| **MATCH-001 canonical-event guard** revised from ticker-level to headline-level (commit `9e2fffa`) | Step 2 acceptance: "no canonical-event headline appears in `MATCH_SUPPRESSED` for its canonical ticker" (was ticker-level) | Already integrated |
| **Lever D demoted to outside closure path** (commit `18e0b6c`) | No Wave-1 impact; Lever D is post-Wave-1 cost-control only | downstream EDGE-004 only |
| **Lever E closed empirically** (commit `4279bd2`) | No Wave-1 impact | downstream EDGE-004 only |
| **Lever C §3.2 hash empirically validated** (Codex `ed3c57d`, spec update commit `f786246`) | No Wave-1 impact; informs Wave-3 only | downstream EDGE-004 only |

Net: **Wave-1 base stack landing-order is stable.** Per-spec implementation details have sharpened (especially MATCH-001's tokenization). Downstream EDGE-004 closure path simplified to A → B → C → D (D outside path; E closed; F out of scope).

## 1. Purpose

Four specs are pre-loaded against the soak window:

| Spec ID | File | Touches | Risk |
|---|---|---|---|
| `PROFIT-OBS-005` | `2026-05-03-obs-005-cooldown-sentinel-fix-design.md` | `trading/executor.py` (1 line × 2 sites) | LOW |
| `PROFIT-MATCH-001 (B')` | `2026-05-03-match-001-token-guard-refinement-design.md` | `analysis/market_matcher.py` (predicate) | MED |
| `PROFIT-OBS-003` | `2026-05-03-obs-003-blendtask-skipped-emission-design.md` | `tasks/blend_task.py` (additive log) | LOW-MED |
| `PROFIT-EXEC-002` | `2026-05-03-exec-002-series-correlation-guard-design.md` | `tasks/blend_task.py` + `config.py` (new gate) | HIGH |

This note is the sequencing decision so 2026-05-15 unblock is **mechanical, not analytical**: branch off main, pull this file, follow the per-step checklist.

## 2. Hard dependency graph

```
OBS-005 ────────────────────┐
MATCH-001 (B') ─────────────┤
OBS-003 ──┐                 │
          ↓                 │
       EXEC-002             ▼
                         (all four landed)
```

Only one hard dependency: **EXEC-002 depends on OBS-003**. The EXEC-002 spec's acceptance criterion §8 states *"the 2026-05-01 FISA replay test asserts exactly 1 of 3 same-series candidates enqueues; the other 2 emit `series_correlation_in_window` SKIPPED records (assumes OBS-003 SKIPPED-emission fix has landed)"*. Without OBS-003, EXEC-002's accounting closure is BLEND_DECISION-only (the existing brittle audit lever), defeating EXEC-002's own validation.

OBS-005, MATCH-001 (B'), and OBS-003 are mutually independent.

## 3. Recommended landing sequence

Lowest-risk → highest-risk, with attribution windows between each step. Total wall-clock: **~13 days from soak close to last-item closed**.

### Pre-deploy expected-state ladder (Codex simulation, 2026-05-03)

The post-soak trajectory below is the strict-counterfactual archive replay from `docs/governance/2026-05-03-post-soak-landing-simulation.md`. Each row reflects the cumulative state after the named step lands.

| state | OPP | SKIP | PAPER | trade_rate | note |
|---|---:|---:|---:|---:|---|
| baseline (current archive) | 260 | 17 | 3 | 1.2 % | observed pre-soak baseline |
| after OBS-005 | 260 | 17 | 3 | 1.2 % | sentinel fix has no archive-visible effect |
| after MATCH-001 (B') | 87 | 9 | 3 | **3.4 %** | 1,076 match keys suppressed; canonical events protected |
| after OBS-003 | 87 | 87 | 3 | 3.4 % | +78 BlendTask blocked-reason SKIPPED records (additive only) |
| after EXEC-002 | 87 | 89 | **1** | 1.1 % | 2 FISA-series trades suppressed; both were historical losses |

Two implications for acceptance criteria:

1. **MATCH-001 is the trade-rate lift.** A 67 % OPP cut combined with full-protection of canonical events triples the conversion rate (1.2 % → 3.4 %). The downstream items don't add to the rate; they add observability and risk control.
2. **EXEC-002's lower raw trade count is *good*.** All three historical FISA trades were losses; suppressing 2 of them improves realized P&L. **Optimizing for raw trade-rate at the EXEC-002 step is the wrong target — optimize for realized P&L instead.**

These numbers are sized estimates, not guarantees; the simulation cannot model below-threshold matches that were never logged, late-arriving signals not in the archive, or shifts in the market mix between archive and post-deploy. Treat them as planning anchors, not strict bounds.

### Step 1 — OBS-005 (one-line sentinel fix)

- **When:** Day 0 post-soak (the moment `PROFIT-PHASE2-001` closes ≥ 2026-05-09).
- **Why first:** smoke-test the post-soak deploy pipeline with a change that has zero observable effect on current running state (the bug is silent because `time.monotonic()` is well past 14 400 on the live process — see OBS-005 spec §5). If the deploy pipeline itself drifted during soak, this surfaces it without any decision-path risk.
- **Smoke window:** 24 h continuous paper-mode runtime. Watch for: any new exception in `bot.log`, any `OPPORTUNITY` count drop, any `SKIPPED` reason regression. None expected.
- **Restart trigger:** unhandled exception in `executor._validate()`, OR cooldown-trip storm (every market trips within first cycle post-restart — would mean the sentinel direction is inverted).
- **Rollback:** revert the 2-line diff; redeploy. Trivial.

### Step 2 — MATCH-001 (B') token-guard refinement

- **When:** Day 1 post-soak (24 h after Step 1 deploys clean).
- **Why second (not first, despite spec §9 self-ranking):** Wanting OBS-005's smoke window to clear first lets us attribute any post-MATCH-001 anomaly to the matcher edit alone. Also: MATCH-001's `MATCH_SUPPRESSED` volume jump and OBS-003's SKIPPED volume jump should not stack into a single attribution-confusing window.
- **Pre-deploy archive sizing (Codex 2026-05-03 simulation, `docs/governance/2026-05-03-post-soak-landing-simulation.md` + `2026-05-03-match001-bprime-anchor-sizing.md`):** the strict counterfactual replay over the 13-day MacBook archive suppresses **1,076 distinct match keys** under B' and reduces retained OPPORTUNITY events from **260 → 87 (-67 %)**. The historical 3 PAPER_TRADE events all survive. Net trade rate moves from 1.2 % (3/260) to **3.4 % (3/87)** — the 67 % OPP cut is the *intended* compression of low-quality candidates, not a regression. This sizing supersedes the original MATCH-001 spec §5 estimate of 600–1,300 records (which was the records *count*, presented before Codex's match-key replay).
- **Canonical-event protection is headline-level, not ticker-level (Codex 2026-05-03 anchor sizing):** Codex's MATCH-001 anchor audit (`docs/governance/2026-05-03-match001-bprime-anchor-sizing.md`) found that exact canonical-event *headlines* are suppressed 0/5 under B', but canonical-event *tickers* (e.g., `KXTRUMPIRAN-26MAY01`) legitimately host 399 *low-quality* match suppressions that *should* be cut. A ticker-level guard is therefore the wrong shape — many canonical tickers will appear in `MATCH_SUPPRESSED` for non-canonical headlines, and that's correct behavior. The acceptance check below is headline-level.
- **Pre-deploy validation:** Codex's anchor audit already confirmed 0/5 canonical-event headlines (the 5 in `scripts/simulations/_common.py:LLM_POSITIVE_EVENTS_2026_04_26`) flip into the suppressed set. Re-run `scripts/simulations/match001_bprime_anchor_sizing.py` against the live archive immediately before deploy to confirm no drift since 2026-05-03.
- **Validation window:** 72 h paper-mode runtime post-deploy.
- **Acceptance:** post-deploy 7-day OPPORTUNITY count lands in **[20 %, 50 %]** of the pre-deploy 13-day baseline (i.e., 87 ± stochastic noise → ~30 % retention; allow ±20 percentage points); **no canonical-event headline appears in `MATCH_SUPPRESSED` for its canonical ticker** (headline-level guard, not ticker-level); `MATCH_SUPPRESSED` count rises by ≥ 80 % of the simulation-estimated 1,076 keys, prorated for the validation window's elapsed time.
- **Restart trigger:** OPPORTUNITY retention drops below **15 %** of baseline (the simulation's −67 % is at the strict end; sub-15 % means the suppression is more aggressive than modelled and likely indicates a `_tokenize` mismatch); OR any of the 5 canonical-event *headlines* appears in the post-deploy `MATCH_SUPPRESSED` stream paired with its canonical ticker.
- **Rollback:** revert the predicate hunk in `analysis/market_matcher.py`; redeploy. Spec §8 documents the exact diff. Trivial.

### Step 3 — OBS-003 (BlendTask SKIPPED emission)

- **When:** Day 4 post-soak (after Step 2's 72 h MATCH-001 validation closes clean).
- **Why third:** OBS-003 is additive-only (no control-flow change), but the spec's own §10 cites the conservative reading of "decision consistency = high-risk during soak" because the file (`tasks/blend_task.py`) is decision-path. Landing OBS-003 *after* MATCH-001 means the post-OBS-003 SKIPPED stream already reflects the post-MATCH-001 OPPORTUNITY rate, so the `OPPORTUNITY = SKIPPED + PAPER_TRADE` accounting check is a clean post-fix baseline rather than a moving target.
- **Pre-deploy archive sizing (Codex simulation):** OBS-003 is additive-logging-only; on the post-MATCH-001 archive (87 retained OPP, 9 SKIP, 3 PAPER) it adds **78 BlendTask blocked-reason SKIPPED records**, lifting visible-skip share from 75 % → 96.7 %. Top reasons distribution from the simulation: `G1_blended_confidence` × 59, `G6_recency_score` × 14, `G2_evidence_source_class_diversity` × 5. The remaining 3.3 % unaccounted gap is in-flight tolerance.
- **Pre-deploy validation:** the **22 xfail-strict tests** in `tests/test_blend_task.py` (the OBS-003 harness — 9 original parametrized + 11 EXEC-002 + 2 Codex-F1/F3 follow-up; the 9 OBS-003-specific) flip to xpass automatically. Remove the `pytest.mark.xfail` markers in the same commit as the implementation. CI will refuse the merge if the markers stay.
- **Validation window:** 48 h paper-mode runtime post-deploy.
- **Acceptance:** `OPPORTUNITY = SKIPPED + PAPER_TRADE` accounting within **±3 % in-flight** (the simulation predicted 96.7 % closure on the strict counterfactual; allow 3 percentage points of slop for at-the-moment in-flight candidates); SKIPPED stream contains G1–G6 + blender-side reasons in addition to the executor's pre-existing reason set; `bothealth.sh` aggregator renders correctly with the new high-volume reasons; `G1_blended_confidence` is the dominant reason (matches the 59/78 simulation distribution).
- **Restart trigger:** SKIPPED double-emit (i.e., OPPORTUNITY > SKIPPED + PAPER_TRADE accounting drifts negative), OR `bothealth.sh` chokes on a new reason value, OR `BLEND_DECISION` count regresses (would imply the new emission is replacing rather than adding to existing logging).
- **Rollback:** spec §9 covers this — revert the `_emit_skipped` method addition + the call-site insertion. Trivial.

### Step 4 — EXEC-002 (series-correlation guard)

- **When:** Day 6 post-soak (after Step 3's 48 h OBS-003 validation closes clean).
- **Why last:** highest-risk item — the only one of the four that actually changes trade volume (suppresses real candidates that previously enqueued). Landing it after OBS-003 means its SKIPPED accounting is already in the consolidated stream from day 0, so the `series_correlation_in_window` reason audit lever exists as soon as the gate fires.
- **Pre-deploy archive sizing (Codex simulation):** the strict counterfactual replay suppresses **2 of the 3 historical paper trades** (the FISA-MAY02 + FISA-MAY03 entries inside the 1 h window after FISA-MAY01). Trade count drops from 3 → 1; SKIPPED count rises from 87 → 89; trade rate falls from 3.4 % (post-MATCH-001) → **1.1 %**. **Crucially: the 2 suppressed trades are both from the FISA series, which has a 100 % loss rate in the historical archive (3/3 paper losses).** Net realized P&L *improves* despite the lower trade count. Optimizing for raw trade rate would be the wrong success criterion.
- **Pre-deploy validation:** the 2026-05-01 FISA replay test passes (1 of 3 enqueues); cross-series + window-expiry + window-override tests all pass; `cfg.series_correlation_window_seconds=0` env override disables the guard cleanly. (All 11 xfail-strict harness invocations land in the same commit as the implementation.)
- **Validation window:** **7 d paper-mode runtime post-deploy.** Longer than the others because trade-rate impact is the primary signal and trade-rate is sparse — fewer than 1 trade per day historically, so a shorter window has insufficient statistical basis.
- **Acceptance:** 24 h post-deploy SQL audit returns zero rows from `SELECT series_ticker, COUNT(*) FROM paper_trades WHERE ts >= datetime('now', '-1 day') GROUP BY series_ticker HAVING COUNT(*) > 1 AND (MAX(ts) - MIN(ts)) < <window>;` (per EXEC-002 §8); 7 d realized-P&L comparison vs the 13-day MacBook baseline is **flat or improved** (the simulation says the suppressed 2 FISA trades would have been losses; the operator should verify on actual post-deploy data); `series_correlation_in_window` SKIPPED count is non-zero only on cycles where a same-series candidate fires within the window.
- **Restart trigger:** trade volume drops to zero across the 7 d window (`_series_prefix` extraction too aggressive — grouping unrelated tickers), OR `series_correlation_in_window` SKIPPED records fire for tickers that share no actual series prefix, OR realized P&L is *worse* than the matched-window pre-deploy baseline (would mean the gate is suppressing wins, not losses — opposite of the simulation prediction).
- **Rollback:** primary fast revert is operator-side — `SERIES_CORRELATION_WINDOW_SECONDS=0` in env + restart. Disables the guard with zero code change. Code revert is the BlendTask + config diff per EXEC-002 §9.

## 4. Combined timeline

| Day | Event | State |
|---|---|---|
| 0 | OBS-005 deploy | All bots: paper, MacBook idle |
| 0–1 | OBS-005 24 h smoke window | live |
| 1 | MATCH-001 (B') deploy | — |
| 1–4 | MATCH-001 72 h validation | live |
| 4 | OBS-003 deploy | — |
| 4–6 | OBS-003 48 h validation | live |
| 6 | EXEC-002 deploy | — |
| 6–13 | EXEC-002 7 d validation | live |
| 13 | All four items closed in `profit_path_debt_log.md` | post-soak baseline complete |

If `PROFIT-PHASE2-001` closes 2026-05-09 (organic), this puts full closure at 2026-05-22. If it stretches to 2026-05-16 (hard ceiling), full closure at 2026-05-29.

## 5. Per-step VERSION / CHANGELOG / debt-log discipline

Each step is its own commit + version bump:

- Stage `VERSION` first; pre-commit hook auto-syncs README per project CLAUDE.md.
- `CHANGELOG.md` entry in same commit, citing the spec ID and acceptance evidence.
- `docs/profit_path_debt_log.md` status flip OPEN → COMPLETE in same commit; update top-of-file counters (`Open HIGH n → n−1`, `Items COMPLETE += 1`).
- Tag the commit `vX.Y.Z` per project rules (solo project, but tags are the rollback anchor).

Cross-reference closure between items where applicable:

- OBS-003's closure note should mention that EXEC-002's accounting cleanup now has a clean lever.
- EXEC-002's closure note should reference OBS-003's SKIPPED stream as the audit basis for the `series_correlation_in_window` reason.
- MATCH-001's closure should reference whether `PROFIT-EDGE-004`'s no-edge investigation can be closed against the post-fix data (likely partial closure, not full — EDGE-004 has multiple levers).

## 6. Risk-aware deviation rules

The sequence is the recommendation; deviation is allowed only on the following triggers:

- **Step 2 (MATCH-001) pre-deploy regression-anchor check fails.** Skip MATCH-001; proceed Step 3 → Step 4 with MATCH-001 deferred. File a follow-up entry for MATCH-001 root cause.
- **Step 3 (OBS-003) post-deploy `bothealth.sh` choke.** Roll back OBS-003 immediately; do NOT proceed to Step 4 (Step 4's accounting depends on OBS-003 working). Fix `bothealth.sh` first, re-land OBS-003, then proceed.
- **Operator decides to disable EXEC-002 via env-var rather than land it as code.** Acceptable, but file a debt-log note that EXEC-002 is "deferred via env-disable, not landed". Future operator return must re-evaluate.

No other deviation. In particular: do **not** parallelize OBS-005 + MATCH-001 + OBS-003 into a single deploy. The validation windows are non-fungible — each step's audit lever proves something different.

## 7. Out of scope

- **Codex's adversarial spec reviews** — those happen *before* landing each item, in parallel. Outside this sequence note's scope; tracked separately in the work-stream that produced this note.
- **`PROFIT-EDGE-004` matcher-quality work** — not in this set. Codex's archive-replay matcher Jaccard sweep (separate task) sizes it; landing decision is post-MATCH-001-validation.
- **Phase 2 governance Real-mode flip (`GOV.P3`)** — depends on PHASE2-001 close + 2-week real-mode soak + Phase 2 retro. Separate sequence.
- **`PROFIT-LLM-001`** signal-analyzer LLM unification — gated behind GOV.P4. Out of scope.
- **VERSION bump strategy** — solo project; semver applies; each item is at minimum a patch bump, MATCH-001 + EXEC-002 may warrant minor bumps if behavioral surface changes are notable.

## 8. Soak-window contract

This sequence note itself is documentation, not code, and lands during the active `PROFIT-PHASE2-001` soak. Documentation commits during soak are explicitly allowed (the recent commit history shows 4 such commits for the underlying specs). This commit follows the same convention.

The note becomes operationally active the day the soak closes. Until then it is design + reference.
