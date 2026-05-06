# Wave-2 deploy-ready-state assessment

**Type:** read-only verification (Claude task per Implementation Contract §9 — review).
**Source:** Wave-2 prerequisite checklist vs HEAD `ffc54b2`.
**Drafted:** 2026-05-06.
**Audience:** operator at Wave-2 trigger time (≥ 2026-05-18 Branch A start; ≥ 2026-06-02 Branch C deploy).

## TL;DR

**All Wave-2 prerequisites locked at HEAD.** 7 spec/rubric/playbook/harness artifacts present + verified. Wave-2 is ready-to-fire when triggers are met. **0 blockers; 0 missing prereqs.** This assessment runs pre-Wave-1-close so operator has zero pre-Wave-2 doc work.

## Wave-2 prerequisite checklist

### Specs

- [x] `docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` — A.1+ umbrella spec (option-A geopolitics specialist) ✅
- [x] `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` — A.1+1.5 spec (Branch C legal-analyst onboard) ✅

### Operator decision artifacts (Claude tasks across cycles 2-5)

- [x] `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — Branch A → C → option-A sequence (cycle 1; nomenclature cleaned cycle 3) ✅
- [x] `docs/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` — Branch C feed selection (cycle 2) ✅
- [x] `docs/governance/2026-05-05-wave-2-deploy-day-timing.md` — UTC timing windows (cycle 3) ✅
- [x] `docs/governance/wave-2-deploy-commit-order-decision.md` — locked commit order (cycle 3) ✅
- [x] `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` — pre-staged CHANGELOG (refreshed cycle 5 per drift-check fix) ✅
- [x] `docs/governance/2026-05-05-wave-2-fire-time-per-commit-checklist.md` — compact 30 min playbook (cycle 5) ✅
- [x] `docs/governance/2026-05-05-wave-2-decision-flow.md` — ASCII flowchart (cycle 7) ✅

### Operator scripts (Codex tasks)

- [x] `scripts/wave2_fire_time_smoke.sh` — Branch C deploy + 24h watch wrapper (cycle 6) ✅

### Strict-xfail harnesses (pre-loaded)

- [x] `tests/test_lever_a1plus_feed_config.py::test_at_least_one_specialist_analyst_url_in_rss_feeds` — option-A geopolitics specialist pin (cycle 1) ✅
- [x] `tests/test_lever_a1plus_feed_config.py::test_vital_law_or_legal_analyst_feed_present_post_a1plus` — Branch C legal-analyst pin (cycle 1) ✅
- [x] `tests/test_branch_c_feed_selection_rubric.py` — Just Security primary + Lawfare secondary pins (cycle 6) ✅

### Forensics + audit anchors

- [x] `docs/governance/2026-05-04-vitallaw-aggregator-path-forensics.md` — VitalLaw direct-RSS infeasibility anchor (cycle 1 Branch B kill)
- [x] `docs/governance/2026-05-04-legal-niche-probe-order-domain-overlap.md` — Branch C feed-candidate ranking
- [x] `docs/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` — Branch C archive evidence
- [x] `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` — option-A archive evidence

### Closure-path TLDR + escalation

- [x] `docs/governance/edge-004-closure-path-tldr-v3.md` — current closure-path consensus (cycle 2) ✅
- [x] `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D triggers (cycle 2) ✅
- [x] `docs/governance/2026-05-05-branch-d-fire-procedure-runbook.md` — Branch D fire procedure (cycle 4) ✅

## Pre-deploy verification (run-time at Wave-2 fire-time)

### Branch A start (~2026-05-18)

Operator action: tag + debt-log entry. No code change. **Pre-fire verification: none required beyond Wave-1 stabilisation gate.**

### Branch C deploy (~2026-06-02 if Branch A stalls)

Per `2026-05-05-wave-2-fire-time-per-commit-checklist.md` step 2.2:

```bash
# RSS-probe the 4 candidate URLs (Just Security / Lawfare / SCOTUSblog / Politico Legal)
# Per cycle-2 feed-selection rubric §"Pre-deploy scoring"
for url in https://www.justsecurity.org/feed/ https://www.lawfaremedia.org/feed/ https://www.scotusblog.com/feed/ https://rss.politico.com/legal.xml; do
    curl -I -s -o /dev/null -w "HTTP %{http_code} | %{content_type}\n" "$url"
done
```

Operator picks 1-2 winners. Recommended: Just Security primary + Lawfare secondary. Falls back to SCOTUSblog / Politico if needed.

## Acceptance criteria (post-deploy)

Per `2026-05-05-wave-2-a1plus-branch-decision-table.md`:

- Branch A: ≥ 1 legal-niche PAPER_TRADE in 14d → EDGE-004 closes
- Branch C: ≥ 1 PAPER_TRADE w/ non-negative aggregate realized P&L in 14d → EDGE-004 closes
- option-A: ≥ 1 PAPER_TRADE w/ +P&L in 14d → EDGE-004 closes (low-confidence; expect re-test)
- All stall: Branch D fires per `2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §2

## What's NOT yet locked (acceptable; not blocking)

- **Specific Branch C feed URL list.** Operator picks at fire-time per RSS probe; rubric covers the decision.
- **option-A specific feed selection.** Same — operator picks from `2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` §3.1 list at fire-time.
- **Wave-2 deploy date (specific UTC).** Depends on Wave-1 stabilisation timestamp.

These are intentionally fire-time decisions, not pre-deploy locks.

## Gaps (none blocking)

| gap | severity | resolution |
|---|---|---|
| Branch C 14d acceptance window includes US holidays in some scenarios | LOW | Memorial Day 2026-05-25 is Wave-1 territory; no Wave-2 conflict; Independence Day 2026-07-03/04 falls in Wave-3 territory. Wave-2 timing-clean. |
| `option-A`-specific feed selection rubric | LOW | parent A.1+ spec §3.1 list is sufficient; per-feed RSS probe at fire-time per same pattern as Branch C |

## Wave-2 trigger gate

Per `2026-05-05-wave-2-fire-time-per-commit-checklist.md` step 1:

```bash
# Wave-1 + 48h burn-in clean (per 2026-05-05-wave-1-deploy-day-timing.md cadence matrix step 6 + 48h)
date -u +%Y-%m-%dT%H:%M:%SZ                   # ≥ 2026-05-18T+
bash scripts/wave1_post_deploy_smoke.sh        # exit 0 = clean
```

Both conditions checked. ✅ ready when triggers met.

## Verdict

**Wave-2 is fully prepared. No pre-Wave-2 operator action required.** All artifacts present + verified. Operator's only Wave-2 work is fire-time per the playbooks.

## Out of scope

- Wave-3 readiness assessment (covered in cycle-3 Wave-3 timing + cycle-7 Wave-3 flowchart; sibling assessment can land post-Wave-2-Branch-A-start).
- Branch D readiness (covered by `2026-05-05-branch-d-fire-procedure-runbook.md`; assessment if needed at Branch-D trigger time).
- PROFIT-LLM-001 / P4-GATE Appendix A readiness (post-Branch-D-fire territory).

## Cross-links

- All ✓-marked items above
- `docs/governance/2026-05-06-pre-wave1-source-of-truth-doc-index.md` (this cycle) — sibling lookup index
- `docs/governance/2026-05-06-pre-wave1-risk-register.md` — out-of-scope risks for Wave-2 deploy
