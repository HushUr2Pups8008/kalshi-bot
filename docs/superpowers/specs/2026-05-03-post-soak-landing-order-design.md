# Post-PROFIT-PHASE2-001 landing order — OBS-005 / MATCH-001 (B') / OBS-003 / EXEC-002

**Status:** design (operationalized once `PROFIT-PHASE2-001` closes; earliest start 2026-05-09 organic, hard ceiling 2026-05-16)
**Tracker:** umbrella note, no new debt-log entry; references `PROFIT-OBS-003`, `PROFIT-OBS-005`, `PROFIT-MATCH-001`, `PROFIT-EXEC-002`
**Owner:** Claude (sequencing) + Codex (per-spec adversarial review remains in scope)
**Drafted:** 2026-05-03

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

### Step 1 — OBS-005 (one-line sentinel fix)

- **When:** Day 0 post-soak (the moment `PROFIT-PHASE2-001` closes ≥ 2026-05-09).
- **Why first:** smoke-test the post-soak deploy pipeline with a change that has zero observable effect on current running state (the bug is silent because `time.monotonic()` is well past 14 400 on the live process — see OBS-005 spec §5). If the deploy pipeline itself drifted during soak, this surfaces it without any decision-path risk.
- **Smoke window:** 24 h continuous paper-mode runtime. Watch for: any new exception in `bot.log`, any `OPPORTUNITY` count drop, any `SKIPPED` reason regression. None expected.
- **Restart trigger:** unhandled exception in `executor._validate()`, OR cooldown-trip storm (every market trips within first cycle post-restart — would mean the sentinel direction is inverted).
- **Rollback:** revert the 2-line diff; redeploy. Trivial.

### Step 2 — MATCH-001 (B') token-guard refinement

- **When:** Day 1 post-soak (24 h after Step 1 deploys clean).
- **Why second (not first, despite spec §9 self-ranking):** MATCH-001's archive-replay impact is large (~600–1,300 records flip survived → suppressed per spec §5). Wanting OBS-005's smoke window to clear first lets us attribute any post-MATCH-001 anomaly to the matcher edit alone. Also: MATCH-001's `MATCH_SUPPRESSED` volume jump and OBS-003's SKIPPED volume jump should not stack into a single attribution-confusing window.
- **Pre-deploy validation:** re-run `scripts/simulations/match_score_audit.py` against the 13-day MacBook archive — confirm zero canonical-event tickers in the survived → suppressed flip set. (This is the 5-event regression-anchor check. Codex's MATCH-001 adversarial review should pre-cover this.)
- **Validation window:** 72 h paper-mode runtime post-deploy.
- **Acceptance:** post-deploy 24 h `MATCH_SUPPRESSED` count rises proportionally to the pre-deploy archive estimate (within ±20 %); no canonical-event ticker appears in `MATCH_SUPPRESSED`; OPPORTUNITY rate stays within the 13-day pre-deploy band.
- **Restart trigger:** OPPORTUNITY rate drops > 50 % vs pre-deploy 13-day baseline (matcher regression), OR any of the 5 canonical-event tickers appears in the post-deploy `MATCH_SUPPRESSED` stream within the validation window.
- **Rollback:** revert the predicate hunk in `analysis/market_matcher.py`; redeploy. Spec §8 documents the exact diff. Trivial.

### Step 3 — OBS-003 (BlendTask SKIPPED emission)

- **When:** Day 4 post-soak (after Step 2's 72 h MATCH-001 validation closes clean).
- **Why third:** OBS-003 is additive-only (no control-flow change), but the spec's own §10 cites the conservative reading of "decision consistency = high-risk during soak" because the file (`tasks/blend_task.py`) is decision-path. Landing OBS-003 *after* MATCH-001 means the post-OBS-003 SKIPPED stream already reflects the post-MATCH-001 OPPORTUNITY rate, so the 24 h `OPPORTUNITY = SKIPPED + PAPER_TRADE` accounting check (OBS-003 §8) is a clean post-fix baseline rather than a moving target.
- **Pre-deploy validation:** the 9 xfail-strict tests in `tests/test_blend_task.py` (the OBS-003 harness, landed pre-soak per task #2 of this work-stream) flip to xpass automatically. Remove the `pytest.mark.xfail` markers in the same commit as the implementation. CI will refuse the merge if the markers stay.
- **Validation window:** 48 h paper-mode runtime post-deploy.
- **Acceptance:** `OPPORTUNITY = SKIPPED + PAPER_TRADE` accounting within ±5 in-flight; SKIPPED stream contains G1–G6 + blender-side reasons in addition to the executor's pre-existing reason set; `bothealth.sh` aggregator renders correctly with the new high-volume reasons.
- **Restart trigger:** SKIPPED double-emit (i.e., OPPORTUNITY > SKIPPED + PAPER_TRADE accounting drifts negative), OR `bothealth.sh` chokes on a new reason value, OR `BLEND_DECISION` count regresses (would imply the new emission is replacing rather than adding to existing logging).
- **Rollback:** spec §9 covers this — revert the `_emit_skipped` method addition + the call-site insertion. Trivial.

### Step 4 — EXEC-002 (series-correlation guard)

- **When:** Day 6 post-soak (after Step 3's 48 h OBS-003 validation closes clean).
- **Why last:** highest-risk item — the only one of the four that actually changes trade volume (suppresses real candidates that previously enqueued). Landing it after OBS-003 means its SKIPPED accounting is already in the consolidated stream from day 0, so the `series_correlation_in_window` reason audit lever exists as soon as the gate fires.
- **Pre-deploy validation:** the 2026-05-01 FISA replay test passes (1 of 3 enqueues); cross-series + window-expiry + window-override tests all pass; `cfg.series_correlation_window_seconds=0` env override disables the guard cleanly.
- **Validation window:** **7 d paper-mode runtime post-deploy.** Longer than the others because trade-rate impact is the primary signal and trade-rate is sparse — fewer than 1 trade per day historically, so a shorter window has insufficient statistical basis.
- **Acceptance:** 24 h post-deploy SQL audit returns zero rows from `SELECT series_ticker, COUNT(*) FROM paper_trades WHERE ts >= datetime('now', '-1 day') GROUP BY series_ticker HAVING COUNT(*) > 1 AND (MAX(ts) - MIN(ts)) < <window>;` (per EXEC-002 §8); 7 d trade-volume comparison vs the 13-day MacBook baseline shows a measurable but bounded reduction (expected: −1 to −3 trades on the FISA replay path; net trade volume should not drop > 50 %).
- **Restart trigger:** trade volume drops to zero across the 7 d window (`_series_prefix` extraction too aggressive — grouping unrelated tickers), OR `series_correlation_in_window` SKIPPED records fire for tickers that share no actual series prefix.
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
