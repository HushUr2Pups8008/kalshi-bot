# EDGE-004 closure-path TL;DR

**Status:** operator-facing one-pager. Companion to the dense `2026-05-03-edge-004-lever-menu-design.md` lever menu and the per-lever specs.
**Drafted:** 2026-05-03
**Last refresh:** 2026-05-03 (post-Codex archive replays + Lever-E closure + Lever-D demotion + A.1 reframing)

The full lever menu spec is correct but dense. This is the punchline.

## Current closure path (post-2026-05-03 empirics)

```
A.1 (prerequisite)  →  A.1+ (specialist analyst feed)  →  B (G1)  →  C (cross-series)
   hygiene only           the only edge-producer        attribution    risk-control
                                                       (1-2 trades/14d)
```

Followed by escalation to **PROFIT-LLM-001** or **P4-GATE Appendix A** if A.1+ stalls. Both already ROADMAP-tracked outside EDGE-004.

## Lever map at a glance

| lever | role | empirically validated? | first deploy |
|---|---|---|---|
| A.1 | prerequisite hygiene (classifier patch) | ✅ correct per-source; ❌ ~0 archive lift | 2026-05-22 (Wave 2 first) |
| A.1+ | the only edge-production lever | ✅ specialist analyst class produced 21 OPP + 3/3 historical PAPER_TRADE | 2026-05-23 (Wave 2 first feed) |
| B | G1 calibration; attribution lever | ⚠️ predicted lift 1-2 trades / 14 d | 2026-06-13 (Wave 3 if A.1+ stalls) |
| C | cross-series headline correlation; risk-control | ✅ 49.2 % overlap empirically | 2026-06-20 (Wave 3 if A + B stall) |
| D | pre-LLM gate re-enablement | volume-destructive (74 % OPP cut) | demoted; outside closure path |
| E | multi-source corroboration | empirically infeasible (closed) | n/a |
| F | P4-GATE Appendix A market-mix | open question; ROADMAP-tracked | out of EDGE-004 scope |

## What changed between drafts (4 revisions in one session, 2026-05-03)

1. **Original draft (early 2026-05-03):** A → D → B → E → C. Naive "weight tweak" framing of Lever A.
2. **Post-Lever-D audit:** A → B → E → C → D. Codex's pre-LLM-gate audit demoted D (74 % OPP cut → noise/budget knob, not edge lever).
3. **Post-Lever-E sizing audit:** A → B → C → D. Codex's source-instance audit closed E (empirically infeasible at any threshold).
4. **Post-Lever-A.1 archive replay:** **A.1 (prerequisite) → A.1+ (specialist analyst) → B → C → D.** Codex's A.1 classifier replay showed standalone lift on archive ≈ 0; A.1 reframed as hygiene-not-lever; A.1+ feed onboarding becomes the only edge-producer.

The lever menu spec carries this 4-revision history; this TL;DR carries only the resulting **current canonical state**.

## What "closure" looks like

EDGE-004 closes OPEN → COMPLETE when:

- One lever from the closure path produces a measurable lift in the OPPORTUNITY → PAPER_TRADE conversion rate (target: ≥ 5 % over 14 d, vs the post-MATCH-001 baseline of 3.4 %).
- The lift is sustained over a 14-day post-deploy window with per-lane attribution (via the post-OBS-003 SKIPPED stream).
- Closure does NOT require all levers to land. **A.1+1 closing on its own is the cleanest outcome.**

If A.1+ stalls after 2 honest feed-onboarding attempts (A.1+1 specialist analyst, A.1+2 second specialist or government bulletin), EDGE-004 escalation is to PROFIT-LLM-001 or P4-GATE Appendix A — **outside EDGE-004's scope.** The honest read: *if both fail, EDGE-004 is unclosable through intake-side levers.*

## Honest read

EDGE-004's closure now hangs on a single empirical question: **does landing 1-2 specialist analyst feeds (War on the Rocks / CSIS / ISW / CFR / Atlantic Council) lift conversion above 5 %?**

Codex's archive sizing says specialist analyst sources produced 21 OPP + 3 PAPER_TRADE on the 13-day archive (3/3 of all historical paper trades came from this class). Adding more feeds in this class should multiply the signal — but the multiplication may be sub-linear if existing specialist feeds (Kyiv Post / Times of Israel / Iran International / bellingcat / Defense News / Breaking Defense) already cover most of the addressable headlines.

Pre-deploy sizing for the specific A.1+1 URL candidates is Codex's next planned audit. Until that lands, the operator should expect:

- **A.1 deploy** (Wave-2 day 13 post-soak): silent, no lift, prerequisite hygiene only.
- **A.1+1 deploy** (Wave-2 day 14 post-soak): real edge production opportunity. 14 d window to verify ≥ 5 %.
- **If A.1+1 stalls:** A.1+2 within 7 d. After 2 attempts, escalate.

## See also

- `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` — full lever menu (4-revision history).
- `docs/superpowers/specs/2026-05-03-edge-004-lever-a-source-class-diversification-design.md` — Lever A umbrella.
- `docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` — A.1 classifier patch (revised to prerequisite).
- `docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` — A.1+ first feed (specialist analyst).
- `docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md` — Lever B (attribution).
- `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` — Lever C (risk-control).
- `docs/superpowers/specs/2026-05-03-edge-004-lever-e-multi-source-corroboration-design.md` — Lever E (CLOSED).
- `docs/governance/2026-05-03-source-class-diversification-audit.md` — Codex empirics, Lever A.
- `docs/governance/2026-05-03-edge004-lever-d-pre-llm-gate-audit.md` — Codex empirics, Lever D.
- `docs/governance/2026-05-03-lever-e-source-corroboration-sizing.md` — Codex empirics, Lever E.
- `docs/governance/2026-05-03-g1-admittance-counterfactual.md` — Codex empirics, Lever B.
- `docs/governance/2026-05-03-cross-series-headline-overlap-audit.md` — Codex empirics, Lever C.
- `docs/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md` — Codex empirics, A.1.
- `docs/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md` — Codex empirics, A.1+.
