# PROFIT-EDGE-004 Lever A.1+1.5 — legal-analyst feed onboarding (option-B for first-feed deploy)

**Status:** design (Wave 2 of post-soak landing — legal-analyst alternative to A.1+1 specialist-geopolitics)
**Tracker:** `PROFIT-EDGE-004` Lever A → Stage A.1+ → option-B
**Owner:** Claude (design) + Codex (per-feed candidate sizing for legal niche — Codex task #5 of 2026-05-04 cycle)
**Severity:** HIGH (load-bearing source profile; protects 100 % of historical PAPER_TRADE)
**Drafted:** 2026-05-04
**Empirical context:**

- `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` — per-source audit identifying `VitalLaw.com` as the load-bearing source (3/3 PAPER_TRADE)
- `docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` — unified forecast sizing both option-A and option-B
- `docs/governance/2026-05-03-claude-commits-728f3bd-cfc0b60-adversarial-review.md` F5 — flagged the per-source ranking gap that this spec closes

## 1. Why this spec exists

The 2026-05-04 per-source audit revealed that `VitalLaw.com` (legal/regulatory analysis) produced **100 % of historical PAPER_TRADE in the specialist_analyst class** on the 13-day Mac archive (3/3). The original A.1+ spec (`2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md`) recommended a geopolitics-analyst pivot (war on the rocks / CSIS / ISW / CFR / Atlantic Council). Those candidates target a different sub-niche than `VitalLaw.com`.

**Aggregator-path forensics (2026-05-04, `docs/governance/2026-05-04-vitallaw-aggregator-path-forensics.md`):** the Mac archive's VitalLaw records came via **Google News RSS** (`news.google.com/rss/articles/...?oc=5`), NOT a direct VitalLaw RSS feed. The Google News query family is **active in the current canonical config** (re-enabled 2026-04-23 per `config.py:DISABLED_SOURCE_FAMILIES`), and queries are derived dynamically from current market titles by `feeds/search_news_monitor.py`. Therefore the ingestion path IS already deployed — the question is not "re-onboard VitalLaw" but "why is Google News no longer surfacing VitalLaw articles for the current market mix."

## §1.5 — Decision-tree revision (post-aggregator-path forensics)

| branch | action | when |
|---|---|---|
| **Branch A** (passive) | observe Wave-1 close for 14 d; watch for VitalLaw / legal-niche surfacing on Google News query family | Day-14 default; NO code change required |
| **Branch B** (active) | probe `vitallaw.com` for a direct RSS endpoint; add to `RSS_FEEDS` if found | only if Branch A produces 0 legal-niche PAPER_TRADE in 14 d |
| **Branch C** (fallback) | onboard open-RSS analogues (Lawfare / Just Security / SCOTUSblog / Politico Legal) | only if Branch A + B both fail |
| **Branch D** (escalation) | PROFIT-LLM-001 / P4-GATE Appendix A | if Branch B + C both stall (≥ 2 deploy attempts) |

This spec (§2 / §3) covers Branch B and Branch C. Branch A requires no code change and is the operator's first response — covered in `docs/governance/post-soak-close-rehearsal-checklist.md` §7.

This spec proposes a parallel A.1+1 deploy track — **option-B: legal-analyst onboarding** — covering vital_law-niche analogues. Operator picks at deploy time whether option-A (geopolitics) or option-B (legal-analyst) lands first, based on probe-time tractability (paywall / rate-limit / robots.txt friction). Note: per Branch A above, deploy may not be required at all if Google News surfaces VitalLaw during Wave-1 close.

## 2. First-feed candidate selection: LEGAL ANALYST

Empirical case: 100 % concentration on `VitalLaw.com`. Sub-niche analogues (sized by Codex task #5 of the 2026-05-04 cycle):

| candidate | niche | accessibility | priority |
|---|---|---|---|
| `VitalLaw.com` | legal/regulatory analysis | unknown — was previously polled, may have been removed for paywall reasons | **first probe** |
| `politico.com/news/legal` | legal/political analysis | partial paywall; RSS at `politico.com/rss/legal.xml` (probe) | second |
| `lawfaremedia.org` | national-security law | open RSS at `lawfaremedia.org/feed.xml` (verify) | third |
| `justsecurity.org` | national-security law | open RSS at `justsecurity.org/feed/` (verify) | fourth |
| `scotusblog.com` | Supreme Court analysis | open RSS at `scotusblog.com/feed/` (verify) | fifth |
| `reuters.com/legal` | wire-service legal news | proprietary; agency feed via `reutersagency.com` (paywall likely) | last resort |

Codex's per-feed sizing audit (planned task #5 of the 2026-05-04 cycle) ranks these by:

- Headline volume (per 14 d)
- Match-score distribution against current Kalshi market mix
- Source-class assignment (court / regulatory / legal_analyst — currently `_source_class_for_evidence` does not have a `legal` branch, so all of these classify as `news` today)
- Robots.txt + rate-limit + paywall checks

## 3. The fix

### 3.1 RSS feed config addition

Append at least one of the candidates above to `config.py:RSS_FEEDS`. The order at deploy time depends on Codex's sizing + probe-time tractability:

```python
RSS_FEEDS = [
    ...existing feeds...

    # Legal / regulatory analysis (Lever A.1+1.5 — load-bearing per
    # 2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md).
    # 100% of historical PAPER_TRADE in specialist_analyst class came from
    # VitalLaw.com (Mac archive). Re-onboarding probe order:
    # 1) VitalLaw.com itself (if still accessible / not paywall-locked)
    # 2) Lawfare / Just Security (open RSS, partial overlap with VitalLaw niche)
    # 3) SCOTUSblog (Supreme Court only; narrower niche but strong signal)
    # 4) Politico legal (partial paywall; preview-only RSS may suffice)

    "https://www.vitallaw.com/news/feed",  # exact URL TBD; probe at deploy
    # ALTERNATIVELY (if VitalLaw is paywall-locked):
    # "https://lawfaremedia.org/feed.xml",
    # "https://www.justsecurity.org/feed/",
    # "https://www.scotusblog.com/feed/",
]
```

### 3.2 Feed-source-label classification

`main.py:_source_class_for_evidence` currently has no `legal` branch. Add one (sibling to the `analysis` branch in commit `356a35c`):

```python
def _source_class_for_evidence(source: str) -> str:
    src = (source or "").strip().lower()
    if not src:
        return "unknown"

    # ... existing official / news / analysis branches ...

    # Lever A.1+1.5: legal / regulatory analysts.
    if any(token in src for token in (
        "vitallaw",
        "vital-law",
        "lawfare",
        "just security",
        "justsecurity",
        "scotusblog",
        "politico legal",
        "reuters legal",
    )):
        return "legal"

    # ... fall through to news / unknown ...
```

`evidence_scorer._SOURCE_CLASS_QUALITY` then needs a weight assignment for `legal`. Recommend `0.65` (between `analysis=0.60` and `news=0.70` per the actual current dict) — legal analysts have slightly stronger primary-source proximity than general analysis but less than mainstream news wires which carry primary-source statements verbatim. Pre-loaded harness (`tests/test_lever_a1plus1_5_evidence_scorer_legal_weight.py`) pins the 0.65 value plus the strict ordering invariant `analysis < legal < news`. Operator may pick any value in that interval at deploy time (e.g., 0.62 / 0.68); values outside the interval require a spec revision.

### 3.3 Optional source-mapping additions

If using `VitalLaw.com` itself:

```python
SOURCE_DOMAIN_TO_CLASS = {
    ...,
    "vitallaw.com": "legal",
}
```

## 4. Sizing methodology (Codex task #5 reference)

Codex's planned per-feed sizing audit should produce:

- Per-source 14-d headline volume estimates
- Per-source match-score distribution against `kalshi_universe.json` market mix
- Per-source source_class assignment under both pre-fix and post-fix `_source_class_for_evidence`
- Probe results (HTTP 200 vs 401/403 vs 429) at audit run time
- Robots.txt diff against existing polled feeds (rate-limit headroom)

Audit output should be a single doc at `docs/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` ranking the 6 candidate sources.

## 5. Components touched

- `config.py:RSS_FEEDS` (1 line — operator probes; not landed pre-deploy)
- `main.py:_source_class_for_evidence` (1 new branch — `legal`)
- `analysis/evidence_scorer.py:_SOURCE_CLASS_QUALITY` (1 new entry — `"legal": 0.65`)
- `tests/test_lever_a1plus_feed_config.py` (already pinned — `test_vital_law_or_legal_analyst_feed_present_post_a1plus`)
- `tests/test_main_pipeline.py` (NEW: pre-load `TestSourceClassClassifierLeverA1Plus15LegalBranch` strict-xfail harness — Claude follow-up task)

## 6. Acceptance criteria

- [ ] `pytest tests/test_lever_a1plus_feed_config.py::test_vital_law_or_legal_analyst_feed_present_post_a1plus -q` xpasses (currently strict-xfail).
- [ ] At least 1 vital_law-niche URL in `RSS_FEEDS`.
- [ ] `_source_class_for_evidence` returns `"legal"` for vitallaw / lawfare / justsecurity / scotusblog source strings.
- [ ] Post-deploy 14 d validation: conversion ≥ 5 % (or ≥ 1 PAPER_TRADE — conservative, given the 3-trade historical sample is small).
- [ ] Codex's per-feed sizing audit (task #5) lands BEFORE the deploy commit so the operator has empirical headline-volume data per candidate.

## 7. Rollback

- Remove the appended URL from `RSS_FEEDS`.
- Remove the `legal` branch from `_source_class_for_evidence` (revert).
- Restore the strict-xfail marker on `test_vital_law_or_legal_analyst_feed_present_post_a1plus`.
- See `docs/governance/post-soak-rollback-runbook.md` §3 for the standard A.1+ rollback drill.

## 8. Closure logic for EDGE-004

Same as A.1+ option-A: ≥ 5 % conversion lift over 14 d post-deploy. Option-B has the empirical advantage that the historical archive shows the niche IS load-bearing; option-A has the deploy-friction advantage that the geopolitics URLs are open RSS / no-paywall.

If option-B's first deploy (`VitalLaw.com` probe) succeeds, the load-bearing-source restoration alone may be sufficient to clear ≥ 5 %. If `VitalLaw.com` is paywall-locked at probe time and option-B falls back to Lawfare / Just Security / SCOTUSblog, the niche analogue is sub-linear; expect smaller lift.

## 9. Risks

- **Paywall lockout on VitalLaw.com.** The reason VitalLaw was silently removed from canonical config may have been a paywall transition. If probe returns 401/403 the operator must fall through to analogues immediately.
- **Sub-niche mismatch.** SCOTUSblog covers only Supreme Court rulings; Lawfare / Just Security cover national-security-law (overlap with `lawfaremedia.org` may be high). The fall-through analogues may not produce the same Kalshi-market headline coverage that VitalLaw did.
- **Source-class weight collision.** Adding `legal=0.65` between `analysis=0.60` and `official=0.75` shifts the relative weighting. Verify the change doesn't regress `analysis`-class historical PAPER_TRADE (currently 0/0 in archive, so vacuously safe).
- **Probe-time burn.** Codex's per-feed sizing audit is an upstream gate; deploying without it risks picking a lower-volume candidate first.

## 10. Soak-window contract

This spec lands as a pre-loaded design only during the 2026-05-15 soak window. No code changes; no `RSS_FEEDS` modifications until post-soak. The harness `tests/test_lever_a1plus_feed_config.py::test_vital_law_or_legal_analyst_feed_present_post_a1plus` (commit landing in this cycle) is the only soak-window-active test artifact.

## 11. xfail harness pre-load decision

DEFERRED: pre-load of `TestSourceClassClassifierLeverA1Plus15LegalBranch` in `tests/test_main_pipeline.py` (mirror of the analysis-class harness in commit `87c3f15`). To be added in a follow-up commit; this spec carries the candidate token list (vitallaw / lawfare / justsecurity / scotusblog / politico legal / reuters legal) which the harness will pin.

## 12. Out of scope

- Mainstream news (already covered by existing RSS_FEEDS).
- Government bulletins (Lever A.1+ option-C, deferred per umbrella spec).
- Market microstructure (Lever A.1+ option-D, ROADMAP-tracked outside EDGE-004).
- Replacement of the legal-analyst feed if it stalls — that is A.1+1.6 territory, not specced here.

## 13. Cross-links

- `docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` — option-A (geopolitics) parent spec
- `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` — per-source audit identifying VitalLaw concentration
- `docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` — unified forecast sizing both options
- `docs/governance/post-soak-close-rehearsal-checklist.md` §7 — operator decision point at deploy
- `tests/test_lever_a1plus_feed_config.py::test_vital_law_or_legal_analyst_feed_present_post_a1plus` — the pre-loaded option-B xfail
