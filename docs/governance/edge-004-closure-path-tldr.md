# EDGE-004 closure-path TL;DR — v2 (2026-05-04 refresh)

**Status:** operator-facing one-pager. Companion to the dense `2026-05-03-edge-004-lever-menu-design.md` lever menu and the per-lever specs.
**Drafted:** 2026-05-03; **last refresh:** 2026-05-04 (post-per-source audit + B' orthogonality + A.1+1.5 option-B addition + day-4 confirmation)

The full lever menu spec is correct but dense. This is the punchline.

## Current closure path (post-2026-05-04 empirics)

```
A.1 (prerequisite)  →  A.1+ (decision point: option-A geopolitics OR option-B legal-analyst)  →  B (G1)  →  C (cross-series)
   hygiene only           the only edge-producer; sub-niche pivot live           attribution    risk-control
                                                                                  (1-2 trades/14d)
```

Followed by escalation to **PROFIT-LLM-001** or **P4-GATE Appendix A** if both A.1+ branches stall.

## What changed since v1 (2026-05-03)

1. **Per-source audit (cca3cea, 2026-05-04)** drilled into the specialist_analyst class and found `VitalLaw.com` produced **3/3 of historical PAPER_TRADE** on the 13-day Mac archive. The geopolitics-sub-niche feeds (Kyiv X / Times of Israel / Iran International / bellingcat / Defense News / Breaking Defense) produced **0/18 PAPER_TRADE** at the class-internal level.
2. **Audit-of-the-audit:** `VitalLaw.com` is NOT in the current canonical `config.py:RSS_FEEDS` (silently removed at some unknown prior point). The bot has lost the load-bearing PAPER_TRADE-producing source.
3. **A.1+1.5 spec added (2892101):** legal-analyst option-B parallel deploy path. Probe order (Codex `5e5849a`): VitalLaw > Just Security > Lawfare > SCOTUSblog > Politico Legal > Reuters Legal.
4. **B' orthogonality finding (83a9477):** the post-fix B' predicate is orthogonal to existing pre-fix MATCH_SUPPRESSED logic (100 % flip rate). Codex's spec-parity verification (`b56c261`) confirms; B' deploy is clean w.r.t. un-suppression of existing noise.
5. **Day-4 mid-soak confirmation (966f69e):** soak healthy at 62.7 h elapsed; 36 cycles / 158 decisions / 20 distinct targets / 0 safety counters firing.

## Lever map at a glance (refreshed)

| lever | role | empirically validated? | first deploy |
|---|---|---|---|
| A.1 | prerequisite hygiene (classifier patch) | ✅ correct per-source; ❌ ~0 archive lift | 2026-05-22 (Wave 2 first) |
| A.1+ option-A | specialist-geopolitics feed | ⚠️ 0/18 conversion at sub-niche level — bounded lift | 2026-05-23 (Wave 2 if option-A picked) |
| **A.1+1.5 option-B** | **legal-analyst feed (vital_law-niche)** | ✅ **VitalLaw.com produced 3/3 historical PAPER_TRADE; load-bearing** | **2026-05-23 (Wave 2 if option-B picked — recommended)** |
| B | G1 calibration; attribution lever | ⚠️ predicted lift 1-2 trades / 14 d; B' deploy clean (orthogonal) | 2026-06-13 (Wave 3 if A + A.1+ stall) |
| C | cross-series headline correlation; risk-control | ✅ 49.2 % overlap empirically | 2026-06-20 (Wave 3 if A + B stall) |
| D | pre-LLM gate re-enablement | volume-destructive (74 % OPP cut) | demoted; outside closure path |
| E | multi-source corroboration | empirically infeasible (closed) | n/a |
| F | P4-GATE Appendix A market-mix | open question; ROADMAP-tracked | out of EDGE-004 scope |

## A.1+ decision point (REVISED v2.1: 4-branch tree post-aggregator-forensics)

The 2026-05-04 aggregator-path forensics (`docs/governance/2026-05-04-vitallaw-aggregator-path-forensics.md`) revealed that the load-bearing PAPER_TRADE-producing source (`VitalLaw.com`) came via **Google News RSS**, not a direct feed. Google News query family is **already active** in canonical config (`config.py:DISABLED_SOURCE_FAMILIES` — re-enabled 2026-04-23). Therefore A.1+ is now a 4-branch tree with the FIRST branch requiring no code change:

| branch | action | when |
|---|---|---|
| **A** (passive) | observe Wave-1 close 14 d; Google News query family already deployed | Day-14 default |
| **B** (active) | probe `vitallaw.com` direct RSS; add to `RSS_FEEDS` | only if A surfaces 0 legal-niche PAPER_TRADE |
| **C** (fallback) | onboard Lawfare / Just Security / SCOTUSblog / Politico Legal | only if A + B both fail |
| **option-A** (parallel) | specialist-geopolitics per A.1+ spec §3.1 (war on the rocks / CSIS / ISW / CFR / Atlantic Council) | parallel to B/C if operator pursues breadth |

The legacy "option-A vs option-B" framing is preserved below for harness-naming compatibility. The 4-branch tree above supersedes it as the actual operator decision-point at Day-14:

| dimension | option-A: specialist-geopolitics | option-B: legal-analyst (recommended) |
|---|---|---|
| empirical PAPER_TRADE record | 0/18 sub-niche conversion | 3/3 from VitalLaw on 13-d archive |
| candidate set | war on the rocks / CSIS / ISW / CFR / Atlantic Council | VitalLaw / Lawfare / Just Security / SCOTUSblog / Politico Legal / Reuters Legal |
| deploy friction | low (open RSS) | medium-to-high (paywall risk on VitalLaw / Politico) |
| expected lift | bounded above by existing geopolitics-sub-niche conversion ≈ 0 | conditional on accessibility — if VitalLaw probe succeeds, **load-bearing-source restoration alone may close EDGE-004** |
| harness | `tests/test_lever_a1plus_feed_config.py::test_at_least_one_specialist_analyst_url_in_rss_feeds` | `tests/test_lever_a1plus_feed_config.py::test_vital_law_or_legal_analyst_feed_present_post_a1plus` |

**Default recommendation: option-B first** (highest empirical EV); fall through to option-A if all option-B candidates are paywall-locked at probe time.

## Sequencing-history (4 revisions tracked in lever-menu spec, now 5 with v2)

1. Original draft: A → D → B → E → C
2. Post-Lever-D audit: A → B → E → C → D (D demoted)
3. Post-Lever-E audit: A → B → C → D (E closed)
4. Post-Lever-A.1 archive replay: A.1 (prerequisite) → A.1+ → B → C → D
5. **Post-per-source audit (2026-05-04):** A.1 → A.1+ {option-A | option-B-recommended} → B → C → D

## What "closure" looks like

EDGE-004 closes OPEN → COMPLETE when:

- One A.1+ option produces a measurable lift in the OPP → PAPER_TRADE conversion rate (target: ≥ 5 % over 14 d, vs the post-Wave-1 base of ~1 % per the unified forecast `2bf3da1`).
- Lift sustained over a 14-day post-deploy window with per-lane attribution (via the post-OBS-003 SKIPPED stream).
- Closure does NOT require all levers to land. **A.1+1 (or A.1+1.5) closing on its own is the cleanest outcome.**

If both A.1+ branches stall after 2 honest deploy attempts (option-A or option-B), EDGE-004 escalates to PROFIT-LLM-001 / P4-GATE Appendix A — outside EDGE-004 scope. Honest read: *if both fail, EDGE-004 is unclosable through intake-side levers.*

## Honest read (refreshed)

EDGE-004's closure now hangs on a single empirical question: **does the A.1+ deploy restore or expand the load-bearing source profile?**

Per-source audit (cca3cea) sized the answer empirically:

- **Option-B (legal-analyst):** if VitalLaw.com is re-onboardable, the load-bearing source returns and conversion ≥ 5 % is plausible without further levers. If VitalLaw is paywall-locked, fall-through analogues (Lawfare / Just Security / SCOTUSblog) are sub-linear; expected lift is smaller but non-zero.
- **Option-A (specialist-geopolitics):** the historical 0/18 conversion at sub-niche level bounds expected lift. A.1+1 alone probably fails closure; A.1+2 (second specialist) would be needed.

**Probability ranking:** option-B first deploy succeeds (~ moderate) > option-A succeeds (~ low) > both stall, escalate (~ moderate-to-high).

Pre-deploy sizing for the specific A.1+ URL candidates is locked (Codex `2a15d55` for option-A, Codex `5e5849a` for option-B). Until Day-14 the operator should expect:

- **A.1 deploy** (Wave-2 day 13 post-soak): silent, no lift, prerequisite hygiene only.
- **A.1+ deploy** (Wave-2 day 14 post-soak): real edge production opportunity. 14 d window to verify ≥ 5 %. Operator picks option-A or option-B at deploy time.
- **If A.1+ stalls:** A.1+2 within 7 d (the OTHER option). After 2 attempts total, escalate.

## See also

- `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` — full lever menu (5-revision history)
- `docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` — A.1+ option-A spec
- **`docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md`** — A.1+1.5 option-B spec (NEW)
- `docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md` — Lever B
- `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` — Lever C
- `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` — per-source audit (Claude)
- `docs/governance/2026-05-04-lever-a1-plus-specialist-analyst-domain-normalized-audit.md` — per-source verification (Codex)
- `docs/governance/2026-05-03-match001-bprime-false-suppression-audit.md` — orthogonality finding (Claude)
- `docs/governance/2026-05-04-match001-bprime-spec-parity-verification.md` — spec-parity verification (Codex)
- `docs/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` — option-B probe order (Codex)
- `docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` — unified forecast
- `docs/governance/post-soak-close-rehearsal-checklist.md` §7 — operator decision-point at Day-14
