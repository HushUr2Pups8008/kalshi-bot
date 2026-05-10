# Wave-2 A.1+ branch decision table — operator decision input

**Type:** ambiguity resolution (Claude task per Implementation Contract §9 — "Resolving ambiguity in contract requirements before implementation proceeds").
**Drafted:** 2026-05-05.
**Audience:** operator picking the Wave-2 A.1+ deploy branch at ≥ 2026-05-15 (Wave-2 first-feed earliest deploy under §8.5.1 path).
**Companion specs:**
- `docs/_archive/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` (the umbrella spec) (ARCHIVED Stream G R27)
- `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` (legal-analyst niche)
- `docs/_archive/governance/2026-05-05-vitallaw-direct-rss-probe.md` (Codex's branch-B feasibility kill) (ARCHIVED Stream G R22)

## Why this doc

The A.1+ feed-onboarding spec accumulated three branch options across 2026-05-03 → 2026-05-05 cycles. The original 4-branch tree collapsed to 3 after Codex's direct-RSS probe killed Branch B (direct VitalLaw RSS infeasible). The remaining 3 branches each have different acceptance rates, deploy effort, and rollback profile. Operator must pick **one** at Wave-2 first-feed deploy time.

This doc presents the decision table + recommended verdict per branch. **No spec edits applied.** Operator picks; spec gets updated downstream of the pick.

## The 3 branches

| branch | label | what it does | spec ref |
|---|---|---|---|
| **A** | passive Google News observe | take no action; let `feeds/search_news_monitor.py` (already active per `config.py:DISABLED_SOURCE_FAMILIES` post-2026-04-23 re-enable) keep running; observe whether VitalLaw / legal-niche surfaces under current market-mix queries over 14 d | A.1+ spec §2.5 callout; "the Mac archive's VitalLaw records came via the Google News query family" |
| **C** | open-RSS legal-analyst onboard | deploy 1-2 open-RSS legal-analyst feeds (analogues to VitalLaw) that pass the paywall + token-classifier gates; no specialist-analyst geopolitics feeds | `2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` (the §3.1bis path; "vital_law-niche option-B"); xfail harness `tests/test_lever_a1plus_feed_config.py::test_vital_law_or_legal_analyst_feed_present_post_a1plus` |
| **D** | escalation — geopolitics specialist-analyst onboard | deploy 1-2 specialist-analyst feeds (war on the rocks, CSIS, ISW, CFR, Atlantic Council) per the original §3.1 list; this is the geopolitics-sub-niche path that produced 18 OPP / 0 PAPER_TRADE on the archive | A.1+ spec §3.1; xfail harness `tests/test_lever_a1plus_feed_config.py::test_at_least_one_specialist_analyst_url_in_rss_feeds` |

(Branch B — direct VitalLaw RSS — was killed 2026-05-05 by Codex's direct-RSS probe. Removed from active consideration.)

## Decision table

| dimension | Branch A (passive observe) | Branch C (open-RSS legal-analyst) | option-A (geopolitics specialist) |
|---|---|---|---|
| **Code change** | none | 1-3 lines `config.py:RSS_FEEDS` + 5-10 lines `main.py:_source_class_for_evidence` | same shape as C; different feed URLs |
| **Deploy effort** | 0 — observation only | ~30 min: URL probe + classifier patch + xfail flip | ~30 min: URL probe + classifier patch + xfail flip |
| **Expected lift (14 d post-deploy)** | unknown — depends on Kalshi market mix surfacing legal-niche markets under the active Google News queries | **upper bound** = 1 PAPER_TRADE / 14 d (matches VitalLaw historical 3/13 d archive rate; the load-bearing source's profile) | **upper bound** = 0 PAPER_TRADE per archive replay (18 OPP / 0 PAPER on 13 d → no historical conversion) |
| **Risk of overtrading** | LOW (no new evidence ingest path) | LOW-MED (new sources but already-tested classifier path; 1-2 feeds; G2 diversity gate keeps the bar high) | LOW-MED (same as C; risk shape identical) |
| **Risk of zero lift** | MED (Google News may not surface legal-niche under current market mix) | LOW (legal-analyst has historical PAPER_TRADE evidence; the VitalLaw signal is real) | **HIGH** (archive replay produced 0 PAPER_TRADE for the candidates — the surface is empirically proven non-converting) |
| **Rollback profile** | n/a — nothing deployed | code revert: 2-line `RSS_FEEDS` + classifier patch revert; trivial | same as C |
| **Pre-deploy attribution-data dependency** | none | needs Wave-1 OBS-003 SKIPPED-stream live for ≥ 7 d (so post-deploy attribution distinguishes "killed at G1" vs "fired") | same as C |
| **Acceptance criterion** | 14 d window: ≥ 1 legal-niche PAPER_TRADE OR ≥ 5 OPP from a legal-niche source | 14 d window: ≥ 1 PAPER_TRADE from the new legal-analyst feed; non-negative aggregate realized P&L | 14 d window: ≥ 1 PAPER_TRADE from any of the 5 specialist feeds; non-negative aggregate realized P&L |
| **Closes EDGE-004?** | only if it produces ≥ 5 % conversion lift over the 14 d (very unlikely; passive surface) | candidate yes — if 1+ PAPER_TRADE materialises with positive realized P&L | unlikely — historical conversion = 0; would require unprecedented archive→deploy distribution shift |

## Recommended verdict per branch

**Branch A: recommended as the FIRST step.** Cheap; observation-only; takes 14 d but no operator effort. The passive Google News path is already active. If A produces ≥ 1 legal-niche PAPER_TRADE in the 14 d window, the operator gets free signal that VitalLaw-equivalent surfaces are reachable without onboarding new feeds; subsequent decisions (C or D) become better-informed.

**Branch C: recommended SECOND, if Branch A produces 0 legal-niche signal in 14 d.** The legal-analyst niche has historical evidence (3/3 PAPER_TRADE on the 13-day archive — the only proven non-mainstream PAPER_TRADE class). Onboarding 1-2 open-RSS analogues to VitalLaw is the highest-EV second-feed move.

**option-A: recommended LAST, only if A + C both produce 0 PAPER_TRADE over their 14 d windows.** The empirical case is weak (18 OPP / 0 PAPER on archive). Branch D's value is exhaustion — eliminating the geopolitics-sub-niche before declaring EDGE-004 closed via Lever B / Lever C / Lever D / Lever E.

## Sequencing

```
Wave-1 close (2026-05-08+)
    │
    ▼
Branch A: passive observe (14 d)
    │
    ├─── 1+ legal-niche PAPER_TRADE? ───► EDGE-004 closes via passive surface; stop
    │
    └─── 0 legal-niche PAPER_TRADE
            │
            ▼
        Branch C: open-RSS legal-analyst onboard (deploy + 14 d window)
            │
            ├─── 1+ PAPER_TRADE w/ positive realized P&L? ───► EDGE-004 closes via Branch C
            │
            └─── 0 PAPER_TRADE OR negative realized P&L
                    │
                    ▼
                option-A: geopolitics specialist-analyst onboard (deploy + 14 d window)
                    │
                    ├─── 1+ PAPER_TRADE w/ positive realized P&L? ───► EDGE-004 closes via option-A (low-confidence; expect re-test)
                    │
                    └─── 0 PAPER_TRADE
                            │
                            ▼
                        EDGE-004 closure path moves to Lever B (G1 calibration; Wave-3) or Lever C (cross-series; Wave-3)
```

**Total wall-clock to a Branch A → C → D walk:** 14 + 14 + 14 = 42 d. EDGE-004 closure target before this walk completes is unlikely; the walk produces durable evidence about WHICH lever closes EDGE-004, which is more valuable than a fast-but-unfounded closure.

## Open questions for operator

1. **Branch A acceptance threshold.** Is ≥ 1 PAPER_TRADE in 14 d a strong-enough Branch A pass, or should it be ≥ 2 (statistical confidence)? **Recommendation:** ≥ 1 is sufficient — confirms the surface is reachable; downstream Branch C / D decisions don't depend on the count.
2. **Branch C feed selection.** A.1+1.5 spec lists candidate URLs but doesn't pick. **Recommendation:** Codex sizes 2 candidates pre-deploy (15 min audit) and picks the higher-domain-overlap one against the current Kalshi market mix.
3. **option-A fall-back ordering.** If Branch A produces some signal but not enough (≥ 1 OPP, 0 PAPER_TRADE), do we try Branch C or option-A first? **Recommendation:** still C first — the legal-analyst class has historical PAPER_TRADE (the proven conversion class).
4. **Time-pressure compression.** If operator wants to compress the 42 d walk: deploy A + C in parallel (Branch A is observation-only, so it can run concurrently with Branch C). option-A stays serial behind C. **Compresses to 14 + 14 = 28 d.** Recommendation: defer the parallelism unless operator explicitly requests it; serial is safer for attribution clarity.
5. **§8.5.2 implications.** Each branch deploy is a behavioural code change to evidence-pipeline surfaces. If a future governance shadow-soak overlaps with a Wave-2 deploy, the §8.5.2 carve-out criteria (`docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5.2) need to be evaluated for the deploy commit. **Recommendation:** target Branch C / D deploys to occur OUTSIDE of any active soak window. PROFIT-PHASE2-002 is the next soak; sequence Branch C deploy either before PHASE2-002 starts (Wave-2 between Wave-1 close and PHASE2-002 start) or after PHASE2-002 closes.

## Cross-links

- `docs/_archive/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` — A.1+ umbrella spec (§2.5 callout is the load-bearing context) (ARCHIVED Stream G R27)
- `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` — Branch C spec
- `docs/_archive/governance/2026-05-05-vitallaw-direct-rss-probe.md` — Branch B kill (Codex 2026-05-05) (ARCHIVED Stream G R22)
- `docs/_archive/governance/2026-05-04-legal-niche-probe-order-domain-overlap.md` — Branch C feed-candidate domain-overlap audit (ARCHIVED Stream G R6)
- `docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md` — Codex's Wave-2 expected-state forecast
- `docs/governance/edge-004-closure-path-tldr.md` v2 — current EDGE-004 closure-path consensus
- `tests/test_lever_a1plus_feed_config.py` — both branches' xfail-strict harnesses pre-loaded
