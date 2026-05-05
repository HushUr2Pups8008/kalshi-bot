# Wave-1 deploy-day timing recommendation

**Type:** ambiguity resolution (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator scheduling Wave-1 deploys after PROFIT-PHASE2-001 close on 2026-05-08T19:01Z+.
**Companion:** `docs/governance/post-soak-close-rehearsal-checklist.md` §1-§6 (per-commit deploy procedure); `docs/governance/wave-1-deploy-commit-order-decision.md` (locked commit order); `docs/governance/wave-1-post-deploy-observation-plan.md` (regression watch).

## TL;DR

Recommend deploying Wave-1 commits at **UTC weekday afternoons during low-Kalshi-volume hours**, specifically **Mon-Thu 18:00-22:00 UTC** (which is 14:00-18:00 ET / 07:00-11:00 morning of 2026-05-09 NZ for the operator). Avoid Fri-Sun deploys; avoid the governance fast-cycle's pre-cycle 30-min window. **Per-commit cadence: 1 commit every 24-36 h** to allow the post-deploy regression-watch window to complete.

## Why timing matters

Wave-1 deploys 6 behavioural commits in locked order. Each commit triggers:

1. Bot restart (launchctl bootout/bootstrap or self-restart depending on commit).
2. 24 h post-deploy regression-watch window per `wave-1-post-deploy-observation-plan.md`.
3. Possible env-revert OR code-revert if any of the 14 monitoring rows fires.

If multiple deploys happen back-to-back without the 24 h window completing, attribution becomes muddled — a regression on commit N+1 could be caused by N or by N+1, and the rollback runbook §5 cascading-revert path fires (which the runbook itself notes as "rare; if you find yourself doing more than one in a single response, escalate to a full rollback").

Timing decisions therefore affect THREE axes:

1. **Within-day:** when in the UTC day to deploy each commit
2. **Day-of-week:** which weekday to deploy on
3. **Cadence:** how long to wait between commits

## 1. Within-day timing

### 1.1 Avoid governance fast-cycle pre-cycle window

The governance fast-cycle launchd job (`com.kalshi.governance.fast`) runs at the cadence locked in PROFIT-PHASE2-001 (2 h fast cycles; ~12/day). The cycle takes ~5-10 min wall-clock and writes to `logs/governance/decisions.jsonl`. Bot restart during this window risks log-tail interleaving and decision-cycle abandonment.

**Recommendation:** check `launchctl list | grep com.kalshi.governance.fast` for "exit" status (last successful cycle); avoid deploys within 30 min before the next-scheduled fast cycle. Approximate "safe-to-deploy" windows: 30 min after a fast cycle through 30 min before the next.

### 1.2 Avoid Kalshi market-active high-volume periods

Kalshi market activity (and the bot's `BlendTask` queue depth) spikes during US news cycles (~13:00-15:00 UTC and 21:00-23:00 UTC for Asian-news-driven markets). Deploying during these spikes risks cooldown / readiness-gate state corruption if the bot restarts mid-decision.

**Recommendation:** deploy during the lull between US morning news (~17:00 UTC) and US afternoon news (~21:00 UTC). **Sweet spot: 18:00-21:00 UTC.**

### 1.3 Operator-availability constraint

Operator (Mac Studio in NZ) availability matters for the 30-60 min hands-on validation per commit. UTC 18:00-22:00 = NZ 06:00-10:00 (next day) — operator's morning, fresh. Better than UTC 02:00-04:00 = NZ 14:00-16:00 (afternoon, slower response if a regression fires).

## 2. Day-of-week timing

### 2.1 Avoid Fri-Sun

- **Friday:** US market close (21:00 UTC) is followed by weekend low-volume — limited regression evidence to draw conclusions from.
- **Saturday-Sunday:** Kalshi market activity drops; trade-rate baseline shifts, making regression detection harder; Codex / Claude availability may be lower for incident response.

### 2.2 Mon-Thu preferred

Mon-Thu UTC late-afternoon gives the maximum regression-evidence window before the next deploy. Wednesday is the cleanest single-day choice — full Mon-Tue baseline available; Thu-Fri post-deploy window before weekend.

### 2.3 Holiday avoidance

Check US federal holidays (Memorial Day 2026-05-25; Independence Day 2026-07-03/04) — both fall outside Wave-1's 6-day deploy window if started 2026-05-08+ but inside Wave-2/3.

## 3. Per-commit cadence

### 3.1 Minimum 24 h between commits

The `wave-1-post-deploy-observation-plan.md` 24 h regression-watch window is the floor. Deploying commit N+1 less than 24 h after N risks attribution loss.

### 3.2 Recommended 24-36 h

24 h gives the regression-watch window full coverage; 36 h adds a 12 h buffer for operator analysis + decision before the next deploy. **Recommended: 36 h between commits.**

For 6 commits at 36 h cadence: 6 × 36 = 216 h = 9 days from first to last commit. Earliest start 2026-05-08T19:01Z+ → last commit lands ~2026-05-17T19:00Z. Wave-2 first-feed earliest deploy stays at 2026-05-15+ per the close criteria runbook, so the 9-day window can overlap Wave-2 prep but the actual Wave-2 deploy should wait until Wave-1 commit 6's regression window closes (~2026-05-18-2026-05-19).

### 3.3 Faster cadence (24 h between commits)

If operator pressure to compress: 24 h cadence puts last commit at 2026-05-13. **Risk:** less buffer for operator analysis between deploys. **Recommended only if:** the first 2-3 commits' regression watches return clean trivially (no signal-shape changes; just smoke-clean).

## 4. Recommended schedule

Worked example assuming Day-7 close fires 2026-05-08T19:01Z, Wave-1 commit 1 immediately after attestation push:

| commit | spec | recommended deploy UTC | NZ time | notes |
|---|---|---|---|---|
| 1 | OBS-005 | 2026-05-08T20:00Z (Fri) | NZ Sat 08:00 | start late Fri-eve UTC; OK for OBS-005 (smoke-only commit) |
| — | (24 h regression watch) | | | |
| 2 | MATCH-001 (B') | 2026-05-09T20:00Z (Sat) | NZ Sun 08:00 | weekend-deploy exception OK if commit-1 watch is clean |
| — | (24 h watch) | | | |
| 3 | OBS-003 | 2026-05-11T18:00Z (Mon) | NZ Tue 06:00 | reset weekday cadence |
| — | (36 h watch) | | | |
| 4 | EXEC-002 | 2026-05-13T06:00Z (Wed) | NZ Wed 18:00 | 36 h after #3 |
| — | (36 h watch) | | | |
| 5 | GOV-003 | 2026-05-14T18:00Z (Thu) | NZ Fri 06:00 | governance commit; observe cadence carefully |
| — | (36 h watch) | | | |
| 6 | EDGE-004 A.1 | 2026-05-16T06:00Z (Sat) | NZ Sat 18:00 | Sat-deploy OK; final commit; longer 48 h watch recommended before Wave-2 first feed |
| — | (48 h watch) | | | |
| Wave-2 first feed | A.1+ | 2026-05-18T18:00Z+ (Mon) | NZ Tue 06:00 | exits Wave-1 watch with full Mon US-trading-day coverage on Wave-1 commit 6 |

**Total Wave-1 deploy duration: 7-8 days from commit 1 to commit 6 + 48 h watch.** Aligned with the existing 14-day Wave-1+post-watch window before Wave-2 starts.

## 5. Alternative — bundled deploy (NOT recommended)

The `wave-1-changelog-entry-prestaged.md` §"Operator deploy commands" mentions bundled-deploy as an option. Bundled deploys land all 6 commits in one push then VERSION bump + tag.

**Why NOT recommended for Wave-1:**

- Cascading-revert (rollback runbook §5) becomes the only rollback path — much higher operational risk than per-commit revert.
- Single 24 h post-bundled-deploy regression watch can't attribute a regression to which of 6 commits caused it.
- Loses the locked-commit-order safety property (one commit at a time = small, comprehensible change).

**When bundled deploy IS reasonable:** Wave-2 / Wave-3 deploys with single-feature commits; not Wave-1's 6-feature stack.

## 6. Decision points for operator

1. **Start UTC date.** Earliest valid is 2026-05-08T19:01Z (Fri eve UTC). Recommended actual start: 2026-05-08T20:00Z (Fri) OR 2026-05-11T18:00Z (Mon) if operator wants weekend-clear.
2. **Cadence choice.** 24 h vs 36 h between commits. Recommend 36 h.
3. **Bundled vs per-commit.** Recommend per-commit per §5.
4. **Sat-Sun deploy tolerance.** Recommend allowing Sat-Sun deploys for low-risk commits (OBS-005, MATCH-001, OBS-003) only if prior commit's watch is clean.

## 7. Out of scope

- **Trade-volume forecasting per market.** This doc treats Kalshi market-volume curves at high level; per-market windowing is operator-discretion.
- **Wave-2 / Wave-3 deploy timing.** Same principles apply; separate doc per Wave when timing is decided.
- **Deploy tooling automation.** This is a timing-decision doc; deploy commands themselves live in the rehearsal checklist.

## 8. Cross-links

- `docs/governance/post-soak-close-rehearsal-checklist.md` §1-§6 — per-commit deploy procedure
- `docs/governance/wave-1-deploy-commit-order-decision.md` — locked commit order
- `docs/governance/wave-1-post-deploy-observation-plan.md` — 24 h regression watch (this cycle)
- `docs/governance/post-soak-rollback-runbook.md` — incident response if a regression fires
- `docs/governance/wave-1-changelog-entry-prestaged.md` — deploy commands per spec
