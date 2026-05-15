# Lever A.1+ specialist-analyst per-source ranking — major finding

**Generated:** 2026-05-03 (post-snapshot-5; reassigned Codex task #3, Codex usage exhausted)
**Methodology:** Per-source extension of Codex's class-level audit (`scripts/simulations/lever_a1_plus_candidate_feed_sizing.py`, commit `2a15d55`). Reads the same Mac archive (`mac_archive/macbook_2026-05-01_import/logs/trades`).
**Tool:** `scripts/simulations/lever_a1_plus_specialist_analyst_per_source_sizing.py`

## TL;DR — surprise finding

**All 3 historical PAPER_TRADE in the `specialist_analyst` class came from a single source: `vital_law` (a legal / regulatory analysis publisher).**

Codex's class-level audit showed `specialist_analyst` produced 21 OPP + 3 PAPER_TRADE on the 13-day archive. The implicit reading was that this signal came from the existing geopolitical-analyst feeds (Kyiv Post / Times of Israel / Iran International / bellingcat / Defense News / Breaking Defense). **That implicit reading is wrong.** The actual breakdown:

| source | MATCH_DIAGNOSTIC | OPP | PAPER_TRADE | %class_OPP | %class_PAPER |
|---|---:|---:|---:|---:|---:|
| `kyiv_post` | 66 | 3 | **0** | 14.3 | 0.0 |
| `kyiv_independent` | 75 | 5 | **0** | 23.8 | 0.0 |
| `times_of_israel` | 54 | 6 | **0** | 28.6 | 0.0 |
| `iran_international` | 26 | 2 | **0** | 9.5 | 0.0 |
| `bellingcat` | 1 | 0 | 0 | 0.0 | 0.0 |
| `defense_news` | 5 | 1 | **0** | 4.8 | 0.0 |
| `breaking_defense` | 3 | 1 | **0** | 4.8 | 0.0 |
| `defense_one` | 0 | 0 | 0 | 0.0 | 0.0 |
| `foreign_policy` | 0 | 0 | 0 | 0.0 | 0.0 |
| **`vital_law`** | **21** | **3** | **3** | **14.3** | **100.0** |
| `war_on_the_rocks` (A.1+1 candidate) | 0 | 0 | 0 | 0.0 | 0.0 |
| `csis` (A.1+1 candidate) | 0 | 0 | 0 | 0.0 | 0.0 |
| `isw` (A.1+1 candidate) | 0 | 0 | 0 | 0.0 | 0.0 |
| `cfr` (A.1+1 candidate) | 0 | 0 | 0 | 0.0 | 0.0 |
| `atlantic_council` (A.1+1 candidate) | 0 | 0 | 0 | 0.0 | 0.0 |

`vital_law` is a legal-news source (publishes M&A / regulatory / antitrust analysis). It is *not* in the same sub-niche as the geopolitics-analyst feeds (war on the rocks / CSIS / ISW / CFR / Atlantic Council).

## What this changes about the A.1+ recommendation

**Previous (Codex 2026-05-03 candidate-feed sizing audit) recommendation:** specialist_analyst is the highest-ROI feed class; pick from war on the rocks / CSIS / ISW / CFR / Atlantic Council for A.1+1.

**Recommendation refinement post-this-audit:**

1. **`vital_law` is load-bearing — protect it first.** It produced 100 % of class PAPER_TRADE on the 13-day archive. If it is in `RSS_FEEDS` today, it MUST stay. If polling rate / staleness is a concern, it should be the *first* source to harden, not last.

2. **The other "specialist" geopolitics feeds (Kyiv X / Times of Israel / Iran International / Defense News / Breaking Defense) produced ~ 18 OPP + 0 PAPER_TRADE.** That's a 0/18 = 0 % conversion rate at the class-internal sub-niche level. They generate match candidates but the candidates do NOT clear EV threshold. Either:
   - The sub-niche signal is genuinely weak (existing feeds saturated the addressable headlines and none clear EV), or
   - The headlines clear MATCH but get killed downstream by G1-G5 / blender (post-OBS-003 attribution will tell us which).

3. **A.1+1 candidate sources (war on the rocks / CSIS / ISW / CFR / Atlantic Council) target the same geopolitics sub-niche the existing 0/18 feeds occupy.** The expected lift is bounded above by the addressable-headline gap between existing geopolitics feeds and these 5 new ones, *minus* the 0 % conversion rate of the existing geopolitics class. **Honest read: the expected post-A.1+1 PAPER_TRADE lift is small.**

4. **The high-ROI pivot would be onboarding ANOTHER `vital_law`-shaped source** (legal / regulatory analysis), not another geopolitics feed. Candidates: Politico Pro / Lawfare / Just Security / SCOTUSblog / Reuters Legal — these are vital-law-niche analogues. **Defer to operator judgement on whether legal-analysis URL onboarding is even tractable** (paywall friction is higher in the legal sub-niche).

## Caveats

- **N=3 PAPER_TRADE is small.** A single `vital_law` outlier event (e.g., one big Supreme Court ruling that resolved 3 markets in quick succession) could account for the entire signal. Examples are needed: this audit captures up to 3 examples per source for spot-check.
- **The Mac archive is 13 days.** Codex's earlier sizing was on the same archive, so the comparison is fair, but the absolute conclusions about `vital_law` are subject to "the next 13 days could look different."
- **Source classification depends on `feed_class()` token list in `lever_a1_plus_candidate_feed_sizing.py:41`.** "vital-law" / "vitallaw" tokens are in the existing list, so the class assignment is consistent with Codex's prior run; the per-source breakdown only adds finer-grained sub-niche labels within the class.
- **The A.1+1 candidate sub-niche (war on the rocks / CSIS / ISW / CFR / Atlantic Council) shows 0/0/0 in the archive because they are NOT currently polled.** Their post-deploy lift is not directly observable until the deploy fires; this audit only sets the pre-deploy zero-baseline.

## Implications for the A.1+ spec + checklist

- **A.1+ spec §3.1** should add a "load-bearing source" callout: `vital_law` is currently the single point of failure for class PAPER_TRADE. Removing it from `RSS_FEEDS` would erase 100 % of historical paper trades.
- **`tests/test_lever_a1plus_feed_config.py:test_existing_specialist_analyst_feeds_unchanged_today`** asserts at least 3 of 5 existing specialist feeds remain. The list does NOT include `vital_law`. This is a **gap in the regression net**: removing vital-law would NOT trip the existing positive-control test. Add `vital-law` to the existing-domains list as a doc-maintenance follow-up.
- **EDGE-004 closure-path TL;DR (`docs/governance/edge-004-closure-path-tldr.md`)** says A.1+1 onboarding is the "real edge production opportunity." This audit suggests the expected lift is bounded; the honest read should be: A.1+1 (geopolitics-analyst) lift is **probably small**; the higher-ROI alternative is A.1+1.5 (legal-analyst onboarding) — but that is a different feed class than the umbrella spec contemplated.

## Files / cross-links

- `scripts/simulations/lever_a1_plus_specialist_analyst_per_source_sizing.py` — this audit script
- `scripts/simulations/lever_a1_plus_candidate_feed_sizing.py` — Codex's class-level audit (commit `2a15d55`)
- `docs/_archive/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md` — Codex's class-level report
- `docs/_archive/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` — A.1+ spec to update §3.1 with the load-bearing callout (ARCHIVED Stream G R27)
- `docs/governance/edge-004-closure-path-tldr.md` — TL;DR to update with the per-source caveat
- `tests/test_lever_a1plus_feed_config.py` — existing-domains positive control to extend with `vital-law`
