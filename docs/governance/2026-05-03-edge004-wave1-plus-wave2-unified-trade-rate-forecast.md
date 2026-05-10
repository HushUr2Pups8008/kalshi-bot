# EDGE-004 Wave-1 + Wave-2 unified post-deploy trade-rate forecast

**Generated:** 2026-05-03 (post-snapshot-5; reassigned Codex task #5, Codex usage exhausted)
**Scope:** Pre-deploy expected-state synthesis combining Wave-1 base stack (OBS-005 / MATCH-001 B′ / OBS-003 / EXEC-002) and Wave-2 levers (A.1 / A.1+ / B / C) into one operator-readable trajectory through 2026-06-13.
**Companion docs:**

- `docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md` — Codex's Wave-2-only ladder (commit `8001a16`)
- `docs/governance/2026-05-03-post-soak-landing-simulation.md` — Codex's Wave-1 archive-replay sizing (commit `f671468`)
- `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` — per-source ranking finding from this cycle
- `docs/governance/2026-05-03-match001-bprime-false-suppression-audit.md` — orthogonality finding from this cycle

## TL;DR

Pre-Wave-1 baseline (post-MATCH-001 closure): conversion rate 3.4 % (per `docs/governance/edge-004-closure-path-tldr.md`).

| stage | OPPORTUNITY | SKIPPED | PAPER_TRADE | conversion | net Δ vs baseline |
|---|---:|---:|---:|---:|---:|
| **Pre-Wave-1 baseline (today)** | 260 | — | 9 | 3.4 % | — |
| **Wave-1 close (Day 13)** | 87 | 89 | 1 | 1.1 % | -2.3 pp |
| **+ Lever A.1 (prerequisite, Day 13)** | 87 | 89 | 1 | 1.1 % | unchanged |
| **+ Lever A.1+ specialist analyst (Day 14+)** | uncertain | uncertain | **probably bounded** | unknown | small |
| **+ Lever B G1=0.04 (Day 28+)** | +32 admits, +1 trade | -1 (G1-admitted, post-blender) | 2 | est. 2.2 % | -1.2 pp |
| **+ Lever C cross-series hash (Day 35+)** | risk-only | adds `cross_series_headline_in_window` | unchanged | unchanged | unchanged |

**Honest read: Wave-1 base-stack landings reduce conversion volume materially (260 → 87 OPP, 9 → 1 PAPER_TRADE) by design — MATCH-001 B′ tightens the match surface and EXEC-002 suppresses high-correlation FISA-class bursts. The conversion rate drops because OPP volume drops faster than PAPER_TRADE in the simulation, not because EV-positive trades disappear.** The closure target (≥ 5 % conversion over 14 d) is therefore measured against the post-Wave-1 base, not the pre-Wave-1 baseline.

## Wave-1 base-stack expected state (Day 0 → Day 13)

Source: Codex's post-soak landing simulation (`docs/governance/2026-05-03-post-soak-landing-simulation.md`, commit `f671468`).

| stage | OPP | SKIPPED | PAPER | reasoning |
|---|---:|---:|---:|---|
| Today (pre-Wave-1) | 260 | n/a (pre-OBS-003) | 9 | 13-day Mac archive |
| OBS-005 cooldown sentinel fix | 260 | unchanged | 9 | observability-only; no behavioural change |
| MATCH-001 (B′) substring suppression | 87 | unchanged | ~1 | strict-key counterfactual: 1,076 keys suppressed; 260 → 87 OPP retained |
| OBS-003 BlendTask SKIPPED emission | 87 | +78 | ~1 | adds blocked-reason SKIPPED records (G1-G6) — no new trades |
| EXEC-002 series-correlation guard | 87 | +89 | 1 | suppresses 2 FISA paper trades correlated to series-cluster |

**Day-13 base state: 87 OPP / 89 SKIPPED / 1 PAPER_TRADE / 1.1 % conversion.**

Wave-1 conversion rate is *lower* than today because the MATCH-001 + EXEC-002 suppressions cut OPP volume by 67 % while PAPER_TRADE drops only by 89 % (9 → 1). This is the expected behaviour: the post-fix logic should produce fewer but higher-quality candidates. The post-Wave-1 base of 87 OPP / 1 PAPER is the reference for the EDGE-004 closure target.

## Wave-2 expected state (Day 13 → Day 35)

### Lever A.1 (prerequisite, Day 13)

Per Codex's archive replay (`docs/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md`, commit `8001a16`): 0/248 EVIDENCE_INGESTION rows carry source strings; OPPORTUNITY surrogate post-A.1 official stays 1/260. **No archive-replayable trade-rate lift.** Lands silently.

### Lever A.1+ first specialist-analyst feed (Day 14+)

**This is the EDGE-004 closure-path edge-producer.** Expected state is **uncertain** (not archive-replayable: the new feed produces new headlines that don't exist in the 13-day archive).

**Class-level prior** (Codex's class-level audit `2a15d55`): specialist_analyst class produced 21 OPP + 3 PAPER_TRADE on the 13-day archive — 14.3 % class-internal conversion.

**Per-source caveat** (this cycle's per-source audit, `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md`): all 3 PAPER_TRADE came from `vital_law` (legal/regulatory analysis); the geopolitics-sub-niche feeds (Kyiv X / Times of Israel / Iran International / Defense News / Breaking Defense) produced 18 OPP + 0 PAPER_TRADE. The proposed A.1+1 candidates (war on the rocks / CSIS / ISW / CFR / Atlantic Council) target the geopolitics sub-niche.

**Expected post-A.1+1 lift estimate:**

- **Optimistic** (the A.1+1 candidate sources cover an addressable headline gap that existing geopolitics feeds miss, and those new headlines convert at the existing 3.4 % pre-Wave-1 baseline rate): **+1-2 PAPER_TRADE** over 14 d. Rough enrichment: each new specialist source typically adds 5-10 OPP / 14 d at low single-digit conversion.
- **Pessimistic** (the A.1+1 candidates re-cover headlines existing geopolitics feeds already saw, and those headlines have a 0/18 conversion track record): **+0 PAPER_TRADE.** This is the "honest read" version per the per-source audit.
- **Mid-case**: **+0-1 PAPER_TRADE.** Closure target ≥ 5 % conversion on the 87-OPP base = ≥ 4.4 PAPER_TRADE (+ A.1+1 OPP delta). Probably **fails closure on a single A.1+1 deploy**; A.1+2 is required.

### Lever B G1=0.04 (Day 28+ if A/A.1+ stalls)

Per Codex's G1 admittance counterfactual: floor lowered from 0.05 → 0.04 admits 32/197 G1-killed candidates. Of those, only 1 has predicted edge ≥ 0.02 (passes paper threshold). **+1 PAPER_TRADE.**

### Lever C cross-series hash (Day 35+ if A + B stall)

Per Codex's overlap audit: 128/260 OPP records cross-series. Lever C **suppresses** correlated bursts; it does not create trades. **+0 PAPER_TRADE; -correlation risk only.**

## Per-day operator routine (during validation windows)

Per the rehearsal checklist `docs/_archive/governance/post-soak-close-rehearsal-checklist.md` §9 (ARCHIVED Stream G R54):

```bash
# Daily smoke
./scripts/bothealth.sh
git --no-pager log --oneline -10 logs/governance/decisions.jsonl
python scripts/simulations/post_obs003_skipped_attribution_audit.py \
    --since $(date -u -v-1d +%Y-%m-%d)  # post-OBS-003 only
```

Stop-conditions per `docs/_archive/governance/post-soak-rollback-runbook.md` (ARCHIVED Stream G R54).

## Gates / decision tree post-Day-13

```
                  Wave-1 base stable?
                  /              \
             yes                  no → ROLLBACK + investigate
              ↓
        Lever A.1 patches in (silent)
              ↓
        Lever A.1+1 specialist deployed
              ↓
       Wait 14 d. Conversion ≥ 5%?
        /              \
   yes (close)      no (≤ 5%)
                       ↓
                Lever A.1+2 (second specialist or vital_law-niche)
                       ↓
                 Wait 14 d. ≥ 5%?
                  /          \
              yes        no
                            ↓
                Lever B G1=0.04 (Day 28+; +1 trade attribution)
                            ↓
                     Still < 5%?
                      ↓
              Lever C (risk-control only — no closure)
                      ↓
              ESCALATE: PROFIT-LLM-001 / P4-GATE Appendix A
```

## Honest summary

EDGE-004 closure on Wave-1 + Wave-2 alone is **uncertain to negative on the empirical evidence**. The per-source audit (this cycle) reduces confidence in A.1+1 lift; B + C contribute attribution + risk-control only. The most likely outcome is one of:

1. **A.1+1 lifts conversion to ≥ 5 % via a vital_law-niche source onboarding** (legal-analyst, NOT the proposed geopolitics-analyst class). Probability: low-to-moderate.
2. **A.1+2 is required** (and the A.1+ spec needs revision to allow for sub-niche pivot mid-stream). Probability: moderate.
3. **EDGE-004 escalates to PROFIT-LLM-001 / P4-GATE Appendix A** after both A.1+ attempts fail. Probability: moderate-to-high.

Operators should prepare for outcome 2 or 3 as the modal scenario, not outcome 1.

## Cross-links

- `docs/governance/2026-05-03-post-soak-landing-simulation.md` — Wave-1 archive replay (Codex)
- `docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md` — Wave-2 ladder (Codex)
- `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` — per-source audit (this cycle)
- `docs/governance/2026-05-03-match001-bprime-false-suppression-audit.md` — orthogonality finding (this cycle)
- `docs/governance/2026-05-03-g1-admittance-counterfactual.md` — Lever B sizing (Codex)
- `docs/governance/2026-05-03-cross-series-headline-overlap-audit.md` — Lever C sizing (Codex)
- `docs/governance/edge-004-closure-path-tldr.md` — operator-facing closure-path TL;DR
