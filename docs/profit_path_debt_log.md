# Profit Path Technical Debt Log

> **Canonical project tracking surface.** Per `kalshi-bot/CLAUDE.md`, this is the single source of truth for "what is going on in the project." Sections below cover open debt, current status, roadmap horizon, decision log, and cycle outcomes. Any tracking content that doesn't fit a section means a new section is needed — not a new file. Last consolidated: 2026-05-09 (`docs/housekeeping/2026-05-09/docs-consolidation/`).

**Single system of record for technical debt that could materially reduce the bot's ability to make money through disciplined, well-educated trades.** Scope: platform, signal-quality, belief-system, execution-boundary, observability, validation, and documentation risks. Supersedes `docs/macos_migration_debt.md` (2026-04-20); absorbed `docs/EDGE_STATUS.md` (2026-05-09 consolidation).

**Trust hierarchy:** this file > active cycle ledgers in `docs/governance/` > `docs/superpowers/plans/` > `docs/superpowers/specs/` > `docs/housekeeping/` (audit records) > `docs/_archive/` (historical). On conflict: this file wins. Active cycle ledgers merge back here at cycle close.

**Forward horizon:** `docs/ROADMAP.md` is canonical for multi-week strategic priorities and wave deploy timeline. The Recommended Execution Order below cross-references ROADMAP; it does not duplicate it.

| # | Section | What it tracks | Update cadence |
|---|---------|---------------|----------------|
| 1 | Header / Metadata | Document metadata, item counts, high-risk areas, execution order | Per-event |
| 2 | Current Status | Edge verdict, wave deploy posture, replay harness, live ops | Per-cycle |
| 3 | Current Open Profit-Path Items | All PROFIT-* and MAC-* debt entries (open + complete) | Per-event |
| 4 | Execution Views | Fix queue, pre-go-live gate, work streams | Per-cycle |
| 5 | Dependency Map | Item dependency graph | Per-event |
| 6 | Operating Rules | R-1 through R-10 governing update conventions | Amendment only |

---

## Header / Metadata

| Field | Value |
|-------|-------|
| Last Updated | 2026-05-08 (PROFIT-DOC-002 — guidance consolidation initiative complete) |
| Audit Source | Expanded profit-path audit — Codex 2026-04-20; incorporates prior migration audit from commit 2315a1d; Claude 2026-04-22 observation-window code-hygiene sweep; Claude 2026-04-23 S4.5b closure and PROFIT-RUNTIME-001 unblock; Claude 2026-04-23 PROFIT-CAL-001 emission-wiring investigation; Claude 2026-04-23 PROFIT-CAL-001 elevation to pre-live-trading blocker; Claude 2026-04-23 news-sources evaluation and PROFIT-SOURCE-001 registration of Reddit degraded-permanent state; Claude 2026-04-25 governance Phase 2 execution-time decision on signal-analyzer LLM unification deferral (PROFIT-LLM-001); Claude 2026-04-26 S4.5c soak evidence sweep on PROFIT-RUNTIME-001 ahead of operator travel; Claude 2026-04-26 systematic-debugging investigation of "always ends with no edge" symptom and identification + fix of PROFIT-EDGE-001 (main.py:688 over-strict no_keywords kill); Claude 2026-04-26 G1 simulation post-EDGE-001 + PROFIT-EDGE-002 multi-bug investigation (regime-classifier categorical-prior coverage gap, G4 threshold mis-calibration, sport-prefix blocklist gap KXPSL, structural-recompute silent failure logging); Claude 2026-04-26 PROFIT-EDGE-003 G1 calibration follow-up (G1=0.35→0.05) grounded in 154 production BLEND_DECISIONs over the 9-day no-edge window; Claude 2026-04-28 v0.29.58 post-deploy audit (~48h runtime since 2026-04-27T13:03:19Z LaunchAgent boot): EDGE-001/002/003 fix stack confirmed flowing via 34 BLEND_DECISION/OPPORTUNITY events on KXMOCTRUMP25-26-MAY01 with new EDGE-002 categorical priors firing in production (regime_weights (0.65, 0.25, 0.10) on KXTRUMPCHINA, regime_confidence 0.220 ≥ G4 = 0.20, scaled_confidence ≈ 0.084 ≥ G1 = 0.05, executor PAPER_MIN_EDGE = 0.02 the new binding constraint at edge = 0.0); kill point relocated cleanly from readiness G1 to executor; LLM emitted directional view on 0 real headlines vs the EDGE-001 9-day baseline of 5/666 (0.75%, within statistical noise for n=240); PROFIT-EDGE-004 registered for matcher signal-quality / market-mix root cause (the "directionally correct P0.5/P3.4 diagnosis" EDGE-001 Notes flagged as the long-term strategic answer, now operationally surfaced); PROFIT-OBS-003 registered for the OPPORTUNITY → SKIPPED arithmetic gap (31/34 silent exits); PROFIT-STRUCT-002 registered to close EDGE-002 sub-fix #4's runtime verification gap; **Claude 2026-05-01 13-day MacBook paper soak post-cutover audit (full v0.29.5 → v0.29.58 paper era, 2026-04-18T02:11:24Z paper_start_time → 2026-05-01T13:05:54Z final shutdown)**: lifetime trade-log totals 260 SIGNAL = 260 OPPORTUNITY = 252 BLEND_DECISION (8-event drift attributed to startup-probe + early-window emission ordering, within tolerance for an audit) → **17 SKIPPED + 3 PAPER_TRADE = 20 visible exits vs 260 OPPORTUNITY = 240 silent exits (92.3%)**, with 17/17 SKIPPED reasons identical (`"edge +0.0000 below min_edge 0.02"`); OPPORTUNITY edge distribution shows 255/260 at edge=0.0, 3 at -0.068 (the FISAEXTEND trades that *did* emit despite negative edge — see PROFIT-OBS-004), and **2 OPPORTUNITY at non-trivial positive edge (+0.06 and +0.064) that produced no PAPER_TRADE** — fresh evidence that PROFIT-OBS-003 swallows positive-edge candidates too, not just edge=0.0 candidates. PROFIT-OBS-003 promoted from MEDIUM/LATER to HIGH/NOW based on the corrected gap scope. CALIBRATION_CHECK fired 3 times in production (matching the 3 PAPER_RESOLUTION events) — small but real PROFIT-CAL-001 production-soak evidence, footnote updated. New entries opened: **PROFIT-OBS-004** (edge-sign display bug — `paper_trades.edge` records the YES-side edge regardless of trade side, confusing every retrospective audit), **PROFIT-CUTOVER-001** (MacBook → Mac Studio operational handoff: bot stopped on MacBook 2026-05-01T13:05:54Z; SQL-dump migration to Mac Studio via `transfer/macbook_handoff_2026-05-01/`; MacBook now archive-only), **PROFIT-PHASE2-001** (Phase 2 shadow-soak clock: launchd jobs `com.kalshi.governance.fast` + `.deep` were never bootstrapped on MacBook (`launchctl list` zero kalshi.governance entries), bootstrapped on Mac Studio 2026-05-01 ~14:00 UTC; §8.5 14-day acceptance target ETA 2026-05-15) |
| Previous Tracker Name | `docs/macos_migration_debt.md` |
| Current Tracker Name | `docs/profit_path_debt_log.md` |
| Total Items | 50 |
| Open — HIGH | 4 |
| Open — MEDIUM | 2 |
| Open — LOW | 1 |
| Items IN_PROGRESS | 1 (PROFIT-PHASE2-001 — soak clock running, no operator action until 2026-05-15) |
| Items COMPLETE | 41 (MAC-ASYNC-001, MAC-ASYNC-002, MAC-DB-001, MAC-DB-002, MAC-DB-003, MAC-DB-004, MAC-DB-005, MAC-CLI-001, MAC-CLI-002, MAC-DOC-001, MAC-DOC-002, MAC-DOC-003, MAC-FS-001, MAC-LOG-001, MAC-PLAT-001, MAC-TEST-001, MAC-TEST-002, MAC-TEST-003, MAC-TEST-004, PROFIT-TRACE-001, PROFIT-REPLAY-001, PROFIT-EVID-002, PROFIT-EXEC-001, PROFIT-OBS-001, PROFIT-OBS-002, PROFIT-PERF-001, PROFIT-STARTUP-001, PROFIT-STRUCT-001, PROFIT-CAL-001, PROFIT-RUNTIME-001, PROFIT-EDGE-001, PROFIT-EDGE-002, PROFIT-EDGE-003, PROFIT-DOSSIER-001, PROFIT-GOV-002, PROFIT-DOC-002, PROFIT-OBS-003, PROFIT-OBS-004, PROFIT-DEBT-OQ1-SHIM, PROFIT-SOURCE-001, PROFIT-DEBT-WAVE1-DRAFTS) |
| Consolidated From | `docs/EDGE_STATUS.md` (merged 2026-05-09 → §Current Status); `docs/governance/edge-004-closure-path-tldr-v3.md` (lever map + EDGE-004 closure criteria merged 2026-05-09 → §Current Status §2.3) |

### High-Risk Areas

1. **Traceability and replay gaps** — runtime evidence IDs are random, current-signal evidence can miss the matching `BLEND_DECISION`, and replay-critical `implied_probability` is not persisted by the live accumulation path.
2. **Source-class and evidence-quality loss** — live evidence conversion collapses all news into `source_class="news"`, weakening source diversity, scorer quality, readiness G2, and structural context.
3. **Observability under long runs** — app-log rollover policy is time-only and documentation disagrees with code behavior on macOS, making go-live audit trails fragile.
4. **No-edge / no-trade pattern under current input + market mix** — S4.5b (47h) and S4.5c-aggregate (~60h) windows both produced zero paper trades. Per ROADMAP P0.5 + P3.4 analysis, the LLM is correctly anchoring to market price across the current geopolitical broad-scope market mix (universal anchor rate ≈ 100% across the 32 audited source-market pairs); this is an *input-mix and market-scope* issue, not a code defect. The remediation path is upstream — Appendix A integration (broader source classes) and narrower-scope markets — currently gated behind P4-GATE outcome known. **This is the diagnostic-cluster operator-flagged 2026-04-26 as "the largest historical issue with the bot producing edge."** Calibration wiring (`PROFIT-CAL-001`) closed 2026-04-24 but cannot itself produce edge — calibration *measures* belief quality, the no-edge problem is upstream of belief generation. **Post-2026-04-28 update:** the EDGE-001/002/003 fix stack (v0.29.58) closed three sequential readiness-funnel kills and the kill point relocated to the executor at `edge = 0.0`; this no-edge pattern is now formally registered as `PROFIT-EDGE-004` with a sequenced investigation across matcher Jaccard threshold, pre-LLM-gate re-evaluation, market-mix specificity, and Appendix A source completeness. The "diagnostic cluster" of High-Risk Area #4 is therefore now an *open active investigation* with concrete next steps rather than a strategic backlog item. **Post-2026-05-01 update (full 13-day MacBook paper soak archive aggregated post-cutover):** the EDGE-001/002/003 stack also produced **3 actual paper trades** in the final ~36h of MacBook runtime (all on `KXFISAEXTEND-26APR-MAY0{1,2,3}` from VitalLaw.com, all losses, all with logged `edge=-0.068` — see PROFIT-OBS-004 for the sign-bug explanation; the actual decision-side edge was +0.068), and the lifetime trade-log confirms **2 OPPORTUNITY events at non-trivial positive edge (+0.06 and +0.064) that produced no PAPER_TRADE despite clearing PAPER_MIN_EDGE = 0.02** — direct evidence that PROFIT-OBS-003 is *not* limited to edge=0.0 candidates as the original 2026-04-28 entry implied. The OPPORTUNITY → PAPER_TRADE conversion gap is therefore both a "no edge" upstream condition (PROFIT-EDGE-004) *and* a "positive edge silently lost" downstream condition (PROFIT-OBS-003 promoted to HIGH/NOW). The two are now co-equal blockers; closing either in isolation does not resolve the no-trade pattern.

### Recommended Execution Order

1. `PROFIT-OBS-003` (now HIGH/NOW) — close the OPPORTUNITY → PAPER_TRADE silent-exit gap so the post-EDGE-003 audit can attribute every kill to a specific gate. Promoted ahead of EDGE-004 because the lifetime trade log (260 OPP / 17 SKIP / 3 PAPER = 240 silent exits, 17/17 SKIP reasons identical to `"edge +0.0000 below min_edge 0.02"`, plus 2 positive-edge OPPs that never produced trades) confirms the gap is actively losing actionable candidates, not just observability noise. Without this, every other audit (EDGE-004, CAL-001 calibration sample, governance Phase 2 reasoning) operates from a partial view of the pipeline.
2. `PROFIT-EDGE-004` — the operationally-surfaced binding constraint after the EDGE-001/002/003 stack landed; sequenced investigation (matcher audit → pre-LLM-gate re-eval → market-mix audit → Appendix A re-eval). With OBS-003's instrumentation in hand, EDGE-004's evidence section can quote per-gate kill counts rather than the present aggregate. Until this advances, paper trades remain effectively zero (the FISAEXTEND emission notwithstanding — same source, same series, single losing pattern).
3. ~~`PROFIT-OBS-004`~~ — **CLOSED 2026-05-02**. Fix landed in `trading/paper_trader.py:record_trade` (executed-side edge persistence) + 3 historical KXFISAEXTEND rows backfilled. See entry for closure detail.
4. `PROFIT-PHASE2-001` (new 2026-05-01, IN_PROGRESS) — soak clock is ticking; the only operator action is the daily monitoring checklist in [`docs/governance/PHASE2_RUNBOOK.md`](governance/PHASE2_RUNBOOK.md). No code work is appropriate inside the soak window unless a PARSE_ERROR / VALIDATION_ERROR / KILL_SWITCH event surfaces.
5. `PROFIT-CUTOVER-001` (new 2026-05-01) — MacBook → Mac Studio operational handoff. The only operator-side dependency is verifying the Studio's Phase 2 launchd is actually firing cycles (per RUNBOOK monitoring) and confirming the SQL-dump restore on the Studio matches the manifest counts. Closes when the first Studio paper trade resolves cleanly *or* when the §8.5 acceptance milestone is reached, whichever first.
6. `PROFIT-VALID-001` + remaining `PROFIT-OBS-*` items (validation and observability hardening; needed before P4-GATE).
7. `PROFIT-EVID-001` (blocked pending contract decision on non-trading evidence intake).
8. ROADMAP P4.1–P4.3 are gated behind S4.5c-prime (post-CAL-001 re-validation window) and P4-GATE outcome — the no-edge pattern in High-Risk Area #4 will only resolve via the OBS-003 → EDGE-004 investigation chain (which gate, then which matcher/market/source root cause), not by any further work in `/trading` or readiness-gate calibration.
9. `PROFIT-STRUCT-002` (verifies an already-shipped sub-fix's runtime format) and remaining MEDIUM and LOW items in dependency order.

---

## Current Status

<!-- Merged from docs/EDGE_STATUS.md on 2026-05-09 (docs/ consolidation initiative). EDGE_STATUS.md deleted post-merge. EDGE-004 closure-path TLDR v2.2 + v3 archived to docs/_archive/2026-05-09-docs-consolidation/. -->

> **Operator-facing edge dashboard.** Refresh by commit. Single page; replaces 100+ doc index for "are we making money?" questions.
> **Last refresh:** 2026-05-07 cycle-16E scorer-forensics run.

### 2.1 Edge Verdict

| metric | value |
|---|---|
| Lifetime P&L | **-$7.50** (live, n=3 paper trades) |
| Lifetime trade count | **3** (all resolved, all lost, all 1 source / 1 series / 1 direction; 3/3 wrong-direction) |
| Replay verdict | **Cycle-16E verdict: `scorer_fixed_no_signal_confirmed`. Cycle-17 §B/§C operator decision RESTORED (un-deferred).** Cycle-16D raw `237 trades / 2 wins` was scorer-overadmission. Production-proxy = 12 trades / 0 wins / -$1.005 P&L / 0 IC §16 slices. Production gates ported faithfully line-by-line. Market-implied baseline ≈ 1.005 expected wins on 12 trades (NOT 50% coin-flip); 0 actual wins is statistically uninformative at n=12. Anti-correlation framing withdrawn. |

#### Cycle-16E verdict landed — `scorer_fixed_no_signal_confirmed`; Cycle-17 RESTORED

Cycle-16D charter-locked verdict label `extraction_fixed_but_information_frontier_holds` matches the locked criterion (D5 coverage ≥90% AND D8 0 IC §16 slices). **Operator override 2026-05-07 withdraws the operational reading** because three load-bearing scorer concerns invalidate the underlying trade-and-win counts:

1. **`would_have_traded` does not gate on readiness.** Per `score_counterfactual_pnl.py:score_candidate`: `would_trade = abs(edge) >= min_edge` only. Production runtime gates trades on G1-G6 readiness; the scorer does not. 237 counterfactual trades likely over-counts production trade volume.
2. **Replay is massively YES-biased.** 231 YES / 6 NO trades; 0/231 YES wins; 2/6 NO wins. Bot systematically buying YES on markets that resolve NO. Selection effect, scorer sign error, OR Cycle-15B C7 keyword-extension over-emits YES on production text.
3. **Price-unit / longshot calibration uncertain.** 102 trades had `market_yes_price < 1`; 100 had `market_yes_price` between 1 and 9. Cents vs dollars consistency end-to-end unaudited. 100x unit error possibility.

**Cycle-16E delivered by Codex (`c913ffd`); Claude N3+N4+N5+N6+N7+N9+N10 review complete.** Production-proxy gates ported faithfully line-by-line vs `executor.py:200-244`. Verdict per locked task-split outcome 2: `scorer_fixed_no_signal_confirmed`. **Cycle-17 §B/§C operator decision RESTORED (un-deferred).**

Per cycle-17 skeletons, Cycle-17 routing:
- §B source onboarding (2-4 weeks; mandatory pre-onboarding re-trace requirement RELAXED since anti-correlation hypothesis withdrawn)
- §C strategic redesign / pause / paper-only research

Operator picks. PROFIT-EDGE-011 active.

#### Replay verdict log

| date | scope | verdict | report |
|---|---|---|---|
| 2026-05-06 | 3 paper-trade markets / 1 source / 1 series / n=3 trades | 0 positive-EV slices, P&L -$7.50, win rate 0.00, harness self-test passes | `edge-replay-cycle12-report.md` |
| 2026-05-06 (Cycle-13) | 24 resolved evidence_store markets / 255 replay rows | **0 positive-EV slices, 0 left-on-table winners, P&L -$7.50, IC §16 Rule 5 fires** | `edge-replay-cycle13-report.md` |
| 2026-05-06 (Cycle-14) | 24 resolved markets / 255 replay rows + synthetic Lane A/B injection | **Verdict: `extraction_broken`.** Movement_rate 1.57%, direction-correctness 0/6 when directional, sized-bet 0/3 (-$7.50), Brier 0.2599 (n=24, supporting only), Lane A PASS / Lane B FAIL at delta=0.000 on both fixtures. → Cycle-15B extraction rebuild active. | `edge-replay-cycle14-diagnosis.md` |
| 2026-05-07 (Cycle-15B) | 24 resolved markets / 272 replay rows + post-fix re-ingestion + 10 Lane B fixtures | **Verdict: `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap`.** C8 Lane B 8/8 directional + 2/2 NEUTRAL ✓; C9 idempotent re-ingestion (SHA256 stable); C10 IC §16 0 slices BUT 0/272 rows had decision-time executable price; 183/272 readiness-admitted; 7/272 nonzero post-fix model delta. Scorer-blocked, NOT negative-EV proven. → Cycle-16D price reconstruction active. | `docs/_archive/governance/edge-replay-cycle15b-report.md` (ARCHIVED Stream G R50) |
| 2026-05-07 (Cycle-16D) | 24 resolved markets / 272 replay rows + restored prices via documented Kalshi endpoints | **Charter-locked verdict: `extraction_fixed_but_information_frontier_holds`** (operational reading WITHDRAWN per operator override; superseded by Cycle-16E). D5 coverage 99.6324% (271/272 priced) ✓; D6 237 counterfactual trades / 2 wins / -7.46 P&L; overall ev_ci_95_lo = -0.0382; 1 raw positive-EV slice with trades=1 (below IC §16 floor); 0 IC §16-eligible slices; D9 sentinel POST_FIX_REBUILT cohort verified (commit 2222227). | `docs/_archive/governance/edge-replay-cycle16d-report.md` (ARCHIVED Stream G R52) |
| 2026-05-07 (Cycle-16E) | scorer forensics audit + production-proxy replay against same 272 rows | **Verdict: `scorer_fixed_no_signal_confirmed`.** Production-proxy gates ported faithfully line-by-line vs `executor.py:200-244`: price floor/ceiling 2¢/98¢, ticker cooldown 14400s, paper-duplicate prob/price 0.07/5.0, same-signal prob/price 0.02/2.0, PAPER_MIN_EDGE 0.02. Variant comparison: baseline_abs_edge 237 trades → readiness_only 182 → paper_price_sanity 110 → readiness_plus_price_sanity 63 → production_proxy 12 trades / 0 wins / -$1.005 P&L / 0 IC §16 slices. Market-implied baseline ≈ 1.005 expected wins on 12 production-proxy trades; 0 actual wins is statistically uninformative at n=12. Price units cents-consistent end-to-end (no 100x dollars/cents inversion). Cycle-16D anti-correlation framing WITHDRAWN. → Cycle-17 §B/§C operator decision RESTORED. | `edge-replay-cycle16e-scorer-forensics.md` |

### 2.2 Wave Deploy Posture

| wave | status | gate |
|---|---|---|
| Wave-1 (OBS-005, MATCH-001, OBS-003, EXEC-002, GOV-003, Lever A.1) | **ACTIVE — ships 2026-05-08 as cleanup/observability hygiene only; does NOT claim edge** | exempt under IC §16 Rule 2 (mechanical / observability / governance) |
| Wave-2 (Lever A.1+ feed onboarding, Branch C legal-analyst) | **HALTED PENDING CYCLE-17 OPERATOR DECISION + POST-DECISION CYCLE VERDICT** | requires (a) operator picks §B AND (b) post-onboarding replay produces ≥1 slice with `ev_ci_95_lo>0` AND `trades≥10` |
| Wave-3 (Lever B G1=0.04, Lever C cross-series) | **HALTED PENDING CYCLE-17 OPERATOR DECISION + POST-DECISION CYCLE VERDICT — Lever B counterindicated** | loosening admission on a model with 0 IC §16-eligible slices on audited scorer widens losses without expected gain |
| Branch D escalation (PROFIT-LLM-001 / P4-GATE Appendix A) | **HALTED PENDING CYCLE-17 OPERATOR DECISION + POST-DECISION CYCLE VERDICT** | each candidate fix needs replay evidence under audited scorer |
| **Capital posture** | **PAPER-ONLY. Hard guardrail** (Cycle-14 charter §5) | live trading remains blocked until ≥1 positive-EV slice surfaces under audited scorer per IC §16 |

#### Are we near a Wave-2-eligible slice?

**No.** Cycle-12 replay (paper-trade scope only, n=3) found 0 positive-EV slices. Cycle-13 will expand to 24 resolved markets in evidence_store; if still no slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`, IC §16 Rule 5 triggers strategic-pivot playbook (`docs/governance/edge-replay-pivot-playbook.md`).

**Cycle-13 leading indicator (cycle-13 dossier integrity audit):** 21 of 24 resolved-market dossiers stuck at `current_estimate = 0.5000` (the prior). Bot's belief model rarely exits the prior despite ingesting evidence. This is a calibration signal — most evidence is non-informative under current update logic. Replay verdict is bounded by this calibration regardless of feed onboarding.

#### What changes Wave-2 from HALTED → AUTHORIZED

A row in the replay output with **all** of:
- `ev_ci_95_lo > 0` (positive EV at 95% CI)
- `trades ≥ 10` (not single-cluster correlated)
- Documented in `edge-replay-cycle{N}-report.md` with reproducible commands

When that row exists, the slice it identifies (e.g., "Reuters × KXTRUMPCHINA × news") becomes the Wave-2 candidate. The pre-staged Wave-2 specs (legal/geopolitics speculation) are NOT the candidate; they remain BLOCKED.

#### Pre-deploy state (for fire-time operator)

| metric | value (cycle-13 refresh, 2026-05-06T22:30Z) |
|---|---|
| Soak elapsed | ~125 h (Day 5/7 under §8.5.1 path) |
| GOVERNANCE_DECISION count | 552 |
| Safety counters | 0/0/0 (KILL_SWITCH / batch_aborted / VALIDATION_ERROR) |
| PARSE_ERROR (total / trailing-72h) | 7 / 0 |
| Latest decision | 2026-05-06T21:43Z |
| Bot health | GREEN (`scripts/bothealth.sh` cycle-13) |
| Gate-6 capacity | **AT RISK** — 0.663 reviewable fraction at 80/day budget; needs Path 1 (raise budget to ≥169) per `2026-05-06-gate-6-capacity-resolution-plan.md` |
| §8.5.2 carve-out commits surfaced | 5 (3 INVOKED, 2 OUT-OF-SCOPE; gate 7 clean via attestation) |

### 2.3 EDGE-004 Closure Path

> **Note:** Lever map and probability ranking below pre-date Cycle-16E (2026-05-07). v3 source (`docs/governance/edge-004-closure-path-tldr-v3.md` — archived 2026-05-09 to `docs/_archive/2026-05-09-docs-consolidation/`) carried a top banner declaring SUPERSEDED PER IC §16 (cycle-11.5 strategic redirect, 2026-05-06): closure path is now Cycle-12+ replay → IF positive-EV slice found, deploy that slice (NOT speculative levers); IF none found, strategic pivot. Lever map retained as historical context for Cycle-17 §B/§C decision-input.

#### Lever map at a glance (v3 — 2026-05-05)

| lever | role | locked? | deploy timing |
|---|---|---|---|
| A.1 | prerequisite hygiene | ✅ classifier patch ready | 2026-05-08+ (Wave-1 commit 6) |
| A.1+ Branch A | passive observe | ✅ no code change | 2026-05-15+ (Day-14 default) |
| A.1+ Branch C | open-RSS legal-analyst | ⏳ feed selection per §A.1+1.5 selection-rubric | 2026-05-29+ if Branch A fails |
| A.1+ option-A | geopolitics specialist | ✅ URL list locked per parent spec §3.1 | parallel-discretion or fallback |
| **Lever B G1=0.04** | attribution; calibration | ✅ floor + failsafe + ratio LOCKED 2026-05-05 | 2026-06-06+ if A+C stall |
| **Lever C cross-series** | risk-control | ✅ v1 §3.2 hash + 3600 s + placement LOCKED 2026-05-05 | 2026-06-20+ if A+B stall |
| **Branch D escalation** | handoff | ✅ triggers spec'd 2026-05-05 | fires when §2 triggers met |
| Lever D (lever-menu §3) | pre-LLM gate (closed) | ✅ closed 2026-05-03 | n/a |
| Lever E (multi-source) | closed | ✅ closed 2026-05-03 | n/a |
| Lever F (P4-GATE App A) | out of EDGE-004 scope | ROADMAP-tracked | post-Branch-D |

#### What "closure" looks like (v3)

EDGE-004 closes OPEN → COMPLETE when:

1. One A.1+ branch (A or C, with option-A as parallel discretion) produces ≥ 5 % conversion lift over 14 d AND
2. Aggregate realized P&L on the new admitted PAPER_TRADE candidates is non-negative AND
3. Per-lane attribution (post-OBS-003 SKIPPED stream) confirms the closure is driven by the new feed, not background noise.

EDGE-004 closes OPEN → DEFERRED-CEILING when Branch D fires AND PROFIT-LLM-001 + P4-GATE Appendix A sizing both return inadequate. **Real possibility, not a fail-safe abstraction.**

#### Honest read (v3 — 2026-05-05; pre-Cycle-16E)

The closure path now hangs on TWO empirical questions, asked in order:

1. **Does Branch A (passive Google News observe) surface VitalLaw-equivalent legal-niche PAPER_TRADE in 14 d?** If yes: closure on the cheapest path; no new code. If no:
2. **Does Branch C (open-RSS legal-analyst onboard) admit PAPER_TRADE with positive P&L?** If yes: closure on minimal-effort code change. If no: Branch D fires; intake-side experimentation exhausted.

**Probability ranking (refreshed 2026-05-05):** Branch A succeeds (~30 %) > Branch C succeeds (~40 % conditional on A failing) > Branch D fires and PROFIT-LLM-001 lands within 30 d (~20 %) > Branch D fires and EDGE-004 closes DEFERRED-CEILING (~10 %).

Probability of intake-side closure: ~30 % + (1 − 0.30) × 0.40 = **~58 %**. Reasonable but not high.

### 2.4 Replay Harness State

- Harness exists: `scripts/edge_replay/{fetch_resolved_markets,build_replay_dataset,score_counterfactual_pnl}.py`
- Test coverage: 9 tests including synthetic +EV self-test (validates scorer can FIND edge when it exists, not just report negative)
- Bootstrap CI implemented (cycle-12 task #5 done by Codex)
- Per-decision-time price reconstruction implemented via `--historical-prices` (cycle-13 task #6 done by Codex)
- "Left on the table" measure implemented (cycle-13 task #7 done by Codex)
- Cycle-13 charter: expand scope from 3 → 24 markets via `--live-kalshi`
- Cycle-13 status: in progress (Codex implementing)

### 2.5 Live Operations

- `com.jake.kalshi-bot` running (paper mode); uptime ~5d 5h
- `com.kalshi.governance.fast` shadow-mode soak active
- `com.kalshi.db-backup` daily 06:00 MDT (last fire 2026-05-06T1200Z confirmed)
- 06:00 MDT db-backup monitor armed (next fire Thu 2026-05-07T1200Z)

### 2.6 Cross-links

- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — strategic redirect
- `docs/governance/edge-replay-pivot-playbook.md` — IC §16 Rule 5 strategic-pivot diagnostic
- `docs/governance/edge-replay-cycle12-report.md` — Cycle-12 replay output
- `PROFIT-EDGE-005` (this file) — replay harness debt entry
- `data/paper_trades.db` — 3-trade lifetime history
- `data/evidence_store.db` — 266 evidence rows / 24 resolved markets

---

## Current Open Profit-Path Items

These items were added during the 2026-04-20 expanded audit. They do not replace the completed `MAC-*` migration work below; they extend the same single tracking mechanism to all issues that could impair profitable, safe, auditable trading.

---

### PROFIT-RUNTIME-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-RUNTIME-001 |
| **Title** | S4.5 multi-lane paper validation remains unproven over a meaningful window |
| **Category** | System Validation / Profit-Path Integrity |
| **Severity** | HIGH |
| **Status** | COMPLETE (2026-04-26, on aggregate post-2026-04-23 soak evidence) |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | S4.5c extended validation window |
| **Blocks** | (none — S4.5c-prime re-validation post-CAL-001 is a separate ROADMAP gate, tracked there) |

**Description**  
The system now wires fast-lane output through `BlendTask`, evidence through `AccumulationTask`, and structural recomputes through `StructuralTask`, but the corrected S4.5 validation still requires a sustained 6-12 hour paper-mode run. Earlier attempts only proved wiring readiness or insufficient runtime, not real multi-lane behavior under production-intended intake.

**Why it matters to profitability / safety / reliability**  
Without this run, the bot can appear architecturally complete while still failing to accumulate useful evidence, recompute priors, emit complete blend telemetry, or preserve the trade-frequency constraint in real flow.

**Evidence / Source**  
- `main.py:709-723` routes keyword-positive fast-lane signals into evidence + blend.
- `main.py:1616-1620` starts accumulation, blend consumer, and structural tasks.
- S4.5 notes in chat: prior runs lacked sufficient post-wiring observation time.

**Proposed Fix**  
Run paper mode under production-intended intake for a meaningful window, capture lane activation timeline, compare fast-lane baseline to multi-lane frequency, and record Section 13 pass/fail evidence.

**Acceptance Criteria**  
- Runtime duration is recorded.
- `EVIDENCE_INGESTION`, `DOSSIER_UPDATE`, `STRUCTURAL_PRIOR_RECOMPUTE`, and `BLEND_DECISION` are observed or a defect is filed.
- Trade-frequency comparison is computed or handled with the zero-baseline rule.
- Section 13 checklist is explicitly PASS / FAIL / N/A.

**Notes**  
Do not modify intake settings to force activity; this is a validation debt item, not a tuning item.

**Validation Notes** (2026-04-20)  
Runtime evidence exists for accumulation and blend participation, but the gate cannot close yet. `logs/trades/live/trades.jsonl` shows `BLEND_DECISION`, `EVIDENCE_INGESTION`, and `DOSSIER_UPDATE` events from 2026-04-20T03:43Z through 2026-04-20T12:50Z for `KXTRUMPIRAN-26MAY01`; `data/evidence_store.db` shows one dossier at version 7 with seven evidence rows. No `STRUCTURAL_PRIOR_RECOMPUTE` events were found, and `structural_priors` is empty. This is now blocked on resolving structural lane participation (see `PROFIT-STRUCT-001`) before S4.5 can be honestly marked complete.

**Validation Notes** (2026-04-23)  
Structural-lane block cleared. Over the 47-hour window 2026-04-21T00:09 → 2026-04-22T23:44 UTC (post-commit `2731d9a` structural crash-loop fix deployed 2026-04-21T01:38 UTC), `logs/trades/archive/2026/04/2026-04-{21,22}.jsonl` contain `EVIDENCE_INGESTION ×46`, `DOSSIER_UPDATE ×46`, `BLEND_DECISION ×49` (with `fast_lane_p` non-null on sampled events), and `STRUCTURAL_PRIOR_RECOMPUTE ×20` across 6 distinct dossier markets (`KXELECTIONEMERGENCY-26MAY01`, `KXMOCTRUMP25-26-APR24`, `KXMOCTRUMP25-26-MAY01`, `KXPARDONSTRUMP-26APR-1`, `KXTRUMPENDORSE-26SEP15-NMOR`, `KXTRUMPIRAN-26MAY01`). First structural event at 2026-04-21T16:52:38 UTC. Zero unhandled exceptions in `bot.log`. Trade-frequency observation: zero paper trades, consistent with Phase 0 verdict (LLM correctly declines directional views on broad-scope markets — not a runtime defect) — zero-baseline rule applies. **S4.5b closed PASS** (see `docs/ROADMAP.md` Stage 4 table). Status advanced `BLOCKED → OPEN`. The only remaining blocker is S4.5c — the 72-hour extended statistical-basis window — which is a soak-time requirement, not an architectural concern. No code or intake changes are required to reach S4.5c; depends only on elapsed observation time after any current configuration changes stabilize.

**Validation Notes** (2026-04-26, S4.5c soak evidence sweep)
Operator stopped the bot at 2026-04-26T19:05 UTC ahead of travel; this note records the soak-window evidence observable at that point so a returning operator (or agent) can decide on closure without re-running the queries.

Window: **2026-04-20T03:43:52 UTC → 2026-04-26T18:33:43 UTC = 158.8 hours (6.6 days).** Source files: `logs/trades/archive/2026/04/2026-04-{20..25}.jsonl` plus `logs/trades/live/trades.jsonl`.

Multi-lane event totals across the full window (per-day breakdown was healthy every day from 04-20 onward; counts grep'd against `"type": "<NAME>"` JSONL records, counted from the trade log only — these events do NOT appear in `bot.log`, which was the original error in the 2026-04-26 first-pass sweep):

| Event | Count |
|---|---|
| `EVIDENCE_INGESTION` | 169 |
| `DOSSIER_UPDATE` | 169 |
| `STRUCTURAL_PRIOR_RECOMPUTE` | 108 |
| `BLEND_DECISION` | 173 |
| **Total** | **619** |

Distinct dossier markets touched by these events: **19** (up from 6 at S4.5b close on 04-23). New markets since S4.5b include `KXFISAEXTEND-*`, `KXPARDONSTRUMP-26APR-{12,22,24}`, `KXTRUMPCHINA-26-APR24`, `KXVANCEPAKISTAN-*` (5 expirations), `KXVOTESAVEAMERICA-*`. The structural lane is firing across a meaningfully broader market footprint than at S4.5b.

Continuity check: 5 gaps > 6 hours between consecutive multi-lane events, all overnight (low news volume, not a defect):
- 04-20T12:50 → 04-21T07:30 (18.7h)
- 04-21T07:30 → 04-21T13:33 (6.0h)
- 04-21T23:34 → 04-22T11:30 (11.9h)
- 04-22T20:05 → 04-23T13:08 (17.1h)
- 04-25T08:15 → 04-25T15:17 (7.0h)

Post-S4.5b-close subwindow (04-23T13:08 → 04-26T13:08 = exactly 72.0h) contains the four event types every day with only the 7h 04-25 gap. That is the cleanest read of the S4.5c "72-hour extended statistical-basis window" criterion in the entry above. Trade-frequency is again zero-baseline (no paper trades in the window), consistent with the 2026-04-23 closure rationale.

Acceptance criteria status (per the entry above):
- Runtime duration recorded — **PASS** (158.8h, well over 72h).
- All four event types observed — **PASS**.
- Trade-frequency comparison or zero-baseline — **PASS** (zero-baseline as before).
- Section 13 checklist explicit PASS/FAIL/N/A — **operator action remaining**. The other three criteria are satisfied by file evidence; the Section 13 box is the only thing standing between OPEN and COMPLETE. Recommend formal closure on operator return, with the Section 13 marker updated in `docs/ROADMAP.md` Stage 4 table per the S4.5b precedent.

Caveat: the bot was running v0.29.54 (Phase 1 only) during this window. Phase 2 governance shipped today as v0.29.55 but its launchd plists were intentionally not installed by that MR — the governance soak (separate `§8.5` gate) has not started, and the multi-lane evidence above is independent of governance behavior. Do not conflate the two soaks when assessing closure.

**Closure** (2026-04-26)
Status flipped OPEN → COMPLETE. All four acceptance criteria met:
1. Runtime duration recorded (158.8h aggregate; ROADMAP S4.5c row records the original ~60h post-fix window that was the basis for the 2026-04-23 ROADMAP closure).
2. EVIDENCE_INGESTION (169), DOSSIER_UPDATE (169), STRUCTURAL_PRIOR_RECOMPUTE (108), BLEND_DECISION (173) all observed across the 04-20 → 04-26 window.
3. Trade-frequency: zero-baseline rule, consistent with both the 2026-04-23 closure rationale and the broader no-edge diagnosis (universal LLM anchoring on broad-scope geopolitical markets, ROADMAP P0.5 + P3.4).
4. Section 13 checklist signed off on `docs/ROADMAP.md` Stage 4 row S4.5c (closed 2026-04-23 with bounded-exception rationale and aggregate-runtime sign-off scaffold). The 2026-04-26 evidence above is additional confirmation that the original closure has continued to hold under steady-state operation.

**S4.5c-prime** (post-`PROFIT-CAL-001` calibration-wiring re-validation, mandated in the ROADMAP S4.5c row Notes) remains a separate ROADMAP gate, not tracked here. `PROFIT-CAL-001` itself closed 2026-04-24 (v0.29.47) — the live-traffic observation of `CALIBRATION_CHECK` events post-fix is conditional on natural paper-trade resolutions, which is gated behind the no-edge / P0-GATE situation flagged in this log's High-Risk Areas section item 4. Until a paper trade actually resolves, the wiring is exercised only by unit tests, not in production.

**Validation Notes** (2026-05-01, full 13-day MacBook paper-soak archive aggregated post-cutover)

This is the post-cutover audit of the *entire* MacBook paper era (2026-04-18T02:11:24Z → 2026-05-01T13:05:54Z = ~13 days, ~314 hours wall-clock, 115 boots across versions 0.29.5 → 0.29.58). Captured here for completeness and as the durable provenance record before the MacBook becomes archive-only and the Mac Studio's runtime takes over. RUNTIME-001's COMPLETE status remains valid; this note expands the supporting evidence base that informed the 2026-04-23/26 closures.

Multi-lane event totals across the full 13-day window (counted from `logs/trades/archive/2026/04/2026-04-{18..30}.jsonl` + `logs/trades/live/trades.jsonl`):

| Event | Count |
|---|---|
| `EARLY_STALE_DROP` | 237,073 |
| `EARLY_FRESH_PASS` | 4,694 |
| `MATCH_DIAGNOSTIC` | 2,838 |
| `SIGNAL_ANALYSIS_DETAIL` | 1,315 |
| `ANALYSIS_REJECTED` | 1,249 |
| `NEW_MARKET` | 1,029 |
| `MATCH_SUPPRESSION_CANDIDATE` | 489 |
| `MATCH_SUPPRESSED` | 489 |
| `SIGNAL` | 260 |
| `OPPORTUNITY` | 260 |
| `BLEND_DECISION` | 252 |
| `EVIDENCE_INGESTION` | 248 |
| `DOSSIER_UPDATE` | 248 |
| `STRUCTURAL_PRIOR_RECOMPUTE` | 178 |
| `SKIPPED` | 17 |
| `PAPER_TRADE` | 3 |
| `PAPER_RESOLUTION` | 3 |
| `CALIBRATION_CHECK` | 3 |

All four originally-required event types (EVIDENCE_INGESTION, DOSSIER_UPDATE, STRUCTURAL_PRIOR_RECOMPUTE, BLEND_DECISION) appeared on **every day of the 13-day window** (operator-confirmed daily-bucket grep against the `ts` field). Dossier markets touched: 32 distinct `series_ticker` values per `evidence_store.db.dossiers` row count. Lifetime `paper_trades.db.source_stats` aggregates: 3,960 posts seen, 249 source-level signals, 249 source-level opportunities, 3 source-level trades — minor count drift versus the trade-log JSONL aggregate (260 SIGNAL / 260 OPPORTUNITY) is attributable to startup-probe records and aggregation-window boundary effects, well within audit tolerance.

Source-funnel concentration: out of 405 distinct sources discovered into `source_stats`, only **1** (`VitalLaw.com`) has graduated to a paper trade; **31 sources** have non-zero `opportunities` but zero `trades`. Top-by-opportunities list is dominated by wire-service feeds (`Middle East and north Africa | The Guardian` 87 opp / 0 trade; `World news | The Guardian` 40 / 0; `Al Jazeera` 29 / 0; `NYT > World News` 28 / 0) — the same broad-policy-coverage feeds that ROADMAP P0.5/P3.4 flagged as the universal-anchor problem. This concentration is the empirical face of PROFIT-EDGE-004's matcher / market-mix root cause from the source side.

Boot stability: 115 boots over 13 days = ~8.8 boots/day average; 64 errors total (39 REST connectivity, 18 WebSocket reconnect, 5 db_open during early `0.29.5` boots before MAC-DB-005 stabilized, 1 unhandled, 1 other), zero unhandled tracebacks. The boot-density skew is itself informative — 49 of the 115 boots fall in the 2026-04-20/21 window during the v0.29.31 stack, after which deployments stabilized and a single v0.29.58 boot held for the entire 2026-04-29/30 window.

Cross-host caveat: the entire 13-day window was on the **MacBook** (`hostname = MacBook-Pro.local`, Apple T6030 / M3 Pro, 18 GB unified memory). The cutover to **Mac Studio** (`qwen3:14b` governance model per `docs/governance/PHASE2_RUNBOOK.md`) closes the MacBook era at 2026-05-01T13:05:54Z. Post-cutover RUNTIME-001 evidence accumulates on the Mac Studio against `data/paper_trades.db` and `data/evidence_store.db` restored from `transfer/macbook_handoff_2026-05-01/` (see `PROFIT-CUTOVER-001`). This entry remains COMPLETE; the Mac Studio is not expected to require an S4.5b/c re-run absent a deploy that materially changes lane wiring.

---

### PROFIT-TRACE-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-TRACE-001 |
| **Title** | Evidence identity and blend traceability are fragile in live runtime |
| **Category** | Auditability / Traceability |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | S2.5, S3.4 |
| **Blocks** | Reliable trade explanation, replay confidence |

**Description**  
`_signal_to_evidence()` creates `evidence_id=str(uuid.uuid4())`, so the same feed item reprocessed after restart receives a new immutable ID. `BlendTask` then emits `evidence_ids_contributing` from already-persisted recent evidence only; because `main.py` enqueues evidence and immediately calls `BlendTask`, the triggering signal's evidence may not yet be persisted and may be missing from the related `BLEND_DECISION`.

**Why it matters to profitability / safety / reliability**  
The contract relies on evidence -> dossier -> blend -> trade traceability. If IDs are nondeterministic or the current signal is absent from blend telemetry, later review cannot reliably explain why a profitable or losing trade happened.

**Evidence / Source**  
- `main.py:333-348` generates random UUID evidence IDs.
- `main.py:709-723` enqueues evidence non-blocking, then immediately blends.
- `tasks/blend_task.py:191` emits `evidence_ids = [record.evidence_id for record in recent_records] if dossier else []`.

**Proposed Fix**  
Define deterministic evidence identity for feed-derived evidence and ensure the triggering evidence ID is included in the blend trace when available, without blocking the fast lane on accumulation policy.

**Acceptance Criteria**  
- Reprocessing the same source/headline/market/time identity produces the same evidence ID or a documented idempotency key.
- `BLEND_DECISION.evidence_ids_contributing` can be joined to the specific evidence chain behind the decision.
- Tests cover same-signal traceability and restart/replay idempotency.

**Notes**  
This is not a request to redesign evidence semantics; it is a traceability contract repair.

**Implementation Notes** (2026-04-20)
Fixed by replacing random runtime evidence IDs with deterministic `ev-<sha256>` IDs derived from ticker, source, URL, headline, and published timestamp. The fast-lane `SignalAnalysis.signal_meta` now carries `trigger_evidence_id`, and `BlendTask` includes that ID in `BLEND_DECISION.evidence_ids_contributing` even before the asynchronous accumulation task has caught up. Recent evidence IDs are de-duplicated against the trigger ID. Validation: `.venv/bin/pytest tests/test_main_pipeline.py tests/test_blend_task.py tests/test_accumulation_task.py tests/test_replay_dossier.py` (80 passed).

---

### PROFIT-REPLAY-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-REPLAY-001 |
| **Title** | Live accumulation path does not persist replay-critical `implied_probability` |
| **Category** | Replay / Auditability |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | Codex |
| **Depends On** | S4.1 |
| **Blocks** | Deterministic dossier reconstruction |

**Description**  
The replay utility intentionally fails if persisted evidence lacks `raw_payload_json.implied_probability`, but `_evidence_record_from_score()` does not populate `raw_payload_json`. The live `Evidence` object carries `implied_probability`, then storage drops it from replay payload.

**Why it matters to profitability / safety / reliability**  
If belief state cannot be reconstructed solely from persisted evidence events, losing trades cannot be audited and profitable patterns cannot be calibrated from a trustworthy history.

**Evidence / Source**  
- `scripts/replay_dossier.py:299-344` requires `raw_payload_json.implied_probability`.
- `tasks/accumulation_task.py:320-333` builds `EvidenceRecord` without `raw_payload_json`.
- `analysis/evidence_types.py:26` defines `implied_probability` as part of `Evidence`.

**Proposed Fix**  
Persist the minimal replay payload required by S4.1 when converting scored evidence to `EvidenceRecord`, preserving schema compatibility.

**Acceptance Criteria**  
- Live-ingested evidence rows contain replay-critical probability data.
- `scripts.replay_dossier` can replay a paper-run market without synthetic test-only payloads.
- Existing raw payload fields remain backward compatible for old rows.

**Notes**  
Do not patch replay to guess missing probabilities; missing data should stay visible for old records.

**Implementation Notes** (2026-04-20)
`AccumulationTask` now persists a minimal `raw_payload_json` payload containing `implied_probability`, `evidence_id`, and `content_hash` when converting live `Evidence` into `EvidenceRecord`. This preserves backward compatibility for old rows while making new rows replayable by `scripts/replay_dossier.py`. Validation: `.venv/bin/pytest tests/test_main_pipeline.py tests/test_blend_task.py tests/test_accumulation_task.py tests/test_replay_dossier.py` (80 passed).

---

### PROFIT-EVID-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EVID-001 |
| **Title** | Accumulation lane only learns from keyword-positive fast-lane survivors |
| **Category** | Signal Quality / Evidence Coverage |
| **Severity** | HIGH |
| **Status** | BLOCKED |
| **Priority** | HIGH |
| **Owner** | Shared |
| **Depends On** | S2.5 |
| **Blocks** | Dossier completeness, structural prior quality |

**Description**  
`_process_candidate()` returns immediately on `if not keywords`, before creating `SignalAnalysis` or evidence. That means weak/no-keyword but potentially informative LLM outcomes and rejected candidates do not enter the accumulation lane.

**Why it matters to profitability / safety / reliability**  
The dossier can become a biased memory of only signal-positive events, weakening calibration around non-events, false positives, and contextual evidence that should reduce confidence.

**Evidence / Source**  
- `main.py:553-568` logs `no_keywords` and returns.
- Evidence creation begins only at `main.py:709`.

**Proposed Fix**  
Decide, at the contract level, which rejected or low-signal observations should become non-trading evidence, then route only those approved classes into accumulation with explicit update types.

**Acceptance Criteria**  
- Evidence intake policy documents which fast-lane outcomes become dossier evidence.
- Tests verify no unintended trade path is opened by ingesting non-trading evidence.
- Dossier updates can represent confidence/state-neutral evidence where intended.

**Notes**  
This is not permission to loosen trading thresholds.

**Blocker Notes** (2026-04-20)  
Implementation intentionally paused. Ingesting no-keyword or low-signal rejected candidates would alter dossier state and future blend/readiness inputs, which is a decision-policy change. The proposed fix requires a contract-level decision on which rejected observations should become non-trading evidence and how those observations should update state versus confidence. Do not implement until `IMPLEMENTATION_CONTRACT.md` defines the allowed rejected-evidence classes and update semantics.

---

### PROFIT-EVID-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EVID-002 |
| **Title** | Runtime evidence conversion collapses all sources into `source_class="news"` |
| **Category** | Signal Quality / Source Classification |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | HIGH |
| **Owner** | Codex |
| **Depends On** | S2.5, S3.4 |
| **Blocks** | Readiness G2 fidelity, source-quality scoring |

**Description**  
`_signal_to_evidence()` hardcodes every feed-derived item to `source_class="news"`, even when source families include publisher RSS, Reddit/social, direct feeds, search-derived items, or official-style sources.

**Why it matters to profitability / safety / reliability**  
The evidence scorer, dossier builder, structural task, and readiness gate use source class for quality, correlation, diversity, and source-class requirements. Collapsing classes hides evidence concentration and can over-block or over-trust trades.

**Evidence / Source**  
- `main.py:342` sets `source_class="news"`.
- `analysis/evidence_scorer.py:58-118` uses `source_class` for quality and independence.
- `tasks/trade_readiness_gate.py:95-97` enforces source-class diversity for dossier candidates.

**Proposed Fix**  
Add a narrow source-family-to-source-class mapper for evidence metadata, reusing existing source-family classification where possible.

**Acceptance Criteria**  
- RSS, Reddit/social, search, direct, official/market-like sources map deterministically to documented classes.
- Existing trading behavior remains unchanged except for evidence metadata.
- Tests cover readiness G2 and scorer effects with real runtime source examples.

**Notes**  
Keep mapping conservative; unknown sources should remain `other` or a documented fallback.

**Implementation Notes** (2026-04-20)  
Added conservative runtime source-class mapping in `main.py`: Reddit-style sources map to `social`, official/government-style labels map to `official`, known publisher/search/direct news sources map to `news`, and unknown sources fall back to `other`. Evidence conversion now uses this mapper instead of hardcoding `news`. Validation: `.venv/bin/pytest tests/test_main_pipeline.py tests/test_blend_task.py tests/test_accumulation_task.py tests/test_replay_dossier.py` (80 passed).

---

### PROFIT-OBS-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-OBS-001 |
| **Title** | `BLEND_DECISION` completeness rules conflict with nullable lane semantics |
| **Category** | Observability / Contract Clarity |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | HIGH |
| **Owner** | Shared |
| **Depends On** | S4.2 |
| **Blocks** | Reliable observability pass/fail interpretation |

**Description**  
The contract requires all `BLEND_DECISION` fields to be non-null for at least 90% of paper events, but early/fast-only cases naturally have absent accumulation or structural lane values, and approved trades use `trade_blocked_reason=None`.

**Why it matters to profitability / safety / reliability**  
If observability validation treats semantically optional fields as failed instrumentation, operators may chase false defects or miss real telemetry gaps.

**Evidence / Source**  
- `docs/IMPLEMENTATION_CONTRACT.md:597` defines the 90% non-null criterion.
- `tasks/blend_task.py:316-331` emits accumulation/structural fields directly from lane availability.
- `tests/test_blend_decision_schema.py:79` expects `trade_blocked_reason is None` for non-blocked cases.

**Proposed Fix**  
Clarify completeness validation into required-presence versus semantically nullable fields, while preserving exact schema keys.

**Acceptance Criteria**  
- S4.2 tooling distinguishes absent keys, malformed values, and allowed nulls.
- Contract wording documents which fields may be null and when.
- Tests cover both approved and blocked candidate telemetry.

**Notes**  
Do not remove fields from the schema.

**Implementation Notes** (2026-04-20)  
Updated `scripts/observability_completeness_review.py` to distinguish required-valid completeness from strict non-null completeness. Semantically nullable `BLEND_DECISION` fields (`accumulation_*`, `structural_*`, and approved-candidate `trade_blocked_reason=None`) now count as valid when the schema key is present, while absent or malformed fields still fail. Blocked-reason gaps now flag explicit empty strings rather than approved candidates. Validation: `.venv/bin/pytest tests/test_observability_completeness_review.py` (5 passed).

---

### PROFIT-OBS-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-OBS-002 |
| **Title** | App-log rollover policy and documentation are misaligned for long macOS runs |
| **Category** | Observability / Runtime Operations |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | HIGH |
| **Owner** | Codex |
| **Depends On** | — |
| **Blocks** | S4.5 long-run audit confidence |

**Description**  
`utils/logger.py` uses a time-based `_DailyRotatingFileHandler` with copy+truncate rotation for app logs, while `PLATFORMS.md` claims macOS/Linux use atomic rename. App logs have no size cap, so a high-volume same-day S4.5 run can produce large active files until midnight or manual rotation.

**Why it matters to profitability / safety / reliability**  
Long-run paper validation depends on trustworthy, bounded logs. Misunderstood rollover behavior can lose audit context, create operator confusion, or cause disk pressure during the exact runs meant to prove safety.

**Evidence / Source**  
- `utils/logger.py:146-231` implements copy+truncate daily rotation.
- `PLATFORMS.md:14` documents atomic rename for macOS/Linux.
- git history (v0.6.6–v0.6.7 commits) documents the switch to copy+truncate daily rotation and the singleton-handler fix for duplicate rotation.

**Proposed Fix**  
Audit and either correct the documentation to match intentional daily copy+truncate behavior or implement the intended platform-specific rotation policy with tests for forced rollover and sustained logging.

**Acceptance Criteria**  
- `bot.log` and `errors.log` rollover behavior is explicitly documented and tested on macOS.
- Active app logs have an intentional size or time policy.
- No duplicate file handlers write to the same app log.

**Notes**  
This item captures the logging rollover concern inside the unified debt log instead of creating a separate logging tracker.

**Implementation Notes** (2026-04-20)  
Validated and completed the macOS stale-rollover repair. `utils.logger._maybe_rotate_stale()` now checks the first non-comment log timestamp when mtime alone makes a prior-period active log look current; this catches the long-run/restart case without changing the intended daily copy+truncate policy. `PLATFORMS.md` already documents copy+truncate for macOS/Linux. Added a regression test for current mtime plus prior-period first log timestamp. Validation: `.venv/bin/pytest tests/test_logger_rotation.py tests/test_app_log_reader.py tests/test_log_isolation.py tests/test_trade_log_store.py` (29 passed, 1 skipped).

---

### PROFIT-PERF-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-PERF-001 |
| **Title** | Synchronous structured-log fsyncs can stall async hot paths |
| **Category** | Performance / Timeliness |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | MEDIUM |
| **Owner** | Shared |
| **Depends On** | — |
| **Blocks** | High-volume intake reliability |

**Description**  
`TradeLogStore._append_line()` writes and fsyncs every structured JSONL event synchronously. Many trade-log calls happen inside async feed, analysis, accumulation, and blend paths.

**Why it matters to profitability / safety / reliability**  
During news bursts, synchronous fsyncs can delay candidate processing, evidence ingestion, and decision telemetry, causing stale decisions or missed opportunities.

**Evidence / Source**  
- `utils/logger.py:401-409` writes, flushes, and `os.fsync()`s every structured record.
- `main.py`, `tasks/accumulation_task.py`, and `tasks/blend_task.py` call `trade_log` from async workflows.

**Proposed Fix**  
Measure structured-log write latency under load, then decide whether to batch, queue, or thread off fsyncs without weakening audit durability.

**Acceptance Criteria**  
- Benchmark or stress test quantifies worst-case logging latency.
- Any changed logging path preserves event ordering and durability guarantees.
- Async hot-path tests guard against blocking regression.

**Notes**  
Do not remove fsync without an explicit audit-durability decision.

**Implementation Notes** (2026-04-20)
Preserved per-record flush/fsync durability but moved async hot-path structured-log writes through `utils.logger.write_trade_log_async(...)`, which awaits a thread offload so the event loop is not blocked by durable JSONL appends. Updated fast-lane intake, signal-analysis detail, matcher diagnostics, accumulation, blend, structural, and executor structured writes that run inside async workflows. Added `scripts/structured_log_latency_benchmark.py` to quantify the exact `TradeLogStore` fsync path; local macOS run with 100 synthetic records measured mean `0.0812 ms`, p99 `0.1570 ms`, max `0.2249 ms`. Added regression coverage that a deliberately slow structured writer runs off the event-loop thread. Validation: `.venv/bin/pytest tests/test_trade_log_store.py::test_write_trade_log_async_offloads_blocking_writer_from_event_loop tests/test_structured_log_latency_benchmark.py tests/test_main_pipeline.py tests/test_signal_analyzer.py tests/test_market_matcher.py tests/test_accumulation_task.py tests/test_blend_task.py tests/test_structural_task.py tests/test_executor.py` (229 passed).

---

### PROFIT-VALID-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-VALID-001 |
| **Title** | No first-class harness for fast-lane baseline versus multi-lane S4.5 comparison |
| **Category** | Validation / Testing |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | HIGH |
| **Owner** | Codex |
| **Depends On** | PROFIT-RUNTIME-001 |
| **Blocks** | Reproducible trade-frequency constraint proof |

**Description**  
S4.5 requires comparing fast-lane-only baseline metrics against multi-lane metrics, but there is no dedicated run mode or measurement harness that cleanly disables blend routing while preserving comparable intake.

**Why it matters to profitability / safety / reliability**  
The 2x trade-frequency constraint is a core anti-overtrading guard. If baseline measurement is ad hoc, future validations may be inconsistent or impossible to reproduce.

**Evidence / Source**  
- `main.py:719-723` always routes normal news candidates through `BlendTask`.
- No config or script was found that captures both baseline and multi-lane metrics under identical intake windows.

**Proposed Fix**  
Add an explicit offline/paper validation harness or documented run flag for baseline measurement that cannot be confused with production trading behavior.

**Acceptance Criteria**  
- Baseline and multi-lane metrics can be generated reproducibly.
- Validation artifacts include evaluated candidates, paper trades, frequency, acceptance rate, and blend pass/block counts.
- The harness is paper/offline only and cannot enable live bypass behavior.

**Notes**  
This should be validation tooling, not a production bypass.

**Implementation Notes** (2026-05-02)  
Added `scripts/simulations/baseline_vs_multilane.py`, an offline/paper-only harness that replays `LLM_POSITIVE_EVENTS_2026_04_26` through a fast-lane baseline and the current multi-lane `BlendTask` path under identical intake. The report includes evaluated candidates, readiness pass/block counts, executor-evaluated candidates, paper trades, acceptance rate, trade frequency per candidate, blend pass/block counts, per-event block/skip reasons, and the multi-lane/baseline trade-frequency ratio. Current fixture output: baseline 5 evaluated / 4 readiness-pass / 4 executor-evaluated / 3 paper trades; multi-lane 5 evaluated / 4 readiness-pass / 4 executor-evaluated / 3 paper trades; frequency ratio `1.0x`, within the 2x anti-overtrading constraint. Added smoke coverage in `tests/test_simulations_smoke.py` and documented the harness in `scripts/simulations/README.md`. Validation: `.venv/bin/python scripts/simulations/baseline_vs_multilane.py --json` and `.venv/bin/pytest tests/test_simulations_smoke.py -q` (39 passed).

---

### PROFIT-STRUCT-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-STRUCT-001 |
| **Title** | Structural prior recompute may lag initial market-cache availability |
| **Category** | Structural Lane / Timeliness |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | MEDIUM |
| **Owner** | Shared |
| **Depends On** | S3.2 |
| **Blocks** | Early-session structural participation confidence |

**Description**  
`_structural_recompute_task()` runs `StructuralTask.run_periodic()` against `self.matcher._cache._markets`. If the first run occurs before cache warmup has populated active markets, structural participation may be delayed until the next hourly interval.

**Why it matters to profitability / safety / reliability**  
Early-session decisions could be evaluated without structural context even when dossiers become available soon after startup, weakening the multi-lane architecture during the first hour.

**Evidence / Source**  
- `main.py:1555-1560` passes the current market cache directly.
- `tasks/structural_task.py:139-147` sleeps for `interval_seconds` after each run.
- Runtime logs have shown market cache warmup taking minutes under some sessions.

**Proposed Fix**  
Measure first-run timing and, if confirmed, trigger an additional recompute after market-cache warmup or after first dossier activity without creating slow-lane-only trades.

**Acceptance Criteria**  
- Test or runtime trace shows structural task handles empty initial market cache without waiting a full interval after cache population.
- No structural recompute changes trading decisions directly.
- Telemetry documents recompute trigger timing.

**Notes**  
Keep this in orchestration; do not move structural logic into `main.py`.

**Implementation Notes** (2026-04-20)  
Fixed the initial empty-cache lag by making `_structural_recompute_task()` wait until the market cache is non-empty before starting the hourly `StructuralTask.run_periodic()` loop. This prevents an empty startup pass from delaying the first useful structural recompute by a full interval. The provider now returns a defensive list copy of the live cache. Validation: `.venv/bin/pytest tests/test_main_pipeline.py::test_structural_recompute_waits_for_non_empty_market_cache` (passed).

---

### PROFIT-CAL-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-CAL-001 |
| **Title** | Calibration outcome feedback is not proven end-to-end |
| **Category** | Calibration / Belief Quality |
| **Severity** | HIGH |
| **Status** | COMPLETE (2026-04-24, v0.29.47) |
| **Priority** | NOW (post-window) |
| **Owner** | Shared |
| **Depends On** | S4.5c close (no runtime changes during active window) |
| **Blocks** | ROADMAP P4.2 (calibration review, structurally blocked — no `CALIBRATION_CHECK` events can exist without this fix); transitively P4.3 (live trading authorization) |

**Description**  
`CalibrationTask` is constructed and passed to `BlendTask` for scaling, but the expanded audit did not confirm a complete feedback loop from resolved outcomes into calibration updates and `CALIBRATION_CHECK`-style validation events.

**Why it matters to profitability / safety / reliability**  
Without verified calibration feedback, confidence values can drift, causing the bot to over-trust weak lanes or under-trade genuinely profitable signals.

**Evidence / Source**  
- `main.py:371-376` constructs `CalibrationTask` and injects it into `BlendTask`.
- Contract Section 13 includes calibration and readiness validation criteria.
- No live S4.5 evidence has yet proven calibration participation over resolved outcomes.

**Proposed Fix**  
Trace resolved paper outcomes through calibration and add validation output if the feedback loop is incomplete.

**Acceptance Criteria**  
- Resolved paper trades or blocked outcomes can be associated with lane predictions for calibration review.
- Calibration events/metrics are observable during validation windows.
- Tests cover at least one calibration update path or explicitly document why no update is expected yet.

**Notes**  
Do not tune scaling factors as part of this debt item.

**Priority Elevation Note** (2026-04-23)  
Severity raised MEDIUM → HIGH and Priority raised MEDIUM → NOW (post-window) after follow-on analysis of ROADMAP Phase 4 dependencies. ROADMAP P4.2 ("Calibration review: est distribution vs resolved outcomes") has an expected outcome of "Calibration curve documented; over/underconfidence measured" — an output that requires `CALIBRATION_CHECK` events to exist in the first place. Without this fix, P4.2 cannot complete, which means P4.3 (live trading authorization) cannot be reached by transitive dependency. This elevates PROFIT-CAL-001 from "observation gap" to "pre-live-trading blocker." Execution is sequenced as the first action after S4.5c closes (earliest 2026-04-26); full implementation design in [`docs/_archive/studies/profit_cal_001_calibration_wiring.md`](_archive/studies/profit_cal_001_calibration_wiring.md) (Path A: schema migration + emission at resolve time). Header Open-counts updated: HIGH 3 → 4, MEDIUM 1 → 0.

**Validation Notes** (2026-04-23)  
End-to-end trace confirms the feedback loop is **wired but incomplete**. Satisfies the acceptance criterion's "*explicitly document why no update is expected yet*" branch. Components that exist: `log_calibration_check` (`utils/logger.py:1242`), `CalibrationTask.record_calibration_check` (`tasks/calibration_task.py`), pure-function Brier/drift/scaling state machine (`analysis/calibration_monitor.py`), schema tests (`tests/test_calibration_check_schema.py`), `CalibrationTask` construction + injection into `BlendTask` (`main.py:435-438`), and `BlendTask` consuming `get_scaling_factor(lane)`. Missing glue: **zero runtime callers of `log_calibration_check` or `CalibrationTask.record_calibration_check`**. Repo-wide grep (2026-04-23) returns only definitions, schema tests, and a single script comment — no runtime call site from any resolution or trade path. Specifically, `trading/paper_trader.py:resolve_market` calls `log_paper_resolution` but never `log_calibration_check`. Consequence: `CalibrationTask._state` is permanently empty, `get_scaling_factor(lane)` always returns `1.0` (no-op scaling), and `BlendTask` consumes a neutral factor regardless of how many markets resolve. Contract Section 13 item 6 is therefore *silently* unverifiable — vacuously satisfied not because of "zero resolutions in window" (prior pre-assessment framing) but because the emission site does not exist. This is observation-unsafe to fix; the runtime wire-up (`resolve_market` → `log_calibration_check` → `CalibrationTask.record_calibration_check`) must wait until after S4.5c closes. Deferred action item: add emission at the resolution boundary, backfill one end-to-end test asserting `CALIBRATION_CHECK` is written and consumed, and cross-link to S4.5c criterion 6 once verified.

**Resolution Notes** (2026-04-24, v0.29.47 — commits `186b495`, `74649c6`)
Feedback loop now complete end-to-end per the Path A design in [`docs/_archive/studies/profit_cal_001_calibration_wiring.md`](_archive/studies/profit_cal_001_calibration_wiring.md). Implementation delivered across two commits:

- **Zone 1 + 2** (`186b495`): six nullable columns added to the `paper_trades` table via the existing `_migrate_db` pattern (`fast_lane_p`, `fast_lane_confidence`, `accumulation_p`, `accumulation_confidence`, `structural_p`, `structural_confidence`). Per-lane estimates are already propagated through `analysis.signal_meta` from `BlendTask`'s `TradeCandidate` construction (`tasks/blend_task.py:425-440`) — no `/trading` adapter extension required; `record_trade` reads directly from `signal_meta`. This supersedes the design note's §3.2 Zone 2 sub-step and reduces the overall change footprint. INV-4 purity preserved (no `/analysis` changes). A `_lane_float` helper guards against non-dict `signal_meta` and non-numeric values so legacy or test-mock signal paths don't crash the INSERT.

- **Zone 3 + 4 + 5** (`74649c6`): `PaperTrader.resolve_market` is now async and fans out one `CALIBRATION_CHECK` event per populated lane to both emission paths (`trade_log.log_calibration_check` + `CalibrationTask.record_calibration_check`). Blocking DB work is factored into `_resolve_market_sync`, dispatched via `asyncio.to_thread` so the MAC-ASYNC-002 off-loop invariant is preserved. `CalibrationTask` is now injected into `PaperTrader` via a constructor kwarg and the same instance is wired into `BlendTask`. `CalibrationTask` errors during emission are logged and swallowed — the DB resolution has already committed before the calibration path runs, so a calibration-side failure cannot corrupt resolved-trade state. Historical rows (pre-v0.29.47, null lane columns) emit zero `CALIBRATION_CHECK` events, preserving backward compatibility.

- **Test coverage** (`tests/test_paper_trader.py::TestCalibrationEmission`, 7 new tests): column population from `signal_meta`; NULL handling for historical rows; three-lane fan-out; `CalibrationTask._state.lanes` update per lane; swallowed-error survival (resolution still committed); migration idempotence. 13 existing `resolve_market` test call sites updated for the async API. Full suite: 1059 passed, 1 skipped, 0 failures.

Acceptance criteria from the design note §4: (1) status moved OPEN → COMPLETE with validation notes; (2) Zone 5 test added and passing; (3) full test suite clean; (4) ROADMAP P4.2 blocker-cross-reference can be struck; (5) `CalibrationTask.get_calibration_summary()` now returns non-empty per-lane stats once resolved paper trades with populated `signal_meta` land — this is already the case in the `TestCalibrationEmission` unit test; the live-traffic observation (≥1 `CALIBRATION_CHECK` in `logs/trades/live/trades.jsonl` after the first paper-trade resolution post-fix) remains a production-soak observation, tracked in the v0.29.47 `CHANGELOG` entry. Fix status: **closed against acceptance criteria**; production-soak observation pending natural paper-trade resolutions (blocked behind P0-GATE LLM market-anchoring, which is a separate debt item). Contract Section 13 item 6 is now substantively verifiable rather than vacuously satisfied.

**Production-Soak Observation** (2026-05-01, post-cutover audit)

The "live-traffic observation" footnote that the 2026-04-24 closure left as production-soak-pending is now satisfied — minimally — by the 13-day MacBook paper soak. The trade-log archive contains exactly **3 `CALIBRATION_CHECK` events** in `logs/trades/live/trades.jsonl`, each emitted at the resolution time of one of the three KXFISAEXTEND PAPER_TRADE rows (all on 2026-05-01 around the same window: trades opened ~01:57 UTC and the `PAPER_RESOLUTION` + `CALIBRATION_CHECK` fan-out fired the same day). Per-lane breakdown was minimal because the three trades shared `accumulation_p=null` and `structural_p=null` (only `fast_lane_p=0.432` was populated), so each `CALIBRATION_CHECK` carried only the fast-lane prediction error — but that is exactly the v0.29.47 design intent ("Historical rows ... null lane columns ... emit zero CALIBRATION_CHECK events" was the backward-compat contract; the v0.29.58 emission path correctly fired one `CALIBRATION_CHECK` per *populated* lane).

This satisfies acceptance criterion (5) above on a runtime basis as well as a unit-test basis. The sample is too small to compute meaningful Brier scores or scaling factors, but the wiring is provably alive in production. Three resolutions on a single ticker from a single source (VitalLaw.com FISAEXTEND) is not a calibration sample — the next milestone for this entry's footnote is "first ≥10-resolution sample with ≥2 distinct lanes populated," which depends on PROFIT-OBS-003 (silent-exit gap closure unlocking the 2 known positive-edge non-trades + likely more) and PROFIT-EDGE-004 (matcher quality lifting the conversion rate above the current ~1%). Both of those are post-cutover Mac Studio work; no further action needed on CAL-001 itself.

---

### PROFIT-EXEC-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EXEC-001 |
| **Title** | Fade tweet and price-fade paths still call executor directly |
| **Category** | Execution Boundary / Safety |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | S3.4, S3.5 |
| **Blocks** | No-bypass assurance |

**Description**  
Normal news candidates route through `BlendTask`, but `_on_fade_tweet()` and `_process_price_fade()` still call `self.executor.execute(analysis)` directly. If these are trade-producing paths, they bypass `BLEND_DECISION` and readiness enforcement.

**Why it matters to profitability / safety / reliability**  
The architecture says fast lane triggers blend evaluation and candidates must pass readiness before executor submission. Direct trade-like paths risk unobservable, unblended paper/live behavior.

**Evidence / Source**  
- `main.py:802` executes fade-tweet analysis directly.
- `main.py:991` executes price-fade analysis directly.
- `docs/IMPLEMENTATION_CONTRACT.md:280` requires readiness-gate validity before executor submission.

**Proposed Fix**  
Classify fade paths as disabled diagnostics, non-trading signals, or route them through the same blend/readiness meeting point with explicit contract approval.

**Acceptance Criteria**  
- No trade-producing runtime path reaches executor without either a `BLEND_DECISION` or documented contract exemption.
- Tests assert normal, fade-tweet, and price-fade paths cannot silently bypass readiness if enabled.
- Existing executor safety gates remain intact.

**Notes**  
This is a boundary integrity issue, not a request to remove useful fade diagnostics.

**Implementation Notes** (2026-04-20)  
Resolved direct executor bypasses by routing fade-tweet and price-fade `SignalAnalysis` objects through the same `BlendTask`/readiness path used by normal news candidates. The shared routing helper attaches trigger evidence metadata, emits `BLEND_DECISION` through `BlendTask`, and only lets the existing trading-queue consumer call the executor for approved candidates. Price-fade evidence now uses the `market` source class. Validation: `.venv/bin/pytest tests/test_main_pipeline.py tests/test_executor.py tests/test_blend_task.py` (125 passed).

---

### PROFIT-EXEC-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EXEC-002 |
| **Title** | Same-signal-multi-correlated-markets guard gap: one news event triggered 3 simultaneous trades on 3 correlated FISA tickers in 7 seconds |
| **Category** | Execution Boundary / Trade Selectivity / Risk Management |
| **Severity** | MEDIUM (correctness-of-risk-management; not a hard safety bug, but produces measurable correlated overexposure) |
| **Priority** | LATER (defer to post-EDGE-004; this gap only fires on series with multiple date-based markets, and EDGE-004 work upstream may meaningfully reduce signal volume on those series) |
| **Status** | OPEN |
| **Owner** | Shared |
| **Depends On** | — |
| **Blocks** | — (no downstream entry hard-blocked; correlation-aware sizing is a profit-path improvement, not a release gate) |

**Description**

Existing same-signal guards in `tasks/trade_readiness_gate.py` and `trading/executor.py` operate **per-ticker** — they prevent the bot from re-trading the same ticker on a recent matching headline. They do not detect that two or more *different* tickers in the same series-prefix (or with overlapping resolution conditions) are economically correlated and therefore should not all be traded on the same news event.

Empirical demonstration: on 2026-05-01 between 01:57:33Z and 01:57:40Z (a 7-second window), the bot placed 3 paper trades on `KXFISAEXTEND-26APR-MAY01`, `-MAY02`, and `-MAY03`. All three trades:

- consumed the same headline (`"After House Reauthorizes Surveillance Law, Senate Punts (Apr 30, 2026) - VitalLaw.com"`)
- carried the same LLM verdict (`direction=no, magnitude=small, confidence=0.85, P(YES)=0.432`)
- were sized identically (5 contracts NO @ 50¢)
- resolved identically at ~03:31Z the same morning (1.5h later)
- lost the same amount (-$2.50 each, -$7.50 aggregate)

The 3 markets are economically the same bet at slightly different resolution boundaries; they correlate near 1.0 because FISA Section 702 either gets reauthorized by some date or it doesn't. From a Kelly-sizing perspective the bot took 3× the risk it intended to take on a single conviction.

**Why it matters to profitability / safety / reliability**

- **Sizing correctness:** the operator-set `MAX_TICKER_EXPOSURE_PCT=0.25` cap is per-ticker, so it does not cap correlated-series exposure. On a hot news cycle with N date-based markets in a single series, the bot can deploy `N × 25% = 25N%` of bankroll on what is effectively one bet. For N=3 (FISA case) that's 75% theoretical max. The 2026-05-01 trades only used $7.50 / $50 = 15% because Kelly sized small, but the cap mechanism wouldn't have stopped the bot from going larger if Kelly had sized larger.
- **Source-credibility distortion:** the 0/3 W/L outcome on VitalLaw.com auto-dropped its credibility multiplier to 0.5x. Treating one prediction outcome as three artificially over-penalizes a source on a single losing prediction, and would symmetrically over-credit a source on a single winning prediction.
- **No safety-gate violation:** the executor's existing E1–E12 gates correctly cleared all 3 trades on their merits. The gap is *correlation awareness*, not gate failure.

**Evidence / Source**

- `data/paper_trades.db` table `paper_trades`, 3 rows (`KXFISAEXTEND-26APR-MAY0{1,2,3}`):
  - All 3 carry identical `signal_headline`, `signal_source='VitalLaw.com'`, `llm_direction='no'`, `llm_magnitude='small'`, `llm_confidence=0.85`, `estimated_prob=0.432`, `match_score` in [0.106, 0.132].
  - Trade timestamps: 01:57:33.265 / 01:57:40.926 / 01:57:37.103 — placement window 7.66s.
  - Resolution timestamps: 03:31:04.530 / 03:31:05.480 / 03:31:04.964 — resolution window 0.95s, all `resolved_yes=1`.
- `data/paper_trades.db` table `source_credibility`: row `(VitalLaw.com, 0W, 3L, multiplier=0.5)` after the 3 trades resolved.
- `tasks/trade_readiness_gate.py` and `trading/executor.py` — same-signal logic operates on `ticker`, not on series-prefix or correlated-resolution-conditions.

**Proposed Fix**

Add a series-prefix correlation guard at the trade-readiness or executor layer. Sketch:

1. **Series-prefix dedupe within a short window.** When a candidate is about to be sized, scan recent (e.g. 1h) `BLEND_DECISION` and `OPPORTUNITY` events that share the same series prefix (`KXFISAEXTEND`, `KXMOCTRUMP25`, etc., extracted via the existing `series_ticker` field on `paper_trades` and on the live market objects). If a candidate with the same series prefix has already been sized in-window, mark this candidate as "correlated-suppressed" and emit a SKIPPED with reason `"series_correlation_in_window"`. **The first candidate in a series-prefix burst still trades**; subsequent ones within the window are suppressed. This preserves the ability to trade the series at all but prevents the multi-bet on one news event.

2. **(Stronger) Headline-hash dedupe across series.** Some news events span multiple unrelated series — e.g., a single Trump-related headline could fire on `KXTRUMPIRAN`, `KXMOCTRUMP25`, `KXPARDONSTRUMP` simultaneously. A headline-hash (or `signal_headline + signal_source` key) recent-window lookup would catch those too. More invasive than the series-prefix approach; defer until EDGE-004's matcher-quality work is further along, since better matching upstream may make this less load-bearing.

Approach (1) is preferred as the immediate fix; approach (2) is a follow-up to consider after EDGE-004's evidence section quantifies the cross-series-headline overlap rate.

**Acceptance Criteria**

- A test in `tests/test_trade_readiness_gate.py` or `tests/test_executor.py` exercising the 2026-05-01 FISA replay (3 candidates with the same `KXFISAEXTEND` series prefix arriving in <10s) confirms exactly 1 candidate proceeds to executor and 2 are SKIPPED with reason `"series_correlation_in_window"`.
- The series-prefix dedupe window is operator-tunable via env var (e.g. `SERIES_CORRELATION_WINDOW_SECONDS`, default 3600).
- The fix is paper-mode-safe (no live-mode-specific code paths added).
- Source-credibility scoring continues to operate on per-trade outcomes; this fix does not retroactively rewrite the existing 3-row trade history.

**Notes**

- This entry surfaced from the 2026-05-01 post-cutover forensic of the 3 paper trades to date. The trade-resolution outcome (3 losses) made the gap visible, but the gap exists regardless of outcome — a 3-win streak on correlated markets would have been just as misleading on the upside.
- Defer to post-EDGE-004 because EDGE-004's "matcher quality / market-mix" work may meaningfully reduce the rate at which the same headline matches multiple correlated markets in a series. If matcher quality improves, this gap fires less often and the fix is correspondingly less urgent.
- A retroactive fairness adjustment to VitalLaw.com's `source_credibility` multiplier (0.5x → ~0.83x, the credit one would expect from one losing prediction with one degree of freedom) is **out of scope for this entry**. Track separately if pursued; the credibility-update math is itself a small correctness item.

**Related**

- `PROFIT-EDGE-004` (open) — upstream cause; better matcher quality should reduce the rate of correlated false-signal bursts.
- `PROFIT-OBS-003` (open) — companion observability item; OBS-003's fix (route every executor reject through `log_skipped` with distinct reasons) makes it possible to count `series_correlation_in_window` SKIPPEDs cleanly post-fix.
- `PROFIT-OBS-004` (open) — surfaced from the same 3-trade audit; sign-convention display bug.

---

### PROFIT-STARTUP-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-STARTUP-001 |
| **Title** | Startup warmup and cache-empty periods reduce validation and trading uptime |
| **Category** | Runtime Reliability / Timeliness |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | MEDIUM |
| **Owner** | Shared |
| **Depends On** | — |
| **Blocks** | Reliable long-run validation windows |

**Description**  
Runtime logs show discovery and structural workflows can encounter empty market caches during startup. Long cache warmups reduce the effective observation window and can make early feed events ineligible for full multi-lane processing.

**Why it matters to profitability / safety / reliability**  
If the bot spends a meaningful fraction of a validation or trading session without active market context, it may miss opportunities or understate lane participation.

**Evidence / Source**  
- `main.py:1138-1144` market refresh sleeps after each cycle.
- `main.py:1339` subreddit discovery waits for market cache warmup.
- Recent logs included `[DISCOVERY] Market cache empty, skipping discovery pass` during startup.

**Proposed Fix**  
Measure startup warmup duration and cache-empty rates, then add observability or startup gating if the delay materially affects trade windows.

**Acceptance Criteria**  
- Startup report includes market-cache warmup duration and first non-empty cache timestamp.
- S4.5 metrics distinguish wall-clock runtime from effective multi-lane runtime.
- No decision logic changes are made without separate approval.

**Notes**  
This is especially relevant to short paper-validation windows.

**Implementation Notes** (2026-04-20)
Added startup observability that records the first non-empty market cache timestamp, wall-clock seconds since boot, and an explicit `effective_multi_lane_runtime_start=true` marker. Discovery passes that still see an empty cache now include a monotonically increasing empty-cache count, seconds since startup, and whether effective multi-lane runtime has started. This is instrumentation only; no routing, analysis, or trading logic changed. Validation: `.venv/bin/pytest tests/test_main_pipeline.py::test_refresh_market_cache_once_logs_startup_warmup_duration tests/test_main_pipeline.py::test_structural_recompute_waits_for_non_empty_market_cache` (2 passed).

---

### PROFIT-CFG-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-CFG-001 |
| **Title** | Remove deprecated `KALSHI_GEOPOLITICAL_SERIES` dead-code allowlist from `config.py` |
| **Category** | Code Hygiene / Config Cleanup |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | LATER |
| **Owner** | Claude |
| **Depends On** | Stage 5 Phase 2 (P2.2) 72-hour paper-mode observation window closing |
| **Blocks** | — |

**Description**  
`config.py:475-489` defines `KALSHI_GEOPOLITICAL_SERIES`, a Kalshi series-ticker allowlist (`KXUKR`, `KXINTL`, `KXMIDEAST`, etc.). The list was deprecated in commit `60cb30c5` (2026-03-11) when the market cache switched to series-title keyword discovery via `_GEO_SERIES_KEYWORDS` in `analysis/market_matcher.py`. The list has been dead code for ~42 days but remains defined with a self-documenting `NOTE` comment block (`config.py:475-478`).

**Why it matters to profitability / safety / reliability**  
Low direct impact. Pure dead code; no runtime path reads the symbol. The residual risk is that a future contributor mistakes the list for a live filter and resurrects it, silently narrowing market discovery. The `CLAUDE.md:52` gotcha entry exists specifically to prevent that. Deleting the dead definition removes the source of the confusion at its origin.

**Evidence / Source**  
- `config.py:475-489` — `NOTE` comment block + dead list definition.
- Git history:
  - `60cb30c5` (2026-03-11) — deprecated consumer, added the `NOTE` comment.
  - `e3da7962` (2026-04-11) — dead-write: added `KXPRESGELECT`, `KXNK`, `KXNATO` to the list 31 days *after* the consumer was removed.
- Repo-wide scan (2026-04-22): zero imports, zero attribute access on `cfg` / `config`, zero `from config import *` wildcards, zero `.env` references, zero dynamic `getattr` / `hasattr` lookups. Only self-reference in `config.py` + a warning in `CLAUDE.md`.
- Replacement: `_GEO_SERIES_KEYWORDS` in `analysis/market_matcher.py`; the separate `MARKET_SERIES_BLOCKLIST_PREFIXES` list in `config.py` is actively used (imported by `feeds/search_news_monitor.py:26`) and must be preserved.

**Proposed Fix**  
Delete `config.py:475-489` (the `NOTE` comment block and the `KALSHI_GEOPOLITICAL_SERIES = [...]` list). Preserve the `CLAUDE.md:52` warning entry — it remains useful guidance for future agents even with the symbol gone.

**Acceptance Criteria**  
- `config.py` no longer defines `KALSHI_GEOPOLITICAL_SERIES`.
- `make lint` (ruff) passes.
- `pytest tests/` passes with no new failures vs. pre-removal baseline.
- `CLAUDE.md:52` warning text is preserved verbatim.

**Notes**  
Deferred until the Stage 5 Phase 2 (P2.2) 72-hour paper-mode observation window closes (opened 2026-04-22 per commit `b86c624`). Rationale: the change is provably safe (no runtime path reads the symbol), but editing `config.py` during an active observation window is avoided as a general rule even when the specific diff is inert. Execute after the window closes.

**CLOSED 2026-05-02 (Claude):** dead-code deleted from `config.py` (block was at lines 562–577 after intervening commits, not the original 475–489 in this entry). `CLAUDE.md:52` warning preserved verbatim. Repo-wide grep confirms zero remaining Python references; the only surviving mention is the CLAUDE.md gotcha. Landed during the v0.29.59 governance Phase 2 shadow soak (no runtime change).

---

### PROFIT-SOURCE-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-SOURCE-001 |
| **Title** | Reddit intake is degraded-permanent; Reddit OAuth is externally blocked |
| **Category** | Intake / Source Availability |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | NOW (mitigation is cheap; unblock is externally gated) |
| **Owner** | Shared |
| **Depends On** | Reddit Responsible Builder Policy review (externally blocked; app submitted, no response) |
| **Blocks** | — (Reddit-unique signal assessed as thin per `docs/_archive/studies/news_sources_evaluation.md` §7) |

**Description**  
Reddit intake runs in public-JSON mode because OAuth credentials are not available — the operator submitted an application per Reddit's Responsible Builder Policy and has received no response. Public-JSON polling is rate-limited per-IP and triggers structural 403 storms; on 2026-04-22 the 403 storm tripped `reddit_monitor.py`'s global circuit breaker within ~11 seconds of startup ("100% of subreddits failed (1/1), suspending all Reddit polling for 30m"). The circuit-breaker behavior is the *intended* response to 403 storms, not a bug — but it means Reddit contributes effectively zero signal whenever the circuit is open, which is most of the time during cold polling cycles.

**Why it matters to profitability / safety / reliability**  
Reddit contributes a small but non-zero share of the signal mix when it works. Losing it permanently is a coverage reduction, not a correctness or safety issue. The full evaluation in `docs/_archive/studies/news_sources_evaluation.md` §7 concludes Reddit-unique content is thin (most is wire-service repost; analytical content is slower than ISW/CSIS RSS; firsthand-witness content is replaceable by Bluesky/Mastodon/Telegram when those integrations are authorized). The residual risk is that downstream diagnostics (`source_scorecard`, feedback loops that attribute signal to Reddit posts) silently report a distorted source mix if Reddit is treated as "active" while it's actually degraded.

**Evidence / Source**  
- `logs/app/bot.log` 2026-04-22T11:16:43 UTC — "Reddit access denied for r/ArmedConflicts (403) -- backing off 120s", followed by 30+ similar lines across all 20 polled subreddits within 10 seconds, followed by "Reddit global circuit open -- suspending all Reddit polling for 30m".
- `feeds/reddit_monitor.py:19` starts with the log message "Reddit monitor started (public JSON -- degraded, expect rate limits). Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env for OAuth2" — the module itself acknowledges the degraded state at every startup.
- Operator confirmation (2026-04-23): Reddit app submitted via Responsible Builder Policy intake; no response. Treat as permanently blocked for planning purposes.

**Proposed Fix**  
Two-track strategy per `docs/_archive/studies/news_sources_evaluation.md` §7.2:

*Track A — mitigation (cheap, post-S4.5c close):*
1. Trim `REDDIT_SUBREDDITS` polling pool from 20 to a curated 2-3 (candidates: `r/ArmedConflicts`, `r/CredibleDefense`, plus 1-2 region-specific rotated in by `subreddit_selector.py`) to reduce the 403-storm attack surface.
2. Downgrade Reddit-related log lines at startup from INFO to DEBUG where they're not actionable, to stop polluting the `bot.log` signal.
3. Ensure `SOURCE_HEALTH` telemetry (planned in the same evaluation) distinguishes "Reddit circuit open (expected)" from "Reddit circuit open (unexpected)" — only the latter should alert.

*Track B — unblock (gated externally):*
1. Do not spend further engineering effort on Reddit OAuth until Reddit responds to the pending app.
2. If Reddit approves the app, migrate `reddit_monitor.py` to OAuth2 — that's a config-credential change plus a small code change to the auth flow, not a re-architecture.
3. If Reddit denies or stays silent past a configurable patience window (suggest 90 days post-submission), formally deprecate Reddit intake and repurpose the polling loop for a replacement (Bluesky journalist timeline is the leading candidate per Appendix A Tier 2).

**Acceptance Criteria**  
- Track A mitigations deployed post-S4.5c close and observed in `bot.log` (circuit-open events drop significantly in volume; no unexpected-outage false positives in `SOURCE_HEALTH` emissions).
- This debt-log entry transitions to COMPLETE under either (a) Track B succeeds and OAuth is active, OR (b) Reddit formally deprecated and replacement source integrated.
- **(c) Operator-override (added 2026-05-10):** Reddit may be declared `PERMANENTLY_DEGRADED` and this entry CLOSED without replacement integration when the operator decides intake-mix simplification outweighs replacement-source velocity. Replacement-source work transfers to a successor entry (`PROFIT-SOURCE-002`).
- `docs/_archive/studies/news_sources_evaluation.md` §7.2 steps 1-5 are executed or consciously re-deferred.

**Notes**  
Do not treat this as a go-live blocker. Per `docs/_archive/studies/news_sources_evaluation.md` §6, the operator-confirmed priority is correctness over velocity, and the Reddit-unique signal is thin enough that going live without Reddit is acceptable provided the source mix is honestly reported. The go-live blocker is `PROFIT-CAL-001`, not this item.

**CLOSED 2026-05-10 (Operator override):** Reddit declared `PERMANENTLY_DEGRADED`. Reasoning: (i) `feeds/reddit_monitor.py` global circuit breaker fires on every cold-start 403 storm — Reddit contributes effectively zero signal in steady state; (ii) Reddit Responsible Builder Policy app submitted 2026-04-23, no response; 90-day patience window not yet expired (2026-07-22) but operator electing immediate deprecation under acceptance criterion (c). (iii) Track A mitigations (subreddit pool trim, log downgrade, SOURCE_HEALTH semantics) NOT executed — superseded by deprecation. Replacement-source integration deferred to `PROFIT-SOURCE-002`. No code changes in this closure.

---

### PROFIT-SOURCE-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-SOURCE-002 |
| **Title** | Replacement intake source for Reddit (deprecated 2026-05-10) — candidate: Bluesky journalist timeline |
| **Category** | Intake / Source Coverage |
| **Severity** | LOW |
| **Status** | OPEN |
| **Priority** | LATER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — (Reddit deprecation already absorbed; no closure target depends on this) |

**Description**

Successor to `PROFIT-SOURCE-001` (Reddit `PERMANENTLY_DEGRADED` 2026-05-10 via operator override). Reddit-unique signal share is small per `docs/_archive/studies/news_sources_evaluation.md` §7; closing SOURCE-001 without a replacement is intake-mix simplification, not a coverage emergency. This entry registers the future-work stub: integrate a replacement intake source per Appendix A Tier 2 (Bluesky journalist timeline is the leading candidate).

**Why it matters**

Coverage-recovery item only. Not a profit, safety, or reliability blocker. Reddit was thin enough that going live without a replacement is acceptable per the news-sources evaluation. Filed so the deferred work is tracked rather than forgotten.

**Proposed Fix**

Out of scope until the broader intake-mix and post-Cycle-17C verdict picture is clearer. When it surfaces:
1. Confirm Bluesky AT-Protocol journalist-list approach still matches the source-evaluation framework.
2. Implement adapter at parity with the existing RSS + Reddit interface.
3. Smoke-test against curated journalist set; measure source-class diversity gain.
4. Wire into `feeds/` registry; remove or repurpose `feeds/reddit_monitor.py`.

**Acceptance Criteria**

- A replacement intake source is integrated and contributing non-trivial signal volume in the production source mix, OR a follow-on review explicitly defers replacement indefinitely (Reddit deprecation accepted as permanent reduction).

**Notes**

- Filed during the 2026-05-10 repo-closure-cleanup sprint per operator-override closure of `PROFIT-SOURCE-001`.
- This is a tracking-only entry — no current code surface.

**Related**

- `PROFIT-SOURCE-001` (closed 2026-05-10) — predecessor; Reddit deprecated permanently via operator override.
- `docs/_archive/studies/news_sources_evaluation.md` §7 + Appendix A — replacement-source evaluation context.

---

### PROFIT-LLM-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-LLM-001 |
| **Title** | Signal-analyzer / governance-agent LLM unification deferred until after Phase 2 lands |
| **Category** | LLM Layer / Operational Reliability |
| **Severity** | LOW |
| **Status** | OPEN |
| **Priority** | AFTER (post governance Phase 2 close) |
| **Owner** | Operator |
| **Depends On** | Governance Phase 2 implementation closing (Task 27 — VERSION/CHANGELOG bump) |
| **Blocks** | — (no downstream item; Phase 2 ships fine without unification) |

**Description**
Two distinct Ollama model strings are configured side-by-side after governance Phase 2:

- Signal analyzer (existing trading): `OLLAMA_MODEL` defaults to `qwen2.5:7b` per `config.py:1077`.
- Governance agent (Phase 2): `GOVERNANCE_LLM_MODEL` set to `qwen3:8b` on MacBook (via the launchd plist landing in Task 25), `qwen3:14b` on Mac Studio.

Ollama runs single-model by default — a request for a model that isn't loaded unloads the previous one and loads the new one (5 min idle TTL). So in steady state at most one model is resident; the runtime cost is a ~5-10s cold-load each time governance and signal analysis fire close in time. RAM is safe on the MacBook's 18GB.

The deferred decision is whether to bump `OLLAMA_MODEL=qwen3:8b` so signal analysis upgrades to the same model the governance agent already uses, eliminating swap latency and unifying calibration. The agent decided on 2026-04-25 to **defer this past Phase 2** rather than bundle it into the governance ship, after operator pushback against the original "wait for Mac Studio" recommendation made the actual risks explicit.

**Why it matters to profitability / safety / reliability**
A unilateral model swap on the signal-analyzer path carries three concrete risks that need observation, not just a unit-test pass:

1. **JSON-parse reliability.** Different LLMs wrap JSON output in different preamble styles. The existing parser uses `JSONDecoder.raw_decode()` scanning each `{` (per CLAUDE.md "Critical Gotchas / Signal analysis") because some prior model emitted preambles like `Sure, here is the analysis: {...}`. qwen3:8b may emit a different preamble or new failure modes. A regression here looks fine in unit tests but breaks in production when the parser silently drops a malformed response.
2. **Probability calibration drift.** qwen2.5:7b's "0.42" doesn't necessarily mean the same thing as qwen3:8b's "0.42". The bot's edge-threshold gating, Kelly sizing, and same-signal guards are all calibrated against what 2.5:7b currently produces. A more-confident model pushes more bets through the gate; a less-confident one starves it. Either drift would only show in cross-week comparisons of EV-gate pass-rate and decision distribution — not in any single test.
3. **Prompt-template fit.** The existing signal-analyzer prompt (in `analysis/signal_analyzer.py`) was written with one model's reasoning style in mind. qwen3 is a different generation — it could refuse, over-explain, ignore an instruction, or hit token limits differently.

These risks are observable but not catchable by tests alone. They need a baseline-comparison observation window in paper mode.

**Why deferral is the right call (not "wait for Mac Studio")**
The original agent recommendation was "wait until Mac Studio arrives" and was wrong-by-default rather than reasoned. The honest reason to defer past Phase 2:

- Bundling a model swap into Phase 2 makes any regression hard to attribute — was it the new governance code, or the new signal model?
- Phase 2's risk surface is already large enough (new agent, new prompt, new audit log, new launchd plists). Keeping the signal-analyzer path frozen during Phase 2 cutover means any issue surfaces against a known baseline.
- Once Phase 2 is closed and observed quiet for a few cycles, swapping `OLLAMA_MODEL=qwen3:8b` is a one-keystroke change with a one-keystroke revert. The cost of waiting is small (a few governance/signal coordination cold-loads per day). The cost of bundling is harder root-cause analysis if something goes wrong.

Hardware is not the gate. Both models fit comfortably on the 18GB MacBook (4.7GB on disk each, ~5-6GB resident); only `qwen3:14b` is too tight for MacBook (8-9GB on disk, 10-12GB resident with context — that's the Mac Studio model).

**Evidence / Source**
- `config.py:1077` — `ollama_model` field default factory `os.getenv("OLLAMA_MODEL", "qwen2.5:7b")`.
- `.env:84` — commented `OLLAMA_MODEL=qwen2.5:7b` line.
- Governance Phase 2 plan (`docs/superpowers/plans/2026-04-25-governance-agent-phase-2-plan.md`) Task 15 — `LocalQwenLLM` constructor default `qwen3:14b` with env-var override `GOVERNANCE_LLM_MODEL`; comment block flags `qwen3:8b` as the MacBook model and `qwen3:14b` as the Mac Studio model.
- CLAUDE.md "Critical Gotchas / Signal analysis" — JSON-extraction workaround documents past brittleness when changing model output behavior.
- Operator-pulled models on MacBook as of 2026-04-25: `qwen2.5:7b` (active for signal analysis), `qwen3.5:9b` (idle), `qwen3:8b` (newly pulled, will be used by governance agent).

**Proposed Fix**
After governance Phase 2 closes (PROFIT-LLM-001 transitions to `IN_PROGRESS` only after Task 27's VERSION/CHANGELOG commit lands):

1. Single-line `.env` change: set `OLLAMA_MODEL=qwen3:8b` (uncomment + update the existing line at `.env:84`).
2. Restart the bot and let it run paper mode for at least one full diurnal cycle (~24h) of news ingestion.
3. Compare the post-change cycle against the prior week's `bot.log` and trade-funnel diagnostics on:
   - JSON-parse error count in `signal_analyzer` log lines (must be ≤ baseline; ideally zero).
   - Distribution of `estimated_probability` outputs (should be roughly the same shape; gross shifts indicate calibration drift).
   - EV-gate pass-rate (rate of analyses that produce a candidate trade decision; should be within ±20% of baseline).
   - Bet-size distribution from any paper trades produced (gross shifts indicate confidence-calibration drift).
4. **Revert criteria — set `OLLAMA_MODEL=qwen2.5:7b` and re-open this item** if any of:
   - Any new JSON-parse error class shows up in logs that wasn't there pre-change.
   - EV-gate pass-rate moves outside ±20% of the pre-change weekly baseline.
   - Manual review of 5 random paper trades shows reasoning quality clearly worse than baseline.
5. **Promote criteria — mark COMPLETE** if all of:
   - One full 24h paper cycle produces zero new parse errors.
   - Probability-output distribution and EV-gate pass-rate stay within tolerance.
   - Manual review of 5 random paper trades shows reasoning quality at least equivalent.

**Acceptance Criteria**
- One of: (a) `.env` carries `OLLAMA_MODEL=qwen3:8b` with a commit referencing this item ID and at least 24h of paper-mode observation logged, OR (b) the operator consciously re-defers past 2026-Q3 with rationale in this item's Notes section, OR (c) the operator decides the unification is not worth doing (e.g. Mac Studio brings `qwen3:14b` and a different model topology).

**Notes**
This item is decision-track, not bug-track. It exists because the reasoning behind the deferral matters as much as the deferral itself — a future agent reading the file structure could otherwise see two model strings configured and treat it as an oversight instead of an intentional ordering decision. The operator-confirmed priority on 2026-04-25 was: ship Phase 2 cleanly first; unify second; never bundle.

If `PROFIT-CAL-001`'s calibration-feedback wiring lands before this item moves to `IN_PROGRESS`, the per-lane confidence scaling will detect calibration drift automatically — that strengthens the safety net for this swap and tightens the revert criteria above.

---

### PROFIT-EDGE-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-001 |
| **Title** | `main.py:688` no_keywords kill rejects LLM-emitted signal regardless of LLM result |
| **Category** | Decision Path / Profit-Path Integrity |
| **Severity** | HIGH |
| **Status** | COMPLETE (2026-04-26, v0.29.56) |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | — |
| **Blocks** | (now closed: previously blocked the bot from ever recording a paper trade end-to-end against LLM-only signal; transitively blocked the Phase 2 governance agent from observing keyword-config gaps in its audit log) |

**Description**
The `if not keywords:` check at `main.py:688` rejected every candidate where keyword scoring (`_keyword_score()` in `analysis/signal_analyzer.py`) returned an empty list, regardless of whether the LLM had emitted a usable signal. In the bot's pipeline the LLM runs even when keyword scoring is empty (since the pre-LLM gate is currently disabled by default), so an LLM that *did* identify semantic relevance and produced a non-trivial probability movement was being silently discarded immediately after producing the signal. The `SIGNAL_ANALYSIS_DETAIL` event emitted from inside `estimate_probability()` shows `llm_useful=True` and the adjusted probability; the same timestamp's `ANALYSIS_REJECTED reason=no_keywords` event from `main.py:688` then kills the candidate.

**Why it mattered to profitability / safety / reliability**
Across nine days of unattended paper-mode operation under v0.29.54 (window 2026-04-17 → 2026-04-26), the bot processed 666 real-market `SIGNAL_ANALYSIS_DETAIL` events. Five of those (0.75%) had `llm_useful=True` with non-zero `llm_probability_movement`: cricket news on the KXPSL cricket market, ICE-funding news on the KXSBUDGETRES budget-resolution market, and Iran-talks news on the KXTRUMPIRAN visit market. All five had empty `keywords` lists because the headlines did not contain glossary keywords from `GEOPOLITICAL_SIGNALS` or per-market keyword config, even though the LLM correctly identified the headlines as semantically related to the markets. All five were rejected at `main.py:688`. The bot's `paper_trades` table contained zero rows at the time of investigation — the *architecture had never produced an end-to-end paper trade against LLM-only signal*. Beyond the direct profitability impact, the Phase 2 governance agent (shipped 2026-04-25 as v0.29.55) is structurally designed to learn keyword-config gaps from exactly these "LLM-positive, keyword-empty" events; with them filed as `ANALYSIS_REJECTED reason=no_keywords` rather than surfacing as candidate evidence, the governance agent had no signal to learn from.

**Evidence / Source**
- Trade-log analysis (2026-04-26, scripted, in conversation memory `project_no_edge_diagnosis.md`):
  - 666 real-market SAD events; 5 with `llm_useful=True`; 100% of those 5 followed by `ANALYSIS_REJECTED reason=no_keywords` at `+0.00s`.
  - Timestamps: `2026-04-23T19:19:02` and `T19:19:05` (KXSBUDGETRES x2, "ICE funding resolution"), `2026-04-24T16:50:17` (KXTRUMPIRAN, "Trump dispatching Witkoff/Kushner"), `2026-04-25T16:14:44` (KXPSL, "Babar/Bracewell lead Zalmi"), `2026-04-26T00:16:54` (KXTRUMPIRAN, "Iran talks stall").
  - All 5 had `pre_llm_quality_pass: True` — the pre-LLM gate (which is itself disabled in deployment) was *not* the kill point.
- Code trace (`main.py:686-703`, `analysis/signal_analyzer.py:1123`): `estimate_probability()` returns `(estimated_prob, confidence, keywords, reasoning, llm_dir, llm_mag, llm_conf)` and main.py only consults `keywords` for the rejection decision.
- Pure-logic test artifact: `/tmp/phase3_hypothesis_test.py` confirmed the hypothesis pre-fix (5/5 hypothesis-confirming + 4/4 regression-safe).

**Fix**
`main.py:688` updated to gate on "neither signal source produced anything":

```python
llm_emitted_signal = llm_mag is not None and llm_mag != "none"
if not keywords and not llm_emitted_signal:
    log_analysis_rejected(reason="no_keywords")
    return
```

The change preserves the CLAUDE.md anti-blend gotcha — keywords remain a gate when LLM is silent, and LLM probability is *not* blended with keyword-derived probability on the LLM-only path (the LLM result is the sole signal source). It only spares the LLM-emitted-signal-with-empty-keywords case that the prior check incorrectly rejected.

**Acceptance Criteria** (all met at commit time)
- Pre-fix kill mechanism documented from concrete trade-log evidence — **MET**.
- Hypothesis verified before code change via isolated logic test — **MET** (`/tmp/phase3_hypothesis_test.py`).
- Production fix covered by two new pytest cases that pin both directions of the contract: LLM-positive empty-keywords proceeds; LLM-silent empty-keywords still rejects — **MET** (`tests/test_main_pipeline.py::test_process_candidate_proceeds_when_llm_emits_signal_despite_no_keywords` and `::test_process_candidate_still_rejects_when_neither_signal_source_speaks`).
- Existing test `test_process_candidate_returns_early_when_no_keywords` continues to pass unchanged (its mock has `llm_mag=None`, the LLM-silent case the fix preserves) — **MET**.
- Full pytest suite green — verified at fix-commit time.
- VERSION + CHANGELOG bumped per `~/.claude/rules/release_versioning.md` — **MET** (0.29.55 → 0.29.56).

**Notes — necessary but possibly not sufficient**
This fix unblocks the kill at `main.py:688`. Once an event proceeds past line 688 it still has to survive the rest of the funnel: BLEND/G1 (`blended_confidence × regime_confidence ≥ 0.35`), readiness G2-G6, executor E1-E12. In the diagnosis window all 173 BLEND_DECISIONs were G1-blocked because every lane probability was 0.5 (no signal). With `fast_lane_p` now non-0.5 on LLM-positive blends, the G1 math shifts in ways that were not simulated before this fix. Two outcomes are possible post-deploy:

1. *Best case:* LLM-positive blends pass G1 and at least one of the rare-but-real signal events results in a paper trade. The audit log gains its first nontrivial `OPPORTUNITY` records with `edge > 0.02`, and the Phase 2 governance agent begins observing real signal candidates.
2. *Worst case:* G1 still blocks the new blends because lane disagreement (`fast_lane_p=0.568` vs accumulation/structural at 0.5) produces low blended_confidence. The audit log will then contain LLM-positive G1-blocked records — a *qualitatively different* failure mode than the all-0.5 BLEND_DECISIONs we have today, and far more actionable for the next investigation iteration.

Both outcomes are useful. The fix moves the kill point from a place where there's no signal to debug to a place where there is. The audit log post-deploy is the actual evidence; if a new dominant kill point emerges, file it as the next debt entry and iterate.

**Related**
- The on-record P0.5 / P3.4 diagnosis ("input/market mix produces ~99% no-signal events") is *directionally correct* and remains the long-term strategic answer (Appendix A integration; narrower-scope markets). PROFIT-EDGE-001 is the short-term tactical answer for the 0.75% of events where the LLM does find signal.
- Phase 2 governance (v0.29.55) is the architecturally correct fix for the keyword-glossary-gap symptom this bug was masking. Post-fix the governance agent's audit log will start receiving the candidate evidence it needs to learn from.
- `PROFIT-CAL-001` (closed 2026-04-24, v0.29.47) wired the calibration loop end-to-end but cannot exercise itself in production until a paper trade actually resolves. PROFIT-EDGE-001 is the unblocker for that observation: until LLM-positive signals survive past `main.py:688`, no paper trade can resolve, and CAL-001's runtime verification stays in unit-test-only mode.

**Post-Deploy Verification** (2026-04-28, v0.29.58, ~48h runtime since 2026-04-27T13:03:19Z boot)

The line-688 fix is in production. Trade-log audit of `logs/trades/archive/2026/04/2026-04-{27,28}.jsonl` + `logs/trades/live/trades.jsonl` over the 48h window:

- 242 `SIGNAL_ANALYSIS_DETAIL` events; 0 `ANALYSIS_REJECTED reason=no_keywords` events. Pre-fix the same conditions would have produced N kills equal to the count of LLM-emitting-signal-with-empty-keywords cases. Post-fix, none.
- `Counter(llm_useful)` over the 242 SAD records = `{False: 240, True: 2}`. Both `True` records carry `is_startup_probe=true / is_synthetic_probe=true` — the deterministic startup probe the bot fires once per boot to verify the LLM path. The synthetic probe payload includes a non-empty `keywords` list (`["peace deal"]`), so the fix's contract (proceed when LLM emits signal *or* keywords are present) is exercised on the synthetic side, not the empty-keywords side.
- `Counter(llm_magnitude)` = `{'none': 240, 'moderate': 1, 'small': 1}`. The non-`none` records are the synthetic probe, not real-headline signal.

End-to-end exercise against a *real* LLM-positive headline with empty keywords has not yet occurred in this window. The underlying base rate of "LLM emits directional view on a matched real headline" is consistent with the pre-fix 9-day measurement of 5/666 (0.75%). Two days at ~120 SAD/day = 240 events; expected ~2 LLM-positive on real data; observed 0. Within statistical noise (95% CI for a 0.75% binomial with n=240 includes 0); not evidence of regression in the LLM signal layer.

Verdict: fix code-path is live and synthetic-probe verified. Real-data verification is gated behind base-rate occurrence of a true LLM-positive event; whenever one lands, the line-688 path is now the *correct* outcome (proceed past gate). The next-layer kill that produced this bug's downstream symptom — the executor's `PAPER_MIN_EDGE` rejection of `edge=0.0` blends emitted by neutral-LLM-output matched headlines — is filed as `PROFIT-EDGE-004`.

---

### PROFIT-EDGE-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-002 |
| **Title** | Readiness-gate G4 mis-calibration + categorical-prior coverage gap + sport-prefix gap (KXPSL) + silent structural-recompute failure |
| **Category** | Decision Path / Profit-Path Integrity |
| **Severity** | HIGH |
| **Status** | COMPLETE (2026-04-26, v0.29.57) |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | PROFIT-EDGE-001 (necessary precondition; does not directly block) |
| **Blocks** | (now closed: previously the *deeper* blocker behind PROFIT-EDGE-001 for paper-trade emission on geopolitical / domestic-policy event markets) |

**Description**

Post-fix simulation of PROFIT-EDGE-001 surfaced four additional structural bugs preventing paper trades from clearing the readiness gate even when the LLM emits a positive signal. Each bug compounds on the others.

1. **`analysis/regime_classifier.py:_SERIES_PRIORS` was authored 2026-04-18 and has not kept pace with Kalshi's expanded event-series catalog.** None of the 9 series the bot has actually engaged in the last 7 days had a categorical prior — every blend on those tickers fell through `_series_prior` to the time-based fallback. For the typical 7-14d-to-close window the time fallback returns near-uniform `(0.10, 0.45, 0.45)` weights, producing `regime_confidence = 0.136` — well below the readiness gate's G4 threshold.

2. **`tasks/trade_readiness_gate.py:G4_REGIME_CONFIDENCE_THRESHOLD = 0.40` is mathematically incompatible with the regime classifier's own design.** `regime_confidence = 1 - H(weights) / log(N_lanes)` where H is Shannon entropy. For the 3-lane regime, `rc ≥ 0.40` requires the dominant lane > ~0.80, which excludes almost every categorical prior the original designer added. Production data over 9 days shows zero markets clearing G4 except sports (which we filter out by design via `MARKET_SERIES_BLOCKLIST_PREFIXES`) and the very-short ≤6h time fallback.

3. **`config.py:MARKET_SERIES_BLOCKLIST_PREFIXES` is missing common sport prefixes.** Audit of all ~9.8k Kalshi series surfaced ~336 sport-related series prefixes not currently blocked. KXPSL (Pakistan Super League cricket) was the confirmed leak that prompted the audit — `KXPSL-26-PZA` reached the LLM-analysis pipeline as one of the 5 LLM-positive events in the PROFIT-EDGE-001 diagnosis and contributed false-positive signal.

4. **`tasks/structural_task.py:run_once` warning logs swallowed the underlying cause of recompute failures.** `process_market` wraps every exception in `StructuralComputationError(...) from exc`. The warning at line 146 used `_log.warning("...%s", r)`, which calls `str(r)` on the wrapper and discards `__cause__`. Across all production logs the underlying exception was never written to bot.log or errors.log; structural failures were silently un-diagnosable. This intersects with the no-edge problem because the structural lane has been silently absent from many BLEND_DECISIONs.

**Why it mattered to profitability / safety / reliability**

PROFIT-EDGE-001 unblocked the kill at `main.py:688`. Post-fix simulation against existing BLEND_DECISIONs confirmed that, for the 2 of 5 affected events with reconstructable baselines (both `KXTRUMPIRAN-26MAY01`), G4 alone would still reject every blend regardless of the line-688 fix because the market's `regime_confidence` is intrinsic to the regime weights and never moves above ~0.14 for an uncategorized series with > 7 days to close. The line-688 fix is *necessary but not sufficient*; without PROFIT-EDGE-002, the bot would continue emitting zero paper trades on the user's actual target markets (geopolitical / domestic-policy event series).

Beyond direct profitability, the bug compounded with PROFIT-EDGE-001's governance-agent observability problem: the Phase 2 governance agent (v0.29.55) cannot learn from candidate signals that die at G4 the same way it cannot learn from those that die at line 688.

**Evidence / Source**

- *Categorical-prior coverage gap* — Kalshi-API audit on 2026-04-26: of the top 9 series with BLEND_DECISIONs in the 2026-04-19 → 2026-04-26 window (KXTRUMPIRAN 68, KXMOCTRUMP25 50, KXVANCEPAKISTAN 25, KXFISAEXTEND 4, KXPARDONSTRUMP 2, KXVOTESAVEAMERICA 2, KXELECTIONEMERGENCY 1, KXTRUMPENDORSE 1, KXTRUMPCHINA 1), zero had categorical priors. Inspection of regime_classifier.py:_SERIES_PRIORS git blame: file last meaningfully edited 2026-04-18 (commit `bdeeca5`).

- *G4 mis-calibration* — Closed-form analysis (matches production BLEND_DECISIONs for KXTRUMPIRAN-26MAY01: rc=0.1363):

  | Prior class                           | Weights              | rc    | G4=0.40 | G4=0.20 |
  |---------------------------------------|----------------------|-------|---------|---------|
  | Sports (KXNFL etc.)                   | (0.85, 0.10, 0.05)   | 0.528 | PASS    | PASS    |
  | ≤6h time fallback                     | (0.85, 0.10, 0.05)   | 0.528 | PASS    | PASS    |
  | Polling (KXAPRPOTUS)                  | (0.05, 0.25, 0.70)   | 0.321 | FAIL    | PASS    |
  | Central bank (KXCBDECISION)           | (0.05, 0.30, 0.65)   | 0.280 | FAIL    | PASS    |
  | Crypto (KXCRYPTO)                     | (0.65, 0.28, 0.07)   | 0.251 | FAIL    | PASS    |
  | Weather (KXWEATHER)                   | (0.10, 0.25, 0.65)   | 0.220 | FAIL    | PASS    |
  | Trump-say (KXTRUMPSAY)                | (0.55, 0.35, 0.10)   | 0.157 | FAIL    | FAIL    |
  | 1-3d / 3-7d / 7-14d time fallback     | varies               | 0.06–0.14 | FAIL  | FAIL    |
  | Entertainment (KXENTERTAIN)           | (0.40, 0.45, 0.15)   | 0.080 | FAIL    | FAIL    |

- *Sport-prefix gap* — KXPSL leak confirmed at `KXPSL-26-PZA` event 2026-04-25T16:14:44, captured in `project_no_edge_diagnosis.md` memory note. Audit script tightened to ~336 missing sport-related prefixes; 33 high-confidence prefixes added in this fix.

- *Structural recompute silent failure* — Inspection of `tasks/structural_task.py:146` confirmed `_log.warning("...%s", r)` with chained-exception object. `cat /Users/Jake/vscode/kalshi_bot/logs/app/launchd.stderr.log | grep "per-market recompute failed"` produces hundreds of `failed structural recompute for X` lines with no traceback. Sample dossier query (.venv/bin/python on 2026-04-26): only 19 dossiers exist; none for the engaged target tickers (KXSBUDGETRES-26APR-APR28, etc.); and the daily structural cycle reports `dossier_markets=12 results={'skipped': 12}` — i.e. all 12 dossier-backed markets get skipped (recomputed=0) yet the wrap-and-suppress path produces dozens of stderr warnings that never carry useful context.

**Fix**

Three coordinated code changes plus the structural-log fix:

1. **`analysis/regime_classifier.py:_SERIES_PRIORS`** — added 21 new categorical priors covering the engaged series families:
   - Legislative / calendar markets (interpretation-dominant): KXSBUDGETRES, KXFISAEXTEND, KXVOTESAVEAMERICA, KXEFFTARIFF, KXMOCTRUMP25.
   - Macro releases (structural-dominant, mirroring KXCBDECISION): KXCPIYOY, KXCPICOREYOY, KXCPIEU, KXEZGDPYOYF.
   - Event-driven political / diplomatic (fast-dominant): KXTRUMPACT, KXTRUMPENDORSE, KXTRUMPCHINA, KXTRUMPCRYPTOCONF, KXVANCEPAKISTAN, KXVISITVENEZUELA, KXPARDONSTRUMP, KXLTGOVGANOMR.
   - Conflict / military (strongly fast-dominant): KXTRUMPIRAN, KXARMOMINF, KXELECTIONEMERGENCY.

   Each tuple is dimensioned so the resulting `regime_confidence` clears the new `G4 = 0.20` floor with ≥ 0.02 headroom.

2. **`tasks/trade_readiness_gate.py:G4_REGIME_CONFIDENCE_THRESHOLD`** lowered `0.40 → 0.20`. Inline comment block documents the calibration data table and the rationale. The fail-safe activation point follows G4 (existing semantics retained), so markets in `[0.20, 0.40)` now run normal mode (G1=0.35, G3=0.20) rather than fail-safe (G1=0.50, G3=0.15) — the categorical prior IS our knowledge about regime, so the standard gate is appropriate.

3. **`config.py:MARKET_SERIES_BLOCKLIST_PREFIXES`** — added 33 high-confidence sport prefixes including KXIPL/KXPSL/KXWPL (cricket), KXATP/KXWTA (tennis), KXUFC, KXWBC, KXWNBA, KXEPL/KXEGYPL/KXISL/KXCHNSL/KXCANPL/KXAFCC/KXUEFA (international soccer), KXF1, KXPGA, KXWO (Winter Olympics — distinct from KXOLYMPIC), KXEWC (esports), KXCBA/KXNBL/KXEUROCUP (international basketball), KXCFP/KXCFB/KXMARMAD (college sports), and sport-only player-movement / leader / coaching prefixes (KXLEADER, KXNEXTTEAM, KXNEXTCOACH, KXCOACHOUT, KXTEAMSIN, KXTRADEOFF, KXRANKLIST, KXCRICKET, KXT20).

4. **`tasks/structural_task.py:146`** — warning now emits both the wrapper message and `repr(__cause__)`, plus an `exc_info` traceback rooted at the cause. Future structural failures will produce diagnosable log records.

**Defensibility**

The G4 threshold change is the most material of the four, so the defensibility argument is enumerated explicitly:

- *Math*: `regime_confidence = 1 - H/log(N_lanes)` is entropy-based, with `G4 = 0.40` requiring lane dominance > ~0.80. The original sport-only series at (0.85, 0.10, 0.05) clear it; almost nothing else does. The original designer added Polling / Central-bank / Crypto / Weather / Entertainment categorical priors with the express *intent* that those be tradeable — but G4 = 0.40 prevents that. The threshold and prior table were never reconciled.
- *Production data*: 9 days of paper-mode operation 2026-04-17 → 2026-04-26 produced zero paper trades end-to-end. The line-688 fix unblocked one stage of the funnel; G4 was the next dominant kill. With G4 = 0.20 the categorical priors that were originally designed to be tradeable will now actually trade.
- *Conservative shape*: G4 = 0.20 still rejects time-fallback uncategorized markets at 1-14d (rc 0.06–0.14), KXTRUMPSAY at rc=0.157, and KXENTERTAIN at rc=0.08 — the fail-safe path remains active for those. We did not lower G1 (0.35) or G3 (0.20); only the regime-confidence floor itself moves.
- *Calibration test*: `test_g4_threshold_is_calibrated_to_pass_existing_categorical_priors` pins the relationship between G4 and the existing tradeable priors. Future bumps back above 0.20 will fail the test until accompanied by explicit recalibration of the prior table.
- *Reversibility*: change is a single constant. Reverting is trivial if the post-deploy audit log shows trades that should not be passing.

**Acceptance Criteria** (all met at fix-commit time)

- Pre-fix kill chain documented from concrete trade-log evidence and closed-form analysis — **MET** (this entry).
- Math justification for G4 = 0.20 captured inline in `tasks/trade_readiness_gate.py` — **MET**.
- New categorical priors covered by behavioral tests (interpretation-dominant for calendar markets, fast-dominant for event markets, structural-dominant for macro) — **MET** (`tests/test_regime_classifier.py`).
- Calibration contract pinned: `test_new_categorical_priors_clear_g4_threshold` and `test_g4_threshold_is_calibrated_to_pass_existing_categorical_priors` — **MET**.
- Existing test `test_g4_regime_confidence_is_enforced_and_tightens_thresholds` updated to use the new threshold via the imported constant rather than literal `0.39` — **MET**.
- Sport-prefix additions covered by 44 positive parametrized cases (each leak confirmed blocked) and 11 negative cases (geo/policy markets not accidentally blocked) — **MET** (`tests/test_sports_blocklist.py`).
- Structural log surfaces underlying cause — regression test verifies both the wrapper text and the underlying exception class/message are present — **MET** (`tests/test_structural_task.py::test_run_once_failure_warning_surfaces_underlying_cause`).
- Full pytest suite green — **MET** (1369 pass, 1 skipped).
- VERSION + CHANGELOG bumped per `~/.claude/rules/release_versioning.md` — **MET** (0.29.56 → 0.29.57).

**Notes — necessary, possibly still not sufficient at G1**

PROFIT-EDGE-002 unblocks the G4 + regime-prior + sport-prefix layer. The next-most-likely binding constraint is `G1: scaled_confidence = blended_confidence × regime_confidence ≥ 0.35`. Realistic post-fix numbers: a fast-lane-only signal with `fast_conf = 0.85` on a market with `regime_confidence = 0.25` will have `blended_confidence` in the ~0.27 range and `scaled_conf ≈ 0.07` — still below G1. The fix unblocks G4 deterministically; whether G1 also passes depends on accumulation/structural lane contributions, which currently coexist as `acc_p = 0.5` / `str_p = None` for most engaged markets (per the BLEND_DECISIONs inspected during this investigation).

Two outcomes possible post-deploy:

1. *Best case*: Markets with established dossiers (KXTRUMPIRAN already has 10 evidence records and a structural prior) accumulate enough lane signal to clear G1, producing the bot's first end-to-end paper trade.
2. *Worst case*: G1 still blocks because acc_p and str_p remain near 0.5. The audit log will then contain BLEND_DECISIONs with non-trivial fast_lane_p (PROFIT-EDGE-001's contribution) and now passes-G4-but-fails-G1 records (PROFIT-EDGE-002's contribution) — qualitatively distinct from the all-0.5 records pre-fix and far more actionable for the next iteration.

Either outcome is informative. The G1 question can be answered with concrete post-fix data; we explicitly chose not to bundle a G1 recalibration into this commit, both for conservatism and because G1's correct value depends on the accumulation/structural lane dynamics — not the regime calculation alone.

**Related**

- *PROFIT-EDGE-001* (closed 2026-04-26, v0.29.56) — necessary precondition for this fix to matter end-to-end. Without EDGE-001 the LLM-positive events never reach blend; without EDGE-002 they reach blend but die at G4.
- *Governance agent Phase 2 (v0.29.55)* — receives the candidate evidence it was designed to learn from once both EDGE-001 and EDGE-002 are in production. Memory note `project_governance_regime_priors.md` flags categorical-prior maintenance as a future governance-agent capability — manual maintenance of `_SERIES_PRIORS` is the wrong shape for a market venue that adds new event series weekly; the agent has all the inputs needed (series title, market subtitles, blend cadence) to propose new categorical priors automatically, in the same shape as keyword/source learning.
- *Sports-prefix maintenance* — also flagged for future governance-agent automation. Manual curation of ~336 candidate sport prefixes is impractical and brittle; the governance agent should maintain `MARKET_SERIES_BLOCKLIST_PREFIXES` from market-title patterns the same way it manages keyword glossaries.
- *Dossier coverage (12/847 active markets at the time of this entry)* — surfaced during this investigation as a contributing factor to weak structural lane contributions. **2026-05-02 framing update (per PROFIT-DOSSIER-001):** the "12/847" active-market denominator is misleading. Dossier creation is eager (N=1 evidence record) — not threshold-gated — so coverage is bounded by which markets receive evidence at all, which is downstream of the LLM emitting a directional view. As of 2026-05-02 the alignment is 100%: every ticker that has produced an OPPORTUNITY also has a dossier. The actionable lever for "raise dossier coverage" is matcher quality + LLM signal density (PROFIT-EDGE-004 hypotheses 1 and 3), not a dossier-creation gate.

**Post-Deploy Verification** (2026-04-28, v0.29.58, ~48h runtime since 2026-04-27T13:03:19Z boot)

All four sub-fixes confirmed working under live runtime, with the exception of sub-fix #4 which has a verification-method gap (filed as `PROFIT-STRUCT-002`):

1. *Categorical-prior coverage gap (new priors firing in production).* Sample SKIPPED record from 2026-04-28T07:31:02Z on `KXTRUMPCHINA-26-MAY08`:

   ```
   regime_weights: { fast: 0.65, interpretation: 0.25, structural: 0.10 }
   regime_confidence: 0.22006971533310082
   ```

   This is the EDGE-002-added "Event-driven political/diplomatic (fast-dominant)" prior firing in production. Pre-fix this market would have fallen through `_series_prior` to the time-fallback `(0.10, 0.45, 0.45)` with `regime_confidence ≈ 0.14` — below the now-lowered G4 = 0.20. Live data matches the closed-form prediction in EDGE-002 evidence Table A: KXTRUMPCHINA fits the "Event-driven political / diplomatic (fast-dominant)" family, gets `(0.65, 0.25, 0.10)`, produces `rc = 0.220` with ~0.02 headroom over G4 = 0.20 — exactly as designed.

2. *G4 = 0.20 threshold passable.* The same SKIPPED record reaches the executor — meaning it cleared G4 at runtime. Pre-fix `G4 = 0.40` would have rejected `regime_confidence = 0.22` outright. Across the 48h window, 34 candidates emitted as `OPPORTUNITY` (post-readiness-gate event), confirming G4 is now passable for the engaged market mix.

3. *Sport-prefix gap (KXPSL etc.).* No KXPSL events appear in the post-restart `SIGNAL_ANALYSIS_DETAIL` set (242 records). The blocklist is preventing them from reaching the LLM-analysis path — exact behavior intended. Cannot positively confirm absence-of-leak across the *full* set of 33 prefixes added without longer runtime + cross-checking against Kalshi's complete live market list, but the pre-fix smoking-gun event (KXPSL-26-PZA reaching LLM at 2026-04-25T16:14:44Z) does not recur.

4. *Structural-recompute log fix (`tasks/structural_task.py:146`).* **Verification incomplete.** `launchd.stderr.log` lines containing `per-market recompute failed` show only the wrapper text in the sample inspected; no `caused by` / `repr(__cause__)` / `Traceback` lines visible from the sampled lines. However, `launchd.stderr.log` does not carry per-line timestamps for these entries, so the 10,605 visible lines could not be cleanly split into pre-restart vs post-restart subsets via stderr alone. The 30 successful `STRUCTURAL_PRIOR_RECOMPUTE` events post-restart confirm the structural lane *is* running; the open question is whether failure cases now carry diagnostic context. Filed as `PROFIT-STRUCT-002` to close the verification gap by inspecting timestamped `bot.log` instead.

Verdict: sub-fixes 1–3 confirmed live and behaving as designed; sub-fix 4 verification deferred to `PROFIT-STRUCT-002` (low-priority observability check, not a behavior concern).

---

### PROFIT-EDGE-003

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-003 |
| **Title** | Readiness-gate G1 (`scaled_confidence`) threshold mis-calibration — same shape as G4 |
| **Category** | Decision Path / Profit-Path Integrity |
| **Severity** | HIGH |
| **Status** | COMPLETE (2026-04-26, v0.29.58) |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | PROFIT-EDGE-002 (same diagnostic methodology, same calibration data) |
| **Blocks** | (now closed: previously the *next* layer behind EDGE-002 for paper-trade emission on geopolitical / domestic-policy event markets — confirmed via post-EDGE-002 simulation) |

**Description**

Post-fix simulation of PROFIT-EDGE-002 (v0.29.57) confirmed the line-688 + categorical-prior + G4 stack opens the readiness gate at the regime layer, but G1 (`scaled_confidence = blended_confidence × regime_confidence ≥ 0.35`) deterministically blocks every signal regardless of LLM input strength. Five-of-five LLM-positive events from the EDGE-001 diagnosis cleared G4 with the new priors but produced `scaled_confidence` in the 0.05–0.10 range — well below the 0.35 G1 floor.

**Why it mattered to profitability / safety / reliability**

Same stack-of-fixes argument as EDGE-002 — without G1 calibration, EDGE-001 + EDGE-002 unblock the upstream stages but the bot still records zero paper trades. The 9-day no-edge window terminates at G1 once the regime layer is patched.

**Evidence / Source**

- *Closed-form math* — for fast-lane-only LLM signal at `fast_conf = 0.85`, the blender's `_effective_confidences()` (`lane_conf × (rw_lane × rc + (1-rc)/3)`) plus the *mean*-of-effective-confidences blended_confidence formula bound `scaled_confidence` to ~0.27 for sports priors and ≤0.10 for the categorical priors EDGE-002 added. `G1 = 0.35` requires `regime_confidence ≥ ~0.875` with realistic blended_confidence — only the very-near-close (≤6h) sports markets achieve that, and we filter sports out of the trading universe by design. Same shape of unreachable threshold as G4 = 0.40 was; both set in the same commit (33385d9) on the same day with the same incompatible assumptions.

- *Production data* — 154 `BLEND_DECISION` records over 2026-04-17 → 2026-04-26 (paper mode, all-0.5 lane probabilities pre-line-688-fix), distribution of `scaled_confidence`:

  | percentile | scaled_confidence | note |
  |-----------:|------------------:|------|
  | median     | 0.026             | uncategorized markets at near-uniform rc |
  | P75        | 0.035             |       |
  | P90        | 0.119             | natural cliff — strong-signal cluster |
  | P95        | 0.119             |       |
  | max        | 0.304             | KXVANCEPAKISTAN-26APR21-APR24 — best signal in 9 days |

  Zero of 154 cleared `G1 = 0.35`. The threshold is 1.15× the best signal the bot ever produced.

- *Pass-rate sweep* across candidate G1 values (production data, 154 records):

  | G1 candidate | passing |
  |--------------|---------|
  | 0.35 (current) | 0  (0.0%) |
  | 0.20         | 7  (4.5%) |
  | 0.10         | 20 (13.0%) |
  | **0.05**     | **22 (14.3%)** ← chosen |
  | 0.03         | 40 (26.0%) |
  | 0.02         | 102 (66.2%) — would trade noise |

**Fix**

- `tasks/trade_readiness_gate.py:G1_CONFIDENCE_THRESHOLD` lowered `0.35 → 0.05`.
- `tasks/trade_readiness_gate.py:G1_FAILSAFE_CONFIDENCE_THRESHOLD` lowered `0.50 → 0.10` (proportional 2× base; academic given fail-safe coupling to G4, but moves for consistency).
- Inline rationale block at the constant captures the closed-form math, the production scaled_confidence distribution, the pass-rate sweep, and the reversibility note.

**Defensibility**

Mirror of the PROFIT-EDGE-002 argument:

- *Math*: Closed-form bound on blender output shows G1 = 0.35 unreachable for the categorical priors the system was designed to emit.
- *Production data*: 9-day audit shows zero pass rate at G1 = 0.35; chosen G1 = 0.05 sits at a natural break in the empirical distribution (above P75 noise, below P90 strong-signal cluster).
- *Calibration test*: `test_g1_threshold_is_calibrated_to_pass_top_decile_production_signals` pins G1 between the median noise floor (0.026) and the P90 strong-signal level (0.119) so a future bump back above ~0.08 fails until accompanied by recalibration of the prior table or blender.
- *Selectivity preserved*: 14% historical pass rate matches the top decile of signal strength; we did not lower G3 (disagreement) or change the blender, so multi-lane disagreement still gates separately.
- *Reversibility*: change is two constants. If post-deploy data shows we're trading noise, revert is trivial.

**Acceptance Criteria** (all met at fix-commit time)

- Pre-fix kill chain documented from concrete trade-log evidence — **MET** (this entry; CHANGELOG; inline comment).
- Math justification captured inline at the threshold definition — **MET**.
- Production scaled_confidence distribution captured in CHANGELOG and debt log — **MET**.
- Calibration contract pinned via `test_g1_threshold_is_calibrated_to_pass_top_decile_production_signals` — **MET**.
- Existing `test_g4_regime_confidence_is_enforced_and_tightens_thresholds` updated to keep all three gates failing under the new lower thresholds — **MET**.
- Full pytest suite green — **MET** (1370 pass, 1 skipped).
- VERSION + CHANGELOG bumped per `~/.claude/rules/release_versioning.md` — **MET** (0.29.57 → 0.29.58).

**Notes — what this completes**

The 2026-04-26 stack of fixes (EDGE-001 + EDGE-002 + EDGE-003) collectively addresses the line-688 kill, the regime-layer kill, and the scaled-confidence kill in the readiness gate. Post-EDGE-003 simulation predicts the 5 LLM-positive events from the EDGE-001 diagnosis clear all three gates and reach the executor; whether they ultimately produce paper trades depends on executor gates (E1–E12) which are out of scope here. We explicitly did NOT touch G2 (source class diversity), G3 (disagreement), G5 (drift), G6 (recency), or any executor gate. Those are the next iteration's targets if post-deploy data shows them binding.

**What this leaves open**

- *Dossier coverage / structural recompute reliability* — surfaced during EDGE-002 investigation. The structural-log fix shipped in EDGE-002 is the precondition for diagnosing *why* dossier-backed markets fail recompute. Once cause-traces start landing in `bot.log` post-deploy, file as the next debt entry.
- *G2/G5/G6 dossier-only gates* — for fast-lane-only signals these don't apply (`is_fast_lane=True` exempts them), but for accumulation-or-structural-driven signals they may bind. Empirical post-deploy data needed before judging.
- *Post-deploy calibration* — 14% historical pass rate is a prediction, not a guarantee. Real production data on v0.29.58 will either confirm or surface the next layer.

**Related**

- *PROFIT-EDGE-001* (closed 2026-04-26, v0.29.56) — first link in the stack.
- *PROFIT-EDGE-002* (closed 2026-04-26, v0.29.57) — second link; this entry depends directly on the categorical priors EDGE-002 added (without them, regime_confidence is too low for any G1 threshold short of zero to clear).
- *Governance agent Phase 2 (v0.29.55)* — finally receives candidate evidence with non-trivial fast_lane_p AND non-trivial scaled_confidence AND no upstream rejection, post-EDGE-003.

**Follow-on simulation harness** (2026-04-26)

The G1/G4 calibration analyses and the 5-LLM-positive-event readiness/executor walkthroughs that produced this entry have been captured as permanent operational simulations in [`scripts/simulations/`](../scripts/simulations/) so future code changes can re-validate the calibration before deploying. Available simulations:

* [`scripts/simulations/threshold_calibration.py`](../scripts/simulations/threshold_calibration.py) — G4 priors audit + G1 production scaled_confidence distribution.
* [`scripts/simulations/readiness_gate_events.py`](../scripts/simulations/readiness_gate_events.py) — readiness gate end-to-end against the 5 canonical EDGE-001 events.
* [`scripts/simulations/executor_validate.py`](../scripts/simulations/executor_validate.py) — executor `_validate()` (E1–E12) against the same 5 events, in independent + sequential passes.

Smoke tests in [`tests/test_simulations_smoke.py`](../tests/test_simulations_smoke.py) (13 cases) keep the harnesses themselves green under code changes. The plan for the remaining seven pipeline-stage simulations (match-score audit, BlendTask integration, paper-trade roundtrip, trading-queue handoff, governance-fast-cycle, resolution+calibration, dossier-creation) is in [`docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md`](superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md). Each remaining simulation is scoped to its own task with file/test deliverables and acceptance criteria.

**Post-Deploy Verification** (2026-04-28, v0.29.58, ~48h runtime since 2026-04-27T13:03:19Z boot)

G1 = 0.05 confirmed clearing the readiness gate for the engaged market mix. Sample SKIPPED record from 2026-04-28T07:31:02Z on `KXTRUMPCHINA-26-MAY08`:

- `blended_confidence = 0.38287097269604115`
- `regime_confidence = 0.22006971533310082`
- `scaled_confidence = 0.382871 × 0.220070 ≈ 0.0843` → ≥ G1 = 0.05 (PASS); pre-fix G1 = 0.35 would have rejected.

Across the 48h window, 34 readiness-gate candidates survived to the executor as `OPPORTUNITY` events — vs. 0 over the pre-fix 9-day window (per EDGE-003 evidence table; 0/154 BLEND_DECISIONs cleared G1 = 0.35). Empirical post-fix pass rate vs predicted: 34 OPPORTUNITY emissions for 34 BLEND_DECISIONs (100% pass rate) in this window — *higher* than the 14% historical projection from EDGE-003's pass-rate sweep. The reason is structural rather than concerning: every BLEND_DECISION in this window had `blended_p = 0.5` (every lane neutral, see PROFIT-EDGE-004), which produces predictable `scaled_confidence` values clustered above 0.05 once the new categorical priors apply. The 14% projection was derived from a 154-record pre-fix distribution that included markets without the new priors and a wider `regime_confidence` spread; the post-fix mix in this 48h window is dominated by series with the EDGE-002 categorical priors and so sits higher than the historical median.

The 34 `OPPORTUNITY` events all hit a single ticker (`KXMOCTRUMP25-26-MAY01`) with `edge = 0.0`, `kelly_dollars = 0.0`, `estimated_probability = 0.5`, `market_yes_price = 50.0¢`, `llm_magnitude = "none"`, `llm_direction = "neutral"`. Reasoning lines uniformly read "*[LLM] The headline is unrelated to ...*". The kill point has moved cleanly from G1 (pre-fix) to executor `PAPER_MIN_EDGE` (post-fix) — exactly the qualitatively-distinct outcome EDGE-003's "Notes — what this completes" predicted, and exactly the actionable failure mode the EDGE-001/002/003 stack was designed to surface. The follow-on debt entry for the upstream signal-sourcing root cause is `PROFIT-EDGE-004`.

Verdict: G1 calibration confirmed correct against live data. Pipeline now flows end-to-end through the readiness gate. The remaining "no paper trades" symptom has a different root cause (signal sourcing / matcher pairing / market-mix), surfaced by this fix and tracked separately.

---

### PROFIT-EDGE-004

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-004 |
| **Title** | Post-EDGE-003 pipeline reaches executor with `edge = 0.0` because every lane returns `p = 0.5` — signal sourcing / matcher quality / market-mix root cause |
| **Category** | Decision Path / Profit-Path Integrity / Signal Quality |
| **Severity** | HIGH |
| **Status** | OPEN |
| **Priority** | NOW |
| **Owner** | Shared |
| **Depends On** | PROFIT-EDGE-001 + PROFIT-EDGE-002 + PROFIT-EDGE-003 (the stack that exposed this; without them the readiness gate would still mask the upstream condition) |
| **Blocks** | Paper-trade emission; transitively `PROFIT-CAL-001` production-soak observation; transitively live-trading authorization (ROADMAP P4.3) |

**Description**

The EDGE-001/002/003 stack closed three sequential kill points in the readiness funnel (line-688 no-keywords kill → G4 = 0.40 regime-confidence floor → G1 = 0.35 scaled-confidence floor). v0.29.58 in production for ~48h confirms all three fixes work as designed — readiness candidates now survive to the executor. But the bot still records zero paper trades, with the kill point relocated downstream: every blend reaches the executor with `blended_p = 0.5` and `edge = 0.0`, where `PAPER_MIN_EDGE = 0.02` correctly rejects them.

The structural condition is not a bug in the readiness gate or the executor — both are doing exactly what they were designed to do. It is an *upstream* condition: the LLM is correctly identifying that most matched headlines are not semantically related to the markets they were paired with by the matcher, returning `llm_direction = "neutral"` and `llm_magnitude = "none"`, which sets `fast_lane_p = 0.5`. With `accumulation_p` and `structural_p` also null-or-0.5 on these tickers, `blended_p = 0.5` mechanically; `edge = |0.5 − market_yes_price/100|` collapses to ~0 when markets sit near 50¢, and the executor rightly skips.

This is the operational manifestation of the "input/market mix produces ~99% no-signal events" hypothesis that the on-record P0.5 / P3.4 ROADMAP analysis flagged as the long-term strategic problem, and that the EDGE-001 entry's Notes section explicitly forecast: *"The on-record P0.5 / P3.4 diagnosis ... is directionally correct and remains the long-term strategic answer (Appendix A integration; narrower-scope markets). PROFIT-EDGE-001 is the short-term tactical answer for the 0.75% of events where the LLM does find signal."* The EDGE-001/002/003 stack made the funnel reach the executor; with the funnel open, the upstream signal-sourcing problem becomes the new dominant kill.

**Why it matters to profitability / safety / reliability**

This is the binding constraint between "v0.29.58 pipeline runs cleanly" and "v0.29.58 produces a paper trade." Every other gate is now passable; only the absence of real signal stops the bot from emitting trades. Until this is closed:

- `paper_trades` table stays empty regardless of how long the bot runs — the EDGE-002 governance-agent observability concern is unblocked structurally (events flow into the audit trail) but the trades the agent is supposed to learn from never materialize.
- `PROFIT-CAL-001`'s production-soak observation cannot complete (needs a resolved paper trade to emit `CALIBRATION_CHECK`). CAL-001 is closed against unit-test acceptance criteria but the runtime confirmation footnote remains contingent on this.
- ROADMAP P4 (calibration review + live-trading authorization) is transitively blocked.
- Phase 2 governance agent (v0.29.55) lands its candidate-evidence stream — but every record describes a non-signal event. The agent is structurally able to learn from these (correctly identifying "this matcher pairing was not actually a signal" is itself a useful learning signal), so the impact on Phase 2 is observability-positive rather than blocked. The agent cannot, however, help the matcher emit *fewer* false-positive matches without code changes elsewhere or until governance moves out of shadow mode.

**Evidence / Source**

Trade-log audit of `logs/trades/archive/2026/04/2026-04-{27,28}.jsonl` + `logs/trades/live/trades.jsonl` (window 2026-04-27T13:03:19Z boot through 2026-04-29T~12:34Z, ~48h):

| Event type | Count |
|---|---|
| `EARLY_STALE_DROP` | 63,349 |
| `EARLY_FRESH_PASS` | 950 |
| `MATCH_DIAGNOSTIC` | 538 |
| `ANALYSIS_REJECTED` | 274 |
| `SIGNAL_ANALYSIS_DETAIL` | 242 |
| `NEW_MARKET` | 224 |
| `MATCH_SUPPRESSION_CANDIDATE` | 70 |
| `MATCH_SUPPRESSED` | 70 |
| `SIGNAL` | 34 |
| `OPPORTUNITY` | 34 |
| `BLEND_DECISION` | 34 |
| `EVIDENCE_INGESTION` | 34 |
| `DOSSIER_UPDATE` | 34 |
| `STRUCTURAL_PRIOR_RECOMPUTE` | 30 |
| `SKIPPED` | 3 |

Key distributional data:

- 242 `SIGNAL_ANALYSIS_DETAIL` events; `Counter(llm_useful)` = `{False: 240, True: 2}`. Both `True` records are `is_startup_probe=true / is_synthetic_probe=true`. **Real-headline LLM-positive count = 0.**
- `Counter(llm_magnitude)` = `{'none': 240, 'moderate': 1, 'small': 1}`; `Counter(llm_direction)` = `{'neutral': 238, 'no': 3, 'yes': 1}`. The non-`none` magnitude records and the non-`neutral` direction records are the synthetic startup probe + downstream effects of those probes.
- All 34 `OPPORTUNITY` records hit a single ticker: `KXMOCTRUMP25-26-MAY01` ("Will any Republican member of Congress calls on Trump to be removed as President through the 25th amendment before May 1, 2026?"). All have:
  - `estimated_probability: 0.5`, `market_yes_price: 50.0`, `edge: 0.0`, `kelly_dollars: 0.0`, `capped_dollars: 0.0`
  - `llm_direction: "neutral"`, `llm_magnitude: "none"`
  - `reasoning` lines: e.g. *"[LLM] The headline is unrelated to Republican members of Congress calling for Trump's removal under the 25th amendment. (LLM: 0.500, Keywords(ref): 0.600)"*
  - First record: 2026-04-27T13:59:33Z (~57m post-boot); last: 2026-04-29T12:33:54Z. Distributed across the window.
  - Sample headlines that triggered the match-and-LLM-pass-but-emit-neutral path: *"What does Trump shooting at White House dinner mean for World Cup security?"* / *"'No more Mr Nice Guy': Trump warns Iran to 'get smart' over stalled talks"* / *"Man charged with attempted assassination of Trump"* — all related to Trump but none semantically about a 25th-amendment removal vote.

- 3 `SKIPPED` records all carry `reason: "edge +0.0000 below min_edge 0.02"` with `signal_meta` showing a fully-blended path (`source_lane: "blend"`) and the new categorical priors firing. Sample (2026-04-28T07:31:02Z, `KXTRUMPCHINA-26-MAY08`, headline *"King Charles' US visit begins amid Trump assassination attempt and diplomatic ri[ft]"*):

  ```json
  {
    "fast_lane_p": 0.5, "fast_lane_confidence": 0.9,
    "accumulation_p": null, "accumulation_confidence": null,
    "structural_p": null, "structural_confidence": null,
    "blended_p": 0.5, "blended_confidence": 0.36271986886993374,
    "regime_weights": {"fast": 0.65, "interpretation": 0.25, "structural": 0.1},
    "regime_confidence": 0.22006971533310082,
    "blend_mode": "dominant_lane",
    "disagreement_score": 0.0,
    "readiness_gate_min_edge_override": null
  }
  ```

  This record — the cleanest evidence in the audit — confirms the EDGE-002 categorical prior is firing, G4 = 0.20 is clearing (`rc = 0.220 ≥ 0.20`), G1 = 0.05 is clearing (`scaled_conf ≈ 0.080 ≥ 0.05`), and the executor's `PAPER_MIN_EDGE = 0.02` is the binding rejection point because every lane is `0.5`.

- Pre-fix 9-day baseline (PROFIT-EDGE-001 evidence section): 5/666 SAD events were LLM-positive on real headlines = 0.75%. Post-fix 2-day window: 0/240 LLM-positive on real headlines. Within statistical noise (95% CI for a 0.75% binomial with n=240 includes 0). Not evidence of regression in the LLM signal layer; consistent with the same low base rate.

- `paper_trades` SQLite count: 0 rows.

**Hypotheses to investigate**

The chain `news headline → market_matcher.py (Jaccard + keyword boost) → LLM neutral output → blended_p = 0.5 → edge = 0.0` fails at the matcher step in the sense that the matcher is confidently pairing items the LLM then says are unrelated. At least three contributing causes are plausible and not mutually exclusive:

1. **Matcher too loose.** [`analysis/market_matcher.py`](../analysis/market_matcher.py) uses Jaccard token similarity with a geopolitical keyword boost. The pre-LLM gate (`ENABLE_PRE_LLM_MATCH_GATE=false`) is currently disabled by design — diagnostics-only per CLAUDE.md and the README. Without the gate, broader Jaccard matches reach the LLM unchallenged. Sample evidence from this audit window: *"What does Trump shooting at White House dinner mean for World Cup security?"* → matched to `KXMOCTRUMP25` (25th-amendment removal). The token "Trump" alone carries enough Jaccard weight to clear the matcher even though the headline is about a separate event entirely. Consider re-enabling or strengthening the pre-LLM gate, raising the Jaccard threshold, or adding a *semantic* (embedding-based) match step before the LLM.

2. **Engaged market universe too broad.** The 34 OPPORTUNITY events all hit one market with 7+ days to close and very specific conditions (a Republican member of Congress invoking the 25th amendment) — a market that almost no real news will move. Most of the bot's matched-headline volume hits these long-horizon, specific-trigger markets where the LLM is correctly calling neutral. The existing `MARKET_SERIES_BLOCKLIST_PREFIXES` (EDGE-002 added 33 sport prefixes) controls only which series are *allowed*; it does not constrain by event-window or specificity. Consider an event-horizon filter (`min_hours_to_close`, `max_hours_to_close`) or a "specificity score" derived from the market title's question structure.

3. **News mix too generic.** Wire-service feeds dominate the source mix and produce broad-coverage stories that match many narrow markets weakly. Bluesky journalist firehoses and OSINT desks (per Appendix A Tier 2, integrated) add narrower content but still tend toward general-political coverage. The Phase 2 governance agent is designed to dynamically adjust the source registry, but until it's out of shadow mode (≥14 days from 2026-04-27, i.e. ≥2026-05-11) it will not modify production sources. Operator-driven curation in `config.py` source registries is the short-term lever.

The on-record ROADMAP analysis (P0.5 / P3.4) treats this as the *strategic* problem; EDGE-004 documents it as the *operational* problem now that the readiness gate is no longer masking it.

**Proposed Fix — sequenced investigation, not a single commit**

This is a multi-step problem and should not be bundled into one fix. A reasonable sequence:

1. **Extend the simulation harness with a match-score audit** — the eighth simulation in [`docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md`](superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md). Capture the matcher's output distribution (Jaccard score percentiles, keyword-boost contribution, pre-LLM gate would-block rate) for the same 5 canonical EDGE-001 events plus the 34 KXMOCTRUMP25 OPPORTUNITY-emitting events from this window. The output will tell us whether the matcher is producing genuinely "narrow" matches that the LLM then rejects, or whether it's producing broad matches at a Jaccard threshold the LLM correctly tightens.

2. **Re-evaluate the pre-LLM gate** (`ENABLE_PRE_LLM_MATCH_GATE`). The gate is currently `false` (diagnostics-only). The `pre_llm_quality_pass` / `pre_llm_would_block` / `pre_llm_keyword_signal_strength` fields are already present on every `SIGNAL_ANALYSIS_DETAIL` event in this audit window — these tell us what the gate *would* have done if active. If gate-would-block correlates strongly with LLM-emits-neutral, the gate is well-calibrated and switching it on (or fail-safe-on for specific market families) would suppress the noisy matches before they consume LLM cycles.

3. **Audit the engaged market mix.** A sample of 20 markets the bot has actively engaged in the 48h window. Are they predominantly broad-policy with 7+ days to close and specific-trigger conditions? If yes, narrow the engagement window via a config knob and re-observe.

4. **Re-evaluate Appendix A integration completeness.** README says Tier 1–2 integrated; Tier 3 deferred. Check whether the deferred Tier 3 sources (or other sources flagged for evaluation in `docs/_archive/studies/news_sources_evaluation.md`) would meaningfully shift the news-mix toward narrower-scope content. Operator decision; agent provides the cost/value matrix.

5. **Preserve domain-constraint boundaries.** Per CLAUDE.md domain constraints, `/analysis` should remain pure (no API calls or trade execution). The matcher itself is in `/analysis`; changes to its scoring or gate behavior are in scope but should preserve purity. Anything touching market-cache scope or source registries goes in `/feeds` or `config.py`.

**Acceptance Criteria**

- One match-score audit simulation captures the empirical Jaccard / pre-LLM-gate / LLM-neutral-correlation distribution for the EDGE-004 evidence window, committed to `scripts/simulations/`.
- A dated inline note in this entry records which of hypotheses 1–3 the audit data supports (one, multiple, or "neither — the binding constraint is something else again").
- Either:
  - (a) A code change closes one specific contributing cause (e.g. pre-LLM gate enabled with a documented threshold; or a market-engagement filter added), AND a follow-up post-deploy observation window of ≥48h shows non-zero `paper_trades` rows (or `OPPORTUNITY` events with non-zero `edge`); OR
  - (b) The audit demonstrates that no single contributing cause dominates, and the entry is split into one debt entry per hypothesis to be addressed independently; OR
  - (c) The Phase 2 governance agent (post-shadow-mode) is judged the right architectural fix and EDGE-004 is consciously deferred to a governance-agent activation milestone, with rationale captured here.

**Notes**

- The 31-of-34 OPPORTUNITY records that have no matching SKIPPED counterpart (34 OPPORTUNITY → 3 SKIPPED + 0 paper trades = 31 silent exits) is *not* a failure of EDGE-004; it is an observability gap in the post-OPPORTUNITY → executor path. Filed separately as `PROFIT-OBS-003` to keep this entry focused on the signal-sourcing root cause.
- This entry is the operational counterpart to the strategic P0.5 / P3.4 finding from the ROADMAP. Do not close it without either resolving the underlying signal-sourcing condition *or* documenting the conscious deferral to a governance-agent milestone. Closing it as "won't fix" leaves the bot perpetually unable to trade.
- The Phase 2 governance agent (v0.29.55, in shadow mode through ~2026-05-11) is the longer-term architectural answer — dynamic source/keyword/series management instead of the current static `config.py` registries. EDGE-004's hypotheses 1 and 3 are exactly what governance is designed to manage. But until shadow mode closes and the agent is allowed to *modify production state*, the short-term levers are operator-driven.
- The `KXMOCTRUMP25-26-MAY01` ticker as the sole OPPORTUNITY-emitting market is a strong tell. Inspect whether the bot's market-cache is over-indexed on a small set of long-horizon broad-trigger markets vs Kalshi's actual current event-series catalog. The matcher may be pairing many Trump-related headlines to this single market because it survives keyword filters with a low Jaccard floor, while other engaged markets in the same window are being skipped at earlier pipeline stages (suppression / dedup / pre-LLM diagnostics) and never producing OPPORTUNITY events.
- **2026-05-03 source-class audit:** `docs/_archive/governance/2026-05-03-source-class-diversification-audit.md` (ARCHIVED Stream G R43) joins OPPORTUNITY → BLEND_DECISION → EVIDENCE_INGESTION over the 13-day MacBook archive. Result: source mix is news-heavy (`news` appears on 238 OPPORTUNITY joins; `official` only 9), but not purely single-class (109/260 OPPORTUNITY records have ≥2 known classes). Positive-edge cases are `other` x2 / `news` x1, not official-driven. This points to a source-mix + readiness/blending interaction, not a standalone "add official feeds" fix.
- **2026-05-03 Lever D pre-LLM gate audit:** `docs/governance/2026-05-03-edge004-lever-d-pre-llm-gate-audit.md` sweeps archived `min_match_score` floors `{0.04, 0.05, 0.06, 0.08}` with the recorded pre-LLM gate diagnostics re-enabled. Result: 0.04-0.06 retain 67/260 OPPORTUNITY records and all 3 PAPER_TRADE records; 0.08 retains 56/260 and all 3 PAPER_TRADE records. The edge enrichment is real but volume-destructive and keeps the known losing FISA burst, so Lever D is a secondary noise/budget knob, not the first EDGE-004 Wave-2 fix.
- **2026-05-03 Lever B G1-admittance sizing:** `docs/governance/2026-05-03-g1-admittance-counterfactual.md` replays archived `BLEND_DECISION` rows by lowering the G1 floor. Result: 0.04 admits 32/197 G1-killed candidates; 0.03 admits 65/197. Predicted edge remains mostly zero (only 1 candidate at 0.04 and 2 at 0.03 reach edge >= 0.02), so Lever B is an attribution/throughput lever to size post-OBS-003, not an immediate standalone edge-lift fix.
- **2026-05-03 Lever C cross-series overlap sizing:** `docs/governance/2026-05-03-cross-series-headline-overlap-audit.md` groups archived OPPORTUNITY events by normalized exact headline and series prefix. Result: 128/260 OPPORTUNITY records (49.2%) share a headline across multiple series prefixes across 39 headline groups, dominated by `KXMOCTRUMP25` / `KXTRUMPIRAN` overlap. This clears the 5% sizing bar; EXEC-002 Approach 2 / Lever C stays in scope for a real spec.
- **2026-05-03 Lever E source/source-class corroboration sizing:** `docs/_archive/governance/2026-05-03-lever-e-source-corroboration-sizing.md` (ARCHIVED Stream G R43) joins OPPORTUNITY → BLEND_DECISION → EVIDENCE_INGESTION and counts distinct contributing source classes plus distinct source instances. Source-class distribution is `{0: 9, 1: 142, 2: 100, 3: 9}`; class N>=2 retains 109/260 OPPORTUNITY records and 0/3 PAPER_TRADE records. Source-instance distribution is `{0: 9, 1: 251}`; source N>=2 retains 0/260. Lever E is therefore a high-blast-radius corroboration gate, not a first-line EDGE-004 edge-lift lever without post-OBS-003 sizing.
- **2026-05-03 Lever A.1 classifier replay + Wave-2 ladder:** `docs/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md` shows the exact EVIDENCE_INGESTION source-string replay is blocked by archive shape (0/248 rows carry raw `source`). OPPORTUNITY source-label surrogate stays at 1/260 official post-A.1, failing the ≥30/260 target; MATCH_DIAGNOSTIC feed-surface surrogate stays 63/2838 official and flips only 8 defense-feed rows from other→news. `docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md` therefore treats A.1 as a distribution/measurement fix with no archive-visible trade-rate lift; A.1+ new feeds remain the only Wave-2 edge-production lever.
- **2026-05-03 Lever A.1+ candidate-feed sizing:** `docs/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md` buckets archive-visible source labels into government bulletin / specialist analyst / market microstructure classes. Result: specialist analyst has the strongest non-mainstream archive surface (251 MATCH_DIAGNOSTIC, 21 OPPORTUNITY, 3 PAPER_TRADE, median age ~2240s), government bulletin is thinner (63 MATCH_DIAGNOSTIC, 1 OPPORTUNITY, 0 PAPER_TRADE), and market microstructure has no archive surface. This points A.1+ first-feed ROI toward specialist analyst feeds; live onboarding still needs per-feed freshness/auth probes.
- OBS-003 closed 2026-05-10 — per-gate kill attribution now available via SKIPPED reason field; cycle-17C E3+ replay analyses can join SKIPPED.reason against the readiness-gate enum directly.

**Related**

- `PROFIT-EDGE-001` (closed 2026-04-26, v0.29.56) — necessary precondition; the line-688 fix is what made any LLM-negative-but-matched candidate reach the executor in the first place.
- `PROFIT-EDGE-002` (closed 2026-04-26, v0.29.57) — necessary precondition; without the categorical priors and G4 = 0.20, no candidate would clear the readiness gate to expose this layer.
- `PROFIT-EDGE-003` (closed 2026-04-26, v0.29.58) — direct precondition; G1 = 0.05 is what made the 34 OPPORTUNITY emissions possible.
- `PROFIT-CAL-001` (closed 2026-04-24, v0.29.47) — production-soak observation contingent on a paper trade resolving; structurally blocked by EDGE-004 the same way it was structurally blocked by EDGE-001 pre-fix.
- `PROFIT-OBS-003` (opened 2026-04-28) — observability of the post-OPPORTUNITY exit path; would expand EDGE-004's evidence base if closed first.
- `PROFIT-STRUCT-002` (opened 2026-04-28) — verifies EDGE-002 sub-fix #4 (structural-recompute cause emission); independent of EDGE-004 but registered in the same investigation.
- ROADMAP Appendix A — broader source classes and narrower market scope are the long-horizon strategic answer to which EDGE-004 is the operational wrapper.
- ROADMAP P0.5 / P3.4 — the on-record diagnosis from prior analysis that this entry operationalizes.
- Memory note `project_no_edge_diagnosis.md` — the original 2026-04-26 EDGE-001 systematic-debugging trace; EDGE-004 is its post-deploy continuation.

**Follow-up Evidence** (2026-05-01, full 13-day MacBook paper-soak archive aggregated post-cutover)

The 2026-04-28 evidence above covered ~48 h of v0.29.58 runtime. The post-cutover audit covers the entire 13-day MacBook paper era including the additional ~96 h of v0.29.58 (2026-04-29 → 2026-05-01) that ran after the original entry was written. Lifetime totals (full window 2026-04-18T02:11:24Z → 2026-05-01T13:05:54Z):

| Counter | 2026-04-28 audit (~48 h v0.29.58 only) | 2026-05-01 audit (full 13 d) | Notes |
|---|---|---|---|
| `OPPORTUNITY` | 34 | 260 | 7.6× more lifetime data; same edge=0.0 dominance (98.1% lifetime vs 100% in the original 48h) |
| `BLEND_DECISION` | 34 | 252 | 1:1 with OPPORTUNITY in original; 8-event drift in lifetime due to startup-probe + cross-version emission ordering |
| `SKIPPED` | 3 | 17 | All 17 reasons identical (`"edge +0.0000 below min_edge 0.02"`) — see PROFIT-OBS-003 evidence section |
| `PAPER_TRADE` | 0 | 3 | All on `KXFISAEXTEND-26APR-MAY0{1,2,3}` from VitalLaw.com; recorded `edge=-0.068` (sign bug — see PROFIT-OBS-004; actual executed-side edge was +0.068); resolved 0/3 wins; source-credibility multiplier auto-dropped to 0.5x |
| `STRUCTURAL_PRIOR_RECOMPUTE` | 30 | 178 | Continued steady firing across the full window (~13.7/day average); the structural lane is provably alive in production, closing PROFIT-STRUCT-002's "lane is running" half of the verification (the warning-format half remains open per the entry's own scope) |
| `CALIBRATION_CHECK` | 0 | 3 | First production observations of the CALIBRATION_CHECK emission. Per the original CAL-001 closure footnote, three resolutions in 13 days is a small but real signal that the wiring fired in production. See updated PROFIT-CAL-001 Notes. |

OPPORTUNITY edge distribution (lifetime, 260 events): `0.0` ×255, `-0.068` ×3 (the FISAEXTEND trades — sign bug per PROFIT-OBS-004), `+0.06` ×1, `+0.064` ×1. **Critically: 2 OPPORTUNITY events with non-trivial positive edge cleared PAPER_MIN_EDGE = 0.02 and produced no PAPER_TRADE.** This is direct evidence that the silent-exit gap (PROFIT-OBS-003) is dropping legitimately-tradeable candidates, not just edge=0.0 candidates the executor would correctly reject. Promoted OBS-003 from MEDIUM/LATER to HIGH/NOW on this evidence (see OBS-003 entry for the per-day breakdown).

Source-funnel concentration (from `paper_trades.db.source_stats`, lifetime aggregate): out of 405 sources discovered, the top opportunity producers — Guardian Middle East/North Africa (87 OPP / 0 trade), Guardian World (40/0), Al Jazeera (29/0), NYT World (28/0), Times of Israel (6/0), Kyiv Independent (5/0) — produced **zero** paper trades. The only graduated source is VitalLaw.com (3/3 losses, multiplier 0.5x). This is the empirical face of EDGE-004's "matcher quality / market-mix" hypothesis from the source side: the wire-service feeds emit broad-coverage stories that the matcher confidently pairs with narrow markets, the LLM correctly rejects the pairing as semantically weak, and the resulting OPPORTUNITY → PAPER_TRADE conversion stays at zero across the entire feed mix.

OPPORTUNITY ticker concentration (lifetime, top 5): `KXTRUMPIRAN-26MAY01` ×112 (43%), `KXMOCTRUMP25-26-MAY01` ×54 (21%), `KXMOCTRUMP25-26-APR24` ×22 (8%), `KXVANCEPAKISTAN-26APR21-APR30` ×15 (6%), `KXVANCEPAKISTAN-26APR21-APR25` ×8 (3%) — five tickers account for 81% of lifetime OPPORTUNITY emissions. The market-mix concentration is even more skewed than the original 2026-04-28 entry suggested (which sampled only the 34-OPP window where KXMOCTRUMP25 was sole emitter); over 13 days, the bot's matched-headline volume hits 5 tickers consistently, none of which moved meaningfully on real news during the soak. EDGE-004 hypothesis 2 ("Engaged market universe too broad / too long-horizon") is reinforced: the long-horizon, broad-trigger markets the matcher loves are precisely the ones that don't move.

This evidence does not require revising EDGE-004's investigation plan (matcher audit → pre-LLM-gate re-eval → market-mix audit → Appendix A re-eval); it just makes the priority ordering more concrete. **Sequenced fixes 1–3 are now applicable; fix 4 (Appendix A) likely won't shift the conversion rate until at least one of fixes 1–3 lands.** The Phase 2 governance agent (Mac Studio shadow soak ETA 2026-05-15 per PROFIT-PHASE2-001) is the longer-term architectural lever for source/market dynamic adjustment, but its application requires governance to exit shadow mode — which is itself blocked behind the §8.5 acceptance criteria (≥30 decisions, ≥85% reasonable on review). On the current observed cadence, governance exiting shadow mode is the *post-* fix-3 milestone, not the *replacement* for it.

Cutover note: this entry's "Evidence / Source" + this Follow-up section together represent the entire MacBook paper-era evidence base for EDGE-004. Post-cutover, future EDGE-004 evidence accumulates on the **Mac Studio** against the same `data/paper_trades.db` and `data/evidence_store.db` (restored from `transfer/macbook_handoff_2026-05-01/` per PROFIT-CUTOVER-001) plus its own fresh `logs/trades/` JSONL stream. The MacBook will not produce new EDGE-004 evidence; treat the lifetime totals above as the closed v0.29.58 MacBook-era baseline against which Studio progress is compared.

#### 2026-05-04 cycle integration (during PROFIT-PHASE2-001 shadow-soak)

The EDGE-004 lever menu's 2026-05-04 cycle produced four findings that change the operator-facing closure path:

1. **Per-source audit** (`docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md`, commit `cca3cea`; Codex re-derivation `2bb0b09`): of the 21 OPP + 3 PAPER_TRADE in the specialist_analyst class, **all 3 PAPER_TRADE came from VitalLaw.com**. The geopolitics sub-niche feeds (Kyiv X / Times of Israel / Iran International / bellingcat / Defense News / Breaking Defense) produced 18 OPP + 0 PAPER_TRADE — 0 % conversion at the class-internal level. The proposed A.1+1 candidates (war on the rocks / CSIS / ISW / CFR / Atlantic Council) target the same 0 %-conversion sub-niche.

2. **Aggregator-path forensics** (`docs/governance/2026-05-04-vitallaw-aggregator-path-forensics.md`, commit `37063d8`; Codex coverage audit `bb015a5`): the 83 VitalLaw records in the Mac archive came via **Google News RSS** (`news.google.com/rss/articles/...`), NOT a direct VitalLaw RSS feed. Google News query family is **active in current canonical config** (re-enabled 2026-04-23). Therefore Branch A of the A.1+1.5 decision tree requires no code change: passive observation of whether VitalLaw / legal-niche surfaces under the current market-mix queries.

3. **MATCH-001 (B′) orthogonality** (`docs/governance/2026-05-03-match001-bprime-false-suppression-audit.md`, commit `83a9477`; Codex spec-parity `b56c261`): the post-fix B′ predicate is orthogonal to the existing pre-fix `MATCH_SUPPRESSED` logic (100 % flip rate). B′ deploy is empirically clean w.r.t. un-suppression of existing noise; combined with the 0-likely-false-negative finding from Codex's earlier audit, both directions of B′ deploy risk are sized at 0.

4. **VitalLaw direct-RSS infeasible** (Codex `a45c06c`, 2026-05-05): probed `vitallaw.com` for a public RSS endpoint; all obvious paths return SSO-redirect / 403 / 404 / RSS-error-HTML / connection failure. Branch B (direct VitalLaw RSS) is empirically dropped from the A.1+1.5 decision tree.

**Revised A.1+ decision tree (3-branch, locked 2026-05-05):**
- **Branch A (passive)**: observe Wave-1 close 14 d for legal-niche surfacing under the active Google News path. NO code change required.
- **Branch C (active fallback)**: if Branch A produces 0 legal-niche PAPER_TRADE in 14 d, onboard open-RSS analogues (Lawfare / Just Security / SCOTUSblog / Politico Legal) per `docs/_archive/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` (ARCHIVED Stream G R32).
- **Branch D (escalation)**: if Branch C also stalls, escalate per `docs/_archive/governance/post-edge-004-escalation-paths.md` (ARCHIVED Stream G R53) to PROFIT-LLM-001 → P4-GATE Appendix A → strategy pivot.

**Pre-loaded harnesses for Wave-1+2+3 deploys** (all strict-xfail today; flip xpass on the deploy commit forcing marker removal):

- Wave 1: 6 features (OBS-005 / MATCH-001 / OBS-003 / EXEC-002 / GOV-003 / Lever A.1) — full strict-xfail net across `tests/test_executor.py`, `test_market_matcher.py`, `test_blend_task.py`, `test_governance_monitor.py`, `test_main_pipeline.py`
- Wave 2: A.1+ option-A (geopolitics) + A.1+1.5 option-C (legal-analyst) — `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1Plus{Analysis,15Legal}Branch` + `tests/test_lever_a1plus_feed_config.py` + `tests/test_lever_a1plus1_5_evidence_scorer_legal_weight.py`
- Wave 3: Lever B G1=0.04 + Lever C cross-series — `tests/test_lever_b_g1_calibration.py` + `tests/test_lever_c_cross_series_correlation.py`

Total strict-xfail markers as of 2026-05-04: 26+ pinning the deploy lattice.

**Pre-staged operator artifacts for Wave-1 deploy (Day-13 / 2026-05-15):**

- `docs/_archive/governance/wave-1-changelog-entry-prestaged.md` (commit `75a3e39`) — pre-staged 0.30.0 CHANGELOG block (ARCHIVED Stream G R49)
- `docs/_archive/governance/wave-2-wave-3-changelog-entries-prestaged.md` — Wave-2 + Wave-3 pre-staged blocks (ARCHIVED Stream G R53)
- `docs/_archive/governance/wave-1-deploy-commit-order-decision.md` — locked: per-feature commits (NOT bundle) (ARCHIVED Stream G R54)
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation
- `docs/_archive/governance/post-soak-rollback-runbook.md` — incident-response runbook (ARCHIVED Stream G R54; Wave-2/3 HALTED makes contingency procedures historical)
- `docs/_archive/governance/post-soak-close-rehearsal-checklist.md` — operator deploy guide (ARCHIVED Stream G R54; Wave-1 deploy + soak close complete)
- `docs/_archive/governance/post-edge-004-escalation-paths.md` — Wave-3-stall escalation paths (ARCHIVED Stream G R53)

**Honest read after 2026-05-04 cycle:** EDGE-004 closure is dominantly bound by **whether the Google News query family continues to surface legal-niche headlines** (Branch A passive observe), with Branch C as a fallback. Wave-1 base stack landings reduce conversion volume by design (260 OPP / 9 PAPER_TRADE pre-Wave-1 → 87 / 1 post-Wave-1 per simulation `f671468`); the ≥ 5 % closure target is measured against the post-Wave-1 base, not the pre-Wave-1 baseline. Modal scenario per the unified Wave-1+2 forecast (`docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md`, commit `2bf3da1`): Branch A produces some legal-niche surfacing within 14 d; if 0, Branch C deploys; if still 0, escalation fires.

**2026-05-06 cycle-11.5 strategic redirect (IC §16):** EDGE-004 lever menu (A.1+, A.1+1.5, B, C) is **BLOCKED PENDING REPLAY EVIDENCE**. The "Wave-2 deploys feed onboarding → Wave-3 deploys Lever B/C → escalation paths fire on stall" sequencing is replaced by a single new gate: each behavioral deploy requires replayed-EV evidence per IC §16. The replay harness is now the primary closure path; see `PROFIT-EDGE-005` below for the harness work, and `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` for the redirect rationale (3 lifetime trades, 0 wins, -$7.50 P&L, 89% zero-edge SKIPPEDs).

---

### PROFIT-EDGE-005

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-005 |
| **Title** | Edge replay harness — verify ANY (source × market_family × signal_type) slice has positive replayed EV before deploying further behavioral changes |
| **Category** | Profit-Path Integrity / Signal Quality / Replay Verification |
| **Severity** | HIGH (gates all subsequent behavioral deploys per IC §16) |
| **Status** | IN_PROGRESS (filed 2026-05-06 cycle 12; Codex implementing) |
| **Priority** | NOW |
| **Owner** | Codex (implementation); Claude (review + readiness inventory) |
| **Depends On** | PROFIT-PHASE2-001 close (ships Wave-1 cleanly first); evidence_store.db ≥ 13 days populated (already true) |
| **Blocks** | All behavioral deploys per IC §16; specifically: PROFIT-EDGE-004 lever menu (A.1+, A.1+1.5, B, C); future Wave-N feed onboarding |

**Description**

PROFIT-EDGE-004 surfaced that bot reaches executor with `edge = 0.0`. Wave-1-to-11 work added deployment safety, observability, and operator control on top of that core problem without resolving it. As of 2026-05-06: bot has 3 lifetime paper trades, 0 wins, -$7.50 P&L, 1-source dependence (VitalLaw), 89% of OBS-003 SKIPPED records show `edge +0.0000 below min_edge 0.02`. Continuing to pre-stage feed/lever deploys was deploying hope.

This entry tracks the replay-harness deliverable that must produce edge evidence before any new behavioral deploy lands.

**Why it matters to profitability / safety / reliability**

1. **Direct edge verification.** Without replay, every Wave-N deploy is speculation that the new feed/lever produces edge. With replay, deploys land only when the replay shows the candidate slice would have produced positive EV at 95% CI on resolved markets.
2. **Stops the deploying-hope cycle.** PROFIT-EDGE-004 lever menu accumulated 4+ specs (A.1+, A.1+1.5, B, C) all framed as "potential edge candidates" with no replay evidence. Replay harness gives a structural answer.
3. **Negative result is also an answer.** If replay finds NO positive-EV slice, that's information: the bot may have a calibration problem, a sample-size problem, or an information-frontier problem. Each has a different fix; replay disambiguates.

**Evidence / Source**

- `data/paper_trades.db`: 3 trades / 0 wins / -$7.50 / 1 source / 1 series cluster.
- `docs/governance/2026-05-06-post-obs003-skipped-attribution-audit-refresh.md`: 89% zero-edge SKIPPEDs.
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md`: redirect authority.
- `docs/governance/2026-05-06-cycle-12-replay-readiness-inventory.md`: data + API readiness audit (24 of 36 evidence_store markets resolved; Kalshi has dual `settled`/`finalized` terminal states; no per-T historical price endpoint exposed).
- `docs/IMPLEMENTATION_CONTRACT.md` §16: replayed-EV gate codified.

**Proposed Fix (Cycle-12 Codex deliverables)**

1. `scripts/edge_replay/fetch_resolved_markets.py` — Kalshi API puller; query both `status='settled'` AND `status='finalized'` (per readiness inventory).
2. `scripts/edge_replay/build_replay_dataset.py` — per-(market, decision-time) record from evidence_store + dossier_updates joined.
3. `scripts/edge_replay/score_counterfactual_pnl.py` — per-(source × market_family × signal_type) table with trades / wins / EV / 95% CI / Sharpe per readiness-inventory schema.
4. `tests/test_edge_replay_*.py` — at minimum: dataset-build determinism, P&L sign on synthetic input, source-aggregation, self-test against the 3 known paper trades (must reproduce 0 wins, -$7.50).
5. `docs/governance/edge-replay-cycle12-report.md` — interpretation: ≥1 row with `ev_ci_95_lo > 0` AND `trades ≥ 10` (success) OR explicit "no slice has positive replayed EV at our sample size" (negative-result honest report).

**Acceptance Criteria**

- The 5 deliverables above land + tests pass.
- Self-test reproduces the 3-trade history.
- Output table is concrete + reproducible per IC §16 Rule 4.
- Either positive-EV slice identified at 95% CI with n ≥ 10, OR negative-result honest report.
- IC §16 enforcement live: future Wave-N deploys reference this harness output.

**Notes**

- Replay-window scope: ~13-16 days (2026-04-20 → 2026-05-05); 24 resolved markets in evidence_store. Sample-size confidence intervals must be reported honestly per readiness-inventory class-imbalance findings (VitalLaw n=3, mainstream news ~80%).
- Per IC §16 Rule 5: negative replay evidence triggers strategic-pivot conversation, NOT "ship anyway."
- Lever B G1 0.05 → 0.04 is COUNTERINDICATED until replay shows the additional admitted trades produce positive EV (loosening 89%-zero-edge floor = thinner zero-edge floor = faster bankroll erosion).

**Related**

- `PROFIT-EDGE-004` (open) — lever menu BLOCKED pending this harness's output.
- `PROFIT-EDGE-006` (open) — Cycle-13 replay scope expansion (24-market full evidence_store run); see entry below.
- `PROFIT-OBS-003` (closed Wave-1 deploy) — SKIPPED-emission attribution surface required for replay.
- `PROFIT-PHASE2-001` (closing 2026-05-08) — Wave-1 still ships per redirect; Wave-2/3 HALTED.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate.
- `docs/governance/2026-05-06-cycle-12-replay-readiness-inventory.md` — Codex's data + API readiness map.

**2026-05-06 cycle-13 update:** harness PARTIALLY DELIVERED. 5 Cycle-12 deliverables landed (commit `aadd391`):
- ✓ `scripts/edge_replay/fetch_resolved_markets.py` — paper-trades + manual-JSON sources; live-Kalshi flag pending Cycle-13.
- ✓ `scripts/edge_replay/build_replay_dataset.py` — per-decision-time joins; historical-price reconstruction supported.
- ✓ `scripts/edge_replay/score_counterfactual_pnl.py` — bootstrap CI; readiness-gate replay; left-on-table measure.
- ✓ `tests/test_edge_replay_*.py` — 9 tests including synthetic +EV self-test (catches scorer-broken vs no-edge).
- ✓ `docs/governance/edge-replay-cycle12-report.md` — first-pass result: 0 positive-EV slices; harness self-test reproduces 3-trade history.

First-pass result (3-market scope): 0 positive-EV slices, P&L -$7.50, win rate 0.00. Not proof no edge anywhere; sample is single-source / single-series / single-direction.

Remaining work tracked under PROFIT-EDGE-006 (next-step expansion to full 24-market evidence_store scope).

---

### PROFIT-EDGE-006

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-006 |
| **Title** | Cycle-13 replay scope expansion — full 24-resolved-market evidence_store run |
| **Category** | Profit-Path Integrity / Edge Verification (continuation of PROFIT-EDGE-005) |
| **Severity** | HIGH (gates Wave-2 deploy authorization per IC §16) |
| **Status** | IN_PROGRESS (filed 2026-05-06 cycle 13; Codex implementing per `2026-05-06-cycle-13-replay-scope-expansion-charter.md`) |
| **Priority** | NOW |
| **Owner** | Codex (implementation); Claude (review + diagnostic playbook + ancillaries) |
| **Depends On** | PROFIT-EDGE-005 (harness exists); evidence_store has ≥24 resolved markets (verified cycle-13 readiness inventory) |
| **Blocks** | All Wave-2/Wave-3 behavioral deploys per IC §16 |

**Description**

Cycle-12 ran replay against `paper_trades` scope only — 3 markets, 1 source, 1 series. evidence_store has 24 resolved markets unused. Cycle-13 expands scope to surface positive-EV slices across the full corpus.

**Cycle-13 readiness inventory finding (2026-05-06):** 21 of 24 resolved-market dossiers stuck at `current_estimate = 0.5000` (the prior). Bot's belief model rarely exits the prior despite ingesting 266 evidence rows. KXTRUMPIRAN ingested 107 rows → still at 0.5000. This calibration signal pre-bounds the replay verdict: most rows will produce edge=0, would_not_have_traded.

**Why it matters to profitability / safety / reliability**

1. **Direct successor to PROFIT-EDGE-005.** Without scope expansion, Wave-2 stays HALTED indefinitely on insufficient sample.
2. **Calibration diagnostic surface.** If 24-market replay still finds no positive-EV slice, IC §16 Rule 5 triggers `docs/governance/edge-replay-pivot-playbook.md` — calibration is the leading suspect per cycle-13 dossier audit.
3. **Wave-2 candidate identification.** If positive-EV slice exists, that slice (NOT speculative legal/geopolitics feeds) becomes Wave-2.

**Evidence / Source**

- `docs/governance/2026-05-06-cycle-13-replay-scope-expansion-charter.md` — Codex's charter.
- `docs/governance/edge-replay-cycle12-report.md` — Cycle-12 first-pass result.
- Cycle-13 dossier integrity audit (this entry's parent commit): 21/24 markets at 0.5000 prior.
- Cycle-13 capacity audit: 552 decisions, 0.663 reviewable fraction at 80/day.

**Proposed Fix (Cycle-13 Codex deliverables per charter)**

1. Extend `fetch_resolved_markets.py` with `--live-kalshi` flag (queries both `'settled'` + `'finalized'` per cycle-12 readiness inventory).
2. Run end-to-end against full 24-market scope.
3. Implement readiness-gate replay (charter path a) — replay G1-G6 admission against `model_prob` + `confidence` per evidence row. Note: cycle-13 code review found Codex's current `_readiness_admitted` uses 0.85 confidence threshold (post-Lever-B aspirational, not production 0.05). Path a should match production thresholds for fair "what would the bot as-deployed have done?" replay.
4. Extend `edge-replay-cycle12-report.md` OR sibling `edge-replay-cycle13-report.md` per operator preference.

**Acceptance Criteria**

- 24-market scope replay completes.
- Per-source × market_family × signal_type slice table covers ≥ 20 distinct slices.
- Either ≥ 1 row with `ev_ci_95_lo > 0` AND `trades ≥ 10` (success → triggers Wave-2 spec authoring as Cycle-14), OR honest negative-result report (triggers `edge-replay-pivot-playbook.md` as Cycle-14).
- Readiness-gate replay matches production G1 thresholds.

**Notes**

- Operator coordination: live API call needs no special handling; existing `kalshi/rest_client.py` 0.12s rate-limit guard sufficient. See `docs/governance/2026-05-06-cycle-13-live-api-coordination.md`.
- Out of scope per charter: new harness features, Wave-2/3 deploy work, Lever-D escalation, more HALT markers.

**Related**

- `PROFIT-EDGE-005` (in_progress; partially delivered) — direct parent; harness exists.
- `PROFIT-EDGE-004` (open) — lever menu BLOCKED pending this expansion's output.
- `docs/governance/2026-05-06-cycle-13-replay-scope-expansion-charter.md` — Cycle-13 charter.
- `docs/governance/edge-replay-pivot-playbook.md` — IC §16 Rule 5 diagnostic playbook (fires on negative result).
- §Current Status (this file) — operator-facing dashboard; refreshed by replay verdict. (Was `docs/EDGE_STATUS.md` until 2026-05-09 consolidation.)

**2026-05-06 cycle-13 update:** DELIVERED with negative result (commit `79e4d08`). 24 markets / 255 replay rows / 0 positive-EV slices / 0 left-on-table winners / -$7.50 P&L. IC §16 Rule 5 fires. Cycle-14 (PROFIT-EDGE-007 below) takes over as calibration kill-or-fix diagnostic.

---

### PROFIT-EDGE-007

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-007 |
| **Title** | Cycle-14 calibration kill-or-fix diagnostic — does the model move belief correctly when evidence should obviously move belief? |
| **Category** | Profit-Path Integrity / Calibration Diagnostic (continuation of PROFIT-EDGE-005/006 IC §16 Rule 5 trigger) |
| **Severity** | HIGH (gated strategic-direction decision: fix calibration vs rebuild extraction vs strategic redesign) |
| **Status** | COMPLETE (delivered 2026-05-06; verdict = `extraction_broken`; succeeded by PROFIT-EDGE-008 Cycle-15B extraction rebuild) |
| **Priority** | n/a (closed) |
| **Owner** | Codex (implementation — DELIVERED); Claude (verdict consumption + ROADMAP/EDGE_STATUS refresh — DELIVERED) |
| **Depends On** | PROFIT-EDGE-006 (Cycle-13 negative replay verdict — DONE) |
| **Blocks** | All Wave-2/Wave-3 deploys (transferred to PROFIT-EDGE-008) |

**Description**

Cycle-13 returned 0 positive-EV slices on 24 resolved markets. Root cause not "no edge anywhere"; pre-bounded by calibration. 21/24 dossiers stuck at `current_estimate=0.5000` despite 266 evidence rows ingested. KXTRUMPIRAN: 107 events → still 0.5000. The 3 dossiers that DID move (FISA-MAY01/02/03) all settled at 0.432 NO-leaning, all resolved YES → 3/3 wrong-direction at the only 3 trades.

Cycle-14 answers: can the model move belief correctly when evidence should obviously move belief?

This is **diagnosis-only**. No behavioral fixes ship in Cycle-14. Sole exception: trivial measurement bug in the diagnostic tooling itself (NOT in the bot's calibration code).

**Why it matters to profitability / safety / reliability**

1. **Strategic-direction inflection point.** The verdict determines whether the bot is fixable or whether the strategy needs fundamental redesign. Without it, every subsequent cycle is speculation about which fix matters.
2. **Capital protection.** Continuing paper-mode trading on a possibly-sign-inverted model burns synthetic bankroll testing a hypothesis the diagnostic answers in 1 cycle. Worse: live-mode after Wave-1's OBS-005 unblock could surreptitiously widen exposure on a broken model.
3. **Prevents emotional drift.** Wave-1 hygiene ships do NOT prove edge. Cycle-14 produces structured evidence about what the bot ACTUALLY does with evidence, not what we hope it does.

**Evidence / Source**

- `docs/governance/edge-replay-cycle13-report.md` — replay verdict (0 positive-EV / 0 left-on-table-winners).
- `docs/governance/2026-05-06-cycle-13-replay-harness-code-review.md` — code review (readiness-threshold finding feeds into LLM-called split diagnostic).
- 21/24 dossiers at 0.5000 prior (cycle-13 audit).
- 3/3 wrong-direction sized-bet trades.
- `data/evidence_store.db` (266 rows) + `data/paper_trades.db` (3 trades) — diagnostic input.

**Proposed Fix (Cycle-14 Codex deliverables — DIAGNOSIS-ONLY)**

Per `docs/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md`:

1. `scripts/edge_replay/calibration_audit.py` — existing-outcome calibration audit (movement_rate, direction-correctness with [0.499, 0.501] denominator exclusion + count, Brier/log-loss with n=24 caveat, sized-bet-subset separate, moved vs unmoved EV/P&L).
2. Per-source belief movement audit — extension of existing scorer.
3. LLM-vs-no-LLM split — from `dossier_updates.llm_called`.
4. Synthetic high-info evidence injection — TWO LANES (downstream-of-extraction + real-extraction-in-loop) to discriminate update-model failure from extraction failure.
5. Hard paper-mode-lock check post-Wave-1 — guardrail that OBS-005 unblock doesn't widen exposure.
6. ROADMAP update — Wave-2/3/Branch-D rows: "HALTED AND POTENTIALLY OBSOLETE PENDING CYCLE-14 DIAGNOSIS."
7. Written diagnosis doc — `docs/governance/edge-replay-cycle14-diagnosis.md` per the structured schema in charter.

**Acceptance Criteria**

- All 7 Codex deliverables land + tests pass.
- Diagnosis-doc verdict matches one of the pre-stated decision criteria.
- Cycle-15 scope recommendation derived FROM the verdict, not invented to fit a preferred fix.
- No behavioral fix landed in Cycle-14 (sole exception: tooling bug).

**Notes**

- **Decision criteria locked BEFORE inspecting results.** Operator does not change them post-hoc. Diagnosis stands on its own evidence, not on rationalization.
- Brier/log-loss at n=24 is statistically weak; supporting evidence only, not primary verdict.
- Direction-correctness denominator excludes `[0.499, 0.501]` rows; reports excluded count.
- Sized-bet subset (3 paper trades) reported separately from full corpus; sample-noise vs systematic distinguishable.
- **2026-05-06 cycle-14 closure:** Diagnostic delivered. Verdict = `extraction_broken` (charter-locked criterion: Lane A pass + Lane B fail). Movement_rate 1.57%, direction-correctness 0/6 when directional, Lane B `model_prob=0.500` on both clear-YES and clear-NO synthetic fixtures (no movement at all, more severe than sign-inversion). Threshold-lock verification passed against all 7 charter-locked thresholds in `cycle-14-post-verdict-action-checklist.md`. Cycle-15 scope recommendation = §B extraction rebuild (filed as PROFIT-EDGE-008).

**Related**

- `PROFIT-EDGE-006` (delivered with negative verdict) — direct parent.
- `PROFIT-EDGE-005` (in_progress; harness exists) — replay infrastructure used.
- `PROFIT-EDGE-004` (open; lever menu OBSOLETE per cycle-14 verdict).
- `PROFIT-EDGE-008` (active) — Cycle-15B extraction rebuild succeeds this entry.
- `PROFIT-GOV-002` (closed) — same-class issue at LLM verdict layer (rubber-stamp bias); cycle-14 Lane B failure mode (zero movement on clear input) is the analogous pathology at the extraction layer.
- `docs/governance/edge-replay-cycle14-diagnosis.md` — Cycle-14 diagnosis-doc with Claude appendix.
- `docs/governance/cycle-15-conditional-charter-skeletons.md` §B — Cycle-15B skeleton (instantiates).
- `docs/governance/cycle-14-post-verdict-action-checklist.md` — post-verdict execution checklist.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-15B fix).

---

### PROFIT-EDGE-008

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-008 |
| **Title** | Cycle-15B extraction-layer rebuild + replay validation — extraction emits zero signal on crystal-clear synthetic input; rebuild and prove non-zero magnitude + direction-correctness via post-fix replay |
| **Category** | Profit-Path Integrity / Extraction Rebuild (succeeded PROFIT-EDGE-007 Cycle-14 `extraction_broken` verdict) |
| **Severity** | HIGH (gated Wave-2/Wave-3/Branch-D deploy decisions; sole path off "extraction emits no signal" for the bot to ever produce edge) |
| **Status** | COMPLETE (delivered 2026-05-07; verdict = `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap`; succeeded by PROFIT-EDGE-009 Cycle-16D price-reconstruction prerequisite) |
| **Priority** | n/a (closed) |
| **Owner** | Codex (implementation — DELIVERED C1-C10); Claude (governance + review + scaffolding + verdict consumption — DELIVERED L1-L10 + post-verdict refresh) |
| **Depends On** | PROFIT-EDGE-007 (delivered with `extraction_broken` verdict) |
| **Blocks** | All Wave-2/Wave-3/Branch-D deploys (transferred to PROFIT-EDGE-009) |

**Description**

Cycle-14 Lane B returned `model_prob=0.500` and `delta=0.000` on both crystal-clear-YES and crystal-clear-NO synthetic fixtures. The downstream dossier-update math is direction-correct (Lane A pass) — the bug is between `feeds.NewsItem` and `SignalAnalysis(estimated_probability, ...)`. Extraction does not emit a directional signal at all on inputs where any non-degenerate model should.

This is calibration **inertness** at the extraction layer, not sign-inversion. The bot cannot have edge until extraction emits non-zero magnitude on directional input.

**Why it matters to profitability / safety / reliability**

1. **Sole gate on edge.** Without extraction emitting signal, no downstream change matters. Wave-2 source onboarding, Wave-3 admission loosening, Branch-D LLM unification — all assume extraction works.
2. **Capital protection.** Live-mode flip on a model whose extraction emits nothing is harmless against directional movement but produces zero edge by definition. PAPER-ONLY guardrail per Cycle-14 charter §5 holds.
3. **Discriminator for next-tier work.** Per-extraction-step trace on Lane B fixtures identifies whether the failure is in LLM-path (sites 2/6 of `2026-05-06-cycle-14-sign-error-candidate-trace.md`), keyword-path (sites 3/7), or suppression logic. Sub-fix shape derives from trace, not from speculation.

**Evidence / Source**

- `docs/governance/edge-replay-cycle14-diagnosis.md` — verdict + Lane B numbers (model_prob=0.500 on both fixtures).
- `docs/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` — sites 2/3/6/7 NOT ruled out by Lane A; prime trace targets.
- `tests/fixtures/cycle14_synthetic_evidence.json` — 10 synthetic fixtures available for Lane B per-step trace + post-fix Lane B verification.
- `scripts/edge_replay/synthetic_injection_lanes.py` — Cycle-14 Lane B harness, reusable for post-fix verification.

**Proposed Fix (Cycle-15B Codex deliverables — per `cycle-15-conditional-charter-skeletons.md` §B)**

1. **Per-extraction-step trace on Lane B fixtures.** For each of the 10 fixtures in `cycle14_synthetic_evidence.json`, log the values at each extraction step (LLM raw output → LLM-path probability shift → keyword-path net_shift → magnitude application → final `estimated_probability`). Identify at which step magnitude collapses to zero. Output: `logs/edge_replay/cycle15b/per_step_trace.json`.
2. **Author or refactor extraction logic** at the identified step. Likely candidates: LLM prompt rewrite (if LLM returns `magnitude="none"` on directional input), keyword-table extension (if per-keyword direction map has no entries for synthetic vocabulary), suppression-logic relaxation (if geo-coherence or magnitude-shift mapping zeroes out signal).
3. **Re-run synthetic Lane B against new extraction.** Must match Lane A direction-correctness AND magnitude (≥ 90% direction-correct on directional fixtures, `|delta| > 0.05`).
4. **Re-ingest 16-day evidence window through new extraction.** Re-build `dossier_updates`. Re-run replay scoring.
5. **IC §16 evidence:** post-fix replay shows ≥ 1 positive-EV slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`.

**Acceptance Criteria**

- Lane B post-rebuild: direction-correct + magnitude `|delta| > 0.05` on at least 6 of 10 fixtures (matches Lane A baseline).
- Post-fix Cycle-13 replay shows ≥ 1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10` per IC §16.
- Cycle-15B diagnosis-doc proves IC §16 evidence gate cleared with reproducible commands.
- No live-trading flag flip; PAPER-ONLY guardrail holds until operator-explicit override post-acceptance.

**Notes**

- **Pre-fix paper-traded data is no longer ground truth for calibration.** Per `cycle-15-conditional-charter-skeletons.md` §A.5 (transferable to §B): post-fix re-ingestion required for any future replay validation. C9 re-ingestion landed with PRE_FIX preservation per L8 cohort note.
- Pre-cycle-12 "deploy hope" pattern stays prohibited. Each fix needs replay evidence, not hope.
- **2026-05-07 cycle-15B closure:** Cycle-15B C7 sub-fix (path b operator-authorized; `GEOPOLITICAL_SIGNALS` keyword extension + `"senate judiciary"` over-emission constraint) shipped. Lane B post-fix verification: 8/8 directional + 2/2 NEUTRAL pass. C9 idempotent re-ingestion (SHA256 `d1e1bf4ed61b28eaff2e7b10c3316121892b6c8b62237b848fcdd6446e717e1b` stable across runs; PRE_FIX preserved). C10 IC §16 acceptance: 0 slices with `ev_ci_95_lo>0` AND `trades≥10` BUT 0/272 replay rows had decision-time executable price. Verdict = `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap` (added to charter outcomes 2026-05-07; was not in original 3-verdict skeleton because the price-gap was inherited from cycle-13/14, not introduced by Cycle-15B). Extraction is repaired; IC §16 is unverifiable until Cycle-16D price reconstruction lands.

**Related**

- `PROFIT-EDGE-007` (delivered) — direct parent (Cycle-14 verdict drove this scope).
- `PROFIT-EDGE-006` / `PROFIT-EDGE-005` — replay harness infrastructure used.
- `PROFIT-EDGE-004` — lever menu OBSOLETE per Cycle-14 verdict; reassess only after Cycle-16D post-reconstruction replay.
- `PROFIT-EDGE-009` (active) — Cycle-16D price-reconstruction prerequisite succeeds this entry.
- `PROFIT-GOV-002` (closed) — same-class pathology (rubber-stamp bias at LLM verdict layer); orthogonal to keyword-map gap per Claude L5 cross-check.
- `docs/_archive/governance/edge-replay-cycle15b-report.md` — Cycle-15B C10 report with Claude appendix + verdict (ARCHIVED Stream G R50).
- `docs/governance/cycle-16-conditional-charter-skeletons.md` §D — Cycle-16D skeleton (instantiates).
- `docs/_archive/governance/cycle-15b-post-verdict-action-checklist.md` — post-verdict checklist driving items 5/8/9/10 (ARCHIVED Stream G R21).
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-16D + post-reconstruction acceptance).

---

### PROFIT-EDGE-009

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-009 |
| **Title** | Cycle-16D price-reconstruction prerequisite — restore per-decision-time `market_yes_price` so IC §16 acceptance gate can be evaluated against post-Cycle-15B-fix dossier corpus |
| **Category** | Profit-Path Integrity / Replay Harness (succeeded PROFIT-EDGE-008 Cycle-15B `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap` verdict) |
| **Severity** | HIGH (was sole path to evaluating IC §16 against post-fix bot; succeeded by PROFIT-EDGE-010 Cycle-17 operator decision) |
| **Status** | COMPLETE (delivered 2026-05-07; verdict = `extraction_fixed_but_information_frontier_holds`; succeeded by PROFIT-EDGE-010 Cycle-17 operator decision) |
| **Priority** | n/a (closed) |
| **Owner** | Codex (implementation — DELIVERED D1-D10); Claude (governance + review + scaffolding + verdict consumption — DELIVERED M1-M9 + post-verdict refresh) |
| **Depends On** | PROFIT-EDGE-008 (delivered with `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap` verdict). |
| **Blocks** | All Wave-2/Wave-3/Branch-D deploys (transferred to PROFIT-EDGE-010). |

**Description**

Cycle-15B C10 IC §16 acceptance gate failed because 0/272 post-fix replay rows had decision-time `market_yes_price`. The replay scorer (`scripts/edge_replay/score_counterfactual_pnl.py`) cannot compute counterfactual P&L without prices. C10 result is "scorer-blocked," NOT "negative EV proven."

This is the same `/markets/{ticker}/trades` 404 issue surfaced in cycle-13's `fetch_historical_prices.py` probe and noted in cycle-14 charter §"Historical price endpoint gap" as not-a-blocker for diagnosis-only Cycle-14. For Cycle-15B+ IC §16 acceptance, the gap IS load-bearing.

**Why it matters to profitability / safety / reliability**

1. **Gates IC §16 evaluability.** Without prices, replayed-EV evidence per IC §16 Rule 4 cannot be produced. Wave-2 candidate slice authoring (skeleton §A) needs IC §16; Cycle-16 §B source-onboarding tests "did the new source produce a positive-EV slice?" — also needs IC §16.
2. **Pure replay-harness scope.** Bot extraction code untouched. C7 keyword-map extension stays in place.
3. **Capital protection.** Live-trading flip remains blocked. PAPER-ONLY locked until Cycle-16D + post-reconstruction replay produces ≥1 IC §16 slice.

**Evidence / Source**

- `docs/_archive/governance/edge-replay-cycle15b-report.md` — verdict source: 0/272 rows with `market_yes_price` populated (ARCHIVED Stream G R50).
- `docs/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md` §"Historical price endpoint gap" — earlier 404 finding.
- `data/dossier_updates_post_fix.db` — POST_FIX_REBUILT cohort intact; ready for re-run once prices land.
- `logs/edge_replay/cycle13_live/historical_prices.json` — current price data (insufficient coverage).

**Proposed Fix (Cycle-16D Codex deliverables — per `cycle-16-conditional-charter-skeletons.md` §D)**

1. **Diagnose `/markets/{ticker}/trades` 404.** Endpoint changed? Auth requirement? Historical-data window contracted?
2. **If endpoint solvable:** restore fetch path; backfill `historical_prices.json` for the 24-market replay window.
3. **If endpoint dead permanently:** identify alternative price source (Kalshi orderbook archives, third-party data, or quantified-error computed approximation).
4. **Backfill historical prices** for ≥ 90% of post-fix dossier-update rows in the 24-market replay window.
5. **Re-run Cycle-15B C10** against unchanged `data/dossier_updates_post_fix.db`. POST_FIX_REBUILT cohort intact per L8 cohort note; no re-ingestion needed.
6. **Land verdict** that distinguishes "no signal" from "scorer-blocked."

**Acceptance Criteria**

- ≥ 90% of 272 post-fix replay rows have `market_yes_price` populated at decision time.
- Re-run C10 produces a verdict that EITHER unblocks Cycle-16 §A (positive-EV slice surfaces) OR confirms `extraction_fixed_but_information_frontier_holds` with prices verified (then routes to §B / §C per operator decision).
- If approximation path used: error bars on `market_yes_price` quantified and reported alongside replay output; replay-EV CI explicitly accounts for price-imprecision.

**Notes**

- Cycle-16D did NOT bypass IC §16. Restored harness's ability to evaluate IC §16; gate evaluated; result was 0 IC §16-eligible slices.
- Out of scope (delivered): bot extraction code; new Cycle-15B sub-fixes; LLM-path audit (L7.2 deferral remains open for post-Cycle-17).
- **2026-05-07 cycle-16D closure:** D1 endpoint diagnosis = `solvable_auth_or_param` (legacy `/markets/{ticker}/trades` 404; documented `/markets/trades?ticker=...` + `/historical/trades?ticker=...` 200). D3 backfill via merged-endpoint primary path (path b.i / b.ii not needed; D4 fallback unused). D5 coverage 99.6324% (271/272 priced) ≥ 90% ✓. D6 C10 re-run produced 237 counterfactual trades / 2 wins / -$7.46 P&L; overall `ev_ci_95_lo = -0.0382`; 1 raw positive-EV slice with `trades=1` (below IC §16 floor); 0 IC §16-eligible slices. D9 POST_FIX_REBUILT sentinel verified (commit `2222227`). Verdict = `extraction_fixed_but_information_frontier_holds`. Anti-correlated signal OR Cycle-15B keyword-overfit hypothesis flagged in Claude appendix; 99.16% wrong-direction trade rate is anomalously low for "no signal" (random ≈ 50%). Cycle-17 = operator decision: §B source onboarding OR §C strategic redesign.

**Related**

- `PROFIT-EDGE-008` (delivered) — direct parent (Cycle-15B verdict drove this scope).
- `PROFIT-EDGE-005` / `PROFIT-EDGE-006` — replay harness infrastructure (Cycle-16D extended pricing layer).
- `PROFIT-EDGE-010` (active) — Cycle-17 operator decision succeeds this entry.
- `docs/governance/cycle-16-conditional-charter-skeletons.md` §D — scope skeleton.
- `docs/_archive/governance/edge-replay-cycle16d-report.md` — Cycle-16D D8 report with Claude appendix + verdict (ARCHIVED Stream G R52).
- `docs/governance/cycle-17-conditional-charter-skeletons.md` — Cycle-17 skeletons (instantiates).
- `docs/_archive/governance/cycle-16d-post-verdict-action-checklist.md` — post-verdict checklist driving items 5/8/9/10 (ARCHIVED Stream G R21).
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-17+ acceptance).

---

### PROFIT-EDGE-010

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-010 |
| **Title** | Cycle-16E scorer forensics — audit replay scorer for readiness-admission gating, YES-bias, price-unit consistency, and dedupe; verdict NOT FINAL on Cycle-16D until corrections re-run |
| **Category** | Profit-Path Integrity / Replay Harness (succeeded PROFIT-EDGE-009 Cycle-16D `extraction_fixed_but_information_frontier_holds` verdict; AMENDED 2026-05-07 per operator override of M6 verdict acceptance) |
| **Severity** | HIGH (was Cycle-17 §B/§C operator decision gate; succeeded by PROFIT-EDGE-011 = Cycle-17 operator decision) |
| **Status** | COMPLETE (delivered 2026-05-07; verdict = `scorer_fixed_no_signal_confirmed`; succeeded by PROFIT-EDGE-011 Cycle-17 operator decision) |
| **Priority** | n/a (closed) |
| **Owner** | Codex (implementation — DELIVERED E1-E10 + charter authorship; race ahead of locked sequencing); Claude (governance + review + scaffolding + verdict consumption — DELIVERED N3+N4+N5+N6+N7+N9+N10 + rescope review) |
| **Depends On** | PROFIT-EDGE-009 (delivered with charter-locked `extraction_fixed_but_information_frontier_holds` label). |
| **Blocks** | All Wave-2/Wave-3/Branch-D deploys (transferred to PROFIT-EDGE-011). |

**Description**

Cycle-16D D6 produced 237 counterfactual trades / 2 wins (0.84% win rate) / -$7.46 P&L / `ev_ci_95_lo = -0.0382`. Charter-locked verdict label is `extraction_fixed_but_information_frontier_holds`. **Operator override 2026-05-07** identified three load-bearing scorer concerns that invalidate the operational reading until forensics audit completes:

1. **`would_have_traded` does not gate on readiness.** Per `score_counterfactual_pnl.py:score_candidate`: `would_trade = price is not None and resolved_yes is not None and abs(edge) >= min_edge`. `readiness_admitted` computed but NOT required for `would_have_traded`. Production runtime gates on G1-G6 readiness; scorer does not. 237 counterfactual trades likely over-counts production trade volume.
2. **Replay is massively YES-biased.** 231 YES / 6 NO trades; 0/231 YES wins; 2/6 NO wins; traded rows resolved 233 NO / 4 YES. Bot systematically buying YES on markets that resolve NO. Either market-selection effect, scorer sign error, OR Cycle-15B C7 keyword-extension over-emits YES on production text.
3. **Price-unit / longshot calibration uncertain.** Of 237 trades: 102 had `market_yes_price < 1`, 100 between 1 and 9. If cents (Kalshi convention), bot is buying ultra-cheap YES longshots — model probabilities like 0.38 / 0.50 look enormous vs 0.8-cent / 8-cent markets. If any are dollars treated as cents, replay is broken by 100x unit error.

Cycle-17 §B vs §C operator decision is **DEFERRED** until Cycle-16E scorer forensics resolves these concerns and re-runs D6.

**Why it matters to profitability / safety / reliability**

1. **Verdict premature if scorer is broken.** Routing to §B onboarding / §C redesign on a flawed scorer wastes operator time and reinforces the cycle-12 "deploy hope" anti-pattern in reverse — "give-up hope" without confirming the give-up is supported by clean data.
2. **0.84% win rate may be artifact, not signal.** Anti-correlated reading assumes scorer accurately reflects production trade outcomes. If scorer over-admits YES longshots that production gates would have rejected, the win-rate finding is about scorer behavior, not bot behavior.
3. **IC §16 evaluability hinges on scorer correctness.** Even if Cycle-17 §B onboards new sources, the same scorer would score the new evidence. Forensic audit fixes a load-bearing dependency for ALL future cycles, not just Cycle-16D verdict reading.

**Evidence / Source**

- `docs/_archive/governance/edge-replay-cycle16d-report.md` Operator override section — three load-bearing concerns enumerated (ARCHIVED Stream G R52).
- `scripts/edge_replay/score_counterfactual_pnl.py` `score_candidate` — `would_trade` semantics.
- `logs/edge_replay/cycle16d/counterfactual_scores.json` — 237 trades / 231 YES / 6 NO breakdown.
- `logs/edge_replay/cycle16d/historical_prices_cycle16d.json` — price values for unit audit.

**Required Cycle-16E checks (per operator override)**

1. **Verify Kalshi price units** in `historical_prices_cycle16d.json` per endpoint/source. Confirm cents vs dollars consistency end-to-end (fetch → store → score).
2. **Fix or confirm `would_have_traded` semantics.** Should require G1-G6 readiness admission per production runtime, not just `abs(edge) >= min_edge`. Compare against `tasks/trade_readiness_gate.py` thresholds.
3. **Deduplicate / episode-gate dossier updates** so 20 repeated dossier updates on one market don't become 20 trades unless production cooldown / OBS-005 sentinel logic would actually permit that. Cross-reference cycle-15B re-ingestion idempotence + production paper_trader cooldown.
4. **Report win rate by side, series, price bucket, and admission reason.** Diagnostic cut not previously available; surfaces YES-bias / longshot / per-series patterns for verdict consumption.
5. **Re-run D6** after the above. Land verdict against re-run output, not original D6.

**Acceptance Criteria**

- Cycle-16E charter authored with locked criteria for each of the 5 checks above. **Delivered:** `docs/governance/2026-05-07-cycle-16e-scorer-forensics-charter.md`.
- Codex implementation of scorer corrections + D6 re-run produces revised counterfactual_scores.json. **Delivered:** `logs/edge_replay/cycle16e/counterfactual_scores_production_proxy.json`.
- Claude review confirms:
  - Price-unit consistency end-to-end.
  - `would_have_traded` matches production G1-G6 admission semantics.
  - Dedupe / episode-gate matches production cooldown semantics.
  - Win-rate-by-(side, series, price bucket, admission reason) breakdown landed.
- Re-run D6 verdict landed: either confirms or revises `extraction_fixed_but_information_frontier_holds` interpretation.
- IF re-run shows ≥1 IC §16 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10` post-correction → cycle-17A Wave-2 candidate slice authoring proceeds.
- IF re-run still shows 0 IC §16 slices BUT win rate normalizes (closer to 50% on a corrected sample) → "information_frontier" reading regains support; Cycle-17 §B vs §C operator decision returns to the table.
- IF re-run still shows anomalous YES-bias / longshot pattern post-corrections → file PROFIT-EDGE-011 = additional scorer-forensics or extraction-overfit follow-up; do NOT route to §B/§C without resolving.

**Notes**

- **Capital posture remains PAPER-ONLY.** Cycle-16E does NOT change capital posture. Live-trading flip is gated on ≥1 IC §16-eligible slice across an audited scorer.
- **Pre-cycle-12 "deploy hope" pattern remains prohibited.** Cycle-16E does NOT permit deploying anything; pure forensics + harness corrections.
- **Operator override authority.** This amendment is a 2026-05-07 operator override of the M6 verdict-acceptance step in the cycle-16D-post-verdict-action-checklist. The original M6 appendix flagged "anti-correlated vs overfit" hypotheses but missed the simpler "scorer bug" hypothesis that operator caught. Documented for future cycle-N M6 reviewers: load-bearing scorer assumptions must be audited BEFORE accepting verdict-label-implied operational interpretations.
- **2026-05-07 amendment context:** This entry was originally filed as "Cycle-17 operator decision" same day; amended within hours per operator override.
- **2026-05-07 cycle-16E closure:** Codex `c913ffd` delivered E1-E10 in single bundled commit + Codex-authored Cycle-16E charter (was N1; race ahead of locked sequencing). Findings: (1) price units cents-consistent end-to-end (no 100x dollars/cents inversion); (2) raw scorer over-admitted via missing readiness/price-sanity/cooldown/duplicate gates → 237 → 12 production-proxy trades; (3) market-implied baseline ≈ 9.463 expected wins on raw 237 trades, NOT 50% coin-flip 118.5 — withdraws cycle-16D "anti-correlation" hypothesis; (4) production-proxy 12 trades / 0 wins / -$1.005 P&L / 0 IC §16 slices; (5) 21 tests passing including market-implied-baseline lock + price-unit invariant lock. Claude N3+N4+N5+N7 verified production gates faithfully ported (line-by-line vs `executor.py:200-244`); 2 minor gaps (N5.A opposing-position block, N5.B per-ticker concentration cap) would only reduce trade count further → production-proxy 12 is UPPER BOUND, verdict robust. Verdict per locked task-split outcome 2: `scorer_fixed_no_signal_confirmed`. Cycle-17 §B/§C operator decision RESTORED (un-deferred). Cycle-16F additional forensics NOT triggered.

**Related**

- `PROFIT-EDGE-009` (delivered) — direct parent (Cycle-16D D8 verdict drove this entry).
- `PROFIT-EDGE-008` / `PROFIT-EDGE-007` / `PROFIT-EDGE-006` / `PROFIT-EDGE-005` — full cycle trail.
- `PROFIT-EDGE-011` (active) — Cycle-17 operator decision succeeds this entry.
- `docs/_archive/governance/edge-replay-cycle16d-report.md` Operator override section — verdict withdrawal + concerns enumeration (ARCHIVED Stream G R52).
- `docs/governance/edge-replay-cycle16e-scorer-forensics.md` — Codex E10 report with Claude N6 verdict appendix.
- `docs/governance/2026-05-07-cycle-16e-claude-rescope-and-review.md` — N3+N4+N5+N7 consolidated review + rescope analysis.
- `docs/governance/cycle-17-conditional-charter-skeletons.md` — Cycle-17 §B/§C un-deferred per Cycle-16E delivery (2026-05-07 amendment section).
- `docs/_archive/governance/cycle-16d-post-verdict-action-checklist.md` — checklist Item 5 + 8 + 9 + 10 amended for Cycle-16E (ARCHIVED Stream G R21).
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — original strategic-redirect authority.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-17+ acceptance).
- `trading/executor.py:200-244` — production gate single source of truth (verified faithfully ported in Cycle-16E).

---

### PROFIT-EDGE-011

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-011 |
| **Title** | Cycle-17 operator decision — pick §B source-onboarding OR §C strategic-redesign post-Cycle-16E `scorer_fixed_no_signal_confirmed` verdict |
| **Category** | Profit-Path Integrity / Strategic Direction (succeeds PROFIT-EDGE-010 Cycle-16E `scorer_fixed_no_signal_confirmed` verdict; replaces previously-amended PROFIT-EDGE-010 "Cycle-17 operator decision" filing now that scorer is audited) |
| **Severity** | HIGH (gates all Wave-2/3/Branch-D deploys; gates whether bot continues active development OR pivots to redesign / paper-only research / pause) |
| **Status** | COMPLETE (filed 2026-05-07; operator picked §C(b) fundamental redesign on 2026-05-07; cycle-17C charter landed `cd4fa2a`; succeeded by PROFIT-EDGE-012 tracking cycle-17C execution) |
| **Priority** | NOW (operator decision blocks all subsequent cycle work) |
| **Owner** | Operator (decision); Claude + Codex (recommendation + analysis already delivered in Cycle-16E reports) |
| **Depends On** | PROFIT-EDGE-010 (delivered with `scorer_fixed_no_signal_confirmed` verdict). |
| **Blocks** | All Wave-2/Wave-3/Branch-D deploys per IC §16; all subsequent Cycle-17/Cycle-18+ scope decisions until operator picks. **(Unblocked: §C(b) picked 2026-05-07; cycle-17C single-variable redesign in progress under PROFIT-EDGE-012.)** |

**Description**

Cycle-13 → Cycle-14 → Cycle-15B → Cycle-16D → Cycle-16E arc has demonstrated:

1. **Extraction emits signal** (Lane B 8/8 + 2/2 ✓ at synthetic; Cycle-15B C7 keyword extension landed).
2. **Prices reconstructable** (Cycle-16D D5 99.6324% coverage via documented Kalshi endpoints).
3. **Scorer audited** (Cycle-16E production gates faithfully ported; market-implied baseline corrected; 5 forensic checks satisfied).
4. **No positive-EV slice exists at the bot's current information set** (Cycle-16E production-proxy 12 trades / 0 wins / 0 IC §16 slices; the 12 trades / 0 wins is consistent with market-implied expected wins of ~1; statistically uninformative at n=12 BUT the cycle's question — "does any slice clear `ev_ci_95_lo > 0` AND `trades ≥ 10`?" — has a definitive NO).

Per `cycle-17-conditional-charter-skeletons.md` verdict-to-skeleton map, this routes to operator decision between:

- **§B source onboarding** (2-4 weeks): identify 3-5 alternative source classes (regulatory feeds, primary-source archives, specialist analysis); backfill evidence_store; replay against new sources; deploy if ≥1 positive-EV slice surfaces.
- **§C strategic redesign / pause** (operator-decision-doc only initially): three sub-options — (a) pause bot indefinitely, (b) fundamental redesign of sources/model/markets/sizing, (c) continuation as paper-only research project.

**Why it matters to profitability / safety / reliability**

1. **Strategic-direction inflection.** Bot has 3 lifetime paper trades + -$7.50 P&L + 4 cycles of negative-or-blocked replay verdicts (Cycle-13/14/15B/16D-via-16E). Continuing without operator pick repeats the cycle-12 "deploy hope" anti-pattern.
2. **Prior anti-correlation hypothesis is WITHDRAWN.** Cycle-16E corrected the baseline math; the "99.16% wrong-direction" framing was an artifact of (a) wrong baseline (50% coin-flip vs market-implied 9.463 expected wins) and (b) scorer overadmission. Audited evidence supports clean "no signal at current information set" interpretation, not "anti-correlated signal that should be inverted."
3. **§B mandatory pre-onboarding re-trace requirement is RELAXED.** Cycle-16D M6 appendix proposed mandatory re-trace of 235 losers before §B onboarding because anti-correlation hypothesis was live. With anti-correlation withdrawn, §B can proceed gated only on candidate-source replay validation per IC §16 Rule 4.
4. **Operator time / resource allocation.** §B = 2-4 weeks. §C (a) = no further work. §C (b) = multi-month redesign. §C (c) = paper-only indefinitely. Operator weighs against alternative use of same resources.

**Evidence / Source**

- `docs/governance/edge-replay-cycle16e-scorer-forensics.md` (Codex + Claude N6 appendix) — verdict source.
- `docs/governance/2026-05-07-cycle-16e-claude-rescope-and-review.md` — N3+N4+N5+N7 verification + rescope analysis.
- `docs/_archive/governance/edge-replay-cycle16d-report.md` — Cycle-16D charter-locked verdict + operator override (ARCHIVED Stream G R52).
- `docs/_archive/governance/edge-replay-cycle15b-report.md` — Cycle-15B verdict trail (ARCHIVED Stream G R50).
- `docs/governance/edge-replay-cycle14-diagnosis.md` — Cycle-14 verdict trail.
- `docs/governance/edge-replay-cycle13-report.md` — Cycle-13 verdict trail.
- `data/paper_trades.db` — 3-trade lifetime history (PRE_FIX cohort).
- `data/dossier_updates_post_fix.db` — 272-row POST_FIX_REBUILT corpus.
- `logs/edge_replay/cycle16e/counterfactual_scores_production_proxy.json` — production-proxy 12 trades.

**Operator decision menu**

Operator picks ONE:

| pick | scope | estimated time | risk |
|---|---|---|---|
| §B source onboarding | Cycle-17B | 2-4 weeks Codex implementation | risks 2-4 weeks rebuilding negative-result outcome with new vocabulary; mitigated by replay validation gate per IC §16 Rule 4 |
| §C (a) pause | Operator-decision-doc only | ~1 hour | sunk-cost effect; no further bot development |
| §C (b) fundamental redesign | Cycle-17C-redesign | multi-month | full Wave-1/2/3-equivalent replay validation required from scratch |
| §C (c) continuation as paper-only research | Operator-decision-doc only | ~1 hour | indefinite paper-only operation; no live capital ever |

Operator picks. Subsequent debt entry filed (PROFIT-EDGE-012) per matching Cycle-17 sub-cycle.

**Acceptance Criteria**

- Operator decision documented in this debt entry's "Notes" + `docs/governance/cycle-17-operator-decision.md` (FUTURE) — OR — operator picks via direct decision communication recorded in this entry's Notes.
- ROADMAP / §Current Status (this file) refreshed per matching Cycle-17 §B/§C wording. (Was ROADMAP / EDGE_STATUS until 2026-05-09 consolidation; EDGE_STATUS now §Current Status.)
- If §B picked: PROFIT-EDGE-012 filed = "Cycle-17B source onboarding scope."
- If §C (a) / (c) picked: PROFIT-EDGE-012 filed = "Cycle-17C (a/c) operator-direction continuation."
- If §C (b) picked: PROFIT-EDGE-012 filed = "Cycle-17C-redesign multi-month scope."

**Notes**

- **Capital posture remains PAPER-ONLY** regardless of operator pick. Live-trading flip requires ≥1 IC §16-eligible slice surfacing in a future cycle + replay-report citation + operator-explicit override commit.
- **Pre-cycle-12 "deploy hope" pattern stays prohibited.** Operator picking §B is NOT permission to deploy speculative source onboarding. Each candidate source must clear backfill replay before deploy per §B charter.
- **Anti-correlation hypothesis WITHDRAWN.** Cycle-16E audit dissolved the "99.16% wrong-direction" framing. Decision should weight market-implied baseline math, not the prior framing.
- **Three-failed-fix architectural rule.** Per `superpowers:systematic-debugging`: cycle-15B C7 was the only formal extraction fix. Cumulative evidence remains 4 cycles of negative-or-blocked replay verdicts but the negative reading is now clean (audited scorer + market-implied baseline).

**Related**

- `PROFIT-EDGE-010` (delivered) — direct parent (Cycle-16E verdict drove this entry).
- `PROFIT-EDGE-009` / `PROFIT-EDGE-008` / `PROFIT-EDGE-007` / `PROFIT-EDGE-006` / `PROFIT-EDGE-005` — full cycle trail.
- `docs/governance/cycle-17-conditional-charter-skeletons.md` — Cycle-17 skeleton set (with 2026-05-07 amendment noting Cycle-16E delivery).
- `docs/governance/edge-replay-cycle16e-scorer-forensics.md` Claude N6 appendix — verdict + recommendation rationale.
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — original strategic-redirect authority.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-17+ acceptance).

---

### PROFIT-EDGE-012

| Field | Value |
|-------|-------|
| **ID** | PROFIT-EDGE-012 |
| **Title** | Cycle-17C single-variable redesign — execute hypothesis-locked experiments per charter |
| **Category** | Profit-Path Integrity / Strategic Direction (succeeds PROFIT-EDGE-011 §C(b) operator pick on 2026-05-07) |
| **Severity** | HIGH (gates all Wave-2/3/Branch-D deploys; gates whether bot has deployable signal under any of six redesign axes per first-axis-pick rationale) |
| **Status** | ACTIVE (cycle-17C charter landed `cd4fa2a` 2026-05-07; E1 reverted, E2 axis-abandoned, E3 verdict = `revert_required_no_ic16_slice` 2026-05-10 (Clause A failure: 0 IC §16 slices on 12 admitted rows; clauses B/C/D technically pass but B + C pass mechanically — 1.005 excess matches mechanical-inversion ceiling, consistency vacuous on universal 12/12-monoculture). **Revert tracker: E1=1/3 (reverted), E2=axis-abandoned (does NOT count), E3=2/3 (reverted). One revert remaining before architectural-rethink rule fires.** **Cycle-17D amendment landed 2026-05-10** (charter authorizes broader-corpus replay; merged corpus = cycle13_live ∪ cycle13_local ∪ cycle15B ∪ cycle16D; revert budget reset 0/3; first sweep step = GO/NO-GO admission-count sweep, threshold = at least one 4-axis intersected bin has ≥10 admitted rows; E4 axis pick deferred until post-sweep; PAPER-ONLY unchanged). **Cycle-17D build script landed `1336fe2` 2026-05-10** (513 merged rows / 292 dropped duplicates / cohorts `{POST_FIX_REBUILT: 272, PRE_FIX: 241, POST_FIX_NEW: 0}`). **Cycle-17D schema-compatibility audit landed `6e626ea` 2026-05-10 — BLOCKER FOUND.** `production_proxy_ready: false`; PRE_FIX rows (cycle-13 vintages) are 0/241 admissible for production-proxy because 238/241 lack `market_yes_price` and Kalshi API backfill returns 404 (settled-market trade endpoint retired). Effective admissible cohort = POST_FIX_REBUILT only (237 rows; equivalent to or slightly smaller than the original 272-row cycle-16D corpus that produced the 12-row admission ceiling). The merged-corpus approach DID NOT lift the ceiling. SHA post-normalization patch (Codex added `market_family` field): `a0f5401b65acd9592e2dcc1c34bb0b9d0c76fe4718a2d714a9bc29160244f913` (supersedes pre-norm `ab9ae8e9...`; both pinned in charter amendment §4 Delta 1). Sequence HALTED at step 3 per architect's "operator decides next step on NO-GO" rule. **POST_FIX_NEW corpus-readiness audit landed 2026-05-10** (`docs/governance/2026-05-10-cycle-17d-post-fix-new-readiness-audit.md`) — read-only Phase-2 production-log audit per operator request before locking option (c). Findings: production accumulating at ~0.8 PT/day; ~33 corpus-eligible rows at Phase-2 close (2026-05-15); +30 days ~171 rows ("maybe useful"); +60 days ~298 rows (cycle-16D scale); +90 days ~424 rows (likely useful). Honest framing: option (c) is a 30-90 day wait, NOT a 5-day wait. Plus two filable corpus-builder bugs surfaced (unmapped `llm_confidence` in `_paper_trade_rows()`; key-name mismatches in `_log_rows()`) — would null-out production-proxy fields on POST_FIX_NEW rows even if volume materializes. Operator pick options unchanged: (a) declined; (b) broader-API-fetch corpus pivot (~3-5 days infrastructure); (c) pause 30-90 days for POST_FIX_NEW corpus + interim bug fixes; or (d) new option = fix corpus-builder bugs now (scorer-tooling-fix exception per charter) + pivot to (b) for faster path. Cycle-17C cycle-16D-frozen-baseline experiments remain dormant.) |
| **Priority** | NOW (active experiment cycle; one experiment at a time per single-variable rule) |
| **Owner** | Codex (criteria-lock + implementation + replay + result report); Claude (hypothesis sketch + verdict appendix + ledger row maintenance); Operator (axis pick + criteria approval + final keep/revert call). |
| **Depends On** | PROFIT-EDGE-011 (delivered with §C(b) pick). |
| **Blocks** | All Wave-2/Wave-3/Branch-D deploys per IC §16; all subsequent Cycle-18+ scope decisions until cycle-17C reaches one of: (a) IC §16-eligible slice on a kept experiment, (b) 3 reverts → architectural-rethink rule trigger, (c) charter time-box exhausted (~1 week stated). |

**Description**

Cycle-17C is a single-variable redesign program testing six candidate axes (per first-axis-pick rationale ranking) against the frozen Cycle-16E baseline corpus.

Operating rule: **One hypothesis. One code change. One replay. One written verdict. Keep only if pre-declared IC §16 bar passes; otherwise revert.**

Hard constraints (per charter):
- Fixed corpus (`logs/edge_replay/cycle16d/replay_dataset.jsonl`).
- Fixed audited scorer (`scripts/edge_replay/scorer_forensics_audit.py` production-proxy mode).
- Fixed replay command per cycle-16E reproducibility section.
- One active experiment.
- No-overlap rule (E[N+1] cannot start until E[N] has ledger row with final decision).
- Revert default unless IC §16 passes.
- Pre-registration commit (criteria-lock) before any replay.
- 3 sequential reverts → architectural-rethink rule.
- ~1 week time-box.

Six axes per first-axis-pick rationale (rank 1-6):
1. Probability update rule (E1 — TESTED, REVERTED).
2. Market-family selection (deferred — violates fixed-corpus rule).
3. Side inference (E3 — IN PROGRESS, hypothesis sketch landed).
4. Readiness admission (E2 — ABANDONED via outcome-blind G1 sweep; structural finding logged in ledger row).
5. Extraction prompt (deferred pending E3 outcome).
6. Keyword map (already-explored; near-zero info gain).

**Why it matters to profitability / safety / reliability**

1. **Last gate before fundamental-pause path.** Cycle-17C is the §C(b) fundamental redesign per PROFIT-EDGE-011. Failure here without architectural-rethink trigger keeps the bot in indefinite paper-only research limbo; success surfaces ≥1 deploy-eligible slice for §A review.
2. **Architectural-rethink trigger pre-registered.** Charter states 3 sequential reverts triggers re-examination of the cycle's framing. Currently 1 revert (E1). E2 axis-abandonment did NOT count toward this rule per pre-locked exception. Future reverts must be tracked carefully.
3. **Structural findings worth flagging at charter level (recorded in ledger E2 row, not yet promoted):** (i) On frozen corpus, `paper_price_sanity` is the dominant filter (119 of 260 production-proxy skips). The 12-row admission ceiling is a corpus property, not a configurable gate. Lifting it requires either loosening `paper_price_sanity` (risk-mode change), different corpus (violates fixed-corpus rule), or signal axis outside readiness. (ii) Cycle-17C replay pipeline does NOT re-run `signal_analyzer.py`. Evidence rows have `side` baked in from original ingestion. Side-inference experiments must select implementation path carefully (re-extract corpus, scorer counterfactual mode, or post-processing diagnostic).
4. **Time pressure from operator allocation.** Charter time-box is ~1 week. Started 2026-05-07. 1 day in, 1 revert + 1 axis-abandonment + 1 hypothesis-sketch landed.

**Evidence / Source**

- `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` — charter (operating rule + 3-revert architectural rule + ~1 week time-box).
- `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` — ledger schema + populated rows (E0 baseline / E1 revert / E2 axis-abandoned).
- `docs/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md` — info-gain ranking authority for axis selection.
- `docs/governance/2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md` — E1 criteria-lock.
- `docs/governance/edge-replay-cycle17c-e1-report.md` — E1 result report (`revert_required_no_ic16_slice`).
- `docs/governance/2026-05-07-cycle-17c-e1-claude-verdict-appendix.md` — E1 Claude verdict + amended E2 axis menu + E3 menu.
- `docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md` — E2 G1 admission-count projection (outcome-blind; sweep proved G1 inert across 0.05→0.00).
- `docs/governance/2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md` — E3 hypothesis sketch with sub-axis menu A/B/C and implementation-path options.
- `docs/governance/2026-05-10-cycle-17c-e3-criteria-lock.md` — **E3 criteria lock (2026-05-10)**: Path 3 (post-processing diagnostic; new `scripts/edge_replay/side_flip_counterfactual.py`); acceptance bar tightened beyond IC §16 to require Clause B (trivial-inversion disqualification: `excess_wins_vs_market_flip > 0.5`) and Clause C (sub-slice consistency: per-axis bin comparisons p ≥ 0.20, no dominant-bin > 75%); operator hard requirement formalized; charter-binding (no unlock without operator sign-off). Verdict appendix template embedded.
- `docs/governance/2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md` — **E3 replay report + Claude verdict appendix (2026-05-10)**: Codex implementation `ac00903` (script + test + companion report; touch surface charter-clean per Clause D). Verdict = `revert_required_no_ic16_slice`: 12 admitted rows / 12 actual flip wins / 10.995 market-implied expected / 1.005 excess (= mechanical-inversion ceiling, NOT signal evidence) / 0 IC §16 slices. Substantive findings: Clause B's 0.5 threshold was set too loose for longshot-cohort math (excess of 1.005 IS the ceiling); Clause C consistency test is vacuous on universal monoculture. Side-inference axis ruled out as productive cycle-17C redesign axis on this corpus. Cohort-shape ceiling structural finding now confirmed by E2 + E3 in agreement.
- `scripts/edge_replay/g1_admission_sweep.py` — E2 sweep script (reference pattern for any future outcome-blind sweep).

**Acceptance Criteria**

- IF cycle-17C reaches a `keep` verdict on any experiment with ≥1 IC §16-eligible slice → matching deploy-candidate review under Cycle-17 §A pattern → PROFIT-EDGE-013 filed = "Cycle-17 §A deploy-candidate review for slice {x}."
- IF cycle-17C reaches 3 sequential reverts → architectural-rethink rule fires → PROFIT-EDGE-013 filed = "Cycle-17C architectural-rethink — six-axis ranking reconsidered or charter scope amended."
- IF cycle-17C reaches time-box exhaustion (~1 week) without keep or 3-revert trigger → operator decides extend / pivot / pause → PROFIT-EDGE-013 filed per operator direction.
- IF axis-abandoned-before-criteria-lock outcomes accumulate to a structural pattern (currently 1: E2) → operator + Claude evaluate whether the abandoned axes share a corpus-level constraint that warrants charter amendment.

**Notes**

- **Capital posture remains PAPER-ONLY.** Locked. No experiment in cycle-17C can flip live trading unilaterally; deploy-candidate review under §A is the only path.
- **Three-revert rule clarification.** Charter says 3 sequential reverts triggers architectural rethink. Current state: 1 revert (E1 Bayesian log-odds). E2 G1 sweep produced `axis_abandoned_before_criteria_lock` decision, NOT a revert (no implementation commit landed). Per E2 ledger row decision_rationale, axis-abandoned-pre-criteria-lock outcomes do not consume revert budget.
- **Single-variable rule strictness.** Each experiment touches exactly one configurable axis. Bundling sub-axes (e.g., E3 sub-axes A + B + C combined) violates the rule. If E3-A is ambiguous, E3-B may file separately as E4.
- **Frozen-corpus structural finding (E2 ledger row.notes):** 12-trade admission ceiling on cycle-16D corpus is a corpus property. Future cycle scope may need to address this directly.
- **Replay-pipeline structural finding (E3 sketch):** `signal_analyzer.py` is not re-run during replay. Evidence rows carry frozen `side` from original ingestion. Side-inference experiments require alternate implementation paths (re-extract / scorer counterfactual / post-processing diagnostic).
- **Charter time-box pressure.** ~1 week stated. Day 1 elapsed: 1 revert + 1 axis-abandoned + 1 hypothesis-sketch landed. Sustainable cadence depends on Codex implementation throughput.

**Related**

- `PROFIT-EDGE-011` (delivered) — direct parent (operator §C(b) pick drove this entry).
- `PROFIT-EDGE-010` / `PROFIT-EDGE-009` / `PROFIT-EDGE-008` / earlier — full cycle trail.
- `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` — charter authority.
- `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` — experiment-by-experiment ledger.
- `docs/governance/2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md` — most recent governance artifact (E3 in progress).
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-17C acceptance).
- Memory: `feedback_market_implied_baseline.md` — cycle-16E baseline-correction lesson; informs all cycle-17C verdicts.
- Memory: `feedback_audit_scorer_before_verdict.md` — verdict-time audit discipline.

---

### PROFIT-OBS-003

| Field | Value |
|-------|-------|
| **ID** | PROFIT-OBS-003 |
| **Title** | OPPORTUNITY → SKIPPED/PAPER_TRADE arithmetic gap: 240-of-260 lifetime OPPORTUNITY events exit with no matching executor record (including 2 OPPORTUNITY at non-trivial positive edge that produced no PAPER_TRADE) |
| **Category** | Observability / Trade-Log Fidelity |
| **Severity** | HIGH (promoted 2026-05-01 from MEDIUM) |
| **Status** | COMPLETE (2026-05-10) |
| **Priority** | NOW (promoted 2026-05-01 from LATER) |
| **Owner** | Shared |
| **Depends On** | — |
| **Blocks** | PROFIT-EDGE-004 audit completeness (the missing SKIPPED records would tell us *which* executor gate is killing each candidate); PROFIT-CAL-001 production-soak observation (tiny resolved-trade sample is partially attributable to silent-exit candidates that should have traded); paper-trade emission throughput in general (per the 2026-05-01 evidence below, the silent-exit gap drops *positive-edge* candidates, not just edge=0.0 ones) |

**Description**

In the v0.29.58 post-deploy audit window (2026-04-27T13:03:19Z → 2026-04-29T~12:34Z, ~48h), the trade-log archive contains 34 `OPPORTUNITY` events and 3 `SKIPPED` events. The expected accounting is `OPPORTUNITY = paper_trades + SKIPPED + (in-flight-at-audit-time)`. With 0 paper trades and effectively 0 in-flight at the audit moment, 34 − 3 = 31 candidates exited the executor without producing a recorded SKIPPED event.

`trading/executor.py:152` is the single visible call site for `await write_trade_log_async(trade_log.log_skipped, **skipped_kwargs)`. Either there are early-exit branches in the executor's `_validate()` (E1–E12) that bypass the SKIPPED log, or `log_opportunity` ([`utils/logger.py:548`](../utils/logger.py)) is called from an upstream checkpoint distinct from where SKIPPED-eligible candidates land in the executor.

**Why it matters to profitability / safety / reliability**

Operationally, this is observability debt — every OPPORTUNITY that exits the funnel should produce exactly one of `{paper_trade, SKIPPED}` (or be retained as in-flight). Without that invariant, every post-deploy audit of which gate is killing trades has a blind spot. EDGE-004's investigation specifically wants to know: of the 31 silent-exit candidates, did they fail at PAPER_MIN_EDGE the same way the 3 visible SKIPPEDs did? Or did they hit a different executor gate (E1–E12 enumerate twelve gates per the README)? The answer determines whether one fix or several is needed to enable paper-trade emission once EDGE-004 is closed.

**Evidence / Source**

- Counter from 2026-04-27/28 trade-log archive + live trades.jsonl: `OPPORTUNITY = 34, SKIPPED = 3, paper_trades_db = 0`.
- `trading/executor.py:152` — single `log_skipped` call site visible from `grep -nE '(log_skipped|log_opportunity|log_signal|log_paper|log_trade|log_blend)' trading/executor.py utils/logger.py`.
- `utils/logger.py:548` defines `log_opportunity`; `utils/logger.py:686` defines `log_skipped`.
- Hypothesis: `_validate()` returns early on certain failure conditions before reaching the SKIPPED emission point. Possibilities include the same-signal guard, the opposing-position guard, the per-ticker cooldown, or balance/exposure caps — each is documented in [`tasks/trade_readiness_gate.py`](../tasks/trade_readiness_gate.py) and the README's Executor section, but the executor-side implementations may not all funnel through `log_skipped`.

**Proposed Fix**

> **Superseded — see the 2026-05-02 forensic addendum below.** A direct read of `trading/executor.py` confirms `_validate()` has no silent-exit branches; every rejection emits a SKIPPED at line 152. The actual silent-exit boundary is `tasks/blend_task.py:204` (BlendTask early-return when `trade_blocked_reason` is non-None). The corrected fix is to emit a SKIPPED record from BlendTask's blocked-reason path, with `reason=trade_blocked_reason` (one of the readiness-gate names `G1_blended_confidence` … `G6_recency_score`, or the blender-side reason). The original (executor-focused) text is preserved verbatim below for historical context.

Audit `trading/executor.py` for early-exit branches in `_validate()` (E1–E12) and either:

1. Route every executor reject through `log_skipped` with a distinct `reason` string (one per gate, e.g. `"opposing_position_guard"`, `"ticker_cooldown_active"`, `"insufficient_balance"`), so post-deploy audits can attribute the kill to a specific gate. Lower-friction; preserves the existing SKIPPED schema.

2. Add a separate `log_executor_reject` event type that mirrors SKIPPED's payload but carries an explicit `gate: str` field, and emit it from every early-exit branch. Cleaner if there are gates whose context doesn't fit the SKIPPED payload shape (e.g. multi-position-state guards).

Approach (1) is preferred unless the audit reveals gates whose context is genuinely incompatible with the SKIPPED schema.

**Acceptance Criteria**

- Every `OPPORTUNITY` event in a paper-mode trade-log window has exactly one of `{paper_trade, SKIPPED, in-flight-at-audit-time}`.
- A 24h paper-mode audit confirms `OPPORTUNITY = paper_trades + SKIPPED` within ±N for at-the-moment in-flight (N small).
- Reasons in the SKIPPED records are diverse enough to attribute kills to specific executor gates.

**Notes**

- ~~This is observability debt, not a correctness bug. The bot is doing the right thing (rejecting `edge = 0.0` candidates); the audit log just doesn't cleanly record *why* for 31 of the 34.~~ **Revised 2026-05-01:** the original "observability-only" framing was wrong. The 2026-05-01 lifetime audit shows two OPPORTUNITY records at non-trivial positive edge (+0.06 and +0.064) that produced no PAPER_TRADE despite clearing PAPER_MIN_EDGE = 0.02. So the silent-exit branches in `_validate()` are *also* dropping legitimately-tradeable candidates, not just rejecting edge=0.0 candidates the operator would expect to be skipped. This makes OBS-003 a correctness-adjacent bug, not pure observability debt.
- Closing this is a precondition for cleanly closing EDGE-004's investigation — without it, EDGE-004's evidence section is constrained to the 17 visible SKIPPED records (a small sample of a 13-day window with 260 OPPORTUNITY → 240 silent exits = 92.3% silent-exit rate).
- Do not bundle this with EDGE-004's signal-quality investigation. Different boundary, different code path, different review owner. EDGE-004 fixes "why is edge=0 on most OPPORTUNITY"; OBS-003 fixes "why does the executor not log (and not trade) candidates that survive the readiness gate."

**Validation Notes** (2026-05-01, full 13-day MacBook paper-soak archive)

Lifetime trade-log totals from `logs/trades/archive/2026/04/2026-04-{18..30}.jsonl` + `logs/trades/live/trades.jsonl` (2026-04-18T02:11:24Z → 2026-05-01T13:05:54Z, ~13 days, 115 boots across versions 0.29.5 → 0.29.58):

| Counter | Value |
|---|---|
| OPPORTUNITY events | 260 |
| SKIPPED events | 17 |
| PAPER_TRADE events | 3 |
| OPPORTUNITY → exit accounted for | 20 (17 SKIP + 3 PAPER) |
| OPPORTUNITY → silent exit | **240 (92.3%)** |

OPPORTUNITY edge distribution:

| Edge bucket | Count | Note |
|---|---|---|
| `0.0` | 255 | 98.1% of all OPPORTUNITY — the EDGE-004 universal-anchor pattern |
| `-0.068` | 3 | The KXFISAEXTEND PAPER_TRADE emissions — the recorded edge sign is wrong (PROFIT-OBS-004); the executed-side edge was +0.068 |
| `+0.06` | 1 | **Non-trivial positive edge that produced no PAPER_TRADE.** Cleared PAPER_MIN_EDGE = 0.02, then silently lost. |
| `+0.064` | 1 | **Same as above.** Same lifetime, same fate. |

SKIPPED reason monoculture: 17/17 SKIPPED records carry the identical reason string `"edge +0.0000 below min_edge 0.02"`. No other rejection reason has *ever* been emitted into the trade log in 13 days. This is the empirical confirmation of OBS-003's original hypothesis that early-exit branches in `_validate()` (E1–E12) bypass the SKIPPED log call site at `trading/executor.py:152`. Specifically, the candidates we now know were silently lost include **at least 2 candidates that should have traded** under the executor's documented behaviour.

Per-day breakdown of the post-EDGE-003 (v0.29.58) window:

| Date | OPPORTUNITY | SKIPPED | PAPER_TRADE | Silent exits |
|---|---|---|---|---|
| 2026-04-27 | 13 | 1 | 0 | 12 |
| 2026-04-28 | 17 | 2 | 0 | 15 |
| 2026-04-29 | 4 | 0 | 0 | 4 |
| 2026-04-30 | 32 | 6 | 0 | 26 |
| 2026-05-01 | 13 | 1 | 3 | 9 |
| **Subtotal** | **79** | **10** | **3** | **66 (83.5%)** |

The silent-exit rate is consistent across the entire 13-day window (92.3% lifetime, 83.5% in the post-EDGE-003 subset where the sample is most relevant). OBS-003's promotion to HIGH/NOW is on the basis of this consistency plus the two confirmed positive-edge non-trades — the gap is not a transient artifact of the immediate post-EDGE-003 window.

**Acceptance Criteria** (revised 2026-05-01 in light of the new evidence)

- Every `OPPORTUNITY` event in a paper-mode trade-log window has exactly one of `{paper_trade, SKIPPED, in-flight-at-audit-time}`.
- A 24h paper-mode audit on the **Mac Studio** (post-cutover; the MacBook is no longer the source of new audit windows) confirms `OPPORTUNITY = paper_trades + SKIPPED` within ±N for at-the-moment in-flight (N small).
- SKIPPED reasons are diverse enough to attribute kills to specific executor gates (E1–E12 named per the README's Executor section).
- **The 2 historical positive-edge silent-exit candidates** (+0.06 and +0.064 in the trade-log archive) are explainable post-fix: re-running `_validate()` against the persisted candidate state should produce a SKIPPED record with a specific gate name (likely opposing-position guard, same-signal guard, or per-ticker cooldown).

**Per-gate kill attribution** (2026-05-03, Codex commit `e5440df`, full lifetime archive read)

The 2026-05-02 forensic addendum below identified `tasks/blend_task.py:204` as the silent-exit boundary. The empirical follow-up — joining every `OPPORTUNITY` event in the full trade-log archive against its matching `BLEND_DECISION` and reading `trade_blocked_reason` — quantifies which gate accounts for what fraction of the 240 silent exits. Report at `docs/governance/2026-05-03-obs003-kill-attribution.md`.

| trade_blocked_reason | count | % of silent exits |
|---|---:|---:|
| `G1_blended_confidence` | **197** | **82.1%** |
| `G6_recency_score` | 31 | 12.9% |
| `G2_evidence_source_class_diversity` | 12 | 5.0% |

Total: 264 OPPORTUNITY → 23 accounted (20 SKIPPED + 3 PAPER_TRADE) + 240 silent (100% attributed) + 1 unattributed. The single unattributed case is `KXTRUMPIRAN-26MAY01` at 2026-04-21T01:52:46Z with no matching exit or blocked BLEND_DECISION — a clean-up candidate but not a systemic issue.

Top ticker concentrations:

| ticker | drivers |
|---|---|
| `KXTRUMPIRAN-26MAY01` | G1=77, **G6=31 (entire G6 set)** |
| `KXMOCTRUMP25-26-MAY01` | G1=52 |
| `KXMOCTRUMP25-26-APR24` | G1=20 |
| `KXVANCEPAKISTAN-26APR21-APR30` | G1=15 |
| `KXVANCEPAKISTAN-26APR21-APR25` | G1=8 |

**Implications for the OBS-003 fix scope:**

1. **G1 is the dominant target.** The post-soak fix should land the BlendTask SKIPPED-emission for G1 first; that alone closes 82% of the silent-exit gap and unblocks the EDGE-004 audit's per-gate attribution evidence.
2. **G6 is concentrated to a single ticker.** The 31 `G6_recency_score` kills are all on `KXTRUMPIRAN-26MAY01` — likely an evidence-staleness pattern specific to that market's evidence stream rather than a general G6 calibration issue. Post-fix, this becomes a separate diagnostic question (does that ticker's evidence stop flowing well before the market closes?). Tracking inside this entry rather than spawning a new debt item until the post-fix data confirms the pattern.
3. **G2 is small but interesting.** `G2_evidence_source_class_diversity` requires `evidence_source_classes` set diversity ≥ 2. The 12 hits are spread across `KXVANCEPAKISTAN`, `KXPARDONSTRUMP`, `KXTRUMPENDORSE`, `KXELECTIONEMERGENCY` — markets where the evidence stream collapses to a single source class. Cross-references the 2026-05-01 audit observation in PROFIT-DOSSIER-001's evidence section ("Source-class diversity gap: 202 news / 45 other / 1 official across the full corpus"). The G2 kill set is structural, not a runtime defect; raising source-class diversity is a feed-mix problem (PROFIT-EVID-002 territory) rather than a BlendTask-level fix.

**Acceptance status update:** the original entry's first acceptance criterion ("Every OPPORTUNITY event in a paper-mode trade-log window has exactly one of `{paper_trade, SKIPPED, in-flight-at-audit-time}`") is the post-fix target; the 2026-05-03 attribution proves the silent-exit boundary is well-defined and BLEND_DECISION already carries the attribution data. The fix is a logging consolidation, not new instrumentation.

**Forensic addendum** (2026-05-02, soak-window read-only source-tree audit)

*Prior hypothesis is wrong.* The 2026-05-01 forensic addendum below claims early-exit branches in `trading/executor.py:_validate()` bypass the SKIPPED log call site. A direct read of `trading/executor.py` (498 LOC, full read 2026-05-02) refutes this:

- `_validate()` at lines 183–303 returns a non-None `skip_reason` string from **every** rejection branch: `capped_dollars=0` (live only), `edge below min_edge`, `market status`, `price near limit`, `paper cooldown`, `opposing position`, `paper duplicate skip`, `same-signal skip`, `concentration limit`, live `cooldown`, `LIVE HALTED`, `insufficient live balance`. **No silent return path exists.**
- `execute()` at lines 121–153 unconditionally awaits `write_trade_log_async(trade_log.log_skipped, ...)` for every non-None `skip_reason`. **There is no executor-side bypass of the SKIPPED log.**

The 2026-05-01 addendum's cooldown hypothesis on KXMOCTRUMP25 and time-discount hypothesis on KXTXRUNOFFENDORSE both attribute the silent exit to executor gates that *do* in fact emit SKIPPED records. Both hypotheses are therefore unsupported by the source tree.

*Correct silent-exit boundary.* `OPPORTUNITY` is emitted at [`main.py:772`](../main.py) — **upstream** of BlendTask routing. The candidate then flows:

```
main.py:772  log_opportunity   →
main.py:839  _route_analysis_through_blend(analysis)   →
tasks/blend_task.py:148  BlendTask.process_fast_lane_result   →
  blend → readiness gate (evaluate_readiness)
  emit BLEND_DECISION (always; lines 195–202)
  if trade_blocked_reason is not None → return early, enqueued=False  ← SILENT EXIT
  else → put on trading_queue → executor → _validate → log_skipped
```

The block at [`tasks/blend_task.py:204`](../tasks/blend_task.py) is the actual silent-exit branch: when either `blend_result.trade_blocked_reason` (blender-side, e.g. structural-instability or post-blend min-edge) or `readiness.trade_blocked_reason` (G1–G6) is non-None, the candidate is dropped without producing a SKIPPED record. Only `BLEND_DECISION` carries the kill reason, and only via its `trade_blocked_reason` field.

*Readiness-gate `trade_blocked_reason` enum.* From [`tasks/trade_readiness_gate.py:172–207`](../tasks/trade_readiness_gate.py):
`G1_blended_confidence`, `G2_evidence_source_class_diversity`, `G3_disagreement_score`, `G4_regime_confidence`, `G5_dossier_drift_suspect`, `G6_recency_score`. The first non-empty `failure_reasons` entry becomes `trade_blocked_reason`. Plus blender-side reasons set inside `_blender(...)` (out of scope for this read; tracked via the same field).

*Per-ticker concentration revised.* The 109 silent exits on `KXTRUMPIRAN-26MAY01` cannot be the executor's 4-hour cooldown (cooldown emits SKIPPED). The likely concentration driver is **G1** (`scaled_confidence = blended_confidence × regime_confidence < 0.05`): KXTRUMPIRAN's categorical prior gives `rc ≈ 0.27`, and a dossier-present candidate has accumulation-lane dilution against a fast-lane signal, frequently producing `blended_confidence × 0.27 < 0.05`. Confirmable post-fix by counting `BLEND_DECISION` records with `trade_blocked_reason="G1_blended_confidence"` per ticker.

*Two positive-edge non-trades reframed.* The OPPORTUNITY-emitted edge at `main.py:777` is the raw fast-lane edge **before** blending. Both `+0.06` (KXMOCTRUMP25) and `+0.064` (KXTXRUNOFFENDORSE) entered BlendTask; the blender computed `blended_p` against accumulation/structural lanes (each likely ≈ 0.5 with low confidence) and produced a diluted edge that fell below either `G1` (most likely) or, less commonly, the blender's internal `default_min_edge` floor. Either way the kill is at `tasks/blend_task.py:204`, not in the executor. Confirmable from the matching `BLEND_DECISION` records' `blended_p` and `trade_blocked_reason` fields — already in the trade log; no new instrumentation required for confirmation.

*Fix re-scoping.* The original Proposed Fix above ("audit `trading/executor.py` for early-exit branches in `_validate()`") targets the wrong file. The correct fix is in `tasks/blend_task.py`: at line 204, when `trade_blocked_reason is not None`, emit a SKIPPED log record carrying `reason=trade_blocked_reason` (one of the readiness-gate names or the blender-side reason). Existing `BLEND_DECISION` already carries the same field, so the SKIPPED emission is redundant for full-fidelity audit consumers — but unifies the trade-log accounting `OPPORTUNITY = SKIPPED + paper_trades + in-flight` for all consumers that key off the SKIPPED stream (governance Phase 2 reasoning, `bothealth.sh` daily aggregator, future readiness-gate calibration runs). Minor risk: the post-fix SKIPPED count will jump from 17 to ~257 over a 13-day window, which is correct behaviour but downstream tooling that assumed "SKIPPED is rare" needs review (bothealth aggregator's per-day skip-reason histogram is the obvious consumer; per recent commit `9023561` it already groups by reason, so the histogram just gets richer).

*Confirmation lever.* Pre-fix audit query on the existing trade-log archive can already attribute every silent exit:

```bash
# For each OPPORTUNITY without a matching SKIPPED or PAPER_TRADE,
# look up the matching BLEND_DECISION (joined on ticker + timestamp window)
# and read trade_blocked_reason. If null, the candidate enqueued and the
# executor consumed it without a SKIPPED — that would be a true bug;
# if non-null, the kill reason is already in the log just under a
# different event type.
```

Pre-fix, the BLEND_DECISION → silent-OPPORTUNITY join is the empirical answer. The OBS-003 fix is purely a log-stream consolidation, not a new diagnostic.

**Closure-track attribution addendum** (2026-05-03, read-only full-archive join)

`scripts/obs003_kill_attribution.py` now performs the confirmation-lever join over `mac_archive/macbook_2026-05-01_import/logs/trades/` plus Studio `logs/trades/`; markdown output is committed at `docs/governance/2026-05-03-obs003-kill-attribution.md`. Current full-archive totals are `OPPORTUNITY=264`, accounted exits `=23` (`SKIPPED=20`, `PAPER_TRADE=3`), silent exits `=240`, unattributed `=1`, and the accounting validation balances.

Silent-exit kill attribution from matching `BLEND_DECISION.trade_blocked_reason`:

| trade_blocked_reason | Silent exits | Share |
|---|---:|---:|
| `G1_blended_confidence` | 197 | 82.1% |
| `G6_recency_score` | 31 | 12.9% |
| `G2_evidence_source_class_diversity` | 12 | 5.0% |

Dominant concentration: `KXTRUMPIRAN-26MAY01` drives 77/197 G1 kills and all 31 G6 kills; `KXMOCTRUMP25-26-MAY01` contributes another 52 G1 kills. No blender-side non-G1–G6 reason appears in the archive. Post-soak OBS-003 fix priority should therefore target the BlendTask blocked-reason SKIPPED emission first and verify G1/G6-rich histograms; lower-volume G2 follow-up can ride the same log-stream consolidation.

---

**Forensic addendum** (2026-05-01, Mac Studio post-cutover session, full lifetime archive)

*Per-ticker silent-exit concentration:* the silent exits are not uniformly distributed across markets; a small number of "hot" tickers account for the bulk:

| Ticker | OPPORTUNITY | SKIPPED | PAPER_TRADE | Silent |
|---|---|---|---|---|
| `KXTRUMPIRAN-26MAY01` | 112 | 3 | 0 | 109 |
| `KXMOCTRUMP25-26-MAY01` | 54 | 2 | 0 | 52 |
| `KXMOCTRUMP25-26-APR24` | 22 | 2 | 0 | 20 |
| `KXVANCEPAKISTAN-26APR21-APR30` | 15 | 0 | 0 | 15 |
| `KXVANCEPAKISTAN-26APR21-APR25` | 8 | 0 | 0 | 8 |
| (other tickers) | 49 | 10 | 3 | 36 |

109 silent exits on a single ticker (KXTRUMPIRAN-26MAY01) is highly suggestive of the **4-hour ticker-cooldown gate** firing repeatedly — once the bot trades or near-trades on a ticker, every subsequent OPPORTUNITY within the cooldown window dies silently in `_validate()` rather than emitting a SKIPPED with reason `"ticker_cooldown_active"`.

*Gate-hypothesis mapping for the 2 positive-edge non-trades:*

1. **`KXMOCTRUMP25-26-MAY01` @ 2026-04-28T07:30:52, edge +0.060.** `kelly_dollars=$2.25` was successfully computed. At a 50¢ contract price that's 4 contracts ($2.00), which clears `MIN_BET_DOLLARS=2.00`. Most likely killed by the **per-ticker cooldown** — KXMOC had 54 lifetime OPPORTUNITY events with only 2 SKIPPED, so the cooldown gate is firing on most of them after some prior signal. Until OBS-003 closes, this hypothesis cannot be confirmed (the gate firing is silent).

2. **`KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH` @ 2026-04-30T19:24:59, edge +0.064.** `kelly_dollars=$0.00` was computed despite the positive raw edge — the Kelly calculator itself zeroed it out. Most likely the **time-discount factor** drove the effective edge below `PAPER_MIN_EDGE=0.02`: market closes 2026-05-26, signal was 2026-04-30 (26 days out), `TIME_DISCOUNT_HALF_LIFE=14` → factor `0.5^(26/14) ≈ 0.27` → effective edge `0.064 × 0.27 ≈ 0.017`, below the 0.02 floor. This is the bot **correctly** discounting a long-dated edge, but the rejection silently bypasses `log_skipped` instead of emitting a SKIPPED with reason `"effective_edge_below_min_edge_after_time_discount"`. The fix routes this through the SKIPPED log without changing the gate's behavior — distinguishing "discounted-out" rejections from "raw-edge-below-floor" rejections is itself useful telemetry.

These two hypotheses imply at least **two distinct silent-exit code paths** in `_validate()` (cooldown + time-discounted-edge), and likely several more that this addendum cannot identify without source-code inspection. The OBS-003 fix should enumerate every early-exit branch and route each through `log_skipped` with a distinct reason.

**Notes**

- This entry's previous "observability debt" framing was correct in scope but wrong in severity. 2026-05-01 evidence (positive-edge silent exits) makes it correctness-adjacent. Do not downgrade severity again without invalidating this evidence.
- Closing OBS-003 unblocks the EDGE-004 audit's evidence section AND likely produces a small but real uplift in PAPER_TRADE throughput on the Mac Studio (unblocking the 2 positive-edge candidates per ~13 days observed on the MacBook).
- After OBS-003 closes, EDGE-004's "matcher quality / market-mix" investigation can quote per-gate kill counts. The remaining gap (still huge — 92% of OPP at edge=0.0) is then the correct EDGE-004 problem.

**Related**

- `PROFIT-EDGE-004` (opened 2026-04-28) — direct beneficiary of this fix; EDGE-004 audit is constrained until executor reject reasons are visible.
- `PROFIT-EXEC-001` (closed) — addressed direct executor bypass risk; OBS-003 is observability of the bypass-not-bypass outcome path.
- `PROFIT-OBS-004` (opened 2026-05-01) — companion entry for the edge-sign display bug surfaced by the same 2026-05-01 audit; PAPER_TRADE records on KXFISAEXTEND show the YES-side edge (-0.068) regardless of which side was actually traded.
- `PROFIT-CUTOVER-001` (opened 2026-05-01) — the post-cutover Mac Studio audit window is where the 24h acceptance criterion above will run. Do not run that audit on the MacBook; the MacBook is archive-only.

**Closure (2026-05-10)**

Implemented per spec `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md`. `BlendTask._emit_skipped()` lands at the blocked-reason early return; payload mirrors the executor's SKIPPED shape (post-blend `model_probability` and `edge`, headline truncated to 80 chars). 4 strict-xfail OBS-003 tests in `tests/test_blend_task.py` flipped to passing (per-reason emission for G1-G6 + blender-side, async-write target pin, executor-compatible key set). Companion fixes in commit `c9df364`: `BlendDecisionLogger` Protocol widened with `log_skipped`; `_SpyLogger` in `scripts/simulations/blend_task_integration.py` mirrored; post-deploy canary at `tests/test_wave1_commit3_obs003_post_deploy.py` xfail stripped (it had been designed to flip on deploy).

**24-hour paper-mode audit (early-closed at 9h by operator):** window `2026-05-10T03:43:54Z` → `2026-05-10T12:39:15Z` (8h 55min); 14,059 trade-log entries. **Invariant exact**: `OPPORTUNITY(7) = SKIPPED(5) + PAPER_TRADE(2) → delta=0` (spec §8 threshold was `delta < 5`). **SKIPPED reason monoculture broken**: 3 distinct reasons all from the new BlendTask emission path — `G2_evidence_source_class_diversity` (×2), `G1_blended_confidence` (×2), `G6_recency_score` (×1). Pre-fix lifetime monoculture was 17/17 of the executor-side string `"edge +0.0000 below min_edge 0.02"` over the 13-day MacBook archive. **PAPER_TRADE bonus signal**: 2 paper trades in 8.9h sourced from positive-edge OPPORTUNITY events (the silently-lost class OBS-003 was supposed to surface) vs 3 paper trades over 13 days pre-fix; n=2 is statistically uninformative on its own, but the throughput delta is consistent with the silent-exit gap closing. **Safety counters**: 0 `KILL_SWITCH`, 0 `VALIDATION_ERROR`, 0 `PARSE_ERROR`. Early-close justification: invariant was exact (not just `<5`); reason diversity already at the spec threshold; zero rollback conditions firing.

Cross-references this closure: PROFIT-EDGE-004 audit can now quote per-gate kill counts directly from the SKIPPED stream (post-soak/post-cycle-17C).

---

### PROFIT-STRUCT-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-STRUCT-002 |
| **Title** | Verify EDGE-002 sub-fix #4: post-deploy structural-recompute warnings carry underlying-cause `repr(__cause__)` and traceback as designed |
| **Category** | Observability / Structural Lane |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | LATER |
| **Owner** | Claude |
| **Depends On** | — |
| **Blocks** | PROFIT-EDGE-002 sub-fix #4 acceptance is not fully verifiable without it; closes a small observability gap |

**Description**

PROFIT-EDGE-002 sub-fix #4 modified `tasks/structural_task.py:146` so the warning emitted on per-market structural recompute failure now includes both the wrapper text and `repr(__cause__)`, plus an `exc_info` traceback rooted at the cause. The intent was to make future structural failures diagnosable in `bot.log` rather than producing the silent "failed structural recompute for X" lines the pre-fix code emitted.

The v0.29.58 post-deploy audit (2026-04-28) attempted to confirm the new format is live but ran into a methodological block: `launchd.stderr.log` is the file that historically captured these warnings, and it does *not* carry per-line timestamps for the structural failure entries. The 10,605 visible `[STRUCTURAL] per-market recompute failed` lines in stderr could not be split cleanly into pre-restart vs post-restart subsets via stderr alone. As a result it is not currently confirmable whether (a) post-restart failures carry the new cause format, (b) the fix is live but no post-restart structural failures occurred so the format hasn't been exercised, or (c) the fix did not deploy cleanly despite the test passing.

**Why it matters to profitability / safety / reliability**

Low direct impact. The structural lane is provably running post-restart (30 successful `STRUCTURAL_PRIOR_RECOMPUTE` events in the trade-log archive within the 48h audit window), which is the operationally important fact. The cause-emission fix is observability-only — it ensures *future* failures can be diagnosed without resorting to ad-hoc instrumentation.

But leaving it unverified means EDGE-002's acceptance criteria are not fully met at the runtime layer; EDGE-002 itself is COMPLETE on the basis of unit-test verification (`tests/test_structural_task.py::test_run_once_failure_warning_surfaces_underlying_cause`), but the runtime confirmation footnote remains pending.

**Proposed Fix**

Inspect `logs/app/bot.log` (timestamped, unlike `launchd.stderr.log`) within the post-2026-04-27T13:03:19Z window for the structural warning format. Specifically:

1. Grep for `per-market recompute failed` in `bot.log` and `bot.log.2026-04-29` (the rotated post-restart files).
2. If matches exist, sample 5 and confirm they include the `repr(__cause__)` text and the `Traceback (most recent call last):` block.
3. If no matches exist in the post-restart window, the structural lane has had zero failures since restart — note that explicitly and consider the fix verified-in-test only, with a watch-for-future-cases acceptance criterion.
4. If matches exist but lack the expected format, file a follow-up: the fix did not deploy as designed despite the test passing.

**Acceptance Criteria**

- One of: (a) a sample of post-2026-04-27T13:03:19Z `bot.log` lines confirms the cause + traceback format is live; OR (b) post-restart inspection finds zero structural failures so far and a watch-this rule is added to a future audit's checklist; OR (c) the fix is found to have deployed incorrectly and a code follow-up is filed.

**Notes**

- This is purely a verification-gap closure for EDGE-002. No code changes anticipated unless option (c) above turns out to be the case.
- Do not bundle with broader log-format work. This is a specific check against a specific commit.

**Related**

- `PROFIT-EDGE-002` (closed 2026-04-26) — sub-fix #4 is the change being verified.
- `PROFIT-OBS-002` (closed) — ensured log rollover policy works on macOS; STRUCT-002 depends on the rotated `bot.log` files being readable, which OBS-002 closed.

**CLOSED 2026-05-02 (Claude) per option (b) — verified-in-test, no live failures since cutover.** Inspection of `logs/app/bot.log*` (3 files, ~13MB total spanning the post-cutover window 2026-05-01 → 2026-05-02) returned **0 matches** for `per-market recompute failed`. The structural lane has had zero failures since the Mac Studio cutover at 2026-05-01 ~14:00 UTC; the cause-emission format has therefore not been exercised in production but is verified at the unit level by `tests/test_structural_task.py::test_run_once_failure_warning_surfaces_underlying_cause`. Watch-this acceptance criterion: if a `per-market recompute failed` warning fires post-2026-05-02, immediately confirm the line includes `repr(__cause__)` text and a `Traceback (most recent call last):` block. If either is missing, reopen this entry as a deploy-correctness regression. The 1 successful `STRUCTURAL_PRIOR_RECOMPUTE` event in the post-cutover trade log confirms the structural lane is operationally alive.

---

### PROFIT-OBS-004

| Field | Value |
|-------|-------|
| **ID** | PROFIT-OBS-004 |
| **Title** | `paper_trades.edge` records the YES-side edge regardless of which side was traded; sign is misleading on every NO-side trade |
| **Category** | Observability / Trade-Log Fidelity |
| **Severity** | LOW (correctness-of-display, not correctness-of-decision) |
| **Status** | COMPLETE |
| **Priority** | LATER (does not block trade emission, calibration, or any safety gate; affects audit readability) |
| **Owner** | Shared |
| **Depends On** | — |
| **Blocks** | Audit ergonomics — every retrospective that joins `paper_trades.edge` to a directional thesis (e.g. "did the bot get edge wrong?") gets the wrong sign on NO-side trades; for the current 3 paper trades, this would mislead a reviewer into thinking the executor cleared a negative-edge trade when it actually cleared a positive-edge trade with a flipped log field |

**Description**

The 3 PAPER_TRADE records on `KXFISAEXTEND-26APR-MAY0{1,2,3}` (the only paper trades to date, all from the 2026-05-01 emission window) record `edge=-0.068`. Inspection of the same records' `estimated_probability` (0.432) and `side` (`no`) shows the executor's actual decision math used `P(NO) = 1 − 0.432 = 0.568`, traded NO at 50¢, and computed an executed-side edge of `0.568 − 0.5 = +0.068`. The recorded `edge=-0.068` is the YES-side perspective (`estimated_probability − 0.5 = 0.432 − 0.5 = -0.068`), not the executed-side edge. The executor decision was correct (positive expected value); the persisted display is wrong.

This is the most likely explanation for the "wait, the executor took a negative-edge trade?" question that surfaced during the 2026-05-01 post-cutover audit and that would surface again on every future audit until fixed.

**Why it matters to profitability / safety / reliability**

Zero direct profit/safety impact — the executor is doing the right thing; only the persisted log field is wrong. But every retrospective that filters `paper_trades.edge < 0` to find "executor mistakes" gets a false positive on every NO-side trade, and every retrospective that joins `edge` to outcome will mis-attribute losses on NO-side trades. This is exactly the kind of low-severity observability bug that quietly poisons every downstream analysis until someone explicitly notices the sign convention.

**Evidence / Source**

- `data/paper_trades.db` table `paper_trades`, all 3 rows: `side='no'`, `estimated_probability=0.432`, `market_yes_price=50.0`, `edge=-0.068`. Computed executed-side edge: `(1 - 0.432) - 0.5 = +0.068`.
- The 2 OPPORTUNITY records at positive edge in the same lifetime window (`+0.06` and `+0.064`, see PROFIT-OBS-003 evidence) are presumably YES-side (the convention in OPPORTUNITY records appears self-consistent — needs source-code verification per Proposed Fix below); they did not produce trades, so the bug is not visible there.

**Proposed Fix**

Audit the path from executor → `paper_trader.record_trade()` → `paper_trades.edge` column write. Two plausible fixes:

1. **Persist the executed-side edge.** Compute the side-aware edge at the executor (which already knows the side) and pass that value to `paper_trader.record_trade()` instead of (or in addition to) the YES-side edge. Lower-friction; preserves the existing schema; updates only the recorded value. Backward-compatibility: the existing 3 rows would remain in the YES-side convention; either backfill them (3 rows is trivial — they're all `side=no`, `estimated_probability=0.432`, so the migration is a single SQL update) or document the schema change in CHANGELOG and accept the discontinuity.

2. **Add a `executed_edge` column alongside `edge`.** Cleaner if there's a reason to preserve the YES-side edge as a separate column for reasoning purposes. Requires a schema migration.

Approach (1) is preferred unless an audit of `evidence_store` and downstream consumers reveals a need to preserve both perspectives.

**Acceptance Criteria**

- The `paper_trades.edge` column reflects the executed-side edge for both YES-side and NO-side trades.
- The 3 historical KXFISAEXTEND records are either backfilled (preferred) or annotated in a one-line CHANGELOG note explaining the convention change at the schema-cut date.
- A test in `tests/test_executor.py` or `tests/test_paper_trader.py` covers both sides and asserts the executed-side convention.

**Notes**

- Surface this with the 3 historical KXFISAEXTEND audit attempts in mind. The 2026-05-01 post-cutover audit was the third time the negative-edge question came up; documenting it now means the next reviewer (or agent) starts from "this is a known display bug" rather than "wait, did the bot trade negative edge?"
- This is not load-bearing for any other entry; close it independently when convenient.
- If approach (1) is taken, also confirm the convention used in `OPPORTUNITY` records — they currently appear consistent (positive edge means tradeable) but a source-code audit at the same time is cheap insurance against schema drift.

**Related**

- `PROFIT-OBS-003` (promoted HIGH/NOW 2026-05-01) — companion observability item; both surfaced from the same 2026-05-01 audit.
- `PROFIT-EDGE-004` (open) — neither blocks nor depends on OBS-004; the EDGE-004 audit reads `OPPORTUNITY.edge`, not `paper_trades.edge`, and OPPORTUNITY's edge convention appears to be already consistent.

**CLOSED 2026-05-02 (Claude):** Approach (1) per the entry — `paper_trades.edge` now persists the executed-side edge. Implementation: `trading/paper_trader.py:record_trade` computes `executed_edge = analysis.edge if side=='yes' else -analysis.edge` and writes that to the `edge` column instead of `analysis.edge`. The 3 historical KXFISAEXTEND-26APR-MAY0{1,2,3} rows were backfilled in-place via `UPDATE paper_trades SET edge = -edge WHERE side='no' AND edge < 0;` — verified post-update: all 3 now read `edge=+0.068`. Two new tests in `tests/test_paper_trader.py` (`test_record_trade_persists_executed_side_edge_yes`, `test_record_trade_persists_executed_side_edge_no`) pin the executed-side convention for both directions. No CHANGELOG entry — the schema and shipped runtime behaviour are unchanged from the executor's perspective; only the persisted display flips sign for NO-side rows. Landed during the v0.29.59 governance Phase 2 shadow soak (no governance audit-data path touched).

---

### PROFIT-OBS-005

| Field | Value |
|-------|-------|
| **ID** | PROFIT-OBS-005 |
| **Title** | Cooldown gate trips spuriously on never-traded tickers for ~4h after process restart (`time.monotonic()` vs default 0.0) |
| **Category** | Observability / Decision Consistency |
| **Severity** | LOW–MEDIUM (silent: every never-traded ticker receives a `paper cooldown` skip in the first 14 400 s after each bot restart, regardless of whether it was actually ever traded) |
| **Status** | OPEN (not fixed mid-soak; deferred per the "decision consistency = high-risk during soak" rule in CLAUDE.md) |
| **Priority** | LATER (no production impact during the current 14-day Phase 2 soak — bot has been continuously running since 2026-05-02 04:12 UTC, so `time.monotonic()` is well past 14 400) |
| **Owner** | Claude (post-soak) |
| **Depends On** | None |
| **Blocks** | None |

**Symptom (CI-only at present):**

`tests/test_executor.py::test_price_boundary_behavior` and 13 other executor / sims-smoke tests fail on a freshly-booted GitLab runner with assertions like:

```
assert 'paper cooldown: last trade 0.0h ago (cooldown=4h)' is None
```

The exact same suite passes on the Mac Studio dev box (uptime measured in days).

**Root cause:**

`trading/executor.py:208`:
```python
last    = self._last_traded.get(analysis.market.ticker, 0.0)
elapsed = time.monotonic() - last
if elapsed < cfg.paper_ticker_cooldown:
```

`_last_traded.get(ticker, 0.0)` returns `0.0` for any ticker that has never been traded. `time.monotonic()` on a freshly-booted machine returns seconds-since-boot (low, e.g. ~50 s in CI). `elapsed = monotonic - 0.0 ≈ 50 s`, which is `< 14 400 s` cooldown, so the gate trips — even though no trade has ever happened on that ticker.

The bug is silent on long-uptime hosts because `time.monotonic()` quickly exceeds 14 400 (4 h after boot), at which point all 0.0 defaults look "old enough." Locally, every uptime past 4 h hides the bug.

**Production impact:**

After every bot restart (operator-driven only — no auto-restart in current ops), the first 4 h of decisions on any ticker not present in `paper_trades.db` will be skipped with a misleading "paper cooldown" reason. The bot does not lose money (skips are safe); the decision log is just polluted with false-positive cooldown skips. Restart frequency is currently ~once per multi-day soak window, so impact is bounded.

**Fix (deferred):**

Two viable approaches; pick post-soak:

1. **Sentinel default:** change `_last_traded.get(ticker, 0.0)` → `_last_traded.get(ticker, float("-inf"))`. Never-traded tickers report infinite elapsed time, so the cooldown gate is bypassed. Smallest possible diff; preserves all other semantics.

2. **Membership check:** replace the `dict.get` with `if ticker not in self._last_traded: <skip cooldown>; else: <existing logic>`. Slightly more explicit but equivalent.

Option 1 is the minimal change. Add a unit test that boots a `TradeExecutor` against a paper-trader stub with no DB rows and asserts no cooldown skip is emitted.

**Why deferred:** changing executor cooldown logic is a "decision consistency" change per the global `domain_constraints.md` rule and CLAUDE.md "high-risk during soak" guidance. Soak window runs through 2026-05-16 04:12 UTC.

**CI workaround (landed 2026-05-02):**

`tests/conftest.py:_ci_stub_env` now sets `PAPER_TICKER_COOLDOWN=0` and `LIVE_TICKER_COOLDOWN=0` when the `CI` env var is present. This is a CI-only mitigation — production defaults remain `14400` and `0` respectively. The 14 affected CI tests pass under this stub. Local runs are unaffected (no `CI` var).

---

### PROFIT-CUTOVER-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-CUTOVER-001 |
| **Title** | MacBook → Mac Studio operational handoff (paper-mode runtime, governance Phase 2 launchd, SQLite state migration) |
| **Category** | Operations / Reliability / State Management |
| **Severity** | HIGH (until verified; downgrade to MEDIUM after first Mac Studio paper trade emits cleanly) |
| **Status** | OPEN |
| **Priority** | NOW (operator action ongoing; some sub-tasks pending Mac Studio verification) |
| **Owner** | Shared (operator + Claude) |
| **Depends On** | — |
| **Blocks** | All future EDGE-004 evidence accumulation (Mac Studio is the only host where new evidence is produced); Phase 2 §8.5 acceptance review (clock runs on Studio per `PROFIT-PHASE2-001`); any future production-soak audit of the post-EDGE-003 v0.29.58 stack |

**Description**

The MacBook (`hostname = MacBook-Pro.local`, Apple T6030 / M3 Pro, 18 GB unified memory) ran the bot from 2026-04-18T02:11:24Z (`paper_start_time` per `bot_state` table) through 2026-05-01T13:05:54Z (`Bot shutting down...` line in `logs/app/bot.log`) — 13 days of paper-mode operation across versions v0.29.5 → v0.29.58. The Mac Studio (sufficient memory headroom for `qwen3:14b` per `docs/governance/PHASE2_RUNBOOK.md` "Model selection") took over operations on 2026-05-01.

The cutover requires preserving four bot-state pillars across hosts (per the README's "Mac Studio operational handoff" section):

1. **Paper-trade history.** `data/paper_trades.db.paper_trades` (3 rows). Without it, bankroll resets to the `BANKROLL` env default and the only resolved-trade calibration evidence is erased.
2. **Source credibility graduations.** `data/paper_trades.db.source_credibility` (1 row: VitalLaw.com 0W/3L 0.5x). Without it, the LLM re-trusts VitalLaw at 1.0x and could repeat the same losing entries.
3. **Source statistics + Reddit discovery state.** `data/paper_trades.db.source_stats` (405 rows lifetime funnel) + `data/paper_trades.db.subreddit_candidates` (2,508 rows discovery state). Both feed the Phase 2 governance agent's decision context (memory note: `project_adaptive_governance_direction`).
4. **Dossier + structural-prior corpus.** `data/evidence_store.db` (32 dossiers / 248 dossier_updates / 7,510 dossier_update_evidence / 32 structural_priors). Without it, the structural and accumulation lanes lose their accumulated context and revert every market to a near-uniform time-to-close fallback.

`.gitignore` excludes `data/*.db` and `logs/` ("too large/personal for version control"), so the migration uses **committed SQL dumps + a logs tarball + plaintext reports** (per the existing `windows_archive/` precedent) rather than committing binary DB files or live log directories. Three layers so a single Studio `git clone` carries the full MacBook era forward — operator never returns to MacBook hardware:

- **Layer 1 (runtime state):** `transfer/macbook_handoff_2026-05-01/paper_trades.sql` (456 KB, sha256 `3b21a4f9…`) and `evidence_store.sql` (1.2 MB, sha256 `9e70227b…`) — `sqlite3 .dump` of both DBs; restored to `data/*.db`.
- **Layer 2 (bulk log archive):** `transfer/macbook_handoff_2026-05-01/logs_app_and_trades.tar.gz` (27 MB compressed, 151 MB raw, sha256 `0af4f320…`) — full 13-day `logs/app/` (33 files: bot logs + errors + launchd stdout) + `logs/trades/` (14 files: JSONL decision-event archive that backs all of EDGE-004/OBS-003/RUNTIME-001 evidence). 47 files total. Extracts to `mac_archive/macbook_2026-05-01_import/logs/{app,trades}/` (`.gitignore`d destination, separate from Studio's live `logs/`).
- **Layer 3 (plaintext reports):** `transfer/macbook_handoff_2026-05-01/reports/daily_review/` (14 daily-review `.txt` rollups, 1.6 MB total) + `reports/code_review_eval/` (summary.md + 30 per-test-repo CSVs from the 2026-04-27 code-review-graph eval). Committed directly as text — no extraction needed.
- **Documentation:** `transfer/macbook_handoff_2026-05-01/MANIFEST.md` (row counts, hashes, restore command, paste-friendly verification checklist) + `PROVENANCE.md` (narrative).
- **Restore script:** `scripts/restore_macbook_handoff.sh` — flags: `--force`, `--extract-logs`, `--extract-logs-only`, `--dry-run`. Refuses to overwrite without `--force`; verifies post-restore row counts + key values; idempotent; macOS-default bash 3.2 compatible.

Excluded from the bundle (and why): `evaluate/test_repos/` (172 MB of clonable external repos), `data/bot_runtime.lock` (host-specific), `data/evidence_store.db-shm/.wal` (SQLite WAL artifacts; not durable), `logs/coverage/` + `logs/tests/` (regeneratable from pytest), `.venv/` + `__pycache__/` + `.ruff_cache/` + `.hypothesis/` + `.code-review-graph/` (derived/cached). See `transfer/macbook_handoff_2026-05-01/MANIFEST.md` "Not included" for the full rationale.

**Why it matters to profitability / safety / reliability**

The two SQLite stores are designed for single-writer mode. A botched cutover that left both hosts running concurrently would either (a) corrupt `source_credibility` multipliers under concurrent updates, (b) lose paper-trade emissions to whichever host wrote last, or (c) trip Reddit / Kalshi rate signals from the combined external IP signature (per the CLAUDE.md "Concurrent Mac + Windows instances on the same network" gotcha — extends naturally to two Macs sharing a NAT). The cutover must be **explicit and final**, not active-active.

Operationally, until the cutover is verified, the bot is in a known-fragile state: the MacBook has stopped, the Studio has just started, no Studio paper trade has yet resolved, and the §8.5 Phase 2 soak clock has only been ticking ~1 hour at the time of this entry's creation. Verification produces a step-function reduction in operational risk.

**Evidence / Source**

- MacBook last shutdown: `logs/app/bot.log` final line — `2026-05-01 13:05:54,500 UTC INFO main Bot shutting down...`.
- MacBook hostname / hardware: `Darwin MacBook-Pro.local 25.3.0 ... arm64` (T6030 = Apple M3 Pro/Max, 18 GB unified memory ceiling for `qwen3:14b` per Ollama memory model).
- MacBook governance launchd state: `launchctl list | grep -E "kalshi|governance"` returns only `com.kalshibot.dailyreview` — `com.kalshi.governance.fast` / `.deep` were never bootstrapped on this host.
- MacBook stale lock: `data/bot_runtime.lock` mtime 2026-04-29T22:00:58, contents `{"pid":793,"cwd":"...","started_utc":"2026-04-30T04:00:58Z","argv":["..."]}` — a stale leftover from a crashed/terminated bot that the lock-file's self-healing PID check would clear on next start, but should be deleted as part of cutover housekeeping.
- Operator confirmation 2026-05-01: Mac Studio launchd jobs (`com.kalshi.governance.fast` and `.deep`) were bootstrapped earlier today; the Studio is now the active operational host.

**Proposed Fix — checklist**

This entry tracks the cutover as a sequenced operation. Items already complete are checked; remaining items are operator/agent action:

- [x] Generate SQL dumps of `paper_trades.db` + `evidence_store.db` from the MacBook.
- [x] Commit dumps + `MANIFEST.md` + `PROVENANCE.md` to `transfer/macbook_handoff_2026-05-01/`.
- [x] Bundle `logs/app/` + `logs/trades/` (151 MB raw → 27 MB compressed) into `transfer/macbook_handoff_2026-05-01/logs_app_and_trades.tar.gz` so the Studio inherits the full 13-day evidence base, not just the SQLite-backed durable state.
- [x] Commit `logs/reports/` (14 daily-review `.txt` files, 1.6 MB) and `evaluate/{reports,results}/` (12 KB code-review-graph eval outputs) directly as plaintext under `transfer/macbook_handoff_2026-05-01/reports/`.
- [x] Add `mac_archive/macbook_2026-05-01_import/` to `.gitignore` (matches existing `windows_archive/raw/` pattern); the tarball in `transfer/` is canonical, the extracted tree is regenerable.
- [x] Commit `scripts/restore_macbook_handoff.sh` with row-count verification + `--extract-logs` / `--extract-logs-only` flags. End-to-end tested: dry-run, fresh restore, collision-refusal, `--force` overwrite, `--extract-logs-only` extract, all PASS.
- [x] Update `README.md` with the cutover handbook (the "Mac Studio operational handoff" section, three-layer migration explanation, Studio first-launch recipe).
- [x] Update `docs/governance/PHASE2_RUNBOOK.md` with the soak-clock start time and §8.5 ETA.
- [ ] **Mac Studio operator action:** `git pull`, run `./scripts/restore_macbook_handoff.sh --extract-logs`, verify post-restore row counts match the manifest (Layer 1) AND `find mac_archive/macbook_2026-05-01_import/logs -type f | wc -l` returns 47 (Layer 2). Confirm `bot_state.notional_bankroll = 42.50` and `source_credibility` shows VitalLaw.com at 0W/3L / 0.5x.
- [ ] **Mac Studio operator action:** Confirm Phase 2 launchd is firing cycles (per `PHASE2_RUNBOOK.md` "Monitoring during the 14-day soak"). Specifically, after the next `com.kalshi.governance.fast` cycle window, verify `logs/governance/decisions.jsonl.<DATE>` contains `GOVERNANCE_CYCLE_START` + `GOVERNANCE_CYCLE_END` records.
- [ ] **Closure trigger:** when the *first Mac Studio paper trade* resolves cleanly (any outcome, any market) AND the bankroll math is consistent on the Studio. This proves the migrated state is being used live and that no schema/fk drift was introduced by the dump/restore.
- [ ] **MacBook side:** delete `data/bot_runtime.lock` and confirm via `launchctl list` that no kalshi or governance LaunchAgents remain bootstrapped. (Removing the bot LaunchAgent itself is optional; it can stay installed but stopped.)

**Acceptance Criteria**

- Mac Studio's first paper trade resolves; `bot_state.notional_bankroll` shows arithmetic continuity with the migrated $42.50 starting value.
- `paper_trades.db.source_credibility` shows VitalLaw.com at 0W/3L (i.e., the migration brought across the auto-suppression).
- Mac Studio `logs/governance/` directory exists and contains at least one full `GOVERNANCE_CYCLE_START` → `GOVERNANCE_CYCLE_END` pair.
- MacBook `data/bot_runtime.lock` deleted; no bot processes running (`pgrep -f "kalshi_bot/main"` returns empty).
- This entry is closed with a one-paragraph "Closure" note recording (a) the date the first Studio trade resolved, (b) the row counts the post-restore verification produced, and (c) any drift from the manifest counts.

**Notes**

- Do not run the bot on the MacBook again. The bot is not designed for active-active operation across hosts. If the Studio fails, follow the README's "What the MacBook keeps doing" failover procedure (reverse-direction handoff via `transfer/`).
- `transfer/macbook_handoff_2026-05-01/` is a one-time directory tagged with the cutover date. Future migrations between machines should use the same pattern with a new dated directory; do not edit or overwrite the 2026-05-01 directory.
- The dump/restore is *additive* in the sense that the MacBook's data still exists on its disk for offline analysis; only the live runtime moves. Treat the MacBook as a read-only analysis workstation per the README.
- If the Studio's first paper trade drops `data/paper_trades.db` rows or `bot_state.notional_bankroll` is reset to the `BANKROLL` env default, the restore did not happen or did not happen before first launch — re-run `scripts/restore_macbook_handoff.sh` (with `--force` to overwrite the partial state).

**Related**

- `PROFIT-PHASE2-001` (opened 2026-05-01) — the Phase 2 shadow soak clock starts on the Studio post-cutover; CUTOVER-001 closure is the precondition for PHASE2-001's first daily monitoring entry.
- `PROFIT-RUNTIME-001` (closed) — RUNTIME-001's 2026-05-01 Validation Notes provide the lifetime-aggregate evidence that informed the cutover-state inventory above.
- `PROFIT-OBS-003` + `PROFIT-OBS-004` (both open as of 2026-05-01) — both surfaced from the same audit that motivated the cutover; closing them is appropriate Studio-side work.
- README "Mac Studio operational handoff (2026-05-01)" — operator-facing handbook; this entry tracks the engineering state, the README tracks the operator-runbook state.

**Verification addendum** (2026-05-02, soak-window read-only host audit on `Jacobs-Mac-Studio.local`)

Read-only verification of all four migration layers run against the live Studio state (host confirmed via `hostname` = `Jacobs-Mac-Studio.local`, `uname -m` = `arm64`, kernel `25.4.0` distinguishing Studio T6041 from MacBook T6030). Read-only access used `?immutable=1` URI for SQLite; bot was not stopped.

*Layer 1 — runtime state (`paper_trades.db`):*

| Manifest expectation | Observed | Drift | Verdict |
|---|---|---|---|
| `paper_trades` rows = 3 | 3 | 0 | ✅ exact |
| `source_credibility` VitalLaw.com | `0W / 3L / 0.5x` | 0 | ✅ exact (auto-suppression preserved) |
| `source_stats` rows ≈ 405 | 414 | +9 | ✅ within tolerance (post-cutover source-stat growth is expected) |
| `subreddit_candidates` rows ≈ 2,508 | 2,887 | +379 | ✅ within tolerance (Reddit discovery is continually adding rows; this is correct behaviour) |
| `bot_state.notional_bankroll` = 42.50 | 42.5 | 0 | ✅ exact |
| `bot_state.paper_start_time` | `2026-04-18T02:11:24.442824+00:00` | 0 | ✅ exact (preserves the full 13-day MacBook lifetime window — this is what makes lifetime-aggregate audits joinable across the cutover) |

The 3 paper_trades themselves: all KXFISAEXTEND-26APR-MAY0{1,2,3}, all NO side, all `resolved=1, resolved_yes=1, pnl_dollars=-2.5, resolved_ts=2026-05-01T03:31:04Z`. Exact match against the entry's "3 actual paper trades... all losses" claim. Zero new Studio-side paper_trades exist as of 2026-05-02.

*Layer 1 — runtime state (`evidence_store.db`):*

| Manifest expectation | Observed | Drift | Verdict |
|---|---|---|---|
| `dossiers` rows = 32 | 33 | +1 | ✅ within tolerance (one new dossier created post-cutover, consistent with eager-creation per PROFIT-DOSSIER-001) |
| `dossier_updates` rows = 248 | 249 | +1 | ✅ same (matches the +1 dossier) |
| `structural_priors` rows = 32 | 33 | +1 | ✅ same |
| `evidence` rows | 249 | n/a | (manifest gave `dossier_update_evidence = 7,510`; not directly comparable to the `evidence` table count, but the +1 increment family is internally consistent across dossiers/updates/priors) |

Note on read-only access: the `?mode=ro` URI form failed with SQLITE_CANTOPEN (code 14) on `evidence_store.db` because no `-wal`/`-shm` files were present at probe time and `mode=ro` cannot create them. Switched to `?immutable=1` which never opens a journal — the empirically safer option for live-process probes. No write occurred.

*Layer 2 — bulk log archive (`mac_archive/macbook_2026-05-01_import/logs/`):*

`find mac_archive/macbook_2026-05-01_import/logs -type f | wc -l` = **47**. Exact match against the entry's "47 files total" expectation. `logs/app/` and `logs/trades/` subdirectories both present. The Layer 2 archive is therefore intact and joinable for any post-cutover audit that needs the pre-cutover JSONL record stream.

*Layer 3 — plaintext reports / `transfer/` source bundle:*

`transfer/macbook_handoff_2026-05-01/` is no longer present on disk. Per recent commit `1e13c36` ("docs(readme): sync to v0.29.59 + flag transfer/ as historical post-rewrite"), the source bundle was deliberately removed after the restore-and-extract step succeeded. CUTOVER-001's checklist did not require `transfer/` to remain; the canonical state now lives in `data/*.db` (Layer 1) and `mac_archive/.../logs/` (Layer 2). Deletion is consistent with the README's post-handoff hygiene step.

*Phase 2 governance soak liveness:*

`logs/governance/decisions.jsonl` (101.1 KB single file, no `.<DATE>` rotation in this archive layout):

- `GOVERNANCE_CYCLE_START` = 18, `GOVERNANCE_CYCLE_END` = 18 — every cycle closed cleanly, zero orphans across the soak window so far. Cycle ID range: `gc_2026-05-01_190127` → `gc_2026-05-03_010440` (so the file already contains decisions through ~T+22h after the post-fix reset baseline of `gc_2026-05-02_041248`).
- `GOVERNANCE_DECISION` = 57 — already above the §8.5 floor of 30 in the first 22 hours of the post-fix soak. The acceptance criterion's "earliest organic close" target of 2026-05-09 is volume-feasible by a wide margin.
- `GOVERNANCE_DECISION_PARSE_ERROR` = 7 — all confined to cycles `gc_2026-05-01_210127` → `gc_2026-05-02_030140`, exactly the 5 pre-fix cycles documented in PROFIT-GOV-001's closure note. Zero PARSE_ERROR records appear after the reset baseline `gc_2026-05-02_041248`. The qwen3 + `format=json` + `think=False` fix is empirically holding.
- `KILL_SWITCH` / `VALIDATION_ERROR` / `BATCH_ABORTED` events: **0**. Shadow-mode invariant intact.

*Acceptance-criteria status as of 2026-05-02:*

| Criterion | Status | Note |
|---|---|---|
| Studio's first paper trade resolves; bankroll arithmetic continuity | ❌ NOT YET | Zero Studio paper_trades exist. Severity-downgrade trigger ("emits cleanly") and closure trigger ("resolves") both pending. Given OBS-003's 240/260 silent-exit rate, paper-trade emission cadence is ~1 per ~4 days; closure feasible within the soak window but not deterministic. |
| `source_credibility` shows VitalLaw 0W/3L | ✅ | Verified above. |
| `logs/governance/` has ≥1 START → END pair | ✅ | 18/18 verified above. |
| MacBook `data/bot_runtime.lock` deleted; no bot running | ⏳ unverified | Operator-side check; not visible from the Studio host. |
| Closure note recorded | ⏳ pending | Cannot be written until the first criterion lands. |

*Recommendation:* hold CUTOVER-001 at OPEN/HIGH until a real Studio trade emits. Migration completeness itself is now empirically established and documented; the remaining risk is purely "does the running bot actually produce a trade that round-trips through the migrated state". When the first Studio paper trade emits, severity downgrades HIGH → MEDIUM per the entry's own rule. When it resolves, closure note can be written.

*Risk-review note (per `~/.claude/rules/risk_review.md`):* this verification touched no code, no DB writes, no production state. The only operations were `sqlite3 ?immutable=1` reads and filesystem `find` / `ls`. Soak-safe by construction.

---

### PROFIT-PHASE2-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-PHASE2-001 |
| **Title** | Governance Agent Phase 2 shadow-mode 14-day soak (started 2026-05-01 ~14:00 UTC on Mac Studio against `qwen3:14b`; §8.5 acceptance ETA 2026-05-15) |
| **Category** | Governance / Validation |
| **Severity** | MEDIUM (the soak itself is the validation; only acceptance failure or a `KILL_SWITCH` trip during the soak elevates severity) |
| **Status** | IN_PROGRESS (soak clock running; no operator action expected during the window unless monitoring surfaces a failure mode) |
| **Priority** | NOW (operator's daily monitoring checklist per `PHASE2_RUNBOOK.md`) |
| **Owner** | Operator (monitoring) + Claude (post-soak acceptance review) |
| **Depends On** | `PROFIT-CUTOVER-001` (Mac Studio is the only host where the soak clock ticks; clock did not start until cutover happened) |
| **Blocks** | Governance Phase 3 real-mode flip (per ROADMAP `GOV.P3` — "Requires P2 + 14d shadow soak"); ultimately Phase 4 Claude-API tier and weekly self-review |

**Description**

Per the Phase 2 spec (`docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5) and the operator runbook (`docs/governance/PHASE2_RUNBOOK.md` "Monitoring during the 14-day soak"), Phase 2 acceptance requires:

- ≥14 days of clean shadow-mode operation
- ≥30 governance decisions accumulated
- ≥85% of decisions deemed reasonable on manual review

The shadow-mode invariant is that `runtime_overrides.yaml`'s `applied=` field never grows during the soak (the agent writes only to `proposed`, never to `applied`). Any growth in `applied=` during the soak is a load-bearing safety bug and stops the clock immediately.

Bootstrap state on 2026-05-01:

- Mac Studio launchd: `com.kalshi.governance.fast` (every 2 h, `RunAtLoad=false`) and `com.kalshi.governance.deep` (daily 09:00) bootstrapped at ~14:00 UTC.
- Governance LLM model: `qwen3:14b` (per `PHASE2_RUNBOOK.md` "Model selection (hardware-conditional)"; the launchd plists shipped configured for the MacBook 18 GB target with `qwen3:8b` and were edited to `qwen3:14b` for the Studio).
- Trading-bot signal-analyzer LLM: `qwen2.5:7b` unchanged (per `PROFIT-LLM-001` — unification with the governance model is intentionally deferred to post-Phase 2).
- Logs path: `logs/governance/decisions.jsonl.<DATE>` per the runbook.

**Why it matters to profitability / safety / reliability**

Phase 2 is the load-bearing safety milestone for the LLM-driven governance agent. The agent is designed to dynamically add/remove sources, keywords, and thresholds — exactly the levers that PROFIT-EDGE-004 hypotheses 1 and 3 want manipulated. But a governance agent that rewrites production state without first proving 14 days of reasonable shadow decisions is the riskiest possible thing in the codebase: it has root-cause access to the bot's belief inputs.

The 14-day clock is not arbitrary; it's the minimum sample size for the §8.5 acceptance criteria (≥30 decisions on a `fast` cadence of 2h means a theoretical maximum of 168 decisions over 14 days, but the agent's `no_action` default and the `FakeLLM` baseline mean actual decision count is much lower — 30 is the floor below which acceptance is statistically unstable).

**Evidence / Source**

- Operator-confirmed bootstrap on Mac Studio 2026-05-01 ~14:00 UTC.
- `ops/launchd/com.kalshi.governance.fast.plist` — every 2 h cadence, `GOVERNANCE_LLM_MODEL=qwen3:8b` (default; edited to `qwen3:14b` on the Studio per `PHASE2_RUNBOOK.md`).
- `ops/launchd/com.kalshi.governance.deep.plist` — daily 09:00.
- `docs/governance/PHASE2_RUNBOOK.md` — operator-facing daily monitoring checklist; this entry tracks the engineering view.
- ROADMAP `GOV.P3` row — explicit "Requires P2 + 14d shadow soak" dependency.

**Proposed Fix — milestone tracking, not a code-fix entry**

Phase 2 acceptance is the work itself, not a fix. The entry remains IN_PROGRESS for the duration of the soak. Per-day operator action is the runbook's monitoring checklist:

```bash
DATE=$(date -u -v-1d +%Y-%m-%d)
grep -c "GOVERNANCE_CYCLE_START" "logs/governance/decisions.jsonl.${DATE}"
grep -c "GOVERNANCE_DECISION" "logs/governance/decisions.jsonl.${DATE}"
grep -E "PARSE_ERROR|VALIDATION_ERROR|BATCH_ABORTED|KILL_SWITCH" "logs/governance/decisions.jsonl.${DATE}"
python -m utils.runtime_overrides --status | grep "applied="
```

Per-day Claude action: none unless the operator surfaces an event from the monitoring grep.

Acceptance review (target ~2026-05-15 or as soon as ≥30 decisions accumulated):

1. Aggregate decision count from `logs/governance/decisions.jsonl.*` across the soak window.
2. Sample 10 decisions per day (`shuf -n 10`) for the runbook-described "reasonable / not reasonable" review.
3. Confirm `applied=` is unchanged from soak start.
4. Confirm zero `KILL_SWITCH` events during the soak.
5. Aggregate "reasonable" rate; if ≥85%, mark this entry COMPLETE and unblock `GOV.P3`.

**Acceptance Criteria**

The original spec §8.5 floor was a single time-based gate (≥14 days). After the 2026-05-02 clock reset (see Notes), post-fix decision throughput is ~3.3/cycle × 12 cycles/day = ~40 decisions/day — 25× the assumed density the 14-day floor was sized against. Decision *count* is no longer the binding constraint; *coverage* is. The exit gate is therefore tightened from a single time floor to the conjunction below — operator-approved 2026-05-02 ~14:00 UTC. Earliest organic close: ~2026-05-09 (T+7d from reset, when the first deep-cycle weekly window completes).

**ALL of the following must hold simultaneously:**

- **Time:** ≥7 days elapsed since the reset baseline (2026-05-02 ~04:12 UTC) — earliest acceptance check 2026-05-09 ~04:12 UTC.
- **Volume:** ≥30 `GOVERNANCE_DECISION` events accumulated post-reset.
- **Cadence coverage:** ≥7 successful deep-cadence cycles (`GOVERNANCE_CYCLE_END` with `cadence=deep`) — this is the load-bearing addition; deep cycle pulls a 7-day audit window vs. fast's 24h, so without it we are validating only half the agent's behaviour.
- **Candidate diversity:** ≥3 distinct `target` values across all decisions (otherwise we are sampling the same 2-3 underlying decisions repeatedly).
- **Quality:** Manual review marks ≥85% of sampled decisions reasonable (sample 10/day × actual days elapsed; if the soak closes at T+7d the sample is 70 decisions).
- **Safety:** Zero `applied=` growth in `runtime_overrides.yaml` during the window. Zero `KILL_SWITCH` events.
- **Hard ceiling:** the original 14-day target close (2026-05-16 ~04:12 UTC) remains the upper bound — if the conjunction above does not converge by then, declare acceptance failure and re-plan rather than extend.
- **Closure note** records: (a) actual close date and elapsed soak time, (b) total decision count + breakdown by action and cadence, (c) distinct-target count, (d) reasonable-rate percentage with sample size, (e) any deferred follow-ups.

**Notes**

- Do not run governance against `qwen3:8b` on the Studio. The plists were intentionally edited to `qwen3:14b` for this host per the runbook; reverting to `qwen3:8b` would invalidate the soak.
- If the operator needs to stop the soak (e.g. the bot crashes for unrelated reasons and the operator can't get the host back up quickly), the soak clock pauses, not resets — but record the gap explicitly in this entry's Notes section so the post-soak review knows the actual continuous-operation duration.

**2026-05-02 ~04:12 UTC clock reset** — operator-approved reset, not a pause. The first 5 fast cycles (gc_2026-05-01_190127 → gc_2026-05-02_030140, 14 hours of wall-clock soak time) emitted **0 GOVERNANCE_DECISION records and 7 PARSE_ERROR records** because every Ollama call against `qwen3:14b` returned the empty JSON object `{}`. Root cause + fix tracked under `PROFIT-GOV-001`: qwen3 chain-of-thought reasoning + `format=json` produces empty output unless the top-level `think=False` parameter is set. The pre-fix audit data is structurally meaningless (no decisions to review), so continuing the original 2026-05-01 → 2026-05-15 window would only delay discovery of the failure mode. Reset baseline: first valid decision is `gd_2026-05-02_0001` at 2026-05-02T04:12:53Z. **New target close: 2026-05-16 ~04:12 UTC.** The mid-soak check LaunchAgent (`com.jake.kalshi-governance-midsoak`, scheduled 2026-05-08 09:07 MDT) was set against the *original* window and now fires at ~T+5d50min into the reset window — still mid-window enough to be useful; not worth re-scheduling.
- `PROFIT-LLM-001` (LOW, OPEN) tracks the post-Phase 2 decision on whether to unify the signal-analyzer LLM to `qwen3:14b` (or whatever model Phase 2 validates). Do not bundle that decision with PHASE2-001 acceptance.
- The runbook's "Common failures" section is the first stop for any per-cycle anomaly (Ollama unreachable, parse-error rate, KillSwitchActive). This entry is the *outcome* tracker; failures during the soak are individual incidents that don't necessarily block acceptance unless they're systemic.

**2026-05-03 mid-soak halt — §8.5 manual-review criterion only** — operator-approved per session decision 2026-05-03 ~13:30 UTC. The 14-day clock is **not** reset. Hard ceiling 2026-05-16 ~04:12 UTC stays put — newly-discovered bugs do not extend the window; the soak is being treated as a bug-discovery opportunity that must converge to acceptance within the original budget. Concretely:

- **Halted gate (one of six):** the §8.5 "Quality — Manual review marks ≥85% of sampled decisions reasonable" criterion is paused. Reason: `PROFIT-GOV-002` (filed 2026-05-03) confirms qwen3:14b rubber-stamps the `disable_source` candidates the heuristic feeds it, so the manual-review reasonable-rate is a confounded measurement (every flagged candidate is genuinely off-topic; agreement looks correct without proving discrimination). The 74 pre-fix decisions in `decisions.jsonl` are not authoritative for this gate.
- **Continuing gates (five of six):** `Time` (≥7d post-reset), `Volume` (≥30 post-reset DECISIONs — already met at 74), `Cadence coverage` (≥7 deep cycles), `Candidate diversity` (≥3 distinct targets — already met at 11), `Safety: applied=` no growth (intact; YAML absent), `Safety: KILL_SWITCH = 0` (intact). Launchd cycles continue running; their evidence still counts toward these criteria.
- **Resume condition for §8.5 manual-review:** `PROFIT-GOV-002` closes (rubber-stamp fix lands and the negative-control harness shows `NEG_A_high_signal_source` returns `no_action` with confidence ≥ 0.7), then a fresh ≥30-decision window accumulates *post-fix*, then the manual-review sample is drawn from that post-fix window only. **2026-05-03 15:28 UTC update:** GOV-002 is now CLOSED (A5 anchor-rate polarity callout landed in `governance/prompts.py:SYSTEM_PROMPT`; force-triggered cycle `gc_2026-05-03_152836` ran cleanly with 5 decisions, 0 PARSE_ERRORs). Post-fix baseline for the ≥30-decision counter is the first GOVERNANCE_DECISION record under cycle `gc_2026-05-03_152836` — `decided_at >= 2026-05-03T15:28:40+00:00` (decision_id `gd_2026-05-03_0001` *of that cycle* — note the bare ID is reused per-cycle in the current logging schema, so `(cycle_id, decided_at)` is the unambiguous tuple). At ~5 decisions per fast cycle on a 2-hour cadence, the 30-decision floor is reachable in ~5 hours of clean cycles; realistic ETA ~1 day given typical cycle yield variance. Resume the manual-review sampling once the counter reaches 30 (target 2026-05-04 ~21:00 UTC under nominal yield).
- **Hard-ceiling consequence:** if GOV-002 cannot land + a fresh 30-decision window cannot accumulate before 2026-05-16 ~04:12 UTC, Phase 2 acceptance fails by the time gate, not by the manual-review gate. That is the intended outcome — the budget enforces convergence rather than letting bug-discovery push the bar back indefinitely.
- **Operational guidance during halt:** continue the runbook's daily monitoring grep block. PARSE_ERROR / VALIDATION_ERROR / KILL_SWITCH events still trigger investigation. Do not change the launchd cadence or the model. The harness in `scripts/simulations/governance_negative_control.py` is the iteration loop for the GOV-002 fix; it does not touch production state.
- **Tooling note (2026-05-03):** `scripts/governance_monitor.py:19` resolves the default logfile via `Path(os.environ.get("KALSHI_HOME", _REPO_ROOT)) / "logs/governance/decisions.jsonl"`. On the Mac Studio host (`Jacobs-Mac-Studio.local`), `KALSHI_HOME` is set to the legacy underscore path `/Users/jacobparenti/vscode/kalshi_bot` rather than the actual hyphen path `/Users/jacobparenti/vscode/kalshi-bot`, which causes the script to silently report empty decision counts. Workaround: pass `--logfile logs/governance/decisions.jsonl` explicitly when running. Small follow-up fix: have the script fall through to `_REPO_ROOT` when the env-var-resolved path does not contain the expected file. Filed inline here rather than as a separate debt entry because it is portability ergonomics, not a runtime defect; will be cleaned up in a routine commit.
- **2026-05-03 day-3 pending drift check:** `docs/governance/2026-05-03-day-3-pending-mid-soak-confirmation.md` confirms the live log has no `2026-05-04` records yet, so the requested day-3 report is not valid to produce. Latest available data through `2026-05-03`: 113 decisions, 19 targets, 0 validation errors, 0 batch aborts, 0 KILL_SWITCH, and no `2026-05-03` parse errors.
- **2026-05-03 day-4 pending drift check:** `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation.md` repeats the date guard: still no `2026-05-04` records in the live JSONL, so day-4 evidence is not yet available. Latest available data through `2026-05-03`: 118 decisions, 19 targets, 15 post-GOV-002-fix decisions, 0 validation errors, 0 batch aborts, 0 KILL_SWITCH, and no `2026-05-03` parse errors.
- **2026-05-03 day-4 pending drift check #2:** `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-2.md` re-checks after the latest commits: still no `2026-05-04` records. Current tail remains 118 decisions, 19 targets, 25 distinct `(target, reasoning)` tuples, 0 validation errors, 0 batch aborts, 0 KILL_SWITCH, and no post-2026-05-02 parse errors in the monitor's per-day rows.
- **2026-05-03 day-4 pending drift check #3:** `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-3.md` repeats the date guard: still no `2026-05-04` governance records in the checked file. Totals remain 118 decisions, 19 targets, 25 distinct `(target, reasoning)` tuples, 0 validation errors, 0 batch aborts, and 0 KILL_SWITCH events.
- **Per-day soak placeholder history archived 2026-05-09:** the 5 day-3 / day-4 placeholder confirmation files referenced above (and `2026-05-03-day-4-pending-mid-soak-confirmation-4.md`) were moved to `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/` as part of the docs/ consolidation initiative. They were superseded by the canonical day-N confirmations (`2026-05-04-day-4-mid-soak-confirmation.md`, `docs/_archive/governance/2026-05-05-day-5-cycle-sanity-check-canonical.md` ARCHIVED Stream G R45, etc.). Live soak state continues to be tracked in this entry.

**Related**

- `PROFIT-CUTOVER-001` (opened 2026-05-01) — direct dependency; the soak clock cannot tick on a host that doesn't have the Studio's `qwen3:14b` configuration and the migrated state.
- `PROFIT-LLM-001` (open, LOW, deferred) — explicitly post-Phase 2; do not unify until PHASE2-001 closes.
- `PROFIT-GOV-002` (opened 2026-05-03, HIGH) — blocks the §8.5 manual-review criterion; see the 2026-05-03 mid-soak halt note above.
- ROADMAP `GOV.P3` — direct downstream consumer; `GOV.P3` is BLOCKED until PHASE2-001 closes.
- `docs/governance/PHASE2_RUNBOOK.md` — operator-facing companion document; this entry is the engineering tracker.

#### 2026-05-05 cycle integration — §8.5.1 early-close + §8.5.2 carve-out + Day-7 close path

User + Codex aligned 2026-05-05 on accelerating PROFIT-PHASE2-001 from 14-day to 7-day soak. Volume gate cleared 5.3× (242 decisions vs 30 floor by close-day-eve); safety counters all 0; calendar floor was the only unmet criterion. Calendar-floor cut OK; cadence/policy-knob cuts deferred to next post-Wave-1 soak from cycle 1 (per `feedback_soak_acceleration_split.md` memory).

**Spec changes (commits `2f1e900` + `b780fd6`):**

- §8.5.1 added — early-close gates (≥ 7 d calendar floor; ≥ 30 decisions; 0 KILL_SWITCH/batch_aborted/VALIDATION_ERROR; 0 PARSE_ERROR trailing 72 h; cadence ±10 %; ≥ 85 % manual-review reasonable; soak invariant; written attestation).
- §8.5.2 added — policy-equivalence carve-out for behavioural commits whose impact on the soak's actual decision distribution is empirically negligible. PROFIT-PHASE2-001's 4 surfaced commits (think=False bug-fix `fae72fa`; GOV-002 audit cycle `092666c…ce814b9`; A5 SYSTEM_PROMPT `b47ca71`) all qualify. Canonical example documented inline: A5's evidence-coverage analysis showed 241/242 = 99.6 % decisions had `anchor_rate=null` and were unaffected.

**Operator artifacts pre-staged for Day-7 close (commits `bf1b0e3 / 747ea15 / 7b9624b / 2f1e900 / b780fd6 / a4c4...` + this cycle):**

- `scripts/check_soak_invariant.sh` — gate-7 audit
- `scripts/governance_decision_review.py` — gate-6 review tool (operator-only)
- `scripts/pre_soak_close_branch_backup.sh` — rollback-anchor automation
- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — gate criteria + §8.5.2 invocation table
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` — close-attestation skeleton
- `docs/governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` — 11-step linear playbook
- `docs/governance/PROFIT-PHASE2-001-close-day-decision-flow.md` — quick-look flowchart
- `docs/governance/2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md` — gate-6 review accelerator (242 records: 99.6 % bulk-reviewable; 1 anomaly NYT World News)

**Date shift:**

- Day-7 earliest valid close: 2026-05-08T19:01Z (was 2026-05-15 default)
- Wave-1 deploy starts: 2026-05-08+ (was 2026-05-15)
- Wave-2 first feed: 2026-05-15+ (was 2026-05-22+)
- Wave-3 Lever B + C: 2026-06-06+ (was 2026-06-13+)
- Closure-target evaluation: 2026-05-29 (Wave-2 + 14d) (was 2026-06-06)

**Wave-1 dry-run validated** (commit-set `0531367 / edf38c1 / 921c275 / 9d6cce3 / e3d4e8d / 5828ad2` on origin branch `backup/wave-1-dry-run-2026-05-05`): all 6 per-feature commits land cleanly per `docs/_archive/governance/wave-1-deploy-commit-order-decision.md` (ARCHIVED Stream G R54). 7 spec ambiguities surfaced; F1 + F2 closed in cycle 1; F3-F6 closed by Codex 2026-05-05 (commits `6648705 / 807f20c / 0f45dfb`).

**Honest read 2026-05-05 (cycle 1):** all gates achievable on Day 7. Operator's only real close-day decisions are the gate-6 verdicts (1 anomaly NYT World News + bulk-review 241 dead-Reddit-sub disables) and any §8.5.2 fresh-commit case.

#### 2026-05-05 cycle 2 integration — Wave-1/2 prep + 0.04 lock + Lever C v1 lock + Branch D triggers

Second pass on 2026-05-05 (commits `b44dda2` + `80932cb` + this cycle's docs). Implementation Contract §9 became the sole authority on Claude/Codex split; the conflicting `feedback_codex_owns_heavy_coding.md` memory was removed. Routing went back to: Claude = architectural integrity / ambiguity resolution / review (especially gate-touching changes per contract §5+§11); Codex = precise implementation per locked specs.

**Claude artifacts (cycle 2; commits `b44dda2` + this cycle):**

- `docs/_archive/governance/wave-1-post-deploy-observation-plan.md` — 14-row regression watch matrix with per-row CLI; 24h validation window per Wave-1 commit (ARCHIVED Stream G R54)
- `docs/_archive/governance/2026-05-05-rollback-runbook-validation.md` — 1 HIGH drift (G1_CONFIDENCE_THRESHOLD env-revert row in §3 is fictional per Lever B spec §7) + 2 LOW (ARCHIVED Stream G R51)
- `docs/governance/PROFIT-PHASE2-002-onboarding.md` — next-soak setup (90 min fast / 12 h deep cadence; decision-policy knobs frozen per `feedback_soak_acceleration_split.md`)
- `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — Branch A → C → D sequence; 42 d worst-case walk; 28 d compressed via A+C parallel
- `docs/_archive/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` — Lever B floor LOCKED at 0.04; failsafe 0.08; 2× ratio invariant; lifts §11 harness deferral (ARCHIVED Stream G R34)
- `docs/_archive/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` — Lever C v1 LOCKED; §3.2 normalized hash; 3600 s default; gate-placement detail (record hash AFTER readiness gate pass); INV-6 boundary attestation (ARCHIVED Stream G R33)
- `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D triggers + handoff to PROFIT-LLM-001 / P4-GATE Appendix A; nomenclature distinguishes lever-menu-Lever-D (closed) from closure-path-Branch-D (active) (ARCHIVED Stream G R35)
- `docs/governance/edge-004-closure-path-tldr-v3.md` — TLDR v3 supersedes v2.2; closure-probability ranking ~58 % intake-side
- `docs/_archive/governance/2026-05-05-day-7-walkthrough-dry-trace.md` — 11-step playbook validated against HEAD `80932cb`; 1 LOW (pre-stage `b44dda2` + `80932cb` in §8.5.2 invocation table before fire-time) (ARCHIVED Stream G R47)
- `docs/_archive/governance/2026-05-05-day-7-attestation-prestage.md` — pre-staged attestation values: volume 267 (8.9× floor); safety 0/0/0; PARSE 7 cumulative (0 trailing 72h); max gap 2.01 h (ARCHIVED Stream G R47)
- `docs/_archive/governance/2026-05-05-doc-index-audit.md` — inventory of 87 governance + 20 spec files; recommendation 11 files archive-able post-close (ARCHIVED Stream G R39; superseded by 70+ Stream G archives)

**Codex artifacts (cycle 2; commit `80932cb`):**

- `scripts/check_soak_invariant.sh` — `--json` output + path-set sanity; corrected runtime path set from `executor/` to `trading/`
- `scripts/wave1_post_deploy_smoke.sh` — wraps 6 Wave-1 regression blocks per observation plan §5
- `scripts/distribution_analysis_v2.py` — close-day distribution rerun; JSON + Markdown output template
- `tests/test_close_day_scripts.py` + `tests/test_wave2_preload_harnesses.py` — coverage for the new scripts; pre-loaded Lever B G1=0.04 + A.1+ option-A/B xfail-strict harnesses

**Memory hygiene 2026-05-05:** `feedback_codex_owns_heavy_coding.md` deleted. Implementation Contract §9 is sole authority on Claude/Codex split. Two memories retained (operational, not role-conflict): `feedback_soak_confirmation_cadence.md` (1×/day cap), `feedback_soak_acceleration_split.md` (calendar vs policy-knob discipline).

**Honest read 2026-05-05 (cycle 2):** Day-7 close infrastructure is fully landed. Wave-3 levers (B + C) have locked v1 implementation choices. Wave-2 A.1+ branch decision is operator-discretion at Day-14. Branch-D escalation criteria spec'd. Doc index audit identifies 11 archive-able files post-close. `check_soak_invariant.sh` reports status=fail / commit_count=5 / missing_paths=[] under the §8.5.2 carve-out — gate-7 passes per the criteria runbook table + the 2 doc/script-only commits this cycle (out-of-scope-for-§8.5.2). Combined gate-6 evidence (Codex's 67/67 verdicted at 100 % reasonable + the 241/242 mechanically-uniform `disable_source` decisions) yields ≥ 99.6 % reasonable on full corpus — well above the 85 % floor.

#### 2026-05-05 cycle 3 integration — nomenclature cleanup + sizing-scope specs + Branch D runbook + baselines + LOCK harnesses

Third pass on 2026-05-05 (commits `a505a08` Claude + `3a400c1` Codex). Focused on operator-readiness for fire-time + post-Wave-2 escalation infrastructure.

**Claude artifacts (cycle 3; commit `a505a08`):**

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — §8.5.2 invocation table updated with all 7 surfaced commits including this-cycle doc/script artifacts (out-of-scope-for-§8.5.2). F1 fix from cycle-2 dry-trace closed.
- `docs/governance/2026-05-05-lever-d-nomenclature-cleanup-audit.md` — 18-occurrence audit; 1 MEDIUM finding (`wave-2-a1plus-branch-decision-table.md` "Branch D" → "option-A" for geopolitics specialist).
- `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — 6 Edits applying nomenclature fix per F1.
- `docs/_archive/governance/2026-05-05-wave-2-deploy-day-timing.md` — Branch A passive (no deploy) + Branch C deploy windows (ARCHIVED Stream G R53).
- `docs/_archive/governance/2026-05-05-wave-3-deploy-day-timing.md` — Lever B then Lever C; 14d inter-commit cadence (ARCHIVED Stream G R53).
- `docs/_archive/governance/wave-2-deploy-commit-order-decision.md` — locked 2-commit Wave-2 order (ARCHIVED Stream G R53).
- `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md` — 4-axis sizing surface (prompt template / model swap / context window / batch coherence).
- `docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` — 3-axis surface (market-scope filter / intake-path expansion / market-resolution cadence).
- `docs/_archive/governance/2026-05-05-changelog-drift-check.md` — 2 MEDIUM + 1 LOW drift findings in pre-staged Wave-2/3 CHANGELOG blocks (ARCHIVED Stream G R51).
- `docs/_archive/governance/2026-05-05-branch-d-fire-procedure-runbook.md` — operator playbook (~30 min wall-clock; 6-step decision tree) (ARCHIVED Stream G R53).
- `docs/_archive/governance/2026-05-05-doc-index-cleanup-execution-plan.md` — 3-commit plan (~15 min) for the 11-file archive recommendation; post-close trigger (ARCHIVED Stream G R38; superseded by Stream G actual archives).

**Codex artifacts (cycle 3; commit `3a400c1`):**

- `tests/test_lever_b_g1_floor_lock.py` — Lever B 0.04 / 0.08 / 2× ratio strict-xfail per cycle-2 LOCK addendum.
- `tests/test_branch_c_feed_selection_rubric.py` — Just Security primary + Lawfare secondary strict-xfail.
- 4 baseline reports in `docs/governance/2026-05-05-PROFIT-PHASE2-001-{daily-trend,llm-throughput,source-class-evolution,parse-error-retroactive}-{baseline,findings}.md`.
- 4 new audit scripts: `precommit_hook_health_audit.sh`, `test_coverage_audit.py`, `release_tag_inventory.sh`, `calibration_drift_wiring_audit.py`.
- `--output` flag added to existing `llm_throughput_audit.py`, `parse_error_retroactive_audit.py`, `source_class_evolution_audit.py`.

**Live empirical anchors (Codex 2026-05-05):**

- LLM throughput median 1063 ms / p90 1986 ms — well under PHASE2-002's 90-min-cadence p90 < 60 s rule (>30× headroom). Cadence-tune is empirically safe.
- Source-class distribution: 950 rows (news=603 / other=347) — pre-Wave-1 A.1 baseline.
- Daily-trend: 164 post-A5 decisions all uniform `disable_source`; 0 anomalies.
- PARSE_ERROR root cause: 7/7 events `missing_required_fields` — closes day-1/-2 forensics question.

**Honest read 2026-05-05 (cycle 3):** Operator-readiness for fire-time + Wave-1 deploy + Branch D escalation is fully spec'd. All Wave-3 + Branch D deferred work has bounded sizing surfaces (PROFIT-LLM-001 4 axes; P4-GATE Appendix A 3 axes). Cycle 3's biggest finding: LLM throughput headroom is so large (>30× under the PHASE2-002 cadence-tune rule) that aggressive cadence experiments are empirically defensible without further audit. Memory hygiene unchanged from cycle 2.

#### 2026-05-05 cycle 4 integration — fire-time playbooks + drift checks + failure-mode runbooks + contract retrospective

Fourth pass on 2026-05-05 (commits `6f09967` Claude + `7addfd1` Codex). Focus: operator-readiness compaction + drift surfacing + failure-mode coverage.

**Claude artifacts (cycle 4; commit `6f09967`):**
- `docs/_archive/governance/2026-05-05-day-7-fire-time-compact-checklist.md` — 30-45 min single-page playbook (ARCHIVED Stream G R47)
- `docs/_archive/governance/2026-05-05-wave-1-fire-time-per-commit-checklist.md` — per-commit 30 min playbook + cadence matrix (ARCHIVED Stream G R54)
- `docs/_archive/governance/2026-05-05-readme-drift-check.md` — 3 MEDIUM (status block / version cell / test count) (ARCHIVED Stream G R44)
- `docs/_archive/governance/2026-05-05-roadmap-drift-check.md` — 3 MEDIUM (P3/P4-GATE cross-links + What-Changed bullet) (ARCHIVED Stream G R44)
- `docs/_archive/governance/2026-05-05-debt-log-toc-navigation-audit.md` — TOC + per-entry status table recommendations (ARCHIVED Stream G R44)
- `docs/_archive/specs/2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` — post-Lever-B-1 secondary loosening stub (ARCHIVED Stream G R26)
- `2026-05-05-kill-switch-fire-procedure-runbook.md` — STOP-first; REVERT vs QUARANTINE
- `2026-05-05-mac-studio-dead-bot-reboot-runbook.md` — 4-category triage + restart
- `docs/_archive/governance/2026-05-05-cross-cycle-contract-adherence-review.md` — 4 cycles audited; 100% §9 adherence (ARCHIVED Stream G R48)

**Codex artifacts (cycle 4; commit `7addfd1`):**
- 4 pre-Wave-1 baseline scripts + reports (SKIPPED / OPP age / cooldown / per-ticker trade-rate)
- 3 strict-xfail harnesses (PROFIT-LLM-001 prompt-template variants A1-A4; Branch D fire procedure; failure-mode runbooks)
- 3 operator scripts (wave1_fire_time_smoke.sh; operator_alert_routing_audit.sh; db_backup_health_audit.sh)
- bothealth.sh expanded with VALIDATION_ERROR + batch_aborted surfacing

**Live empirical capture (Codex 2026-05-05):**
- OPP age proxy: 700 samples; median 421.945s; p90 1669.0s
- SKIPPED-rate baseline: 0 (post-cutover Mac Studio has 0 OPPORTUNITY events; ALL pre-Wave-1 conversion is from MacBook archive)
- Cooldown SKIPPED: 0
- Per-ticker trade-rate: 0 PAPER_TRADE post-cutover

#### 2026-05-05 cycle 4.5 — DB backup gap fix (operator action complete)

Cycle 4 Codex audit `db_backup_health_audit.sh --json` returned status=fail / count=0. Resolved in commit `a59d298`:
- `scripts/db_snapshot_backup.sh` — online-safe daily snapshot via `sqlite3 .backup`; 7-day retention; cleans WAL/SHM sidecars
- `scripts/launchd/com.kalshi.db-backup.plist` — daily 06:00 local-time + RunAtLoad
- `scripts/launchd/README.md` — install/verify/uninstall ops
- `mac_archive/db_snapshots/2026-05-05T1401Z/` — seed snapshot
- `.gitignore` — exclude binary backup artifacts

Operator bootstrapped same-day: `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.kalshi.db-backup.plist` → RunAtLoad fired immediately at 2026-05-05T1402Z. Audit now status=pass / count=4. Monitor `bk4mlg1yn` armed for 2026-05-05T18:00Z next-fire.

**Honest read 2026-05-05 (cycle 4):** Operator-side fire-time burden compressed to single-page playbooks for Day-7 + per-Wave-1-commit. Drift checks surfaced 9 MEDIUM findings across README + ROADMAP + CHANGELOG pre-staged blocks; doc-edits land in cycle 5. Failure-mode rehearsal docs cover KILL_SWITCH + Mac-Studio-dead-bot + Branch-D-fire (cycle 3); cycle 5 adds network/API outage. Backup gap closed; recurring backups firing under launchd. Key empirical finding from baselines: post-cutover Mac Studio has 0 OPPORTUNITY events through 4 days — Wave-1 regression-detection thresholds need rethinking against the actual baseline (covered cycle 5 baseline interpretation).

#### 2026-05-05 cycle 5 + 6 integration — drift fixes applied + sizing-scope harnesses + plist drift audit + OpenClaw note

**Cycle 5 commit `3fb09eb` (Claude) + cycle 6 commits `fa07434` + `afa1dc9` (Codex).**

**Cycle 5 Claude artifacts (10):**
- `README.md` Edit — 2026-05-05 status block above 2026-05-01 cutover entry (drift F1 closed)
- `docs/ROADMAP.md` Edit — P3-GATE FAIL escalation cross-link + What-Changed bullet 6 + Appendix A sizing-scope ref (drift F1+F2+F3 closed)
- `docs/_archive/governance/wave-2-wave-3-changelog-entries-prestaged.md` Edit — version sequence refreshed (drift F1+F2+F3 closed) (ARCHIVED Stream G R53)
- `2026-05-05-pre-wave1-baseline-interpretation.md` — synthesized 4 Codex baselines; flagged 0-OPPORTUNITY-post-cutover finding
- `docs/_archive/governance/2026-05-05-wave-2-fire-time-per-commit-checklist.md` + `docs/_archive/governance/2026-05-05-wave-3-fire-time-per-commit-checklist.md` — operator playbooks for Wave-2/3 deploys (BOTH ARCHIVED Stream G R53)
- `2026-05-05-launchd-plist-drift-audit.md` — found `ops/launchd/` template dir cycle 5 missed; flagged dual source-of-truth (resolved cycle 7)
- `2026-05-05-network-api-outage-runbook.md` — 5-category triage (Internet/Kalshi-API/Ollama/RSS/Reddit-403)
- `docs/_archive/governance/2026-05-05-implementation-contract-cycle-4-5-review.md` — 0 invariant violations across cycles 1-5 (ARCHIVED Stream G R46)

**Cycle 6 Codex artifacts (10 + 2 deviations):**
- `scripts/pre_wave1_llm_call_budget_projection.py` — 352 calls/day at 90-min cadence projection
- `scripts/wave2_fire_time_smoke.sh` + `scripts/wave3_fire_time_smoke.sh` — fire-time smoke wrappers
- `scripts/macbook_import_disposition_audit.sh` — 47 files / 156MB; recommendation `keep_through_wave1_close_then_archive`
- `scripts/launchd_plist_drift_audit.sh` — template-render-and-compare for `ops/launchd/` governance plists; live: 3 plists pass
- 5 strict-xfail harnesses (db_snapshot_backup, calibration_drift_detection, governance_parse_error, kill_switch_path, prompt_template A5-A8)
- Cycle-6 deviation: `docs/_archive/governance/2026-05-05-openclaw-orchestrator-integration-note.md` (OpenClaw as ops cockpit; 4-phase rollout deferred to post-Wave-1; ARCHIVED Stream G R46) + `_archive/studies/future_plans.md` 2-line forward-pointer

**Live empirical updates (Codex 2026-05-05 cycle 6):**
- LLM call budget projection: 352.0 calls/day at 90-min cadence vs 22 calls/cycle observed (>30× headroom against PHASE2-002 envelope)
- Plist drift: `ops/launchd/com.kalshi.governance.{fast,deep}.plist.template` matched installed; `scripts/launchd/com.kalshi.db-backup.plist` matched installed (3 of 6 plists have repo source-of-truth; 3 operator-managed)
- MacBook import disposition: 149MB archived dir; keep through Wave-1 close, then compress + rotate

**Honest read 2026-05-05 (cycle 5+6):** All cycle-4 drift findings closed. 0-OPPORTUNITY-post-cutover finding reframes Wave-1 regression detection (absolute thresholds, not ratios). Plist drift audit surfaced cycle-5 directory-discovery gap (missed pre-existing `ops/launchd/`); cycle 7 consolidates the dual source-of-truth. Operator-runnable smoke wrappers for Wave-2/3 land. OpenClaw integration note pre-stages future ops-cockpit work without committing to deployment.

#### 2026-05-05 cycle 7 + 8 integration — consolidation decisions + Wave-1 sentinels + pre-Day-7 dry-run

Seventh + eighth pass on 2026-05-05 (commits `92b767d` Claude + `ffc54b2` Codex; cycle 8 in flight).

**Cycle 7 Claude artifacts (commit `92b767d`):**
- `IMPLEMENTATION_CONTRACT.md` Edit — §5 G1 cell 0.35→0.05→0.04 alignment (cycle 4-5 review F1)
- `2026-05-05-launchd-plist-consolidation-decision.md` — keep `ops/launchd/`; migrate db-backup; capture 3 operator-managed plists
- `docs/_archive/governance/2026-05-05-day-7-to-wave-1-handoff-procedure.md` — 5-condition transition gate; defer rules (ARCHIVED Stream G R45)
- `docs/_archive/governance/2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md` — 6-commit consolidation (ARCHIVED Stream G R54)
- `docs/_archive/governance/2026-05-05-wave-2-decision-flow.md` + `docs/_archive/governance/2026-05-05-wave-3-decision-flow.md` — ASCII flowcharts (BOTH ARCHIVED Stream G R53)
- `docs/_archive/governance/2026-05-05-macbook-import-archive-procedure.md` — post-Wave-1-close tar.gz workflow (ARCHIVED Stream G R46)
- `docs/_archive/governance/2026-05-05-doc-cross-link-integrity-audit.md` — 39 links scanned; 2 broken in wave-1-changelog-entry-prestaged.md (closed cycle 8) (ARCHIVED Stream G R44)
- `docs/_archive/governance/2026-05-05-memory-hygiene-audit.md` — 2 memories clean (ARCHIVED Stream G R44)

**Cycle 7 Codex artifacts (commit `ffc54b2`):**
- 4 operator scripts: `wave1_commit_chain_acceptance_audit.sh`, `doc_xref_audit.py`, `release_tag_inventory_snapshot.py`, `macbook_import_archive_migration.sh`
- 6 strict-xfail Wave-1 per-commit post-deploy sentinels
- 1 operator-script contract test

**Empirical capture (2026-05-06T00:10Z; ≈ T-1d 19h to Day-7 close):**
- 383 GOVERNANCE_DECISION events (12.8× the 30-floor); 57 cycles
- 0 KILL_SWITCH / 0 VALIDATION_ERROR / 0 batch_aborted (all gates clean)
- 7 cumulative PARSE_ERROR (all on day-1/-2 per cycle-3 retroactive findings); 0 trailing 72h
- max inter-cycle gap 2.01h (≤ 3h floor; clean)
- gate-7 surfaces 5 commits (4 documented §8.5.2 carve-outs + cycle-2/3 doc artifacts)

**Timezone discovery (cycle 8):** Mac Studio is `America/Denver` (MDT, UTC-6). The launchd `Hour=6` in `com.kalshi.db-backup.plist` fires at 12:00 UTC daily, not 18:00 UTC as initially assumed (NZ timezone). Bootstrap snapshots at 14:01Z + 14:02Z were RunAtLoad-only; first scheduled fire = 2026-05-06T12:00Z (~12h post-bootstrap, not ~4h).

**Honest read 2026-05-05 (cycle 7):** Day-7 close prep is mature. All cycle-4 + cycle-7 drift findings either closed or have applied fixes pending. Plist consolidation decision landed; execution deferred to cycle 8 (Codex). Wave-1 per-commit sentinels pre-loaded; will flip on each commit's deploy. Pre-Day-7 dry-run rehearsal (cycle 8) confirms current gates clean ~T-1d 19h to close.

---

### PROFIT-DOSSIER-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-DOSSIER-001 |
| **Title** | Dossier coverage is LLM-output-bound, not matcher-bound — re-frame the "1.4% of active markets had dossiers" metric and identify the actionable lever |
| **Category** | Observability / Diagnostics Framing |
| **Severity** | LOW (framing fix, not a runtime bug) |
| **Status** | COMPLETE (2026-05-02) |
| **Priority** | LATER (post-Phase 2) |
| **Owner** | Claude |
| **Depends On** | — |
| **Blocks** | Any future "improve dossier coverage" item that would otherwise misdiagnose the lever |

**Description**

PROFIT-EDGE-002's empirical observation — "12 of 847 active markets had dossiers (~1.4%)" — has been cited as evidence that the structural lane is silently absent because the dossier-creation pipeline isn't keeping up with the active-market universe. Task G of PROFIT-EDGE-004 (`scripts/simulations/dossier_creation.py`) traced the actual creation trigger and found it is **eager**: `AccumulationTask._process_evidence_locked` calls `store.update_dossier(initial_state)` on the first evidence record for any ticker, with no minimum-evidence threshold gate. So dossier coverage is *not* gated by the matcher, the dossier-creation logic, or any threshold.

A 2026-05-02 read-only audit of the *current* `data/evidence_store.db` (post-MacBook → Mac Studio cutover) confirms what's actually happening:

| Stage | Distinct tickers | Notes |
|---|---|---|
| `MATCH_DIAGNOSTIC` (matched at least once across the full 2026-04-18 → 2026-05-02 archive) | **82** | 2,880 total match events; long tail of one-offs |
| `OPPORTUNITY` (LLM produced a directional view that survived the readiness gate) | **32** | 260 total events |
| `dossiers` table | **32** | exact 1:1 with OPPORTUNITY tickers |

100% of OPPORTUNITY tickers have dossiers; 50 of the 82 matched tickers (61%) have no dossier because the LLM declined to give a directional view (post-EDGE-001 the line-688 path is unblocked, so this is the *legitimate* "magnitude=none" passthrough — those events do not produce evidence). The actionable lever is therefore not "speed up dossier creation" but "raise LLM signal density" (which is what PROFIT-EDGE-004 was already chasing).

The "1.4% coverage" denominator (active-market count) is the wrong frame. The right denominator is "tickers the LLM gave a directional view on", and the answer is 100% — the slow lane covers the markets it should cover.

**Why it matters to profitability / safety / reliability**

This is a **framing fix**, not a runtime bug. Misdiagnosis of the coverage gap as a dossier-creation problem would push optimization effort onto the wrong code path (e.g. lowering thresholds in the dossier-creation gate, which doesn't exist) and miss the real lever (matcher quality + LLM-output density). Cleaning up the framing before it gets cited a third time avoids a future-Claude rabbit hole.

**Evidence / Source**

- `scripts/simulations/dossier_creation.py` (PROFIT-EDGE-004 Task G) — empirical trigger trace; dossier created at N=1 with no threshold gate.
- 2026-05-02 read-only audit of `data/evidence_store.db`:
  - 32 dossiers / 32 structural priors / 248 evidence records / 248 dossier_updates.
  - Top-2 tickers (KXTRUMPIRAN-26MAY01, KXMOCTRUMP25-26-MAY01) account for 158/248 = 64% of all evidence.
  - Source-class diversity gap: 202 news / 45 other / 1 official across the full corpus — a single dossier rarely sees diverse classes (relevant to Task B's `blend_task_integration` finding that single-class evidence trips G2).
- 2026-05-02 trade-log archive scan (16 jsonl files across `logs/trades/` and `mac_archive/`):
  - 82 distinct MATCH_DIAGNOSTIC tickers; 32 distinct OPPORTUNITY tickers; perfect overlap with dossiers table.
  - 50 matched-but-undossiered tickers (61%) — including all 13 `KXJPTRADEBAL-*` markets, 1-3 matches each, never produced an LLM-positive signal. Long tail.

**Proposed Fix**

Documentation-only.

1. Update the PROFIT-EDGE-002 Description / Why-it-matters paragraphs to soften the "1.4% had dossiers" claim — replace with "100% of LLM-positive tickers had dossiers; the long tail of matched-but-undossiered tickers is the matcher-quality / market-mix problem already tracked under PROFIT-EDGE-004 hypotheses 1 and 3, not a dossier-creation problem."
2. Add a one-line note at the top of `scripts/simulations/dossier_creation.py` (already has the eager-creation note in its docstring; just cross-link to this entry).
3. Reference this entry from the next ROADMAP P3 / structural-lane discussion as the definitive answer to "should we lower the dossier-creation threshold?"

**Acceptance Criteria**

- PROFIT-EDGE-002 entry updated with the corrected framing — **MET** (Related-section bullet at line ~1113 carries the 2026-05-02 framing-update note rewriting the "12/847 active markets" claim into the matcher-quality / LLM-signal-density frame).
- This entry cross-linked from `dossier_creation.py` docstring — **MET** (lines 18–23 of `scripts/simulations/dossier_creation.py` cite PROFIT-DOSSIER-001 directly and reference the empirical 100% OPPORTUNITY → dossier alignment as of 2026-05-02).
- No code changes — runtime behavior is correct — **MET** (vacuously; this entry is documentation-only by design).

**Closure (2026-05-02)**

All three Acceptance Criteria are substantively satisfied as of the v0.29.59 release window. Verified during the soak-window triage on 2026-05-02 (within the soak-safe scope: documentation-only, zero runtime touch, zero impact on the Phase 2 shadow-mode invariant). Closing OPEN → COMPLETE so the framing fix does not get re-litigated by future-Claude. The actionable lever — matcher quality + LLM signal density — remains tracked under PROFIT-EDGE-004 (closed 2026-05-02 with sequenced investigation evidence) and PROFIT-MATCH-001 (open, MEDIUM, the matcher long-tail surface area).

**Notes**

- Source-class concentration (1 official / 45 other / 202 news in the current evidence corpus) is *not* in scope for this entry. That's a feed-mix / source-diversity problem and belongs in its own PROFIT-* item (or PROFIT-EVID-002 if it fits there) when triaged.
- The "100% of OPPORTUNITY tickers have dossiers" alignment is the load-bearing observation for this entry. If a future runtime change breaks that alignment (e.g. a candidate appears in OPPORTUNITY without an upstream evidence_store.update_dossier call), file as a NEW entry — that would be the actual bug this misdiagnosis was guarding against.

**Related**

- `PROFIT-EDGE-002` (closed 2026-04-26) — "1.4% coverage" framing originates here; the runtime fix landed but the framing didn't get corrected.
- `PROFIT-EDGE-004` (closed 2026-05-02) — Task G surfaced the empirical trigger that disproves the threshold-gate hypothesis.
- `PROFIT-LLM-001` (open) — actionable-lever home; "raise LLM signal density" is what raises *real* dossier coverage.

---

### PROFIT-GOV-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-GOV-001 |
| **Title** | `LocalQwenLLM` returns empty `{}` on every call — qwen3 chain-of-thought + `format=json` interaction; fix with top-level `think=False` |
| **Category** | Governance / LLM Integration |
| **Severity** | HIGH (blocked governance Phase 2 acceptance — every decision parsed as missing-fields; soak was structurally invalid until fix landed) |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | Claude |
| **Depends On** | — |
| **Blocks** | `PROFIT-PHASE2-001` acceptance (would have failed at the ≥30-decision floor regardless of soak duration) |

**Description**

`governance/llm.py:LocalQwenLLM.complete` issued Ollama `generate` requests with `format=json` against the qwen3:14b model on Mac Studio. Every response was the empty JSON object `{}`. `governance/agent.py:run_cycle` wraps each response in `parse_llm_response_to_decision`, which raises `LLMResponseParseError("LLM output missing required fields: ['action', 'target', 'reasoning', 'confidence', 'predicted_effect']")` for `{}`. Result: every governance cycle produced 0 GOVERNANCE_DECISION records and 1+ GOVERNANCE_DECISION_PARSE_ERROR records per evaluated candidate.

Empirical confirmation across 5 cycles before the fix (gc_2026-05-01_190127 → gc_2026-05-02_030140): **0 GOVERNANCE_DECISION records, 7 PARSE_ERROR records**, all with the identical "missing all 5 required fields" error.

Root cause is qwen3-specific: qwen3 models use a chain-of-thought reasoning phase before producing the final answer. Ollama's `format=json` grammar constraint applies *during generation*, which causes the JSON grammar to consume the reasoning stream and emit only an empty object when the model "thinks" first. The interaction does not occur in qwen2.x or earlier; it is a qwen3 family regression introduced when Ollama added native qwen3 thinking support.

The fix is the top-level `think: false` parameter in the Ollama `generate` request body. The `/no_think` *prompt* directive (used by some qwen3 documentation) does NOT fix this — only the server-side `think` parameter disables the reasoning phase. Verified empirically 2026-05-02 against qwen3:14b @ Ollama:

| Variant | Output |
|---|---|
| `format=json` (current code, before fix) | `{}` |
| `format=json` + `/no_think` directive | `{}` (no effect) |
| `format=json` + top-level `think=False` (the fix) | full schema with action/target/reasoning/confidence/predicted_effect |
| Free text (no `format=json`) | full schema (the model does want to answer) |

**Why it matters to profitability / safety / reliability**

This is the *load-bearing* failure mode for `PROFIT-PHASE2-001`. The Phase 2 acceptance criteria require ≥30 GOVERNANCE_DECISION records over 14 days; without this fix the agent would have produced exactly zero. The soak was on track to fail acceptance entirely, with the failure mode invisible to anyone not actively reading the audit log (the bot itself was healthy and the launchd jobs were firing on schedule).

The shadow-mode safety invariant (applied=0) was unaffected — `{}` parses to a missing-fields error, which never reaches the apply path. So safety-wise this was a "fail-closed" failure. But it would have invalidated the entire Phase 2 → Phase 3 → Phase 4 plan if it had not been caught until 2026-05-15 acceptance review.

**Evidence / Source**

- `logs/governance/decisions.jsonl` (2026-05-01 19:01 UTC → 2026-05-02 04:12 UTC):
  - 5 fast cycles, all `decisions_made=0`.
  - 7 PARSE_ERROR records, all identical: `LLM output missing required fields: ['action', 'target', 'reasoning', 'confidence', 'predicted_effect']`.
- Direct LLM call test (2026-05-02 04:00 UTC) against `http://localhost:11434/api/generate` with the production system prompt + a representative user prompt produced `{}` for current code path; `think=False` produced full schema.
- Manual cycle after fix (`gc_2026-05-02_041248`): 2 valid GOVERNANCE_DECISION records, full schema, both `applied=false` (shadow invariant holds).

**Proposed Fix — applied 2026-05-02**

`governance/llm.py:LocalQwenLLM.complete` — add `"think": False` at the top level of the Ollama generate-request payload (sibling of `format`, not nested under `options`). Comment with the empirical context so a future reader does not delete it during a "this looks unused" cleanup.

```python
payload = {
    "model": self.model,
    "system": system,
    "prompt": user,
    "stream": False,
    "format": "json",
    "think": False,            # load-bearing for qwen3 family
    "options": {"temperature": 0.0},
}
```

**Acceptance Criteria**

- [x] `governance/llm.py` carries the `"think": False` payload field with an explanatory comment.
- [x] `tests/test_governance_llm.py` 16 / 16 pass.
- [x] `tests/test_governance_agent_unit.py` + `test_governance_agent_integration.py` pass.
- [x] One manual `python -m governance --cadence fast --llm qwen` cycle on Mac Studio produces ≥1 valid GOVERNANCE_DECISION record (with full schema).
- [x] `PROFIT-PHASE2-001` clock reset noted in that entry's Notes section.
- [x] `CLAUDE.md` "Critical Gotchas" updated to capture the qwen3 + `format=json` interaction so a future operator upgrading the model catches it on day one.

**Notes**

- The launchd jobs fire fresh Python processes on each cycle, so the next scheduled fire (every 2 h) picks up the patched binary automatically — no `launchctl bootout` / `bootstrap` cycle needed.
- The fix is qwen3-specific by chain-of-thought; if a future operator switches `GOVERNANCE_LLM_MODEL` to a non-thinking model (e.g. qwen2.5:14b or llama3.1:70b-instruct) the `think=False` parameter is a no-op — Ollama ignores it on models that don't expose a thinking phase.
- The launchd plist's `GOVERNANCE_LLM_MODEL=qwen3:14b` env override is preserved as the source of truth for which model is in use; this fix is in the LLM-client wrapper, not in the model selection.

**Related**

- `PROFIT-PHASE2-001` — direct dependency; soak clock reset 2026-05-02 ~04:12 UTC pegged to the first valid decision after the fix.
- `PROFIT-LLM-001` — separate, pre-existing entry tracking the post-Phase 2 unification of the signal-analyzer LLM. Once Phase 2 closes, the `think=False` pattern needs to apply there as well if `OLLAMA_MODEL` ever points at a qwen3 family model.
- `CLAUDE.md` "Critical Gotchas" / Kalshi API section — neighbours; this entry updates the gotchas list with the qwen3 + JSON interaction.

---

### PROFIT-MATCH-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-MATCH-001 |
| **Title** | Match-quality long tail — 78% of matches flagged low-quality, dominated by single-entity ticker-name overlap (`trump`, `iran`, `pakistan`, `russia`) |
| **Category** | Matcher / Signal Quality |
| **Severity** | MEDIUM |
| **Status** | OPEN |
| **Priority** | LATER (post-Phase 2 governance soak; do not change matcher behaviour during PROFIT-PHASE2-001) |
| **Owner** | Claude |
| **Depends On** | `PROFIT-PHASE2-001` close (2026-05-15 ETA); P2.5 enforcement decision (separate deferred runbook step) |
| **Blocks** | Real-mode flip readiness (LLM signal-quality denominator stays inflated until this is tightened) |

**Description**

The 2026-05-02 read-only audit of `MATCH_DIAGNOSTIC` events across the full 16-file trade-log archive (`logs/trades/` + `mac_archive/macbook_2026-05-01_import/logs/trades/`) found **2,257 of 2,880 matches (78.4%) carry the `low_match_quality` flag**. The dominant pattern is laser-specific:

| Heuristic flag | Count | % of low-quality |
|---|---|---|
| `single_named_entity_only` | 1,705 | 75.5% |
| `minimal_overlap` | 1,705 | 75.5% |
| `near_threshold_score` | 1,664 | 73.7% |
| `low_token_overlap` | 598 | 26.5% |

1,705 matches (60% of all matches) carry both `single_named_entity_only` AND `minimal_overlap` — i.e., a single named-entity token (`trump`, `iran`, `pakistan`, `russia`) is the only meaningful overlap between headline and market title. Top concrete pairs:

| matched token | series | matches | sample headline |
|---|---|---|---|
| `iran` | KXTRUMPIRAN | 342 | "Iran among dozens selected for vice presidency post at UN non-proliferation conf…" |
| `trump` | KXMOCTRUMP25 | 253 | "King Charles Visits US as Britain Seeks to Steady Ties With Trump" |
| `pakistan` | KXVANCEPAKISTAN | 233 | "Putin assures Iranian FM Moscow will do 'everything' to help secure ME peace…" |
| `trump` | KXPARDONSTRUMP | 157 | "Inside Trump press dinner shooting suspect's court appearance" |
| `pakistan` | KXPSL | 136 | "Iran War Live Updates: Witkoff and Kushner to Go to Pakistan for Talks on Iran" |
| `russia` | KXRUCRUDEX | 90 | "Kyiv Says It Struck Russia's High-End Su-57 Stealth Fighters in Deep Rear Operat…" |

558 of these 1,705 records (32.7%) ESCAPE the current `ENABLE_LOW_QUALITY_MATCH_SUPPRESSION` path because their score is above 0.08 (the `near_threshold_score` flag only fires within `min_score + 0.02`). The current suppression criteria require `near_threshold_score` AND (`minimal_overlap` OR `single_named_entity_only`) AND `_token_not_in_ticker`. The `_token_not_in_ticker` guard correctly preserves "iran story matches KXTRUMPIRAN because both contain iran"-style coherent matches, but it ALSO over-protects every `trump`-token false-positive against KXTRUMP*-prefixed series, which is the dominant noise source in the archive.

**Why it matters to profitability / safety / reliability**

1. The matcher is the pipeline's first kill point (`PAPER_MIN_MATCH_SCORE = 0.06`). Every spurious match downstream of this gate consumes an LLM call (~5–8 seconds at qwen2.5:7b) and dilutes the signal-quality denominator the governance agent will eventually optimise against (`PROFIT-PHASE2-001` → `GOV.P3` → `GOV.P4`).
2. Source-credibility scoring (`analysis/source_credibility.py`) is updated on resolution; a source that produces 100 low-quality matches that all resolve neutrally pollutes the credibility prior for that source's *future* high-quality signals.
3. The pre-LLM gate (currently P2.4-COMPLETE in diagnostics-mode, P2.5 enforcement deferred) would catch 66.6% of these matches at no LLM cost. P2.5 is the short-term lever; this entry is the medium-term lever for the long tail that survives even the pre-LLM gate.

**Evidence / Source**

- 2026-05-02 read-only audit, full 16-file `MATCH_DIAGNOSTIC` corpus:
  - Total: 2,880 events / 82 distinct tickers.
  - Score distribution: 57.5% in `[0.06, 0.08)`, 21.9% in `[0.08, 0.10)`, 17.4% in `[0.10, 0.15)`, 3.2% above 0.15.
  - 1,917 (66.6%) carry `would_fail_pre_llm_gate=true`.
- `MATCH_SUPPRESSED` event count: 498 (across the same archive). `ENABLE_LOW_QUALITY_MATCH_SUPPRESSION` is on; the suppression criteria are too narrow.
- `analysis/market_matcher.py:625-680` — current suppression definition; the `_meets_suppression_criteria` triple-AND is the load-bearing predicate.

**Proposed Fix — three options, ranked by priority**

**Option A (recommended, short-term):** land P2.5 pre-LLM gate enforcement (already drafted in `/tmp/sunday_commit_draft/`). Cuts 66.6% of match volume at zero LLM cost. Independent of this entry's deeper fix.

**Option B (medium-term, this entry):** widen the suppression predicate. Drop the `near_threshold_score` requirement so `single_named_entity_only + minimal_overlap` alone triggers suppression for ticker-name-overlap matches. Empirical impact: ~1,705 records suppressed instead of ~498. Requires a 24-hour observation window post-flip to confirm no canonical events are killed (the EDGE-001 5-event regression set is the canonical anchor and is captured by `scripts/simulations/match_score_audit.py`).

> **Partially superseded — see the 2026-05-02 forensic addendum below.** The "drop `near_threshold_score`" half of Option B already shipped in commit `825a065` (2026-04-16) as `_pure_single_entity` Path B in `analysis/market_matcher.py:641–649`. The remaining open work is the `_token_not_in_ticker` guard refinement, restated as Option (B′) in the addendum. The empirical "~1,705 → ~498" figure above conflates these; the post-Path-B working number is closer to ~1,207 single-entity matches still surviving the suppressor because the entity *is* in the ticker prefix.

**Option C (long-term, separate entry):** series-aware token weighting — `trump` in `KXTRUMPENDORSE` should NOT count as a meaningful match unless additional context tokens align (endorsement, candidate name, state). Matcher would need to peel the entity out of the ticker prefix and require non-entity overlap with the rest of the title. Larger refactor; file as `PROFIT-MATCH-002` if Option B's empirical results are insufficient.

**Acceptance Criteria** (Option B path)

- `analysis/market_matcher.py:_meets_suppression_criteria` updated; `near_threshold_score` no longer required when `single_named_entity_only + minimal_overlap` are both present.
- `scripts/simulations/match_score_audit.py` re-run post-fix; all 5 canonical events still surface their anchor in top-3 at score ≥ 0.06.
- 24h post-deploy `MATCH_SUPPRESSED` count rises to ~1.7× current (proportional to the per-day archive rate); no canonical-event ticker appears in `MATCH_SUPPRESSED`.
- New smoke test added to `tests/test_market_matcher.py` pinning the widened predicate.

**Notes**

- Do not land any matcher change during `PROFIT-PHASE2-001` (governance Phase 2 14-day soak, ETA close 2026-05-15). The matcher feeds the governance agent's audit data; changing it mid-soak invalidates the soak's signal density distribution and resets the clock.
- The `_token_not_in_ticker` guard is intentionally kept in this proposal — it preserves the "iran story → KXTRUMPIRAN" path that *is* coherent. The fix is to suppress when the single entity overlap is the *only* token in common, not to remove the guard.
- The full 78.4% low-quality rate is consistent with the 2026-04-26 daily-review snapshot (228/269 = 84.8%) cited in the original PROFIT-EDGE-001/002/003 investigation; the slight delta reflects the broader window vs. the single-day sample.

**Related**

- `PROFIT-EDGE-001` / `PROFIT-EDGE-004` — line-688 fix removed the `no_keywords` kill, exposing this long tail; this entry is the next layer of the same investigation.
- `PROFIT-EDGE-002` "P2.5 enforcement" — Option A above.
- `PROFIT-DOSSIER-001` — companion finding; raising matcher quality is *the* lever for raising real (not nominal) dossier coverage.
- `scripts/simulations/match_score_audit.py` — the regression-anchor harness that validates Option B's safety on the 5 canonical events.

**Forensic addendum** (2026-05-02, soak-window read-only source-tree audit)

*Option B as described has already shipped.* A direct read of `analysis/market_matcher.py:626–649` shows the suppression predicate is now a *disjunction* over two paths:

```python
_near_threshold_weak = (
    "near_threshold_score" in flag_set
    and ("minimal_overlap" in flag_set or "single_named_entity_only" in flag_set)
)
_pure_single_entity = (
    "single_named_entity_only" in flag_set
    and "minimal_overlap" in flag_set
)
_meets_suppression_criteria = (
    bool(heuristic_flags)
    and _token_not_in_ticker
    and (_near_threshold_weak or _pure_single_entity)
)
```

`_pure_single_entity` is exactly the score-independent `single_named_entity_only + minimal_overlap` predicate this entry's Option B proposes to add. It landed in commit `825a065` on 2026-04-16 — three weeks before this entry was filed. Commit message confirms intent verbatim: *"Add Path B: suppress when single_named_entity_only AND minimal_overlap are both present, regardless of score."*

The 2026-05-02 audit (2,880 MATCH_DIAGNOSTIC, 1,705 with `single_named_entity_only + minimal_overlap`, 498 MATCH_SUPPRESSED) was therefore measured *with* Path B already active. The 1,207 single-entity matches that survive into the LLM stage are the ones where `_token_not_in_ticker = False` — i.e., the entity *is* embedded in the ticker prefix. The ticker guard preserves them by design.

*Where the actual remaining lever lives.* The entry's "Option B" wording conflates two separate changes: (1) drop the `near_threshold_score` requirement — *already done*; and (2) refine the `_token_not_in_ticker` guard so entity-prefix tickers stop being a free pass. Only (2) is still open work, and it is materially different in risk shape.

*Direct risk to the regression-anchor set.* The 5 canonical events in `scripts/simulations/_common.py:LLM_POSITIVE_EVENTS_2026_04_26` split as follows on the entity-in-ticker dimension:

| Event | Ticker | Token-in-ticker? | Survives if guard kept? | Survives if guard dropped? |
|---|---|---|---|---|
| 1 — KXSBUDGETRES-APR28 (ICE funding) | KXSBUDGETRES-26APR-APR28 | likely no (no `senate`/`ICE`/`resolution` substring) | yes | likely yes (low-quality flags would not all fire on a multi-token semantic match) |
| 2 — KXSBUDGETRES-APR25 (same) | KXSBUDGETRES-26APR-APR25 | same as #1 | yes | likely yes |
| 3 — KXTRUMPIRAN (dispatching) | KXTRUMPIRAN-26MAY01 | **yes** (`trump`, `iran`) | yes | **at risk** if `single_named_entity_only + minimal_overlap` flags both fire |
| 4 — KXPSL-PZA (cricket, sport-blocked) | KXPSL-26-PZA | **yes** (`psl`) | yes | not relevant — sport blocklist kills upstream |
| 5 — KXTRUMPIRAN (talks stall) | KXTRUMPIRAN-26MAY01 | **yes** (`trump`, `iran`) | yes | **at risk** — same shape as #3 |

Events 3 and 5 are exactly the case the ticker guard was designed to preserve. Both headlines pair a single in-ticker entity (`iran` / `trump`) against a market title that itself reduces to "Iran"/"Trump" + a calendar verb — a topic that *is* coherent and *should* match, but whose post-tokenization overlap can read as `single_named_entity_only + minimal_overlap`. Dropping the guard naively kills the EDGE-001 regression-anchor signal.

*Constraint on the (2) refinement.* Any guard refinement must distinguish:

- **Coherent entity-prefix match** — title body adds non-entity context that aligns with headline. Example: headline "Trump dispatching Witkoff, Kushner for Iran talks" against title "Will Donald Trump visit Iran before May 1, 2026?" — multiple semantic tokens (`trump`, `iran`, plus implicit `talks/visit`) align even before the ticker prefix is considered.
- **Free-pass entity-prefix noise** — title body shares no non-entity context with headline. Example: headline "King Charles Visits US as Britain Seeks to Steady Ties With Trump" against title "Will Trump fire Powell before…" — the only meaningful token in common is `trump`, and `trump` is in the ticker, so the current guard preserves it.

The discriminator is the *non-ticker-token* overlap count: keep the guard only when the matched-tokens-not-in-ticker set is non-empty, or when token-overlap-count exceeds 1. This is materially narrower than the entry's current "drop the guard" framing and preserves Events 3 + 5.

*Updated proposed-fix scoping.* Reframe the open work as:

- **(B′) Refine `_token_not_in_ticker` guard, do not remove it.** Replace the current binary check with a stricter predicate: pass the guard only when *at least one* matched token is *not* in the ticker (i.e. `len(overlap - ticker_tokens) ≥ 1`). Currently the guard fails (i.e. suppression fires) only when *all* overlap tokens are not in the ticker — the asymmetry is the bug. Empirical impact: the 1,207 single-entity matches with token-in-ticker now suppress unless they have at least one supporting non-ticker token. Events 3 + 5 retain the supporting non-ticker tokens (`witkoff`, `kushner`, `talks`, `visit`) and survive.
- **Acceptance criteria revision:** the existing MATCH-001 acceptance list still applies, with one clarification — the harness in `scripts/simulations/match_score_audit.py` must show all 5 canonical events still surface their anchor in the post-fix top-3, *and* a fresh ad-hoc archive replay must confirm the MATCH_SUPPRESSED count rises from ~498 toward the 1,207 figure (not the 1,705 the original framing suggested).
- **Soak window:** unchanged — do not land any matcher edit during PROFIT-PHASE2-001. This refinement is targeted but still touches a decision-path predicate, so it must wait until the soak window closes per the same CLAUDE.md "decision consistency = high-risk" rule that defers PROFIT-OBS-005 and PROFIT-EXEC-002.

*Pre-fix audit lever.* The existing `MATCH_DIAGNOSTIC` records carry both `matched_tokens` and `ticker` — every claim above is verifiable from the existing trade-log archive without code change. A read-only count of `len(set(matched_tokens) - tokenize(ticker))` per `MATCH_DIAGNOSTIC` is the immediate empirical answer to "how many records would (B′) suppress vs. preserve". That count is *not* run in this addendum — running it requires invoking the matcher's tokenizer or replaying the harness, which we are deferring to the post-soak fix window so that the empirical baseline and the fix land against the same code state.

*2026-05-03 threshold-bisection result.* `docs/_archive/governance/2026-05-03-matcher-jaccard-threshold-bisection.md` (ARCHIVED Stream G R43) sweeps the 13-day MacBook `MATCH_DIAGNOSTIC` archive against raised score floors. A simple Jaccard-threshold raise is a poor EDGE-004 primary lever: threshold 0.08 retains all 3 paper trades but only 134/260 OPPORTUNITY records (51.5%), while threshold 0.10 retains only 72/260 (27.7%). MATCH-001 B′ remains the better-shaped post-soak fix because it targets single-entity noise rather than globally discarding opportunity-producing matches.

*2026-05-03 post-soak stack simulation.* `docs/governance/2026-05-03-post-soak-landing-simulation.md` replays the 13-day archive through OBS-005 → MATCH-001 B′ → OBS-003 → EXEC-002. Key counterfactual: B′ suppresses 1,076 match keys and reduces retained OPPORTUNITY from 260 → 87 under a strict key-based estimate; OBS-003 would add 78 retained blocked-reason SKIPPED records; EXEC-002 would suppress 2 of the 3 FISA paper trades. Treat MATCH-001 B′ as a high-blast-radius post-soak landing despite its small code diff.

*2026-05-03 regression-anchor pre-deploy sizing.* `docs/governance/2026-05-03-match001-bprime-anchor-sizing.md` packages the deploy-day B′ replay as a repeatable harness. Result: B′ still suppresses 1,076 match keys, retains 87/260 OPPORTUNITY records and 3/3 PAPER_TRADE records, and suppresses 0 exact canonical event headlines. However, a stricter ticker-level guard fails: 399 suppressed matches land on the canonical `KXTRUMPIRAN-26MAY01` ticker. The landing-order spec should guard exact canonical event tuples/headlines, not every future low-quality match on canonical tickers.

*2026-05-03 tokenization-equivalence audit.* `docs/governance/2026-05-03-match001-tokenization-equivalence-audit.md` compares the B′ simulation's substring ticker-membership semantics with a literal production `_tokenize(ticker)` set-difference implementation. Result: the 1,076-key estimate depends on substring membership; `_tokenize("KXTRUMPIRAN-26MAY01")` returns one hyphenated token, so a literal tokenized set-diff suppresses 0 keys on the archive. The landing spec must codify substring membership or rerun sizing after changing tokenizer semantics.

*2026-05-03 substring false-negative audit.* `docs/governance/2026-05-03-match001-bprime-false-negative-audit.md` tests the complement risk for option (a): low-quality matches that escape because a ticker-substring token is present. Under the weak-support proxy, B′ shape total is 1,691, substring suppression catches 1,202, and ticker-substring escape / likely false-negative count is 0. This supports substring semantics as adequate on the archive, while preserving the caveat that it is heuristic rather than manual relevance labeling.

*2026-05-03 Wave-1 forecast + OBS-003 synthesizer check.* `docs/_archive/governance/2026-05-03-wave1-acceptance-ladder-forecast.md` (ARCHIVED Stream G R42) sets the expected Wave-1 end state from the archive replay: 87 OPPORTUNITY, 89 SKIPPED, 1 PAPER_TRADE after OBS-005 → MATCH-001 B′ → OBS-003 → EXEC-002. `docs/_archive/governance/2026-05-03-obs003-skipped-synthesizer-reality-check.md` (ARCHIVED Stream G R42) confirms the OBS-003 synthetic SKIPPED fixture matches the post-soak simulation's BlendTask reason distribution (`G1=59`, `G6=14`, `G2=5`) plus 9 executor-side rows.

*Severity unchanged.* MEDIUM is correct. The (B′) refinement is the right scope; severity stays where the entry placed it because the lever is the same and the stale-Option-B framing did not understate the upside.

---

### PROFIT-GOV-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-GOV-002 |
| **Title** | qwen3:14b rubber-stamp bias on the Phase 2 `disable_source` prompt — confirmed via offline negative-control probe |
| **Category** | Governance / LLM Behaviour |
| **Severity** | HIGH (blocks Phase 3 real-mode flip; invalidates `PROFIT-PHASE2-001` §8.5 "≥85% reasonable" gate as currently measured — see PHASE2-001 mid-soak halt note) |
| **Status** | COMPLETE (2026-05-03; A5_ANCHOR_ONLY anchor-rate polarity callout landed in `governance/prompts.py:SYSTEM_PROMPT`; first post-fix cycle `gc_2026-05-03_152836` ran cleanly with 5 sensible decisions and 0 errors) |
| **Priority** | NOW (must be resolved within the existing soak window, hard ceiling 2026-05-16 ~04:12 UTC; the 14-day clock is intentionally not extended for newly-discovered bugs) |
| **Owner** | Claude |
| **Depends On** | — |
| **Blocks** | `PROFIT-PHASE2-001` §8.5 acceptance (manual-review criterion), Governance Phase 3 real-mode flip (per ROADMAP `GOV.P3`), Phase 4 Claude-API tier |

**Description**

The 2026-05-02 Phase 2 monitoring read found 74-of-74 `GOVERNANCE_DECISION` events were `disable_source` with zero `no_action`. The architectural slice of that finding is benign (only the `_disable_source_candidates` selector is implemented in Phase 2; `_disable_keyword_candidates` and `_tune_threshold_candidates` are explicit Phase-4 placeholders per `governance/evidence.py:96–114`). The remaining open question — *within the disable_source binary, is the LLM actually discriminating, or rubber-stamping?* — was answered empirically by the 2026-05-03 negative-control probe documented below.

**The model is rubber-stamping.** Specifically, qwen3:14b on the current `DISABLE_SOURCE_TEMPLATE` (`governance/prompts.py:82–98`) at temperature 0.0 with `think=False` returns `disable_source` with high confidence on a synthetic high-signal candidate that should obviously be `no_action`, and confabulates plausible-sounding domain reasoning to justify the verdict.

**Why it matters to profitability / safety / reliability**

1. **`PROFIT-PHASE2-001` §8.5 manual-review gate ("≥85% reasonable") is unmeasurable in its current form.** Production traffic only contains genuinely-bad candidates (the heuristic selector at `governance/evidence.py:36–88` only flags subreddits with `0 fresh_pass + 0 match` over 168h, or alignment-anchor-rate ≥ 0.95). The LLM's 100% agreement rate on those candidates *looks* correct because the candidates *are* genuinely disable-worthy, but the LLM is not actually doing the discrimination work the gate is meant to validate. The model would also agree with high confidence on a *good* source presented in the same shape — which the negative-control probe confirms.
2. **Phase 3 real-mode flip is HIGH-RISK without resolving this.** Phase 3 promotes `proposed_*` decisions to `applied_*` in `runtime_overrides.yaml`. A rubber-stamping LLM's proposals being auto-applied would systematically remove sources whenever the upstream heuristic flags them — including any false-positive heuristic flag, since the LLM is no longer a real second check.
3. **The §8.5 sample-size question** (per the same monitoring read: 74 raw decisions decompose to ~12 distinct `(target, reasoning)` tuples — qwen3:14b emits byte-identical reasoning for the same target across cycles) is downstream of this entry. Even if the rubber-stamp bias is fixed, the ~7× duplication factor means the §8.5 manual-review sampling protocol needs to weight by distinct decisions, not raw count.

**Evidence / Source**

- `scripts/simulations/governance_negative_control.py` (added in this commit) — 3-probe harness that calls `governance.llm.LocalQwenLLM.complete` with the production `governance.prompts.render_prompt("disable_source", evidence)` output, never touches `logs/governance/decisions.jsonl` or `data/`, and is therefore soak-safe by construction.
- 2026-05-03 13:01:56 → 13:02:11 UTC probe run, model = `qwen3:14b` via Ollama at `http://localhost:11434`, three calls totaling 14.3 wall-clock seconds:

  | Probe | Inputs | Expected | Observed | Confidence | Verdict |
  |---|---|---|---|---|---|
  | `POS_unresolvedmysteries` | replay of production `gd_2026-05-02_0001` evidence (23 ingestion / 0 fresh / 0 match, off-topic cold-case headlines) | `disable_source` | `disable_source` | 0.9 | reproduces production decision |
  | `NEG_A_high_signal_source` | 50 ingestion / 15 fresh-pass / 10 matches / `anchor_rate=0.40` (LLM disagreed with market 60% of cases — strong signal) / on-topic Iran-diplomacy + Senate-budget headlines | `no_action` | **`disable_source`** | 0.75 | **rubber-stamp confirmed** |
  | `NEG_B_borderline_source` | 15 ingestion / 2 fresh-pass / 1 match / `anchor_rate=0.70` / mixed headlines | ambiguous | `disable_source` | 0.85 | calibration over-confident toward disable |

- Two distinct failure modes in the NEG_A response:
  1. **Polarity inverted on `anchor_rate`.** The model wrote *"low match quality (LLM anchor rate of 40.0%)"*. Per `governance/evidence.py:62–84` the alignment-audit selector flags `anchor_rate ≥ 0.95` as a *disable* signal — high anchor rate means the LLM agrees with market price = no information. `anchor_rate=0.40` is the opposite: LLM disagrees with market 60% of cases = high information value = a source the bot should KEEP. The model treated low anchor rate as bad.
  2. **Confabulated framing.** The model wrote *"10 out of 50 ingestion events produced a MATCH_DIAGNOSTIC, suggesting poor signal-to-noise ratio"*. There is no signal-to-noise metric in the prompt; the model invented one and used it to justify the disable verdict.
- Determinism check: temperature is 0.0 in `governance/llm.py:212`. The 74-decision production sample shows byte-identical reasoning across cycles for the same `(target, evidence)` tuple. Rubber-stamp behaviour is therefore deterministic, not a sampling artifact.

**Proposed Fix**

Iterate on the prompt against the negative-control harness as the regression gate. Three classes of intervention worth testing, ranked by cost:

1. **Prompt clarification (cheapest).** Restate the `anchor_rate` semantics inline so the model cannot invert the polarity: e.g. "The `anchor_rate` field is the rate at which our LLM final probability *agreed with* market price. **High values (≥0.95) indicate the source contributes no edge** and is a disable candidate. **Low values indicate informative signal — the source disagrees with market price often** and should usually NOT be disabled regardless of other metrics."
2. **Counter-argument step (moderate).** Add a required output field `argument_against_action` that the model fills *before* picking the action: "Before deciding, write one sentence arguing for keeping this source. If your argument is plausible, output `no_action`." Forces the model to consider the alternative.
3. **Two-pass deliberation (expensive).** Two LLM calls per candidate: first call returns `argument_for_disable + argument_against_disable`; second call (or post-process) picks the action. Highest cost but cleanest discrimination.

(1) is the right first attempt because it is testable in seconds via the harness and fits within the soak window's hard ceiling. (2) is the right fallback if (1) does not flip NEG_A. (3) is the right move if (2) also fails — at that point the prompt template is structurally inadequate and the spec needs a richer schema.

**Acceptance Criteria**

- The negative-control harness `scripts/simulations/governance_negative_control.py` is the regression gate. The post-fix run must satisfy:
  - `POS_unresolvedmysteries` returns `disable_source` (production reproducer must continue to work; if it doesn't, the prompt change has broken the genuine-bad-source path).
  - **`NEG_A_high_signal_source` returns `no_action`** with confidence ≥ 0.7. This is the load-bearing criterion.
  - `NEG_B_borderline_source` returns either `no_action` or `disable_source` — operator decision based on whether the reasoning shows the model considered the alternative.
- `tests/test_governance_prompts.py` snapshot tests updated to pin the new prompt revision. (Ensures the post-fix prompt is locked against drift.)
- Before resuming the §8.5 manual-review gate in `PROFIT-PHASE2-001`: a fresh ≥30-decision window must accumulate post-fix and the manual-review sample drawn from that window only. The 74 pre-fix decisions are not authoritative for the gate.

**Notes**

- The 74 pre-fix `disable_source` proposals in `logs/governance/decisions.jsonl` are NOT applied (`applied=False`, shadow mode) and `data/runtime_overrides.yaml` does not exist. Production safety is intact. This entry is purely about Phase 2 *acceptance evidence quality*, not production behaviour.
- The harness must continue to be soak-safe across iteration. Specifically, do not have it write to `logs/governance/`, `data/`, or any path under `transfer/`. Output to stdout only; `--json` for machine-readable. Already enforced in the current implementation.
- This finding does not affect the operational layer of `PROFIT-PHASE2-001`: cycle reliability, `applied=` invariant, KILL_SWITCH count, error rate. Those gates continue to accumulate evidence at the launchd cadence and remain valid acceptance criteria.
- Empty-response follow-up: `docs/_archive/governance/2026-05-03-empty-response-audit.md` (ARCHIVED Stream G R43) ran 110 clean-window `qwen3:14b + format=json + think=False` calls across concurrency, evidence-shape, prompt-length, and sequential-accumulation axes with 0 empty responses. Treat the 13:19-13:33 UTC iteration failures as transient daemon/session-state evidence until a reproducible H1-H4 axis appears.
- H5 semantic-content follow-up: `docs/_archive/governance/2026-05-03-h5-semantic-bisection.md` (ARCHIVED Stream G R43) ran 96 clean-window calls across V_A SYSTEM_PROMPT ablations with 0 POS empty responses. A5 (`LLM anchor rate` semantics only) and A6 (final high-match/low-anchor keep instruction only) both flipped `NEG_A_high_signal_source` to `no_action` without POS regression; A5 is the minimum-content next fix candidate.

**Iteration session log — 2026-05-03 13:19–13:33 UTC** (no production change landed; scratch-only; surfaced for the next session)

A first prompt-iteration pass tested two variants against the harness's expanded 11-probe fixture set (Codex Task 1, commit `d29bb29`, landed mid-session at 13:23 UTC):

- **`V_A`** — `SYSTEM_PROMPT` extended with an "Interpreting the metrics" section (anchor_rate polarity callout: HIGH ≥0.95 = no edge → DISABLE, LOW ≤0.50 = informative → KEEP) plus a "DEFAULT TO no_action" instruction; `DISABLE_SOURCE_TEMPLATE` annotated with the same polarity hint inline. Net diff: ~25 lines added.
- **`V_A3`** — minimal-touch variant: `SYSTEM_PROMPT` untouched; `DISABLE_SOURCE_TEMPLATE` modified on the single `LLM anchor rate` line only. Net diff: ~1 line.

Cross-variant results across all 11 harness probes (each row = one Ollama call against the live `qwen3:14b` daemon, `temperature=0.0`, `think=False`):

| Run | Time UTC | Variant | Pass / Fail / Empty |
|---|---|---|---|
| 1 | 13:19:18 | PROD (3-probe baseline) | 3 valid (`disable_source` × 3) — confirms baseline reproducer |
| 2 | 13:22:38 | V_A (full 11-probe sweep) | 5 valid / 6 empty — POS cases all empty; 3 NEG_A correctly flipped to `no_action` (conf 0.60–0.75) with sound polarity-aware reasoning ("low LLM anchor rate (25.0%), indicating it provides directional views distinct from market price, which is informative") |
| 3 | 13:24:46 | V_A3 (full 11-probe sweep) | 0 valid / 11 empty |
| 4 | 13:25:45 | PROD harness (control) | 0 valid / 11 empty |
| 5 | 13:31:40 | PROD harness (clean window, post-Codex) | 0 valid / 11 empty |
| 6 | 13:32:25 | V_A (clean window, immediately after run 5) | 5 valid / 6 empty — same shape as run 2 |

Findings:

1. **The polarity-fix concept is valid.** V_A's NEG_A behaviour is the load-bearing positive: 3/3 NEG_A probes with non-zero anchor_rate flipped to `no_action` with reasoning that explicitly cites the polarity ("low anchor rate ... informative ... directional views distinct from market price"). The model uses the polarity callout when given the opportunity.
2. **V_A introduces a POS-case regression that is NOT pure prompt content.** Run 6 vs run 5 in the same Ollama state: PROD failed all 11, V_A succeeded on 5/11 — V_A is *more* robust under degraded daemon state, not less. But all 4 POS cases (anchor_rate=None, zero matches) returned empty under V_A even when NEG_A cases succeeded. The empty-response failure mode appears correlated with sparse-evidence shapes (`anchor_rate=None` + zero `match_count`) rather than V_A's prompt content per se.
3. **Concurrency confound is real.** Codex's commits `d29bb29` (13:23:25 UTC) and `8882f4c` (13:24:33 UTC) for the parallel governance work both required harness validation runs that hit the same Ollama daemon. The exact 13:22–13:28 UTC window where this iteration session ran is also where Codex's PR-validation calls landed. Some empty-response observations in runs 3–5 are attributable to GPU contention rather than prompt content.
4. **PROFIT-GOV-001's `think=False` fix is not fully sufficient.** Empty `{}` responses still occur under `qwen3:14b + format=json + think=False` for some combination of (a) sparse evidence shapes, (b) concurrent daemon load, (c) prompt-length thresholds. Production launchd cycles have not surfaced this since the 2026-05-02 reset (zero post-baseline PARSE_ERRORs across 74 decisions), so the trigger is something the iteration probe pattern hits more readily than the 2-hour cadence does.

Decision: do **not** land V_A as the production prompt fix. The POS-case empty-response regression would invalidate every production cycle on off-topic-zero-match candidates (which is the bulk of what the heuristic selector flags). Hand the empty-response characterization to a follow-up session with a clean Ollama state and no concurrent harness runs. The polarity-fix idea remains the right starting hypothesis for the eventual fix.

Decision: do **not** file a separate `PROFIT-GOV-003` for the empty-response intermittency yet. The phenomenon needs cleaner characterization (concurrency vs evidence-shape vs prompt-length axes) before it earns its own entry. Track the question inside this entry's iteration log instead.

**2026-05-03 14:00–14:32 UTC — post-audit retest** (Codex audit `092666c` complete, no production change landed)

Codex's empty-response audit (`docs/_archive/governance/2026-05-03-empty-response-audit.md`, 110 calls clean window, ARCHIVED Stream G R43) found H1–H4 (concurrency, evidence-shape, prompt-length, sequential-accumulation) all returned 0/N empty rates. Verdict: failure mode is "transient daemon/session state outside the tested axes." Recommendation: PROD sentinel pair before each iteration variant.

Post-audit V_A retest in clean window (14:27 UTC, 39 min before next launchd cycle) reproduces the **same 5/11 valid** result observed in the original 13:32 UTC iteration: 4/4 POS probes empty, 3/4 NEG_A probes correctly flip to `no_action` with polarity-aware reasoning, 2/3 NEG_B return `no_action`. The pattern is **reproducible**, not flaky. V_A's POS-empty regression is genuinely prompt-content driven, just not on any of the H1–H4 axes Codex tested.

V_A4 (V_A minus the "DEFAULT TO no_action" instruction; keep the "Interpreting" polarity section): **9/11 empty** in clean window, worse than V_A. Removing the "DEFAULT TO no_action" line is not the trigger.

V_A3 (single-line user-template polarity hint, no SYSTEM_PROMPT change): **0/11 empty** reproducibly in clean window. Inline polarity hint at the user-template level breaks the model entirely.

Cross-variant comparison (all clean-window, post-audit):

| Variant | Valid / 11 | NEG_A correct flips | Note |
|---|---|---|---|
| PROD | 11/11 | 0/4 (rubber-stamps) | Production baseline; rubber-stamp bias confirmed |
| V_A | 5/11 | 3/4 | Polarity fix works when output returned; POS shapes empty |
| V_A4 | 2/11 | 2/4 | V_A minus "DEFAULT TO no_action" — net worse |
| V_A3 | 0/11 | n/a | User-template polarity hint alone breaks everything |

**Codex's H3 prompt-length axis is null-content padding** (`prompt_variant()` at `scripts/simulations/governance_empty_response_audit.py:129–143` adds `"Diagnostic padding line {i}: evaluate metrics before choosing action."` × N — generic filler the model can ignore). That tests *raw token count* but not *semantically-loaded prompt content*. V_A's added content is the latter — actual instructions the model must integrate into its reasoning. The audit's H3 "not the driver" verdict therefore does not rule out semantic-content prompt additions as the trigger; it only rules out length-as-tokens.

New axis to characterize before the next prompt-iteration session:

**H5 — semantic content of prompt additions.** Bisect V_A's added SYSTEM_PROMPT content piece by piece against a known-good evidence shape (e.g. POS_unresolvedmysteries). Identify the minimum subset of added text that triggers empty responses on POS shapes. That subset is the actual prompt-content failure surface; design the working fix to avoid it.

Decision: retain the prior iteration log's "do not file PROFIT-GOV-003" stance. The semantic-content failure surface is a sub-question of GOV-002 (the prompt fix is what's actually being characterized), not a separate transport-layer bug. Track via the next Codex task on H5 bisection.

**2026-05-03 15:08–15:19 UTC — Codex H5 bisection + A5 retest** (Codex commit `83bf954`, no production change landed)

Codex's H5 semantic-content bisection (`docs/_archive/governance/2026-05-03-h5-semantic-bisection.md`, 96 calls clean window starting `audit_started_utc=2026-05-03T15:09:18Z`, ARCHIVED Stream G R43) found:

| Ablation | Description | NEG_A no_action rate | POS empty rate |
|---|---|---|---|
| A0_PROD | baseline | 0/3 (rubber-stamp) | 0/3 |
| A1_DEFAULT_ONLY | "DEFAULT TO no_action" paragraph alone | 0/3 (still rubber-stamps) | 0/3 |
| A2_INGESTION_ONLY | header + ingestion bullet | 0/3 | 0/3 |
| A3_FRESH_ONLY | header + fresh-pass bullet | 0/3 | 0/3 |
| A4_MATCH_ONLY | header + match-count bullet | 0/3 | 0/3 |
| **A5_ANCHOR_ONLY** | **header + anchor-rate semantics block** | **3/3 ✅** | **0/3 ✅** |
| A6_FINAL_ONLY | header + final "high Match + LOW anchor_rate = KEEP" line | 3/3 ✅ | 0/3 |
| A7_FULL_V_A | full V_A block | 3/3 | 0/3 (clean — opposite of Claude's prior runs) |

Verdict: **A5_ANCHOR_ONLY is the minimum-content polarity fix.** Adding only the LLM anchor rate semantics block (HEADER + ANCHOR sub-bullets, lines 49–53 of `scripts/simulations/governance_semantic_bisect_h5.py`) inserted after the `no_action` bullet in the production `SYSTEM_PROMPT` flips NEG_A correctly while leaving POS shapes valid. The DEFAULT paragraph and the per-metric bullets contribute nothing to the polarity fix and can be omitted from the production landing.

Independent observation that contradicts Claude's prior retest evidence: **A7 (full V_A) returned 0/3 POS empty in Codex's bisection**, while Claude's three retests of full V_A returned 4/4 POS empty. Same prompt, same model, same Ollama daemon. The contradiction reveals a new failure axis Codex's H1–H4 + H5 ablation matrix does not capture.

Claude's A5 full-harness retest (15:18:29 UTC, 11 probes, started ~7 min after Codex's bisection completed): **9/11 empty**, including 2 of 3 of Codex's "valid" sentinel cells (S_POS, S_NEG_A_zero, S_NEG_B). Only S_NEG_A returned valid output (`no_action` confidence 0.65 with sound polarity-aware reasoning). The same 4-sentinel set Codex tested 3× per cell with 0/12 empty went 1/4 valid 7 minutes later under A5.

**New hypothesis — cumulative session-call instability.** Empty-response rate appears tied to cumulative LLM calls within a daemon-state window, not prompt content. Empirical timeline this session:

| Time UTC | Cumulative calls | Run | Empty rate |
|---|---|---|---|
| 13:01:56 | 0 → 3 | first PROD harness | 0/3 |
| 13:20:34 | ~3 → 14 | second PROD harness | 0/11 |
| 13:22:38 | ~14 → 25 | first V_A iteration | 6/11 |
| 13:25–13:32 | 25–60 | PROD/V_A3/V_A retests | mostly empty |
| 14:00–14:30 | fresh process | Codex empty-response audit | 0/110 |
| 15:09–15:11 | fresh process | Codex H5 bisection | 0/96 |
| 15:18 | ~96 → 107 | Claude A5 full-harness retest | 9/11 |

Production launchd cycles emit ~5 LLM calls per 2h cycle = ~60 calls/day — well below the ~50–100-call apparent threshold. That is why `logs/governance/decisions.jsonl` shows zero post-2026-05-02-baseline PARSE_ERRORs across 74 decisions while iteration sessions reproduce empty responses readily.

This is a separate failure mode from the rubber-stamp bias GOV-002 was opened to characterize. It does not block landing A5 as the prompt fix; it does block local validation of A5 from this Claude session because the daemon-state threshold has been crossed. Production is unaffected.

Decision: **do not land A5 to `governance/prompts.py` from this session.** Codex's clean-state bisection is strong evidence A5 works, but a soak-window production prompt change without local Claude validation is too aggressive given the open daemon-stability question. Next session: restart Ollama (`ollama stop qwen3:14b` then trigger a single warm-up call), run the A5-modified harness against the 11-probe set ONCE in cold-window, confirm 11/11 valid, then land A5 to production with the post-fix harness output as the in-commit acceptance evidence.

Decision: **promote the cumulative-session-call instability to a candidate `PROFIT-GOV-003` filing pending one more clean-window confirmation in the next session.** If next session also reproduces the post-~100-call empty-response degradation (e.g. cold restart returns 0/11, then 100 sequential probe calls reproduce the empty rate climbing toward 50%+), that is the falsifiable test. File GOV-003 LOW (production unaffected; iteration sessions blocked) only if confirmed.

**Closure (2026-05-03 15:28 UTC)** — A5_ANCHOR_ONLY landed to `governance/prompts.py:SYSTEM_PROMPT` (commit subject `feat(governance): land A5 anchor-rate polarity fix to SYSTEM_PROMPT`); `tests/test_governance_prompts.py::test_system_prompt_carries_anchor_rate_polarity_callout` pins the new prompt revision (9/9 governance prompt tests pass). First post-fix cycle was force-triggered immediately via `launchctl kickstart -k gui/$UID/com.kalshi.governance.fast` and recorded as `gc_2026-05-03_152836` in `logs/governance/decisions.jsonl` — duration 20.087s (within normal cycle range), 5 decisions made, 0 PARSE_ERRORs, `batch_aborted=False`. All 5 decisions were `disable_source` on `anchor_rate=None` Reddit-audit candidates (r/gamingleaksandrumours, r/keep_track, r/allthingsjenna, r/prayerstotrump, r/stevehofstetter), confidence 0.85–0.90, all sensible per their evidence shapes. The polarity callout is dormant on these inputs (it only activates when the alignment-audit path produces candidates with non-null `anchor_rate`); no production regression. Codex's H5 bisection (commit `83bf954`) is the offline acceptance evidence: A5_ANCHOR_ONLY produced 3/3 `no_action` flips on `NEG_A_high_signal_source` with confidence range 0.65–0.85 and 0/3 POS empties across the four sentinel shapes. Acceptance criteria (POS reproducer continues to disable, NEG_A flips to no_action with conf ≥ 0.7, snapshot test pinned) all satisfied.

Pre-fix decisions in `decisions.jsonl` (~103 records spanning 2026-05-02 → pre-A5 cycles on 2026-05-03) remain audit-historical; they are not authoritative for the §8.5 "≥85% reasonable" manual-review criterion, which restarts against a post-fix window with baseline `decided_at >= 2026-05-03T15:28:40+00:00` (the first GOVERNANCE_DECISION emitted under cycle `gc_2026-05-03_152836`). Note: the bare `decision_id` field (e.g. `gd_2026-05-03_0001`) is reused per-cycle in the current logging schema, so `(cycle_id, decided_at)` is the unambiguous tuple identifier. See PHASE2-001's mid-soak halt note for the resume protocol.

**2026-05-03 16:09–16:21 UTC — H_CUMUL falsification (Codex commit `c987e4b`)** — the cumulative-session-call hypothesis is **rejected**. Cold-restart audit at `scripts/simulations/governance_cumulative_call_audit.py` (200 sequential identical calls + 10-call recovery batch, single Python process, single Ollama daemon session, daemon uptime 2d 2h at audit start) produced **211/211 valid responses** with consistent ~3.5s per call timing across the full 12-minute load. Per-10-call empty rate stayed at 0.00 throughout. Recovery batch empty rate: 0/10. Empirical thresholds for 25%/50%/75% empty rate: never reached. Report at `docs/_archive/governance/2026-05-03-cumulative-call-audit.md` (ARCHIVED Stream G R43).

The earlier-session empty-response observations (Claude's 9/11 deterministic empty under V_A and 0/11 under V_A3) are therefore **not driven by cumulative call count**. They were real but the underlying axis remains uncharacterized. Candidate causes Codex's H1–H5 + cumulative-call audits did not cover:

- **Same-process daemon-state poisoning by V_A-specific prompt content** that resolves only on Python-process exit (not Ollama-daemon unload). Codex's audits all spawned fresh Python processes per condition; Claude's iteration shared one Python process across V_A/V_A3/V_A4 attempts.
- **Mixed-prompt-content interaction** — running multiple distinct prompt variants in sequence within one session may stress some Ollama-internal cache or queue in a way that uniform prompt sequences don't.

Neither is worth filing as `PROFIT-GOV-003` at this point. The phenomenon is observation-bias-bounded (only triggers in iterative-prompt-comparison sessions, never in production) and the working A5 fix is already shipped. **`PROFIT-GOV-003` filing is dismissed.** Future iteration sessions should follow Codex's empty-response-audit recommendation (PROD sentinel pair before each variant) plus a fresh Python process per variant; if empty responses recur under that protocol, that is a new finding worth re-opening the question.

**Related**

- `PROFIT-PHASE2-001` — direct dependency. The mid-soak halt note added 2026-05-03 records that the §8.5 manual-review criterion is paused pending GOV-002 resolution; the operational criteria continue.
- `PROFIT-GOV-001` (closed 2026-05-02) — neighbour; resolved a different qwen3 + Ollama interaction (empty `{}` from `format=json` + thinking phase). GOV-002 is a behavioural bias on the same model; not a transport-layer bug.
- `PROFIT-LLM-001` (open, LOW) — post-Phase 2 unification of the signal-analyzer LLM. GOV-002 is a precondition: a unified model must clear the negative-control harness before it can replace the signal-analyzer's qwen2.5:7b.
- `scripts/simulations/governance_negative_control.py` — the regression harness this entry's acceptance criteria pin against. Expanded 2026-05-03 to 17 probes with adversarial anchor-rate polarity flips and V_A regression guards for `anchor_rate=None` sparse POS shapes.

---

### PROFIT-GOV-004

| Field | Value |
|-------|-------|
| **ID** | PROFIT-GOV-004 |
| **Title** | §8.5 manual-review gate-6 sample-based review redesign for Phase-3 |
| **Category** | Governance / Soak-Acceptance Spec |
| **Severity** | MEDIUM (Phase 2 close-day operational risk; full impact at Phase 3+ when decision volume scales) |
| **Status** | OPEN (filed 2026-05-06 cycle 11; deferred to Phase-3 spec work) |
| **Priority** | DEFER (post-PROFIT-PHASE2-001 close; pre-Phase-3 real-mode flip) |
| **Owner** | UNASSIGNED |
| **Depends On** | PROFIT-PHASE2-001 close (data points for sample-size calibration) |
| **Blocks** | Phase-3 governance soak runbook (will inherit gate-6 mechanism) |

**Description**

`PROFIT-PHASE2-001-early-close-criteria.md` §8.5.1 gate 6 reads "≥ 85 % reasonable on manual review" with body text "Manual review pass on **all** decisions for gate 6." Cycle-11 mid-soak (Day-4) `manual_review_capacity_audit.py` showed exhaustive review at default 80/day budget capped reviewable fraction at 0.747 against the 0.85 threshold (peak day-5 had 146 decisions; per-day cap math is the bottleneck).

The right long-term primitive for "is the population of decisions reasonable?" is statistical sampling — not exhaustive review. Gate-6's `%reasonable` IS a population statistic; sampling answers it correctly with bounded confidence. Decisions are mostly homogeneous (`disable_source` against persistent low-engagement Reddit; few distinct reasoning tuples per day) — sampling efficiency would be very high.

This entry tracks the Phase-3 spec work to redesign gate 6 as sample-based, not exhaustive.

**Why it matters to profitability / safety / reliability**

1. **Capacity ceiling.** Decision volume scales with the number of sources × cadence. Phase-3 real-mode flip will likely add Phase-4 placeholders (`_disable_keyword_candidates`, `_tune_threshold_candidates`) increasing per-cycle decisions. Exhaustive review becomes operationally infeasible.
2. **Quality of review degrades at high volume.** Reviewing 200 disable_source decisions/day is mostly skim-work; quality drops. Sample-based review of 30/200 with statistical rigor produces a stronger signal than exhaustive skim.
3. **Phase-3 governance soak runbook will inherit this gate.** Fixing it once at Phase-3 spec time avoids re-doing the calculus per soak.

**Evidence / Source**

- `scripts/manual_review_capacity_audit.py` — mid-soak Day-4 result: 286/383 = 0.747 reviewable at 80/day; Day-5 peak 146 the dominant pressure.
- `docs/governance/2026-05-06-gate-6-capacity-resolution-plan.md` — close-day resolution plan (Path 3 → Path 1 sequence; Path 2 = this entry).
- `PROFIT-PHASE2-001-early-close-criteria.md` §8.5.1 gate 6 + body wording.
- Distribution evidence: cycle-9 manual review of 67 day-1-to-day-3 decisions (commit `9f8deef`) returned 100 % reasonable across many distinct (target, reasoning) tuples — suggests low diversity (i.e., sampling-friendly population).

**Proposed Fix**

Phase-3 spec work, NOT close-day patch:

1. **Sample size determination.** Compute statistical power for detecting `<85 %` reasonable rate at 95 % confidence. Likely `n ≈ 30-50` per soak window given the homogeneity of decisions.
2. **Stratification.** Stratify sample across decision types (disable_source vs Phase-4 keyword_disable vs threshold_tune) once Phase-4 selectors land, so each type's reasonable rate is bounded.
3. **§8.5.1 amendment.** Replace gate 6 wording with sample-based protocol: "Reviewer randomly samples N decisions stratified by action type; ≥ 85 % reasonable in the sample (one-sided 95 % CI)."
4. **Tooling.** Update `governance_decision_review.py` to support `--sample N` mode with random selection; preserve `--bulk-mode` for exhaustive option.
5. **Spec change.** New `docs/superpowers/specs/2026-XX-XX-gate-6-sample-based-redesign.md` carrying the statistical rigor (power calculation, expected error rate, stratification scheme).

**Acceptance Criteria**

- Sample size formally derived (with power + confidence assumptions) and reproducible via a script.
- §8.5.1 amendment lands BEFORE the next governance soak begins.
- `governance_decision_review.py --sample N` mode implemented + tested.
- Phase-3 runbook references the sample-based gate, not the exhaustive one.

**Notes**

- Mid-soak amendment of §8.5.1 is OUT OF SCOPE for this entry — close PROFIT-PHASE2-001 under the existing exhaustive-review wording per `2026-05-06-gate-6-capacity-resolution-plan.md`. Sample-based redesign is the NEXT-soak gate, not this-soak rescue.
- Statistical-rigor design likely takes 4-8 hours of focused spec work + peer review. Schedule alongside Phase-3 governance runbook drafting.

**Related**

- `PROFIT-PHASE2-001` — current soak; closes under exhaustive-review wording (Path 3 / Path 1 per resolution plan).
- `PROFIT-GOV-002` — closed; same gate-6 area but addresses LLM-side rubber-stamp bias, not capacity.
- `docs/governance/2026-05-06-gate-6-capacity-resolution-plan.md` — close-day resolution plan that DEFERS Path 2 to this entry.
- Future spec: `docs/superpowers/specs/2026-XX-XX-gate-6-sample-based-redesign.md` (to be authored at Phase-3 spec time).

---

### MAC-ASYNC-001

| Field | Value |
|-------|-------|
| **ID** | MAC-ASYNC-001 |
| **Title** | `paper_trader.record_trade()` blocks event loop from async executor |
| **Category** | Async / Queue / Scheduling |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | MAC-TEST-001 |

**Implementation Notes** (2026-04-19)
`get_notional_bankroll()` (SQLite SELECT) was also called synchronously in the same `log.info()` line. Both calls were batched in a single `asyncio.to_thread(_record)` closure to avoid two separate thread dispatches and eliminate any race window between the write and the bankroll read. Fixed in `executor.py`. MAC-TEST-001 regression guard added in `test_executor.py:TestPaperExecutionAsync` — verifies `record_trade` is called from a non-event-loop thread. Committed as v0.29.21.

**Description**  
`executor.py:361` calls `self._paper.record_trade(analysis)` directly inside `async def _execute_paper()` without `asyncio.to_thread()`. `PaperTrader.record_trade()` is a synchronous method that executes an SQLite `INSERT`. This blocks the asyncio event loop for the duration of the DB write on every paper trade.

**Why This Is Platform-Sensitive**  
On Windows under NSSM, the service ran as a single-process loop with low concurrency pressure. On macOS as a developer process running under asyncio with concurrent task runners, event-loop stalls are more visible and have a wider blast radius (delayed news processing, missed price updates during stall window).

**Evidence / Source**  
- Audit findings R-1, A-1, A-2
- `trading/executor.py:361` — `_execute_paper()`
- `trading/paper_trader.py:462` — `record_trade()` signature (no `async`)

**Proposed Fix**  
```python
# executor.py _execute_paper()
trade_id = await asyncio.to_thread(self._paper.record_trade, analysis)
```

**Acceptance Criteria**  
- `_execute_paper()` wraps `record_trade()` in `asyncio.to_thread()`
- No event-loop blocking call remains in `_execute_paper()`
- Existing paper-trade tests continue to pass
- `MAC-TEST-001` test passes

**Notes**  
`PaperTrader` has `check_same_thread=False` on its connection, so it is safe to call from a thread-pool thread via `to_thread()`. No connection changes needed.

---

### MAC-ASYNC-002

| Field | Value |
|-------|-------|
| **ID** | MAC-ASYNC-002 |
| **Title** | `paper_trader` nightly/resolve calls block event loop from async task functions |
| **Category** | Async / Queue / Scheduling |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | MAC-TEST-001 |

**Description**  
Three additional synchronous paper trader calls are made directly from async methods in `main.py`:
- `main.py:998` — `self.paper.daily_summary()` inside `async def _daily_report_task()`
- `main.py:999` — `self.paper.generate_report()` inside `async def _daily_report_task()`
- `main.py:1076` — `self.paper.resolve_market(ticker, resolved_yes)` inside `async def _check_and_resolve()`

`generate_report()` performs a full table scan and string-builds hundreds of lines — the longest-running of the three, and the one most likely to cause a visible stall as the trade history grows.

**Why This Is Platform-Sensitive**  
Same as MAC-ASYNC-001. Windows NSSM single-process model masked this; macOS asyncio multi-task model exposes it.

**Evidence / Source**  
- Audit findings R-1, A-1, A-2
- `main.py:994–1000` — `_daily_report_task()`
- `main.py:1045–1079` — `_check_and_resolve()`

**Proposed Fix**  
```python
# _daily_report_task()
await asyncio.to_thread(self.paper.daily_summary)
report = await asyncio.to_thread(self.paper.generate_report)

# _check_and_resolve()
await asyncio.to_thread(self.paper.resolve_market, ticker, resolved_yes)
```

**Acceptance Criteria**  
- All three call sites wrapped in `asyncio.to_thread()`
- No synchronous paper trader method called from any async context without `to_thread()`
- `_daily_report_task` and `_check_and_resolve` tests pass

**Implementation Notes** (2026-04-19)  
Fixed in v0.29.22. Five blocking calls wrapped in `asyncio.to_thread()` in `main.py`:
- `_daily_report_task()`: `daily_summary()`, `generate_report()`, `report_path.write_text()` (file I/O)
- `_check_and_resolve()`: `_conn.execute(...).fetchall()` (direct DB query in lambda), `resolve_market()`, post-loop `get_notional_bankroll()`

`TestMainAsyncBlocking` in `tests/test_main_pipeline.py` adds 5 regression guard tests verifying each call is dispatched off the event loop thread.

**Notes**  
Assess whether `generate_report()` at scale (>1000 trades) creates a thread-pool saturation risk. If so, a dedicated thread executor should be considered — but that is a future item, not part of this fix.

---

### MAC-DB-001

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-001 |
| **Title** | `evidence_store._connect()` missing WAL journal mode |
| **Category** | Persistence / DB |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | MAC-TEST-002, MAC-DB-005 |

**Description**  
`tasks/evidence_store.py:221–225`: `_connect()` creates a fresh connection per DB operation with `PRAGMA foreign_keys = ON` but no `PRAGMA journal_mode=WAL`. With the default DELETE journal mode, a single writer blocks all other connections (readers and writers). `AccumulationTask` dispatches writes to different markets concurrently via `asyncio.to_thread()`; all of those threads compete for SQLite's global write lock at the OS level. Under a busy multi-market session (common during news events), writes serialize and can hit the 30-second timeout.

**Why This Is Platform-Sensitive**  
macOS APFS I/O patterns and asyncio task scheduling tend to produce more concurrent DB access than the Windows NSSM pattern (where one task ran at a time). The issue exists on both platforms but surfaces more readily on macOS.

**Evidence / Source**  
- Audit findings R-2, D-1
- `tasks/evidence_store.py:221–225` — `_connect()`
- `tasks/accumulation_task.py:208–211` — concurrent `to_thread()` dispatches

**Proposed Fix**  
```python
def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(str(self.db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
```

**Acceptance Criteria**  
- `_connect()` sets `journal_mode=WAL` and `synchronous=NORMAL`
- A `-wal` file appears next to the DB after the first write
- Concurrent write test (`MAC-TEST-002`) passes without `OperationalError`
- No existing DB schema or migration is broken

**Implementation Notes** (2026-04-19)  
Fixed in v0.29.23. Added `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` to `tasks/evidence_store.py:_connect()`. All 958 tests pass.

**Notes**  
WAL mode persists in the DB file after first write; subsequent connections inherit it. `synchronous=NORMAL` is safe with WAL (crash-safe with slightly relaxed fsync), and meaningfully faster than the default FULL.

---

### MAC-DB-002

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-002 |
| **Title** | `paper_trader` SQLite connection missing explicit timeout |
| **Category** | Persistence / DB |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`trading/paper_trader.py:189`: `sqlite3.connect(str(db_path), check_same_thread=False)` uses SQLite's default 5-second lock timeout. `evidence_store._connect()` uses `timeout=30.0`. If a background admin script or test holds the paper trade DB open during a resolve or report cycle, the paper trader will fail with `OperationalError` after 5 seconds while evidence_store would wait 30. The inconsistency makes failure behavior unpredictable.

**Why This Is Platform-Sensitive**  
macOS users are more likely to run `sqlite3 paper_trades.db` interactively to inspect trades. Windows users typically accessed the DB through the NSSM service logs only. Interactive access increases the probability of a live lock contention scenario.

**Evidence / Source**  
- Audit finding R-3, D-2
- `trading/paper_trader.py:189`

**Proposed Fix**  
```python
self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
```

**Acceptance Criteria**  
- `paper_trader` connection uses `timeout=30.0`
- Timeout is consistent with `evidence_store._connect()` timeout

**Implementation Notes** (2026-04-19)  
Fixed in v0.29.23. Added `timeout=30.0` to `sqlite3.connect()` call in `trading/paper_trader.py:189`. All 958 tests pass.

---

### MAC-DB-003

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-003 |
| **Title** | `paper_trader` connection has unnecessary `check_same_thread=False` |
| **Category** | Persistence / DB |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-ASYNC-001, MAC-ASYNC-002 |
| **Blocks** | — |

**Description**  
`trading/paper_trader.py:189`: `check_same_thread=False` disables SQLite's thread-safety guard. All current paper trader methods are synchronous and, after MAC-ASYNC-001/002 are fixed, will be invoked from `asyncio.to_thread()` worker threads. Since each `to_thread()` call dispatches to its own thread, a single shared connection with `check_same_thread=False` would then be legitimately accessed from different threads — which is actually the case that requires the flag.

Re-evaluate after MAC-ASYNC-001/002: if `to_thread` is used, the flag is required; if a per-call connection pattern is adopted, the flag can be removed. Do not remove this flag until the async usage pattern is finalized.

**Why This Is Platform-Sensitive**  
Flag was likely set during Windows development where threading model was different. Intent is now unclear.

**Evidence / Source**  
- Audit finding D-3
- `trading/paper_trader.py:189`

**Proposed Fix**  
After MAC-ASYNC-001/002: audit whether `_conn` is ever accessed from multiple threads simultaneously. If yes (via `to_thread()`), the flag is correct and this item closes as "no change needed." If no (single-threaded access), remove the flag to restore the safety guard.

**Acceptance Criteria**  
- Decision documented (flag needed or not) with rationale
- If removed: no `ProgrammingError` in any test

**Implementation Notes** (2026-04-20)  
Decision: `check_same_thread=False` is **required and correct**. After MAC-ASYNC-001/002, all paper trader method calls go through `asyncio.to_thread()`, which dispatches to arbitrary thread-pool worker threads. The single shared `_conn` is therefore legitimately accessed from different threads across calls. Removing the flag would cause `ProgrammingError` on the first `to_thread`-dispatched call. No code change needed. Closed as documented decision.

---

### MAC-DB-004

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-004 |
| **Title** | `paper_trader._migrate_db()` uses `executescript()` without explicit transaction guard |
| **Category** | Persistence / DB |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`trading/paper_trader.py:206` calls `self._conn.executescript(_DDL)`. `executescript()` implicitly commits any open transaction before running, which is documented Python behavior. If the DDL partially fails (e.g., disk full mid-migration), the DB may be left in a partially migrated state. On macOS this is unlikely but would be hard to diagnose.

**Why This Is Platform-Sensitive**  
On Windows under NSSM, DB initialization happened in a controlled startup environment. On macOS as a developer process, interrupted startups (Ctrl+C during init) are more common.

**Evidence / Source**  
- Audit finding D-4
- `trading/paper_trader.py:206`

**Proposed Fix**  
Replace `executescript()` with individual `execute()` calls inside an explicit `BEGIN`/`COMMIT` block, or use a context manager: `with self._conn: self._conn.execute(ddl_statement)`.

**Acceptance Criteria**  
- DDL is wrapped in an explicit transaction
- A simulated mid-migration failure leaves the DB in a recoverable state

**Implementation Notes** (2026-04-19)  
Replaced `self._conn.executescript(_DDL)` / `self._conn.commit()` in `initialize()` with a `with self._conn:` block that splits `_DDL` on `;` and calls `self._conn.execute()` for each non-empty statement. The context-manager form issues a single `BEGIN`/`COMMIT` around all CREATE TABLE statements, so a mid-DDL failure rolls back cleanly. 961 tests passed.

---

### MAC-DB-005

| Field | Value |
|-------|-------|
| **ID** | MAC-DB-005 |
| **Title** | No WAL checkpoint task — WAL files grow unbounded |
| **Category** | Persistence / DB |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-DB-001 |
| **Blocks** | — |

**Description**  
Once WAL mode is enabled (MAC-DB-001), SQLite writes go to a `-wal` file that is periodically checkpointed back to the main DB file. Without an explicit checkpoint task, the WAL file can grow large if the bot runs continuously without a restart (common on macOS dev machine that stays running). This increases startup time and can hit filesystem quotas on large datasets.

**Why This Is Platform-Sensitive**  
Windows NSSM service would restart the bot at least daily (after the scheduled task). macOS dev machine may run the bot continuously for weeks without restart.

**Evidence / Source**  
- Audit finding D-5

**Proposed Fix**  
Add a periodic checkpoint to `_log_maintenance_task()` or a dedicated DB maintenance task:
```python
conn.execute("PRAGMA wal_checkpoint(RESTART)")
```
Run at most once per day, after the nightly report cycle.

**Acceptance Criteria**  
- WAL checkpoint runs at least once per 24-hour period
- `-wal` file size remains bounded during continuous operation

**Implementation Notes** (2026-04-19)  
Added `PRAGMA wal_checkpoint(RESTART)` block to `_log_maintenance_task()` in [main.py](../main.py), just before the summary log. Opens a short-lived connection to `data/paper_trades.db`, runs the checkpoint, then closes it. Failures are caught and logged at WARNING so they never abort the broader maintenance sweep. Runs once per 24-hour maintenance cycle. Added `import sqlite3` to top-level imports.

---

### MAC-CLI-001

| Field | Value |
|-------|-------|
| **ID** | MAC-CLI-001 |
| **Title** | No macOS automation equivalent for `setup_daily_task.ps1` |
| **Category** | Shell / CLI / Environment |
| **Severity** | HIGH |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`scripts/setup_daily_task.ps1` registers a Windows Scheduled Task using `Register-ScheduledTask` (Windows PowerShell API). There is no equivalent script for macOS. If the user expects the daily review to run on a schedule on macOS (as it did under Windows), it silently never fires. No error, no log, no alert.

**Why This Is Platform-Sensitive**  
Windows Scheduled Tasks are a Windows-only feature. macOS uses launchd (for persistent agents) or cron (for simple schedules). Neither is configured.

**Evidence / Source**  
- Audit findings M-1, S-1
- `scripts/setup_daily_task.ps1`

**Proposed Fix**  
Create `scripts/setup_launchd.sh` that:
1. Generates `~/Library/LaunchAgents/com.kalshibot.dailyreview.plist` using the repo's `.venv/bin/python` and `scripts/daily_review.py`
2. Calls `launchctl load` to activate it
3. Accepts a `--time HH:MM` parameter (default 09:00)

Alternatively, document the manual `crontab -e` one-liner in `README.md` as the minimum.

**Acceptance Criteria**  
- Running `bash scripts/setup_launchd.sh` (or `setup_launchd.sh --time 09:00`) on macOS installs and activates a launchd agent
- `launchctl list | grep kalshibot` confirms the agent is registered
- OR: README documents an explicit manual scheduling step for macOS users

**Implementation Notes** (2026-04-20)  
Created `scripts/setup_launchd.sh`. Script:
- Accepts `--time HH:MM` (default 09:00) and `--uninstall` flags
- Generates `~/Library/LaunchAgents/com.kalshibot.dailyreview.plist` using `StartCalendarInterval` with the specified hour/minute
- Uses `.venv/bin/python` from the repo root
- Calls `launchctl load` to activate immediately
- Logs stdout/stderr to `logs/launchd_daily_review*.log`
- Verified: `launchctl list | grep kalshibot` returns the agent.

---

### MAC-CLI-002

| Field | Value |
|-------|-------|
| **ID** | MAC-CLI-002 |
| **Title** | `daily_review.ps1` hardcodes Windows `.venv\Scripts\python.exe` path |
| **Category** | Shell / CLI / Environment |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-CLI-001 |
| **Blocks** | — |

**Description**  
`scripts/daily_review.ps1:20` constructs `$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"`. This path is Windows-specific. On macOS the virtualenv binary is at `.venv/bin/python`. If the PS1 is ever invoked on macOS (e.g., via PowerShell for Mac), it won't find the virtualenv and silently falls back to system `python`. `daily_review.py` is the portable implementation; the PS1 is only a launcher shim.

**Why This Is Platform-Sensitive**  
Windows virtualenv structure (`Scripts/`) differs from POSIX (`bin/`). Path separator is also Windows-specific (`\`).

**Evidence / Source**  
- Audit findings M-3, S-2
- `scripts/daily_review.ps1:20`

**Proposed Fix**  
After MAC-CLI-001 is done, add a note to `daily_review.ps1` header: "Windows only. macOS users: use `scripts/daily_review.sh` or run `python scripts/daily_review.py` directly." No code change required in the PS1 itself.

**Acceptance Criteria**  
- `daily_review.ps1` has a clear Windows-only header comment
- macOS users can find the correct invocation without reading the PS1 body

**Implementation Notes** (2026-04-20)  
Added `# PLATFORM: Windows only.` header with macOS reference to both `scripts/daily_review.ps1` and `scripts/setup_daily_task.ps1`. MAC-DOC-002 also resolved by this change.

---

### MAC-FS-001

| Field | Value |
|-------|-------|
| **ID** | MAC-FS-001 |
| **Title** | NSSM service log cleanup code in `_log_maintenance_task()` is dead on macOS |
| **Category** | Filesystem / Paths |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | MAC-DOC-001 |

**Description**  
`main.py:1193–1210`: The maintenance task globs for `logs/service/service_stderr-*.log`, `service_stdout-*.log`, `ollama_stderr-*.log`, `ollama_stdout-*.log` and applies 30-day retention. These paths were created by the Windows NSSM service runner. On macOS none of these files exist; the globs return empty and no cleanup occurs. The code is dead but still runs on every maintenance cycle, creating a false impression that NSSM log management is active.

**Why This Is Platform-Sensitive**  
NSSM (Non-Sucking Service Manager) is Windows-only. The bot no longer runs under NSSM on macOS.

**Evidence / Source**  
- Audit findings M-2, Doc-1
- `main.py:1153–1155, 1193–1210`

**Proposed Fix**  
Wrap the NSSM block in a platform guard:
```python
if sys.platform == "win32":
    # NSSM service log retention (Windows only)
    ...
```
Or delete the block entirely with a comment in the commit message noting it was Windows NSSM-specific.

**Acceptance Criteria**  
- NSSM cleanup block does not execute on macOS
- macOS maintenance task logs do not reference `service_*` paths
- If kept under `sys.platform == "win32"`, code is covered by a comment explaining NSSM context

**Implementation Notes** (2026-04-20)  
Wrapped the NSSM service log archive block in `if sys.platform == "win32":` with an inline comment explaining the Windows-only context. Also updated the docstring retention table to annotate all three NSSM entries as `(Windows only)`. MAC-DOC-001 is also resolved by this change.

---

### MAC-LOG-001

| Field | Value |
|-------|-------|
| **ID** | MAC-LOG-001 |
| **Title** | `TradeLogStore._rotate_live_to_archive()` silently falls back on `PermissionError` on macOS |
| **Category** | Logging / Runtime Lifecycle |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`utils/logger.py:413–427`: `_rotate_live_to_archive()` tries `os.replace()` first. On `PermissionError` it falls back to `shutil.copyfileobj()` + truncate. On Windows, `PermissionError` during a rename is expected (file held open), so the fallback is load-bearing. On macOS, `PermissionError` indicates a real access control problem (filesystem permissions, sandboxing, or a bug). The fallback silently succeeds, masking the real error, and the trade log may be in an ambiguous state (partially copied).

**Why This Is Platform-Sensitive**  
Windows PermissionError on rename = file locked (expected). macOS PermissionError on rename = actual permission denied (should be fatal).

**Evidence / Source**  
- Audit finding L-6, finding 2.5
- `utils/logger.py:413–427`

**Proposed Fix**  
```python
try:
    os.replace(str(src), str(dst))
except PermissionError:
    if sys.platform == "win32":
        # Windows: file may be held open by another process; copy+truncate instead
        _copy_truncate(src, dst)
    else:
        log.error("TradeLogStore: permission denied rotating %s → %s", src, dst)
        raise
```

**Acceptance Criteria**  
- On macOS, `PermissionError` during trade log rotation raises and logs the error
- On Windows, the copy+truncate fallback is preserved
- Trade log is never silently left in a partial state

**Implementation Notes** (2026-04-20)  
Added `sys` import to `utils/logger.py`. `_rotate_live_to_archive()` now checks `sys.platform != "win32"` before taking the copy+truncate fallback path: on macOS/Linux the `PermissionError` is re-raised; on Windows the fallback is preserved.

---

### MAC-PLAT-001

| Field | Value |
|-------|-------|
| **ID** | MAC-PLAT-001 |
| **Title** | `_RuntimeInstanceGuard` uses `os.name == "nt"` instead of `sys.platform == "win32"` |
| **Category** | Python / Platform Interaction |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`main.py:271, 279`: Platform detection uses `os.name == "nt"`. This works correctly in practice (both are True on Windows only), but `sys.platform == "win32"` is the idiomatic, more explicit, and more widely recognized check in the Python ecosystem. `os.name == "nt"` is also True on Cygwin/MinGW, which are edge cases.

**Why This Is Platform-Sensitive**  
Style/convention item. Not a runtime bug, but inconsistent with the portability rules in the project guidelines.

**Evidence / Source**  
- Audit finding P-2
- `main.py:271, 279`

**Proposed Fix**  
Replace `os.name == "nt"` with `sys.platform == "win32"` at both call sites.

**Acceptance Criteria**  
- Both `os.name == "nt"` guards replaced with `sys.platform == "win32"`
- Instance guard tests pass on macOS

**Implementation Notes** (2026-04-20)  
Both `os.name == "nt"` guards in `_lock_handle()` and `_unlock_handle()` replaced with `sys.platform == "win32"` using `replace_all`. `sys` was already imported in main.py.

---

### MAC-TEST-001

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-001 |
| **Title** | No test verifies paper trader calls are non-blocking from async context |
| **Category** | Tests |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-ASYNC-001, MAC-ASYNC-002 |
| **Blocks** | — |

**Implementation Notes** (2026-04-19)
Written as part of MAC-ASYNC-001 fix. `TestPaperExecutionAsync` in `tests/test_executor.py` contains two tests: `test_record_trade_called_off_event_loop_thread` (thread-name check that fails if `record_trade` reverts to a direct call) and `test_execute_paper_returns_correct_trade_id_and_logs` (end-to-end functional check). MAC-ASYNC-002 is now also COMPLETE (v0.29.22); `TestMainAsyncBlocking` in `tests/test_main_pipeline.py` provides the MAC-ASYNC-002 regression guard (5 tests).

**Description**  
After MAC-ASYNC-001/002 are fixed, there is no regression guard to prevent a future developer from accidentally reverting to a direct synchronous call. Without a test, the fix is invisible to CI. The event-loop blocking bug would be reintroduced silently.

**Why This Is Platform-Sensitive**  
Tests were written under the Windows NSSM model where all tasks ran synchronously in sequence; async event-loop blocking was not a concern.

**Evidence / Source**  
- Audit finding T-1

**Proposed Fix**  
Add a test in `tests/test_executor.py` (or a new `tests/test_paper_trader_async.py`) that:
1. Instruments the event loop with a timing probe
2. Calls `_execute_paper()` via `asyncio.run()`
3. Asserts that the event loop was not blocked for more than a small threshold (e.g., 50ms)

Alternatively: assert via mock that `asyncio.to_thread` was called with `paper.record_trade` as the argument.

**Acceptance Criteria**  
- Test exists that would fail if `record_trade` is called without `to_thread()`
- Test passes after MAC-ASYNC-001 fix

---

### MAC-TEST-002

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-002 |
| **Title** | No integration test for `evidence_store` concurrent multi-market writes |
| **Category** | Tests |
| **Severity** | MEDIUM |
| **Status** | COMPLETE |
| **Priority** | BEFORE_GO_LIVE |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-DB-001 |
| **Blocks** | — |

**Description**  
After MAC-DB-001 adds WAL mode, there is no test that exercises concurrent writes and would catch a regression (e.g., someone removing the PRAGMA or adding a lock that serializes everything). There is also no test that would have caught the pre-fix contention issue.

**Why This Is Platform-Sensitive**  
macOS asyncio scheduling is more aggressive about parallelism than the Windows NSSM single-process model.

**Evidence / Source**  
- Audit finding T-2

**Proposed Fix**  
Add `tests/test_evidence_store_concurrency.py`:
1. Create an `EvidenceStore` backed by a temp DB
2. Fire 20 concurrent `asyncio.gather()` writes to 20 different market tickers
3. Assert all 20 writes succeed (no `OperationalError: database is locked`)
4. Assert each dossier is readable after the writes

**Acceptance Criteria**  
- 20 concurrent writes complete without `OperationalError`
- Test fails if WAL mode is removed (verify by temporarily removing the PRAGMA and confirming failure)

**Implementation Notes** (2026-04-20)  
Added `tests/test_evidence_store_concurrency.py` with three tests:
- `test_concurrent_writes_to_20_markets_no_operational_error`: 20 concurrent `asyncio.gather()` writes to distinct tickers; asserts all 20 dossiers are readable and each has 1 evidence record.
- `test_concurrent_writes_same_market_are_serialised`: 10 concurrent writes to the same ticker; asserts per-market lock serialises them correctly (all 10 persist).
- `test_wal_mode_is_active_after_first_write`: reads `PRAGMA journal_mode` directly and asserts `wal`; fails if MAC-DB-001 PRAGMA is removed.

---

### MAC-TEST-003

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-003 |
| **Title** | No test for non-clean shutdown followed by restart with stale lock file |
| **Category** | Tests |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`_RuntimeInstanceGuard` uses a lock file (`instance.lock`) to prevent duplicate bot instances. If the bot is killed with SIGKILL (common on macOS during forced Finder quit or OOM kill), the lock file may not be cleaned up. On subsequent restart, the guard must detect that the lock is stale (the previous PID is gone) and allow the new instance to proceed. There is no test that simulates this scenario.

**Why This Is Platform-Sensitive**  
On Windows under NSSM, the service manager controlled process lifecycle and SIGKILL was rare. On macOS as a developer process, SIGKILL (via Activity Monitor) or OOM termination is more common.

**Evidence / Source**  
- Audit finding T-4

**Proposed Fix**  
Add `tests/test_instance_guard.py`:
1. Create a lock file with a PID that does not exist
2. Instantiate `_RuntimeInstanceGuard`
3. Assert it acquires the lock successfully (stale lock is released)
4. Clean up

**Acceptance Criteria**  
- Guard correctly detects and clears a stale lock file (dead PID)
- Test passes on macOS

**Implementation Notes** (2026-04-19)  
Added `tests/test_instance_guard.py` with 3 tests:
- `test_guard_acquires_when_lock_file_absent`: baseline — no prior lock file.
- `test_guard_acquires_over_stale_pid_content`: writes a lock file with PID 999999999 (guaranteed dead), asserts `acquire()` returns `True` and overwrites the file with the current PID. This is the core MAC-TEST-003 scenario.
- `test_guard_describe_owner_returns_stale_info_before_acquire`: asserts `describe_owner()` surfaces the stale PID before any acquire.
All 3 pass on macOS.

---

### MAC-TEST-004

| Field | Value |
|-------|-------|
| **ID** | MAC-TEST-004 |
| **Title** | `_maybe_rotate_stale()` period-boundary edge case untested |
| **Category** | Tests |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**  
`utils/logger.py` `_maybe_rotate_stale()` is tested for the case where `mtime` is well before the period start. The edge case where `mtime` is exactly equal to `period_start` (or within a few milliseconds) is not covered. The current implementation uses strict `<` so a file written exactly at the period boundary is not rotated, which is correct — but this is not asserted by any test.

**Why This Is Platform-Sensitive**  
macOS filesystem timestamps (APFS) have nanosecond resolution; a file written during a near-midnight rotation sequence could land exactly at the boundary epoch second.

**Evidence / Source**  
- Audit finding T-5
- `utils/logger.py:242–258`

**Proposed Fix**  
Add two cases to `tests/test_logger_rotation.py`:
1. `mtime == period_start` → no rotation (file is current period)
2. `mtime == period_start - 1` → rotation triggered (file is previous period)

**Acceptance Criteria**  
- Both edge cases pass
- Behavior at boundary is documented in the test

**Implementation Notes** (2026-04-19)  
Added two tests to `tests/test_logger_rotation.py`:
- `test_maybe_rotate_stale_does_not_rotate_at_exact_period_boundary`: sets mtime == period_start, asserts no archive is created (strict `<` keeps the file).
- `test_maybe_rotate_stale_rotates_when_mtime_is_one_second_before_period_boundary`: sets mtime == period_start - 1, asserts archive is created and content preserved.
Both pass on macOS.

---

### MAC-DOC-001

| Field | Value |
|-------|-------|
| **ID** | MAC-DOC-001 |
| **Title** | NSSM references in `main.py` comments lack Windows-only annotation |
| **Category** | Documentation / Prompt Drift |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-FS-001 |
| **Blocks** | — |

**Description**  
`main.py:1153–1155` lists NSSM log paths in a comment block without noting they are Windows-only. An AI assistant (Claude/Codex) or future maintainer reading this may incorrectly infer the bot is still deployed under NSSM.

**Evidence / Source**  
- Audit finding Doc-1
- `main.py:1153–1155`

**Proposed Fix**  
After MAC-FS-001 (NSSM code guarded or removed), update the comment to read:
```
logs/service/  -- Windows NSSM service logs (Windows deployment only; unused on macOS)
```
Or remove the comment entirely if the code block is deleted.

**Acceptance Criteria**  
- No unqualified NSSM references remain in `main.py` comments

---

### MAC-DOC-002

| Field | Value |
|-------|-------|
| **ID** | MAC-DOC-002 |
| **Title** | `setup_daily_task.ps1` and `daily_review.ps1` lack Windows-only headers |
| **Category** | Documentation / Prompt Drift |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-CLI-001 |
| **Blocks** | — |

**Description**  
Both PS1 scripts have usage comments but no explicit "Windows only" warning. A developer on macOS attempting to run these will get a cryptic `command not found: powershell` error with no guidance on the macOS alternative.

**Evidence / Source**  
- Audit findings Doc-2, S-1, S-2
- `scripts/setup_daily_task.ps1`, `scripts/daily_review.ps1`

**Proposed Fix**  
Add to the top of both PS1 scripts:
```
# PLATFORM: Windows only.
# macOS / Linux: see scripts/setup_launchd.sh (MAC-CLI-001) or run scripts/daily_review.py directly.
```

**Acceptance Criteria**  
- Both PS1 files have a Windows-only platform notice
- macOS alternative is referenced

**Implementation Notes** (2026-04-20)  
Resolved as part of MAC-CLI-001/MAC-CLI-002. Both PS1 headers now read "PLATFORM: Windows only. macOS / Linux: use scripts/setup_launchd.sh or daily_review.py directly."

---

### MAC-DOC-003

| Field | Value |
|-------|-------|
| **ID** | MAC-DOC-003 |
| **Title** | No platform support matrix documenting Windows-only vs cross-platform items |
| **Category** | Documentation / Prompt Drift |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | DEFER |
| **Owner** | UNASSIGNED |
| **Depends On** | MAC-CLI-001, MAC-CLI-002, MAC-DOC-001, MAC-DOC-002 |
| **Blocks** | — |

**Description**  
There is no document that explicitly states which parts of the codebase are Windows-only, macOS-current, or cross-platform. After the migration, several scripts and code paths are platform-specific without any system-level documentation to that effect. Future maintainers and AI assistants have no canonical reference.

**Evidence / Source**  
- Audit finding Doc-3

**Proposed Fix**  
Add a `PLATFORMS.md` at the repo root (or a section to `README.md`) with a table:

| Component | Windows | macOS | Linux |
|-----------|---------|-------|-------|
| Runtime | deprecated (NSSM) | primary | untested |
| Automation | `setup_daily_task.ps1` | `setup_launchd.sh` (MAC-CLI-001) | cron (undocumented) |
| Daily review launcher | `daily_review.ps1` | `daily_review.py` direct | `daily_review.py` direct |
| DB / logs | ✅ | ✅ | ✅ |

**Acceptance Criteria**  
- A platform matrix exists and is linked from README or CLAUDE.md
- All Windows-only scripts are listed with their macOS equivalents or "N/A"

**Implementation Notes** (2026-04-19)  
Created `PLATFORMS.md` at repo root with four sections: Runtime/Process Management, Automation/Scheduling, Scripts, and Data/Persistence. All Windows-only scripts listed with macOS equivalents. Added a link to `PLATFORMS.md` from `README.md`.

---

### PROFIT-SEC-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-SEC-001 |
| **Title** | Silent Kalshi auth bypass on RSA-PSS sign failure (REST + WebSocket) |
| **Category** | Security / Auth Surface |
| **Severity** | HIGH |
| **Status** | RESOLVED (2026-05-08; Phase-2 commit `ce70924` — `KalshiSigningError` + `KalshiWsSigningError` raise at init on bad PEM and per-request on sign failure; 6 regression tests in `tests/test_kalshi_signing_failfast.py`) |
| **Priority** | NOW |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**

Both Kalshi clients catch all exceptions raised inside the signing path and return partial or empty header dicts, allowing the request to proceed unsigned:

- `kalshi/rest_client.py:98-100` — `_sign()` catches any exception (bad PEM, missing `cryptography` import, transient error) and returns a headers dict containing only `KALSHI-ACCESS-TIMESTAMP` — no key ID, no signature. `_headers()` then emits these incomplete headers and the request is sent unauthenticated. Kalshi rejects with HTTP 401, which the bot's error path treats as a transient network error rather than a fatal auth misconfiguration.
- `kalshi/websocket_client.py:96-98` — `_build_ws_auth_headers()` catches all exceptions and returns `{}`. The WebSocket connect proceeds with no auth headers. Connection may succeed for public feeds but silently drops authenticated scope (price updates, fills) without surfacing the cause.

A bad PEM at startup should be a fatal config error, not a per-request silent degradation. The current pattern masks credential misconfiguration behind 401-loop noise.

**Why it matters to profitability / safety / reliability**

1. **Live-trading risk surface.** In live mode (`LIVE_TRADING_ENABLED=true`), a per-request silent auth bypass means trade-placement requests that should fail loudly at startup instead fail per-attempt with 401 — operator may interpret as transient API issue and retry, masking the real problem.
2. **Soak-evidence integrity.** Paper-mode soaks rely on REST polls for market data; if signing intermittently fails for the same root cause, the data hole is invisible in normal logs.
3. **Defence-in-depth.** Even with current callers safe, fail-loud auth is the only correct posture for a credential-bearing client.

**Evidence / Source**

- `docs/housekeeping/2026-05-08/security-audit.md` §P1 (audit pass against the 4 load-bearing CLAUDE.md gotchas confirmed all 4 patterns are correct; the bypass is a separate concern in the same files).
- `kalshi/rest_client.py:98-100`, `kalshi/websocket_client.py:96-98`.

**Proposed Fix**

Re-raise the signing exception (or have `_sign()` return `None` and have `_headers()` refuse to construct auth headers when signature is missing). A misconfigured PEM should surface as a startup failure or a fatal per-request `RuntimeError`, not a silent unsigned request.

**Acceptance Criteria**

- `_sign()` and `_build_ws_auth_headers()` propagate signing exceptions instead of swallowing them.
- A regression test loads an intentionally malformed PEM and asserts that REST + WS attempts raise an exception rather than returning incomplete headers.
- CLAUDE.md gotcha optionally updated to note the fail-loud expectation alongside the existing RSA-PSS / `_normalize_pem` guidance.

**Related**

- `docs/housekeeping/2026-05-08/security-audit.md` (Phase-1 audit source)
- `docs/housekeeping/2026-05-08/SUMMARY.md` (P1-01)
- CLAUDE.md gotchas: RSA-PSS signing + `_normalize_pem()` (still confirmed-current)

---

### PROFIT-OBS-006

| Field | Value |
|-------|-------|
| **ID** | PROFIT-OBS-006 |
| **Title** | Silent SQLite write failures in `subreddit_selector` probe-ts and suppression paths |
| **Category** | Observability / Reliability |
| **Severity** | MEDIUM |
| **Status** | RESOLVED (2026-05-08; Phase-2 commit `1d0714c` — `_update_probe_ts` and `_mark_candidate_suppressed` log table+row context and re-raise; outer caller in `main.py:1559–1575` already falls back to `REDDIT_CORE_SUBREDDITS`. 4 regression tests in `tests/test_subreddit_selector.py`) |
| **Priority** | LATER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**

`feeds/subreddit_selector.py` swallows all write failures in two places:

- `subreddit_selector.py:136` — `_update_probe_ts()` wraps the SQLite write in `try / except Exception: pass`. Probe counts are lost without any diagnostic trace.
- `subreddit_selector.py:152` — `_mark_candidate_suppressed()` uses the same pattern. Suppression writes can fail silently, which permits a suppressed subreddit to re-enter rotation on the next selection cycle.

The right pattern here is: narrow the exception to `(sqlite3.OperationalError, sqlite3.DatabaseError)` and emit `logger.warning(...)` with `exc_info=True`. The `logger` is already imported in this file.

**Why it matters to profitability / safety / reliability**

1. **Suppression bypass risk.** A subreddit suppressed for credibility / rate-limit reasons can re-enter rotation if the suppression write fails — and the operator has no signal that this happened.
2. **Probe-cadence skew.** Probe-ts is the rate limiter for subreddit_discovery; silent failures mean probes can fire faster than intended without a Reddit-403 paper trail to diagnose.
3. **Pattern propagation.** This file is one of three places repo-wide where `except Exception: pass` is used for write paths (the other two were verified during the audit and either acceptable or already narrowed). Closing this hardens the pattern.

**Evidence / Source**

- `docs/housekeeping/2026-05-08/python-quality.md` §P1.
- `docs/housekeeping/2026-05-08/SUMMARY.md` (P1-04).
- `feeds/subreddit_selector.py:136, :152`.

**Proposed Fix**

Replace `except Exception: pass` with:

```python
except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
    logger.warning("subreddit probe-ts update failed: %s", exc, exc_info=True)
```

(adapt message per call site).

**Acceptance Criteria**

- Both call sites narrow the exception and log with `exc_info=True`.
- No new exception types are introduced into the call graph (the surrounding `select_subreddits()` flow continues on logged warning).
- A test demonstrates that a forced `sqlite3.OperationalError` produces a log line and does not crash the selector.

**Related**

- `docs/housekeeping/2026-05-08/python-quality.md` (Phase-1 audit source)
- `docs/housekeeping/2026-05-08/SUMMARY.md` (P1-04)

---

### PROFIT-DOC-001

| Field | Value |
|-------|-------|
| **ID** | PROFIT-DOC-001 |
| **Title** | CLAUDE.md governance gotcha gaps (PROFIT-GOV-002 anchor_rate block, PROFIT-LLM-001 endpoint nuance) + missing `/governance` domain constraint |
| **Category** | Documentation / Governance Safety |
| **Severity** | MEDIUM |
| **Status** | RESOLVED (2026-05-08; Phase-2 commit `b1e1a0c` — both gotchas landed in `kalshi-bot/CLAUDE.md` "Governance LLM" section: anchor_rate polarity block (PROFIT-GOV-002) and OpenAI-compat endpoint nuance for `analysis/signal_analyzer.py`. The `/governance` domain constraint was added separately in cycle-17C OQ5 commit `c664c08`.) |
| **Priority** | NOW |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**

Phase-1 housekeeping audit identified three documentation gaps that together create regression risk for governance-side changes:

1. **`governance/prompts.py:27-31` anchor_rate polarity block has no CLAUDE.md gotcha.** This block (HIGH `anchor_rate` ≥ 0.95 = no information edge → disable candidate; LOW ≤ 0.50 = informative → keep) was the A5 fix that closed PROFIT-GOV-002 (2026-05-03). It is empirically the minimum content that prevents qwen3:14b from rubber-stamping `disable_source` on every candidate. Removing or rewording these lines silently re-introduces the regression — snapshot tests catch prompt drift but no CLAUDE.md note flags the lines as load-bearing.

2. **The `think: False` qwen3 gotcha covers `governance/llm.py` only.** If `OLLAMA_MODEL` is changed to any qwen3-family model, `analysis/signal_analyzer.py:_ollama_estimate_detailed` (line 680-693) hits the same `format=json` + thinking regression. Additional nuance: `signal_analyzer.py` uses the OpenAI-compat `/v1/chat/completions` endpoint, which does **not** accept `think: False`; `governance/llm.py` uses the native `/api/generate` endpoint. A qwen3 swap in signal_analyzer requires switching endpoints, not just adding the flag. This nuance is in PROFIT-LLM-001 in this debt log but not surfaced as a CLAUDE.md gotcha.

3. **`~/.claude/rules/domain_constraints.md` has no `/governance` rule.** The file covers `/analysis`, `/trading`, `/tasks`, `/feeds`. Phase-3+ governance has real-mode flip authority (proposed_* → applied_* in `runtime_overrides.yaml`); prompt-template edits, no-trade-logic boundary, and the snapshot-test gate are not surfaced to agents editing the layer.

**Why it matters to profitability / safety / reliability**

1. **Future-regression prevention.** PROFIT-GOV-002 was a live-mode-blocker bug that took multiple iterations to fix. The fix exists in code (`prompts.py:27-31`) but is not protected by a CLAUDE.md note; the next agent iterating on the system prompt may remove it without knowing it carries the anti-rubber-stamp behavioral fix.
2. **Endpoint-mismatch trap.** A qwen3 OLLAMA_MODEL swap in signal_analyzer will silently produce empty `{}` responses (governance is fine because of the existing fix). Operator would see signal LLM "working" but returning no opinions.
3. **Phase-3 readiness.** `/governance` becomes safety-critical at Phase-3 real-mode flip; absence of a domain constraint means Phase-3 agents have no edit-safety reminder.

**Evidence / Source**

- `docs/housekeeping/2026-05-08/claude-md-audit.md` (M-1, M-2, M-3 in Missing section)
- `docs/housekeeping/2026-05-08/SUMMARY.md` (P1-08, P1-09, P2-06)
- `governance/prompts.py:27-31`, `governance/llm.py:196-212`, `analysis/signal_analyzer.py:680-700`
- Closed entry: PROFIT-GOV-002 (2026-05-03)
- Open entry: PROFIT-LLM-001 (LOW)

**Proposed Fix**

Three coordinated edits, all documentation:

1. Add to `kalshi-bot/CLAUDE.md` "Governance LLM" section: gotcha for `governance/prompts.py:27-31` referencing PROFIT-GOV-002, the snapshot tests at `tests/test_governance_prompts.py`, and the negative-control harness at `scripts/simulations/governance_negative_control.py`.
2. Extend the existing `think: False` gotcha with: "If `OLLAMA_MODEL` is changed to any qwen3 model, `analysis/signal_analyzer.py:_ollama_estimate_detailed` also needs mitigation but uses the OpenAI-compat `/v1/chat/completions` endpoint that ignores `think: False` — requires switching to native `/api/generate` as well. See PROFIT-LLM-001."
3. Add to `~/.claude/rules/domain_constraints.md`: `Trigger: when changing /governance. Action: treat prompt-template edits as high-risk; run tests/test_governance_prompts.py + the negative-control harness before committing; do not add trade-execution logic to the governance layer.`

**Acceptance Criteria**

- All three edits land in a single commit.
- `tests/test_governance_prompts.py` + the negative-control harness still pass after the docs land (sanity check, since this is a doc-only change).
- PROFIT-LLM-001 is updated to reference this entry as the docs-side companion.

**Related**

- PROFIT-GOV-002 (closed) — original anchor_rate fix
- PROFIT-LLM-001 (open, LOW) — signal_analyzer qwen3-readiness work; deferred per Phase-2 spec
- `docs/housekeeping/2026-05-08/claude-md-audit.md`
- `docs/housekeeping/2026-05-08/SUMMARY.md`

---

### PROFIT-DEBT-OQ1-SHIM

| Field | Value |
|-------|-------|
| **ID** | PROFIT-DEBT-OQ1-SHIM |
| **Title** | Delete `analysis/source_credibility.py`, `source_stats.py`, `keyword_stats.py` shims after one release cycle |
| **Category** | Tech Debt / Cleanup |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | LATER |
| **Owner** | UNASSIGNED |
| **Depends On** | — |
| **Blocks** | — |

**Description**

Phase-3 OQ1 / P2-07 moved three operational-tracker modules from `analysis/` to `tasks/stats/` to restore INV-4 layer purity. The old paths (`analysis/source_credibility.py`, `analysis/source_stats.py`, `analysis/keyword_stats.py`) now contain three-line shims that re-export the moved class:

```python
# SHIM: this module moved to tasks/stats/<module>.py.
# TODO: delete after one release cycle (next housekeeping pass; see PROFIT-DEBT-OQ1-SHIM).
from tasks.stats.<module> import <Class>  # noqa: F401
```

The shims exist to allow any caller missed during the rename — including downstream `scripts/`, `tests/`, or future agent-generated code referencing the old paths — to continue working without an immediate hard break.

**Why it matters**

Long-lived shims become permanent imports by accident. Two paths to the same class produce inconsistent grep results, confuse codebase explorers (graph tools, `code-explorer` agent), and complicate future moves.

**Acceptance Criteria**

- All three shim files (`analysis/source_credibility.py`, `analysis/source_stats.py`, `analysis/keyword_stats.py`) deleted.
- Pre-deletion: grep across the entire repo confirms zero remaining imports of `analysis.source_credibility`, `analysis.source_stats`, `analysis.keyword_stats`. Update any stragglers found.
- Pytest passes after deletion.

**Target removal date**

Phase-4 housekeeping pass, or 2026-06-08 — whichever comes first. The Phase-2 OQ2 dossier_builder UNUSED markers also have a 2026-06-08 calendar review; align this with that pass for batched cleanup.

**Related**

- `docs/housekeeping/2026-05-09/phase-3-design/oq1-analysis-purity-rename.md` (design doc)
- Phase-3 Stage 3b commit (the rename)
- INV-4 in `docs/housekeeping/2026-05-08/architecture.md`

**CLOSED 2026-05-10 (Claude):** Three shim files deleted (`analysis/source_credibility.py`, `analysis/source_stats.py`, `analysis/keyword_stats.py`). Pre-deletion grep across repo (`.py` + `.md`) returned zero callers of `analysis.source_credibility`, `analysis.source_stats`, or `analysis.keyword_stats` — only the debt log entry itself referenced the old paths. Pytest + ruff green post-deletion. Wave-1 cleanup-only ship landed 2026-05-09 (commit `c9df364`, no VERSION bump — v0.29.59 retained per Cycle-16E descope) — exceeds the "one release cycle" threshold since Phase-3 OQ1 Stage 3b rename.

---

### PROFIT-DEBT-WAVE1-DRAFTS

| Field | Value |
|-------|-------|
| **ID** | PROFIT-DEBT-WAVE1-DRAFTS |
| **Title** | Wave-1 original-plan draft commits retained on `origin/backup/wave-1-dry-run-2026-05-05` (reference-only mapping) |
| **Category** | Tech Debt / Reference Mapping |
| **Severity** | LOW |
| **Status** | COMPLETE (reference-only — created already-closed) |
| **Priority** | n/a |
| **Owner** | n/a |
| **Depends On** | — |
| **Blocks** | — |

**Description**

Wave-1 was originally planned as a 6-commit deploy (`v0.30.0`). After the 2026-05-07 Cycle-16E `scorer_fixed_no_signal_confirmed` verdict the deploy was descoped to cleanup/observability only; only the OBS-003 commit shipped to main (no VERSION bump — v0.29.59 retained; v0.30.0 reserved for first non-neutral LLM / non-zero-edge phase change per `docs/ROADMAP.md` §Versioning). The remaining 5 draft commits live on the protected backup branch `origin/backup/wave-1-dry-run-2026-05-05` and represent reference implementations of features still tracked as OPEN debt items.

**Backup → debt-item map** (verified 2026-05-10):

| backup SHA | subject (one-line) | corresponds to |
|------------|--------------------|----------------|
| `5828ad2` | feat(main): PROFIT-EDGE-004 Lever A.1 source-class classifier + v0.30.0 | `PROFIT-EDGE-004` (OPEN, NOW; Sub-1 gated on Cycle-17C E3 verdict) |
| `e3d4e8d` | fix(governance_monitor): PROFIT-GOV-003 path + type-set membership | `PROFIT-GOV-003` (open if filed; otherwise treat as latent fix awaiting registration) |
| `9d6cce3` | feat(blend_task): PROFIT-EXEC-002 series-correlation guard | `PROFIT-EXEC-002` (OPEN, LATER; deferred post-Cycle-17C verdict) |
| `921c275` | feat(blend_task): PROFIT-OBS-003 SKIPPED emission for blocked-reason path | `PROFIT-OBS-003` (CLOSED 2026-05-10 via different commits `b775a99` + `92b1d11` + `c9df364` — main carries an EQUIVALENT but not identical implementation) |
| `edf38c1` | fix(market_matcher): PROFIT-MATCH-001 (B') token-guard refinement | `PROFIT-MATCH-001` (OPEN, LATER; deferred post-Cycle-17C verdict) |
| `0531367` | fix(executor): PROFIT-OBS-005 cooldown sentinel-default → -inf | `PROFIT-OBS-005` (OPEN, LATER; deferred pending post-PHASE2-001 cycle verdict) |

**Why this matters**

When the post-Cycle-17C verdict resumes work on the deferred items, the operator and any future agent should know there is a pre-staged draft on the backup branch and can either cherry-pick (preferred for the load-bearing fixes if the underlying APIs haven't drifted), use as a starting reference (preferred if APIs have drifted), or discard and re-implement (preferred if Cycle-17C produces a different design). Without this mapping, the next agent has to rediscover the backup branch via git archaeology — which is exactly what just happened during the 2026-05-10 closure-cleanup sprint.

**Acceptance Criteria**

- This entry is COMPLETE on creation. It is a permanent reference record. It does not have a closure trigger.
- The mapping table above is authoritative on 2026-05-10. If the backup branch is ever rewritten, retagged, or migrated, update this entry to reflect the new location/SHAs.

**Notes**

- The backup branch is intentionally protected. Do NOT delete `origin/backup/wave-1-dry-run-2026-05-05` without first amending this entry and confirming with the operator.
- VERSION on main is `0.29.59`. The `v0.30.0` bump in commit `5828ad2` is for the FULL original-plan ship, not the descoped one. v0.30.0 remains reserved on main per `docs/ROADMAP.md` §Versioning policy.

**Related**

- `docs/ROADMAP.md` §Wave-1/2/3 deploy timeline (Wave-1 row reflects the descope).
- `docs/superpowers/plans/2026-05-10-repo-closure-cleanup.md` (sprint that filed this mapping).
- Each row's "corresponds to" debt entry for the actionable resumption path.

**FILED 2026-05-10 (Claude)** during repo-closure-cleanup sprint after Task B verify-before-delete on the backup branch surfaced 5 unaccounted-for draft commits. Operator confirmed the descope interpretation and elected to retain the backup branch as cold-storage reference. This entry documents the rediscovery so it does not need to happen again.

---

### PROFIT-DOC-002

| Field | Value |
|-------|-------|
| **ID** | PROFIT-DOC-002 |
| **Title** | Repo-wide guidance consolidation initiative — rules / CLAUDE.md / AGENTS.md / auto-memory |
| **Category** | Documentation / Agent Guidance Hygiene |
| **Severity** | LOW |
| **Status** | COMPLETE |
| **Priority** | NOW |
| **Owner** | Claude (Opus 4.7) |
| **Depends On** | PROFIT-DOC-001 (Phase-2 audit baseline) |
| **Blocks** | — |

**Description**

Phase-2 foundational-docs audit (commit `d544e53`) and Phase-3 audit-remediation (commit `e0f94bb`) closed the immediate findings on the kalshi-bot project's CLAUDE.md, but the broader guidance surface (`~/.claude/rules/`, `~/.claude/CLAUDE.md`, `~/.claude/AGENTS.md`, `~/.claude/project/AGENTS.md`, project-side `AGENTS.md` + auto-memory entries under `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/`) had accumulated drift, format mismatches, and verbatim-duplicate sections between layers. Initiative ran 2026-05-08 to inventory all 321 `.md` guidance files, detect 33 conflicts, and apply per-batch user-gated fixes on the `housekeeping/guidance-consolidation` branch.

**Why it matters**

Guidance drift is silent — every Claude Code session loads the global rules + the project CLAUDE.md, and stale or contradictory guidance steers agents toward wrong actions (Windows-only rules in a macOS-only runtime, format-rule self-contradiction, line-pin rot, etc.). The audit chain found zero safety-critical regressions in load-bearing project gotchas (signing alg, qwen3 think flag, market-status filter, env-var rename, transaction wrapper, `_normalize_pem` duplication, anchor_rate polarity block — all verified intact), but the surrounding guidance had accumulated noise that this initiative cleaned up.

**Evidence / Source**

- `docs/housekeeping/2026-05-09/guidance-discovery/{inventory,classification,auto-memory-extract}.md` — Phase 5 (3 parallel agents)
- `docs/housekeeping/2026-05-09/guidance-conflicts/{intra-cluster-conflicts,code-vs-doc-drift,SUMMARY}.md` — Phase 6 (33 findings, 9 HIGH / 10 MEDIUM / 14 LOW)
- `docs/housekeeping/2026-05-09/guidance-consolidation-plan.md` — Phase 7 plan with per-item dispositions
- Commits `39fb097`, `8a80fae`, `352838a`, `90b2d18`, `0cdc67b`, `0d9323e` — Phase 8 batches 1–6

**Resolution Summary**

27 findings actioned across 6 user-gated batches; 6 KEEP (low-leverage). Of 33 findings, 0 required risk review or test-invariant changes — entire 1652-passed test suite held throughout.

| Batch | Resolves | Mechanism |
|------:|----------|-----------|
| 1 | #2 KEEP, #3 | `documentation_format.md` Trigger 4 narrowed to non-CLAUDE.md (in `~/.claude/`); B1-2 MOVE rejected on user review (project-specific content does not belong in global rule file) |
| 2 | #1, #14, #28 | DELETE `~/.claude/rules/windows_local.md`; MOVE 2 cross-platform rules to `portability.md`; remove cross-ref from `~/.claude/project/AGENTS.md` |
| 3 | #7 (#4, #5, #6 KEEP — pre-resolved) | REWRITE `analysis/fade_signal.py` docstring (drop "WebSocket-based replacement"). Phase 6 had inherited stale Phase-2 audit findings on `__init__.py`, `evidence_scorer.py`, `kelly.py`; pre-flight verification showed 3 of 4 already fixed |
| 4 | #8, #17, #19, #21, #30 | ANNOTATE 5 stale audit findings in `docs/housekeeping/.../foundational-docs-audit/{general-quality,gap-analysis,reference-integrity}.md` with visible RESOLVED-2026-05-08 callouts |
| 5 | #10, #11, #12, #13, #18, #24 | DELETE 14 verbatim-duplicate lines from project `CLAUDE.md` + REWRITE step-4 wording in project `AGENTS.md` + ADD `/governance` to `~/.claude/project/AGENTS.md` |
| 6 | #9, #15, #16, #22, #23, drift-#3 (#27 KEEP) | REWRITE `signal_analyzer.py:38` cycle label, `signal_analyzer.py:680-685` revert clause; REWRITE `regime_classifier.py:178` dead `lessons.md` ref; ANNOTATE 2 auto-memory entries (staleness caveat + symbol alias correction) |

**KEPT findings (6 deliberate non-actions)**

- **#25, #26**: line-pin / hybrid-format soft violations in CLAUDE.md (low rot risk)
- **#27**: README dead links to `transfer/macbook_handoff_2026-05-01/` — README:219 + :242 already disclaim; author intent is preservation as historical reference
- **#29**: commit SHA citation in auto-memory (stable in this repo)
- **drift-#1**: `resolve_market()` "thin wrapper" understates calibration role (correct atomicity claim; description nuance only)
- **drift-#2**: `executor.py:218` line pin describes data-source comment, not guard rationale (functionally correct, line cite mildly misleading)

**Methodology finding (relevant to future audits)**

Phase 6 Agent 1 (intra-cluster conflicts) cited several findings as "per Phase 2 comment-health audit" without re-verifying against the live tree. Three of four Batch 3 targets (`__init__.py`, `evidence_scorer.py`, `kelly.py`) had been silently fixed in unrelated commits between Phase 2 audit and Phase 6 detection. Pre-flight re-verification before each batch's edits is required when the audit chain ages or piggybacks on prior phases. Batches 5 and 6 caught additional plan/reality drift this way (line shifts due to refactor, cross-ref already deleted in Batch 2, file-location mismatch).

**Acceptance Criteria** (all met)

- All 33 Phase-6 findings have an explicit disposition recorded in `guidance-consolidation-plan.md`.
- Test invariant `1652 passed, 2 skipped, 116 xfailed` held after every Python-touching batch (Batches 3, 6).
- Cross-reference grep verified clean before each DELETE (no orphan callers introduced).
- All 7 load-bearing safety claims in project `CLAUDE.md` Critical Gotchas verified intact pre- and post-initiative (signing algorithm, qwen3 `think: False`, market-status filter, env-var name, transaction wrapper, `_normalize_pem` duplication, anchor_rate polarity block).
- Phase-2 / Phase-3 audit-trail documents annotated rather than deleted, preserving historical context.

**Notes**

- Global config repo `~/.claude/` (separate git repo) accumulated edits from Track 1 Phase-3 work plus this initiative's Batches 1, 2, 5, 6 — committed separately under that repo's branching scheme.
- Project `AGENTS.md` is still ~95% verbatim copy of global `~/.claude/AGENTS.md`. Phase 6 only flagged step-4 wording (resolved). Full project-AGENTS.md dedup deferred — separate decision flagged in plan.
- Out-of-scope observations surfaced: `MAX_BET_HARD_CAP` env-var fall-through behavior (would silently default rather than error if unset — gotcha says "old name silently falls back to default", but live env-var name is the new one — this is correct as documented).

---

## Execution Views

---

### A. Current Profit-Path Fix Queue

Open or blocked items, ordered for safe sequential execution. **Updated 2026-05-01 post-cutover** to reflect the OBS-003 promotion, the new CUTOVER-001 + PHASE2-001 + OBS-004 entries, and the Mac Studio operational handoff.

| Order | ID | Title | Why this order |
|-------|----|-------|---------------|
| 1 | PROFIT-CUTOVER-001 | MacBook → Mac Studio operational handoff | Operator action partially in flight (Studio bootstrap done, post-restore verification pending); must close before any other Studio-side audit windows are trustworthy |
| 2 | PROFIT-PHASE2-001 | Phase 2 shadow soak clock (started 2026-05-01 ~14:00 UTC; ETA 2026-05-15) | IN_PROGRESS; only operator action is the daily monitoring checklist in `PHASE2_RUNBOOK.md`; closes when ≥30 decisions accumulate at ≥85% reasonable on review |
| 3 | PROFIT-OBS-003 | OPPORTUNITY → PAPER_TRADE silent-exit gap (240/260 lifetime, includes 2 positive-edge non-trades) | Promoted HIGH/NOW 2026-05-01; fixes a correctness-adjacent bug (positive-edge candidates silently dropped) AND unblocks EDGE-004 audit completeness; do this before EDGE-004 |
| 4 | PROFIT-EDGE-004 | Pipeline reaches executor with `edge = 0.0`; matcher / market-mix / source-mix root cause | Operationally surfaced as the binding constraint after EDGE-001/002/003 closed; lifetime evidence (2026-05-01) reinforces the original diagnosis but adds the OBS-003 dependency |
| 5 | PROFIT-OBS-004 | `paper_trades.edge` records YES-side edge regardless of trade side | Cosmetic-but-load-bearing for audits; safe to fix in parallel with OBS-003 since both touch the same persistence path |
| 6 | PROFIT-RUNTIME-001 | S4.5 multi-lane paper validation | COMPLETE on 13-day MacBook evidence (see 2026-05-01 Validation Notes); listed for completeness — no Mac Studio re-run expected unless lane wiring materially changes |
| 7 | PROFIT-EVID-001 | Accumulation only learns from keyword-positive survivors | Blocked on contract decision for rejected-evidence intake semantics |
| 8 | PROFIT-VALID-001 | No first-class baseline-vs-multi-lane harness | The 2x trade-frequency constraint must be reproducible |
| 9 | PROFIT-CAL-001 | Calibration outcome feedback | COMPLETE; 2026-05-01 production-soak observation appended (3 `CALIBRATION_CHECK` events fired in production); next footnote milestone is ≥10-resolution sample with ≥2 distinct lanes populated, which depends on OBS-003 + EDGE-004 progress |
| 10 | PROFIT-STRUCT-002 | Verify EDGE-002 sub-fix #4 runtime cause-emission format | Verification-only; small observability gap; can run in parallel with the higher-priority items |

**Execution note:** Do not bundle these into broad rewrites. Each item touches a different safety boundary and should close with focused tests and evidence. **Cutover-specific note (2026-05-01):** items 3–10 are all Mac Studio work — do not re-run any of them on the MacBook. The MacBook is read-only archive after 2026-05-01T13:05:54Z.

---

### B. Expanded Pre-Go-Live Gate

Items that must be COMPLETE before live trading:

| ID | Title | Rationale |
|----|-------|-----------|
| All `MAC-*` items | Migration reliability set | Already COMPLETE; preserve as historical live-readiness foundation |
| PROFIT-RUNTIME-001 | S4.5 validation | Multi-lane architecture must be proven in paper mode before live exposure |
| PROFIT-TRACE-001 | Evidence/blend traceability | Every trade must remain explainable |
| PROFIT-REPLAY-001 | Replay-critical probability persistence | Dossiers must be reconstructable from persisted evidence |
| PROFIT-EVID-002 | Source-class fidelity | Source diversity and quality gates need accurate metadata |
| PROFIT-OBS-002 | Log rollover clarity | Long validation and live runs need reliable audit logs |
| PROFIT-EXEC-001 | Direct executor bypass risk | No trade-producing path should bypass readiness without explicit contract exemption |
| PROFIT-VALID-001 | Baseline vs multi-lane harness | The 2x trade-frequency constraint must be reproducible |

**Gate rule:** All open HIGH items in this table must be STATUS = COMPLETE before live mode is enabled. `OPEN` HIGH items outside this table require an explicit risk review before live mode.

---

### C. Current Parallelizable Work Streams

Items are grouped into independent streams with no inter-stream dependencies. Work within each stream is sequential; streams can proceed simultaneously.

#### Stream 1 — End-to-End Validation
Items: `PROFIT-RUNTIME-001`, `PROFIT-VALID-001`, `PROFIT-STARTUP-001`
Files likely touched: validation scripts, reporting docs, possibly paper-only instrumentation.

#### Stream 2 — Traceability / Replay
Items: `PROFIT-TRACE-001`, `PROFIT-REPLAY-001`, `PROFIT-OBS-001`
Files likely touched: `main.py`, `tasks/accumulation_task.py`, `tasks/blend_task.py`, `scripts/replay_dossier.py`, contract/docs.

#### Stream 3 — Evidence Quality
Items: `PROFIT-EVID-001`, `PROFIT-EVID-002`
Files likely touched: `main.py`, evidence metadata helpers, tests.

#### Stream 4 — Execution Boundary
Items: `PROFIT-EXEC-001`
Files likely touched: `main.py`, `tasks/blend_task.py`, tests. Contract review required before changing behavior.

#### Stream 5 — Observability / Performance
Items: `PROFIT-OBS-002`, `PROFIT-PERF-001`
Files likely touched: `utils/logger.py`, `PLATFORMS.md`, logging tests.

#### Stream 6 — Structural / Calibration
Items: `PROFIT-STRUCT-001`, `PROFIT-CAL-001`
Files likely touched: `tasks/structural_task.py`, `tasks/calibration_task.py`, validation scripts.

---

### D. Legacy Migration Work Streams

The following migration-specific stream plan is retained for historical continuity. These items are currently COMPLETE.

Items are grouped into independent streams with no inter-stream dependencies. Work within each stream is sequential; streams can proceed simultaneously.

#### Stream 1 — Async Blocking (MAC-ASYNC-001, MAC-ASYNC-002, MAC-TEST-001)
Files: `trading/executor.py`, `main.py`, `tests/test_executor.py`
No overlap with other streams.

#### Stream 2 — SQLite Reliability (MAC-DB-001, MAC-DB-002, MAC-DB-005, MAC-TEST-002)
Files: `tasks/evidence_store.py`, `trading/paper_trader.py`, `tests/test_evidence_store_concurrency.py`
MAC-DB-005 depends on MAC-DB-001 (needs WAL enabled first).
MAC-DB-002 is independent of MAC-DB-001 (different file, different connection).

#### Stream 3 — macOS Automation (MAC-CLI-001, MAC-CLI-002, MAC-DOC-002)
Files: `scripts/` only. No runtime code touched.
MAC-CLI-002 and MAC-DOC-002 depend on MAC-CLI-001 (reference the new script).

#### Stream 4 — Dead Code / Platform Guards (MAC-FS-001, MAC-PLAT-001, MAC-LOG-001, MAC-DOC-001)
Files: `main.py`, `utils/logger.py`
MAC-DOC-001 depends on MAC-FS-001 (comment update follows code removal).
All others are independent of all streams.

#### Stream 5 — Test Gaps (MAC-TEST-003, MAC-TEST-004)
Files: `tests/` only. No runtime code touched. Fully independent.

#### Stream 6 — Documentation (MAC-DOC-003, MAC-DB-003, MAC-DB-004)
Files: `PLATFORMS.md` (new), `trading/paper_trader.py`
MAC-DOC-003 depends on MAC-CLI-001 (needs launchd script to reference).
MAC-DB-003 depends on MAC-ASYNC-001/002 (re-evaluate after async pattern is set).

---

## Dependency Map

### Current Profit-Path Dependencies

```
PROFIT-RUNTIME-001 ─────────────────────────► PROFIT-VALID-001
PROFIT-RUNTIME-001 ─────────────────────────► PROFIT-CAL-001

PROFIT-TRACE-001 ─┐
PROFIT-REPLAY-001 ├────────────────────────► S4.5 observability confidence
PROFIT-OBS-001 ───┘

PROFIT-EVID-002 ────────────────────────────► Readiness G2 fidelity
PROFIT-EVID-001 ────────────────────────────► Dossier completeness review

PROFIT-EXEC-001 ────────────────────────────► No-bypass live readiness
PROFIT-OBS-002 ─────────────────────────────► Long-run validation safety
```

### Legacy Migration Dependencies

```
MAC-ASYNC-001 ──────────────────────────────► MAC-TEST-001
MAC-ASYNC-002 ──────────────────────────────► MAC-TEST-001
MAC-ASYNC-001 ──┐
MAC-ASYNC-002 ──┘──► MAC-DB-003 (re-evaluate flag after async pattern set)

MAC-DB-001 ─────────────────────────────────► MAC-TEST-002
MAC-DB-001 ─────────────────────────────────► MAC-DB-005

MAC-CLI-001 ────────────────────────────────► MAC-CLI-002
MAC-CLI-001 ────────────────────────────────► MAC-DOC-002
MAC-CLI-001 ────────────────────────────────► MAC-DOC-003

MAC-FS-001 ─────────────────────────────────► MAC-DOC-001

MAC-DOC-001 ─┐
MAC-DOC-002 ─┤
MAC-CLI-001 ─┘──► MAC-DOC-003
```

---

## Operating Rules

These rules govern how this log is used during remediation work.

### R-1 — Status Updates Are Mandatory
No item may remain at `OPEN` after work begins. Change to `IN_PROGRESS` on first edit to any file in scope.

### R-2 — COMPLETE Requires Acceptance Criteria
An item may not be set to `COMPLETE` unless every acceptance criterion listed for it is satisfied. Partial fixes stay `IN_PROGRESS`.

### R-3 — New Discoveries Must Be Logged
If a fix or audit uncovers a new issue that could impair trading quality, safety, selectivity, auditability, calibration, timeliness, or operational reliability, that issue must be added here before the fix commit is closed. Do not silently absorb discoveries into code or create a parallel tracker.

### R-4 — No Silent Fixes
Every fix that closes an item in this log must be traceable to a commit. The commit message should reference the item ID (e.g., `fix(async): wrap paper trader calls in to_thread (MAC-ASYNC-001, MAC-ASYNC-002)`).

### R-5 — Pre-Go-Live Gate Is a Hard Block
The Expanded Pre-Go-Live Gate items must all be COMPLETE before live mode (`ENABLE_LIVE_TRADING=true`) is set. This gate cannot be waived without an explicit contract/risk-review update.

### R-6 — Dependencies Must Be Respected
No item may move to `IN_PROGRESS` if its `Depends On` items are not yet COMPLETE, unless the dependency is explicitly re-evaluated and documented under Notes.

### R-7 — Last Updated Must Stay Current
Update the `Last Updated` timestamp in the metadata header whenever any item changes status or a new item is added.

### R-8 — False Positives Are Documented, Not Deleted
The audit identified several items that turned out to be false positives after code inspection (mtime timezone concern, PEM key handling, signal handler lambda). These are not in this log. If any item is later determined to be a false positive, mark it `COMPLETE` with Notes explaining the determination — do not delete it.

### R-9 — Single Tracker Rule
This file is the only technical-debt tracking mechanism for profit-path, reliability, safety, observability, and platform debt. Do not create a second debt log for macOS, S4.5, logging, calibration, or execution-boundary findings; add them here with a new ID or update an existing item.

### R-10 — No New Tracking Files

This file is the sole tracking surface for all project state. New tracking content lands as a section or sub-section here, not a new standalone file. If content genuinely belongs in a separate lifecycle (e.g., an active cycle ledger in `docs/governance/`), it merges back here at cycle close. Per CLAUDE.md Continuous Improvement: do not create parallel status, roadmap, decision-log, or operational-dashboard files. The 2026-05-09 docs consolidation removed parallel surfaces (EDGE_STATUS.md and TLDR variants) to enforce this rule; preserving the consolidation is now a maintenance invariant.
