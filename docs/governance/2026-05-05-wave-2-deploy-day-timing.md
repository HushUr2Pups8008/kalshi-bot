# Wave-2 deploy-day timing recommendation

**Type:** ambiguity resolution (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator scheduling Wave-2 (A.1+ Branch A observation; Branch C legal-analyst onboard) at ≥ 2026-05-15 post-Wave-1 stabilisation.
**Companion:** `docs/governance/2026-05-05-wave-1-deploy-day-timing.md` (Wave-1 timing); `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` (Branch C feed selection).

## TL;DR

Wave-2 has **two deploy events** with very different timing profiles:

1. **Branch A (passive observe) — NO DEPLOY.** Triggers at end of Wave-1 stabilisation (≥ 2026-05-18 per Wave-1 timing). Just operator-flag observation start; 14-day passive window.
2. **Branch C (open-RSS legal-analyst) — code deploy** if Branch A returns 0 legal-niche PAPER_TRADE. Earliest fire: 2026-06-01. Deploy window same recipe as Wave-1: **UTC Mon-Thu 18:00-22:00**, **24 h post-deploy regression watch**.

If operator pursues parallelism per the wave-2 branch decision table §"Time-pressure compression": Branch A (passive) + Branch C (active) run concurrently with the same start trigger. **Recommended: serial sequence A → C** for attribution clarity.

## Branch A (passive observe) — operator action only

No code change. Operator action at 2026-05-18 (~48 h post-Wave-1 commit 6 deploy):

```bash
# Branch A start — log the start of the 14-day observation window
git tag -a a1plus-branch-a-start-${UTC_DATE} -m "A.1+ Branch A passive observe window starts: 14-day legal-niche PAPER_TRADE watch"

# Document in profit_path_debt_log
# Document the date in PROFIT-EDGE-004 entry: "Branch A observation window 2026-05-18 → 2026-06-01"
```

**Window:** 14 d. **Acceptance criterion:** ≥ 1 legal-niche PAPER_TRADE (per Wave-2 branch decision table). **No deploy timing applies** — passive observation has no deploy event.

## Branch C (open-RSS legal-analyst) — code deploy

Fires only if Branch A returns 0 legal-niche PAPER_TRADE in 14 d.

### Earliest fire date

- Wave-1 commit 6 deploys ≥ 2026-05-16 (per Wave-1 timing recommendation).
- Wave-1 post-deploy 48 h watch: ≥ 2026-05-18.
- Branch A starts: 2026-05-18.
- Branch A 14 d window ends: 2026-06-01.
- Branch A verdict + Branch C deploy decision: 2026-06-01 to 2026-06-02.
- **Branch C deploy: ≥ 2026-06-02.**

### Within-day timing (same as Wave-1)

UTC Mon-Thu 18:00-22:00. Avoid:
- Governance fast-cycle pre-cycle 30-min window
- Kalshi market high-volume periods (US news cycles ~13:00-15:00 UTC and ~21:00-23:00 UTC)
- US federal holidays (Memorial Day 2026-05-25 — irrelevant to Branch C 2026-06-02+ window; check independence Day 2026-07-03/04 if Branch C delays)

### Per-commit cadence

Branch C is a **single-commit feature** (1-2 RSS feed URLs + `_source_class_for_evidence` token-list expansion + xfail marker removal). No multi-commit cadence concern.

### Post-deploy regression watch

24 h watch per `wave-1-post-deploy-observation-plan.md` shape, with Branch-C-specific monitoring rows:

| signal | source | regression trigger |
|---|---|---|
| new feed RSS-poll succeeds | `feeds/rss_monitor.py` log | 0 successful polls of new URL in 24 h |
| classifier buckets new source | `EVIDENCE_INGESTION.source_class` | new feed evidence buckets to `other` (post-A.1 token expansion failed) |
| no signal-rate explosion | `OPPORTUNITY` count from new feed | new feed OPPORTUNITY > 5/day in 24 h (under-suppression) |
| no calibration drift | `CALIBRATION_CHECK` | per-lane error > 1.5× pre-Branch-C baseline |

### 14-day acceptance window

After 24 h smoke watch passes, full 14 d window observes whether ≥ 1 PAPER_TRADE materializes. Verdicts:

- ≥ 1 PAPER_TRADE w/ positive realized P&L → EDGE-004 closes via Branch C
- 0 PAPER_TRADE OR negative realized P&L → Branch D fires per Lever-D escalation criteria spec §2

## option-A (geopolitics specialist) — parallel-discretion deploy

`option-A` (per Wave-2 branch decision table; nomenclature cleaned 2026-05-05) is the geopolitics specialist-analyst feed onboard. Operator-discretion to deploy in parallel with Branch A or as a Branch C alternative.

**Recommended:** defer until A → C sequence concludes. If Branch C succeeds, EDGE-004 closes; option-A's archive evidence (0/18 historical PAPER_TRADE) suggests low expected lift.

If deployed: same timing recipe as Branch C (UTC Mon-Thu 18:00-22:00; 24 h watch + 14 d window). Earliest deploy date if parallel with Branch A: 2026-05-18 (same as Branch A start).

## Operator decision points

1. **Serial vs parallel.** Recommend serial Branch A → Branch C; parallelism risks attribution confusion.
2. **Branch C feed selection.** Per `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md`: Just Security primary + Lawfare secondary. Operator confirms at fire-time.
3. **VERSION bump.** Branch C deploy lands as v0.31.0 (Wave-2 minor bump per `wave-1-changelog-entry-prestaged.md` versioning rule). Confirm CHANGELOG drift check (this cycle's task T8) is clean before bump.
4. **option-A fire condition.** Defer unless Branch C also stalls AND operator wants exhaustion-evidence before Branch D.

## Out of scope

- **Branch C feed selection RSS-probe protocol.** Covered in feed-selection rubric.
- **Branch D timing.** Branch D = handoff, not a deploy event; covered by Lever-D escalation criteria spec.
- **Concurrent Wave-3 deploys.** Wave-3 (Lever B + Lever C) is sequential after Wave-2 stalls; covered in `2026-05-05-wave-3-deploy-day-timing.md` (this cycle).

## Cross-links

- `docs/governance/2026-05-05-wave-1-deploy-day-timing.md` — Wave-1 timing analog
- `docs/governance/2026-05-05-wave-3-deploy-day-timing.md` — Wave-3 timing (this cycle)
- `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — Wave-2 sequence + acceptance criteria (post nomenclature cleanup)
- `docs/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` — Branch C feed selection
- `docs/governance/wave-1-post-deploy-observation-plan.md` — observation plan template (Branch C deploy uses Wave-2-specific monitoring rows derived from this template)
- `docs/_archive/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` — Branch C parent spec (ARCHIVED Stream G R32)
- `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D handoff (ARCHIVED Stream G R35)
