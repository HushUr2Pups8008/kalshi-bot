# Wave-3 deploy-day timing recommendation

**Type:** ambiguity resolution (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator scheduling Wave-3 (Lever B G1=0.04 + Lever C cross-series correlation guard) at ≥ 2026-06-06 post-Wave-2-stall confirmation.
**Companion:** `docs/governance/2026-05-05-wave-1-deploy-day-timing.md`; `2026-05-05-wave-2-deploy-day-timing.md`.

## TL;DR

Wave-3 deploys ONLY if Wave-2 (A.1+ Branch A → Branch C) stalls AND Branch D is NOT fired. **Earliest fire: 2026-06-06.** Two commits in sequence (Lever B then Lever C). **Recommended cadence: 14 d between deploys** (longer than Wave-1's 36 h because each Wave-3 commit has a 14 d post-deploy validation window per their respective specs).

If Branch D fires before Wave-3 starts: Wave-3 does NOT deploy. EDGE-004 escalation supersedes Wave-3 cadence.

## Trigger conditions for Wave-3

Per `edge-004-closure-path-tldr-v3.md` §"Current closure path":

```
Wave-2 A.1+ deploy results
    ├── Branch A succeeds (≥ 1 legal-niche PAPER_TRADE) → Wave-3 NOT NEEDED
    ├── Branch C succeeds (≥ 1 PAPER_TRADE w/ +P&L) → Wave-3 NOT NEEDED
    └── Branch A + Branch C both stall
            ├── Branch D fires (operator decision per Lever-D §2) → Wave-3 SKIPPED; escalate
            └── Branch D not yet fired → Wave-3 deploys (B then C)
```

Operator chooses Wave-3 vs Branch D at the Branch-C-stall verdict point. Wave-3 is the "we want more attribution data before escalating" path.

## Earliest fire date calculation

- Wave-1 commit 6 deploys ≥ 2026-05-16
- Wave-1 stabilisation: ≥ 2026-05-18
- Branch A 14 d window: 2026-05-18 → 2026-06-01
- Branch C deploy + 14 d window: 2026-06-02 → 2026-06-16 (if A stalls)
- Branch C verdict: 2026-06-16
- **Wave-3 commit 1 (Lever B) fires: ≥ 2026-06-16+ (assume same-day decision; 2026-06-17 weekday-aligned)**

If both branches A + C run AND both stall: Wave-3 starts 2026-06-16+. Realistic operator wall-clock is closer to 2026-06-20+ allowing decision-day slack.

## Wave-3 commit sequence (2 commits)

### Commit 1: Lever B G1=0.04 floor

- **File touched:** `tasks/trade_readiness_gate.py` (2-line constant edit) + `tests/test_lever_b_g1_floor_lock.py` (xfail marker removal — same hunk as constant edit per Codex's harness pre-load addendum §3).
- **Blast radius:** load-bearing readiness gate (∴ Implementation Contract §5 + §11 territory; operator manually attests gate change at deploy time).
- **Validation window:** **14 d post-deploy** per Lever B spec §6 acceptance criteria (G1 SKIPPED count drops ≥ 30 %; admitted candidates produce non-negative aggregate P&L).
- **Rollback:** code revert (no env-var per Lever B spec §7 + the rollback runbook validation finding F1).

### Commit 2: Lever C v1 cross-series headline correlation

- **Files touched:** `tasks/blend_task.py` (suppression logic per spec §2 + addendum §2 placement detail) + `config.py` (`cross_series_correlation_window_seconds` env-var) + `tests/test_lever_c_cross_series_correlation.py` (xfail marker removal in same hunk).
- **Blast radius:** new BlendTask gate, but suppression-only (INV-6 attested in cycle 2 Lever C v1 LOCK addendum §1).
- **Validation window:** **14 d post-deploy.** Acceptance: cross-series suppression count > 0 (gate fires) AND no legitimate-trade suppression false positives.
- **Rollback:** env-revert (`CROSS_SERIES_CORRELATION_WINDOW_SECONDS=0`) is fast; code revert if env-revert fails.

### Inter-commit cadence

**Recommended: 14 d between commits** = land Lever B; complete its 14 d validation; THEN land Lever C. Reason: each lever's spec acceptance window is 14 d. Co-deploying makes attribution impossible (a regression on Lever C deploy could be Lever B's lagging effect).

**Alternative: 21 d cadence** if operator wants additional buffer before stacking gate changes. Recommended IF Lever B deploy produces edge-case attribution surprises.

### Post-Wave-3 timeline

```
Wave-3 commit 1 (Lever B) fires: 2026-06-17
+ 14 d validation
Wave-3 commit 2 (Lever C) fires: 2026-07-01
+ 14 d validation
Wave-3 final verdict: 2026-07-15
```

If Wave-3 produces edge: EDGE-004 closes. If Wave-3 stalls: Branch D fires post-2026-07-15.

## Within-day timing

Same recipe as Wave-1 + Wave-2:

- UTC Mon-Thu 18:00-22:00
- Avoid governance fast-cycle pre-cycle 30-min window
- Avoid Kalshi market high-volume periods (US news cycles)
- Operator availability (NZ morning = UTC late afternoon)

## US federal holiday avoidance

Wave-3 fires within 2026-06-17 to 2026-07-15 window. Independence Day 2026-07-03/04 (Fri/Sat) falls inside this. **Recommendation:** if Wave-3 commit 2 (Lever C) lands ≥ 2026-07-01, complete it BEFORE 2026-07-02 (avoid Independence-Day-window deploy + 24 h smoke). Else delay to 2026-07-07 (Mon).

## VERSION bump

Wave-1 = 0.30.0 (per `wave-1-changelog-entry-prestaged.md`).
Wave-2 (Branch C) = 0.31.0.
Wave-3 commit 1 (Lever B) = 0.32.0 (single-feature minor bump).
Wave-3 commit 2 (Lever C) = 0.33.0.

Each VERSION bump triggers README badge sync via pre-commit hook. CHANGELOG entries pre-staged in `wave-2-wave-3-changelog-entries-prestaged.md`.

## Rollback timing

If a Wave-3 commit's regression watch triggers within 24 h: prefer fast revert per `post-soak-rollback-runbook.md` §3 (Lever C env-revert) or §4 (Lever B code-revert). Cascading-revert §5 unlikely (commits are 14 d apart; second commit's revert doesn't affect first).

## Operator decision points

1. **Wave-3 vs Branch D.** At Branch-C-stall verdict (~2026-06-16), operator chooses whether to deploy Wave-3 (more attribution data) OR fire Branch D (escalate now). Recommended: Wave-3 first; Branch D after Wave-3 stalls.
2. **14 d vs 21 d inter-commit cadence.** Recommend 14 d. Bump to 21 d if Lever B deploys produces unexpected attribution complexity.
3. **Lever B 0.03 follow-up?** If Lever B 0.04 lands cleanly with non-negative P&L but minimal attribution lift, a follow-up Lever B-2 spec for 0.03 floor could land. Outside Wave-3 scope; separate deploy decision.

## Out of scope

- Branch D handoff timing (Branch D is not a deploy event).
- Wave-4+ deploy timing (post-Branch-D-fire territory; covered if/when PROFIT-LLM-001 sizes to a deploy plan).
- Lever B-2 / Lever C-v2 follow-up timing.

## Cross-links

- `docs/governance/2026-05-05-wave-1-deploy-day-timing.md` — Wave-1 timing analog
- `docs/governance/2026-05-05-wave-2-deploy-day-timing.md` — Wave-2 timing (this cycle)
- `docs/governance/edge-004-closure-path-tldr-v3.md` — closure-path sequencing
- `docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md` — Lever B parent spec
- `docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` — Lever B 0.04 LOCK
- `docs/_archive/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` — Lever C parent spec (ARCHIVED Stream G R29)
- `docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` — Lever C v1 LOCK
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` — pre-staged CHANGELOG entries
- `docs/governance/post-soak-rollback-runbook.md` §3+§4 — rollback paths
