# VitalLaw aggregator-path forensics — concrete identification

**Generated:** 2026-05-04 (post-Codex `cd4c4bc` forensics; concretises the "aggregator/search ingestion" claim)
**Methodology:** Walked all `mac_archive/macbook_2026-05-01_import/logs/trades/**/*.jsonl`. For every record with `source` matching `vital`, extract the `url` field where present and group by URL host.
**Tool:** ad-hoc Python; no committed script (audit is one-shot, conclusion documented here)

## TL;DR — concrete answer

**VitalLaw records on the Mac archive came via Google News RSS** (`news.google.com/rss/articles/...`). They were NOT direct VitalLaw RSS ingestion. The `source` field on each record reflects the underlying publisher (VitalLaw.com), not the feed-of-record (Google News).

This concretises Codex's `cd4c4bc` forensics finding: "archive shape points to attributed aggregator/search ingestion." The aggregator is **Google News** specifically.

## Numbers

| metric | value |
|---|---:|
| total VitalLaw records across full Mac archive | 83 |
| records with `url` field present | 3 |
| URL netloc when present | `news.google.com` (3/3) |

The 80 records WITHOUT a `url` field are the EVIDENCE / OPPORTUNITY / PAPER_TRADE downstream emit types — those don't preserve URL after the initial fetch. The 3 records that DO carry `url` are `EARLY_STALE_DROP` records, which retain the original URL for audit purposes. All 3 EARLY_STALE_DROP records have `news.google.com/rss/articles/...` as the URL host.

Sample URL:

```
https://news.google.com/rss/articles/CBMiwAFBVV95cUxOaTgweFlMNXZTckQ5ZVh6RVZnTC12OHJrcHkyWDBxMzdyWGdHa0JZSk1EMU1Qd0tWTTJIWE1pdDJaQXRyNmRBNjByY2w0WUpRaXE1UXdOMXVDWnRwaFRaWHc1OGowS3I1bG5Gekp2Z3EzSmpza3ZodmZia21lWERHd3BBSENoS29mMnF2MXFGWEtxRUFkZUdKci1KU3J3aUh1bUk5S0dmdkdmME5oemVzWjBqMVFxbjRYUWxudzc3OGY?oc=5
```

## Implications for A.1+1.5 (option-B) deployment

The original A.1+1.5 spec (`2892101`) recommended re-onboarding `VitalLaw.com` directly via `RSS_FEEDS`. Per these forensics, that recommendation is **not the right framing**. Three real options:

### Branch A: Google News query family (CONFIRMED ACTIVE — surprising)

- `feeds/search_news_monitor.py` builds Google News RSS queries dynamically from current market titles. Queries are NOT hand-configured; they are derived from each cycle's market mix.
- `config.py:DISABLED_SOURCE_FAMILIES` shows Google News was re-enabled `2026-04-23` (commit `v0.29.43`+). **It is active in the current canonical config.** Confirmed pre-soak.
- VitalLaw therefore surfaced on the Mac archive because the market-token-derived queries at that time pulled in VitalLaw articles. **The ingestion path is still live today.** VitalLaw is not surfacing because either:
  1. Current market mix produces different queries (different tokens → different Google News result sets)
  2. Google News search-ranking has drifted; VitalLaw articles now rank lower for the active query set
  3. VitalLaw publishing cadence has changed (less frequent → fewer fresh items per query)
- **MAJOR FRAMING CHANGE:** A.1+1.5 is NOT a config change. The Google News path is already deployed; "re-onboarding VitalLaw" is a market-mix or query-relevance phenomenon, not a code change. **There is no Day-14 deploy required for Branch A.**
- **Operator action:** instead of deploying anything, monitor whether VitalLaw surfaces during Wave-1 close. If it doesn't surface within 14 d, the issue is search-relevance / publishing-cadence drift — code change can't fix that. Branch B / C are then the real deploy paths.

### Branch B: VitalLaw direct RSS feed

- Codex task #2 of this cycle: probe `vitallaw.com/feed`, `vitallaw.com/rss`, `wkproductions.cch.com/news/feed` etc. for a direct feed endpoint.
- **Pro:** deterministic; full editorial coverage, not search-filtered subset.
- **Con:** unknown if VitalLaw publishes a public RSS feed; paywall risk.

### Branch C: Open-RSS analogues

- Onboard Lawfare / Just Security / SCOTUSblog / Politico Legal feeds directly.
- **Pro:** deterministic; open RSS; no probe-time uncertainty.
- **Con:** sub-niche mismatch — VitalLaw's specific niche (M&A / regulatory / antitrust analysis from Wolters Kluwer's *Vital Law*) does NOT directly overlap with Lawfare (national-security law) or SCOTUSblog (Supreme Court). The fall-through analogues may not produce the same Kalshi-market headline coverage.

## Recommendation revision

**Day-14 decision-tree (revised: Branch A is passive, not a deploy):**

```
Day-14 sequence:
  1. PASSIVE OBSERVATION: Wave-1 close fires; Google News query family
     active. Watch for VitalLaw / legal-niche surfacing in trades.jsonl
     for 14 d. NO CODE CHANGE.
  2. If VitalLaw surfaces and produces ≥ 1 PAPER_TRADE in 14 d:
     EDGE-004 closure path is intact via Branch A. No A.1+1.5 deploy
     required.
  3. If 14 d pass with no VitalLaw / legal-niche surfacing:
     Proceed to ACTIVE deploy:
       3a. Probe vitallaw.com for direct RSS endpoint (Branch B).
       3b. If Branch B fails, onboard Lawfare / Just Security / SCOTUSblog
           / Politico Legal (Branch C).
  4. If Branch B + C both stall (≥ 2 attempts, < 5% conversion):
     Escalate to PROFIT-LLM-001 / P4-GATE Appendix A per unified forecast.
```

This is a significant simplification of the prior 4-way tree. The honest read is: **the bot's ingestion path is correct; the historical signal came from a query-derivation pattern that may or may not still produce VitalLaw articles.** Operator's Day-14 action is to OBSERVE first, then DEPLOY only if observation fails.

## Caveats

- **3-of-83 sample size for the URL-host inference.** The other 80 records don't carry `url`, so the ingestion path is inferred — not definitively proven — for those records. The 3 EARLY_STALE_DROP records are highly correlated with Google News ingestion (Google News URLs go stale fast because they're search-result links), but it is theoretically possible some VitalLaw records came via a different path.
- **Mac archive vs canonical config divergence.** The Mac archive snapshot is from 2026-05-01. The current canonical `config.py:RSS_FEEDS` may have changed since. A current-state check is needed before Day-14 deploy.
- **Google News search params** (the `?oc=5` parameter in the URL) suggest a specific query string. Operator should preserve those if re-creating the feed entry.

## Cross-links

- `docs/_archive/governance/2026-05-04-vitallaw-archive-forensics.md` — Codex's prior forensics (the "aggregator/search ingestion" claim this audit concretises) (ARCHIVED Stream G R12)
- `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` — original per-source audit
- `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` — A.1+1.5 spec (needs §2 / §3.1 + decision-tree refresh integrating this finding)
- `docs/governance/edge-004-closure-path-tldr.md` v2 — TL;DR (needs option-B sub-decision refresh)
- `docs/governance/post-soak-close-rehearsal-checklist.md` §7 — operator decision-point at Day-14 (needs 4-way branch update)
