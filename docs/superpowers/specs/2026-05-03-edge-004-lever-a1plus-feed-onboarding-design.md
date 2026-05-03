# PROFIT-EDGE-004 Lever A.1+ — feed-onboarding (the only edge-production lever in EDGE-004 scope)

**Status:** design (Wave 2 of post-soak landing — first feed deploy ≥ 2026-05-22 after A.1 classifier patch lands and stabilises)
**Tracker:** `PROFIT-EDGE-004` Lever A → Stage A.1+
**Owner:** Claude (design) + Codex (per-feed candidate sizing — see §4)
**Severity:** HIGH (only EDGE-004 edge-production lever still in scope post-Lever-E-closure + Lever-A.1-archive-replay)
**Drafted:** 2026-05-03
**Empirical context:**
- `docs/governance/2026-05-03-source-class-diversification-audit.md` (single-source rate; `news` 238 / `other` 122 / `official` 9 of OPP joins)
- `docs/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md` (A.1 alone produces ~0 archive lift)
- `docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md` (Codex's Wave-2 forecast)

## 1. Why this spec exists

Lever A.1's archive replay (commit `8001a16`) proved A.1 alone won't lift conversion: classifier patches existing source labels, but the archive contains 0 records from the misclassified canonical sources. Edge production must come from **new sources** the bot doesn't currently poll — this is **Lever A.1+ (feed onboarding).**

The Lever A umbrella spec (`2026-05-03-edge-004-lever-a-source-class-diversification-design.md`) listed three candidate feed classes:

1. **Government / regulatory bulletins** (FBI press, DOJ press, State Department, Treasury OFAC, court PACER feeds) → `source_class="official"`
2. **Specialist analyst feeds** (think-tank trackers, sectoral newsletters) → `source_class="analysis"`
3. **Market microstructure** (Kalshi orderbook deltas, related-prediction-market quotes) → `source_class="market"`

This spec picks the **first concrete candidate** for A.1+. Subsequent feeds (A.1+1, A.1+2, ...) get their own per-feed specs landing in sequence.

## 2. First-feed candidate selection: SPECIALIST ANALYST (revised post-Codex 2026-05-03 empirics)

**Codex's 2026-05-03 candidate-feed sizing audit** (`docs/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md` + `scripts/simulations/lever_a1_plus_candidate_feed_sizing.py`, commit `2a15d55`) ranks the candidates empirically against the 13-day archive surface:

| feed class | MATCH_DIAGNOSTIC | OPPORTUNITY | PAPER_TRADE | median age sec |
|---|---:|---:|---:|---:|
| `specialist_analyst` | **251** | **21** | **3** | 2240 |
| `government_bulletin` | 63 | 1 | 0 | 2396 |
| `market_microstructure` | 0 | 0 | 0 | n/a |
| `mainstream_news` (current dominant) | 2,524 | 238 | 0 | 2233 |

**Specialist analyst dominates non-mainstream candidates** by a factor of 21× on OPPORTUNITY and is the **only non-mainstream class that produced any historical PAPER_TRADE** (3/3, all from specialist analyst sources — Kyiv Post / Times of Israel / Iran International / etc., already polled). Government bulletin reached just 1 OPP and 0 paper trades on the archive — same archive-shape problem as Lever A.1's classifier replay (those source classes don't currently fire on Kalshi-relevant topics).

**Original spec recommendation was wrong.** This spec was drafted recommending DOJ/Treasury (government bulletin) before Codex's audit landed. The empirical data inverts that recommendation.

| candidate | pros | cons | verdict (revised) |
|---|---|---|---|
| **Specialist analyst** (e.g., Atlantic Council, ISW, RAND, Carnegie Endowment, war-on-the-rocks, additional regional / sector specialists) | 21 OPP + 3 PAPER_TRADE on archive — only non-mainstream class with proven Kalshi-relevant signal. Existing specialist feeds (Kyiv Post / Iran International / bellingcat) already produce; adding more of the class multiplies the signal | Often paywalled or login-gated; some specialist titles get bucketed as `news` not `analysis` (per `_source_class_for_evidence`); selection requires curation | **First-feed candidate (REVISED)** |
| Government bulletin (DOJ press, Treasury OFAC, State, Defense.gov) | RSS-native, well-formed, low-frequency, high-quality. Classified as `official` after A.1 | Codex empirics: 1 OPP / 0 PAPER on 13-day archive — feeds don't currently produce Kalshi-relevant evidence at OPP surface | Second-feed candidate (deferred) |
| Market microstructure (Kalshi orderbook deltas) | Always-on; high volume; novel signal class | 0 OPP / 0 PAPER on archive (different pipeline shape; not measurable via RSS replay). Different monitor module | Third-feed; deferred |

**Recommendation (revised): ship 1-2 specialist analyst feeds first.** Concrete URL candidates worth probing:

- `https://warontherocks.com/feed/` (defense / national security analyst commentary)
- `https://www.csis.org/analysis/feed` (Center for Strategic and International Studies)
- `https://www.understandingwar.org/feeds/posts/default` (Institute for the Study of War — Russia/Ukraine analyst)
- `https://www.cfr.org/rss-feeds` (Council on Foreign Relations — broad geopolitical analyst)
- `https://www.atlanticcouncil.org/feed/` (Atlantic Council — broad national-security analyst)

Reasons:

- Empirically validated — specialist analyst class produced 21 OPP + 3 PAPER_TRADE on the 13-day archive.
- Topic match for the current Kalshi mix (Iran / Russia / Trump-administration analyst commentary) is high.
- RSS-native (verify each at landing time per §4 step 1).
- Source-class classification: most will bucket as `analysis` under the post-A.1 classifier; a few may bucket as `news`. Either is acceptable — both classes are higher-quality than `other` and feed corroboration into G2.

**Government bulletin moves to second-feed slot.** Reasons:
- Codex empirics show 0 PAPER_TRADE on archive — speculation that those feeds *would* produce evidence under post-A.1+ market mix is unproven.
- DOJ/Treasury still strategically valuable for a *future* market mix that includes regulatory-action-style markets (sanctions, indictments). Defer until Kalshi adds those market types or until specialist analyst stalls.

## 3. The fix

Three components:

### 3.1 RSS feed config addition (revised — specialist analyst)

```python
# config.py:RSS_FEEDS
RSS_FEEDS = [
    ...
    # ─── A.1+ first feed addition (2026-05-03 spec; deploy ≥ 2026-05-22) ───
    "https://warontherocks.com/feed/",                      # War on the Rocks
    "https://www.csis.org/analysis/feed",                   # CSIS analyst commentary
    # Optional second specialist feed in same commit (size first via §4):
    # "https://www.understandingwar.org/feeds/posts/default",  # ISW
    # "https://www.atlanticcouncil.org/feed/",                # Atlantic Council
]
```

URLs **must be live-probed at landing time.** Verify via `curl -I` that each returns 200 + `Content-Type: application/rss+xml` (or `text/xml`). Document the exact feed `<title>` for the operator's reference. If a URL is dead or paywall-gated, swap for a working alternative from the §2 candidate list before deploying.

### 3.2 Feed-source-label classification

Specialist analyst feed titles are NOT covered by the post-A.1 classifier's `.gov` / `un news` / `iaea` / etc tokens. They will bucket as either `news` (if their title includes a token in the news list — e.g., "Reuters", "Politico") or `other` (the catch-all fallback).

**Per-source classification expectations** for specialist analyst feeds:

- `"War on the Rocks"` — no current token match → `other`. **Recommend adding `"war on the rocks"` to A.1's news-token list** as a §2.2 expansion at deploy time. Or add `"analyst"` / `"strategic studies"` for broader coverage.
- `"CSIS"` / `"Center for Strategic and International Studies"` → no current token match → `other`. Add `"csis"` and / or `"strategic and international"` as new tokens.
- `"Council on Foreign Relations"` → no current token match → `other`. Add `"council on foreign relations"` and / or `"cfr"`.
- `"Institute for the Study of War"` → no current token match → `other`. Add `"institute for the study of war"` and / or `"isw"`.
- `"Atlantic Council"` → no current token match → `other`. Add `"atlantic council"`.

**These are A.1 spec §2.1 + §2.2 expansions** — extend Lever A.1's token list at A.1+1 deploy time (single `_source_class_for_evidence` edit; same risk profile). Bucket the specialist analyst sources to `analysis` class (not `news`), since `_SOURCE_CLASS_QUALITY` weights `analysis=0.60` vs `news=0.70`. The 0.10-weight delta is small but `analysis`-class signals also touch G2 differently; cross-class diversity is the lever.

Add a new `analysis`-class branch to `_source_class_for_evidence`:

```python
# main.py:_source_class_for_evidence — A.1+ companion patch
if any(token in lower for token in (
    "war on the rocks",
    "csis",
    "center for strategic",
    "council on foreign relations",
    "atlantic council",
    "institute for the study of war",
    "strategic studies",
    # ... per per-feed-deploy commit
)):
    return "analysis"
```

This branch is currently absent — `analysis` is a defined class in `analysis/evidence_scorer.py:_SOURCE_CLASS_QUALITY` (weight 0.60) but no source-string maps to it. A.1+1 lights up the class.

### 3.3 Optional source-mapping addition

Per Lever A.1 spec §2.3, a URL-based classifier override is the durable fix. **Defer to A.1+2 or beyond.** For A.1+1 (this feed), the token-list patch is sufficient if the canonical title strings cover the deployed sources.

## 4. Sizing methodology (Codex's pre-deploy task)

Pre-deploy validation cycle for the first-feed candidate:

1. **Live URL probe.** `curl -I` each candidate feed URL. Verify 200, content-type, no auth challenge.
2. **24-hour ingestion-rate audit.** Run a feed monitor against the URL for 24 h; count distinct headlines + their published timestamps.
   - Acceptance threshold: ≥ 5 events / 24 h (matches the umbrella Lever A spec §3 Stage A.1).
3. **Match-score audit on captured headlines.** Run each captured headline through `scripts/simulations/match_score_audit.py` against the current Kalshi market set. Count headlines with match score ≥ 0.06 against any market.
   - Acceptance threshold: ≥ 1 match / 24 h. If 0, the feed produces well-formed events but none are Kalshi-relevant — kill it and try the next candidate.
4. **Latency audit.** For each captured headline, measure `(now - published_at)` at ingestion. Compute distribution.
   - Acceptance threshold: ≥ 50 % of events arrive within 24 h of publication. The G6 recency floor at 0.30 cuts events older than ~24 h; sub-50% recency-fresh feeds produce mostly-stale evidence.
5. **Source-class verification.** Pipe each captured headline through the post-A.1 classifier. For specialist analyst feeds, verify ≥ 80 % bucket as `analysis` (per the §3.2 token expansion). Anything else means the classifier needs another token.

If all four acceptance thresholds pass, deploy the feed. If any fail, kill the candidate and run §4 against the next candidate (specialist analyst, market microstructure) per §2 ordering.

**Sizing-cost:** MED. Each candidate feed needs a 24 h live audit; serialise per-candidate.

## 5. Components touched

If the first feed lands cleanly:

- `config.py`:
  - `RSS_FEEDS` += [War on the Rocks + CSIS URLs (or substitute specialist analyst feeds per §2 candidate list)].
  - `EARLY_MAX_NEWS_AGE_BY_SOURCE` overrides if any feed is slow-cadence (specialist analyst feeds often emit < 5 events/day; the default 1800-s-cap may need lifting to 3600 s for these sources).
- `main.py:_source_class_for_evidence`:
  - May need additional tokens if feed `<title>` strings don't match the existing `.gov` / "department of war" / etc tokens. Verify at landing and amend A.1 spec §2.1 if needed.
- `tests/test_main_pipeline.py`:
  - New positive-control tests for the feed `<title>` strings → expected `source_class`. Same shape as the existing `TestSourceClassClassifierLeverA1`.
- No changes to `analysis/`, `tasks/`, `trading/`, or `feeds/rss_monitor.py` itself (RSS infra already supports the addition).

**Soak invariant:** intake-side change. Decision-path is unchanged. Per the same logic as MATCH-001 (intake quality lever), Wave-2 landing post-soak; cannot land mid-soak.

## 6. Acceptance criteria

- 1-2 specialist analyst RSS URLs live in `config.py:RSS_FEEDS` (War on the Rocks / CSIS / ISW / CFR / Atlantic Council per §2 candidate list).
- Pre-deploy 24 h ingestion rate ≥ 5 events / 24 h per feed.
- Pre-deploy 24 h match-score audit shows ≥ 1 match / 24 h at score ≥ 0.06.
- Pre-deploy 24 h latency audit shows ≥ 50 % events within 24 h of publication.
- Post-deploy 7 d EVIDENCE_INGESTION distribution shows `analysis` class records ≥ 5 / week (a meaningful lift over the archive's 0/260 baseline; bounded by feed cadence). Specialist analyst signals are the empirically-validated lever per Codex's 2026-05-03 sizing audit (21 archive OPP + 3 PAPER_TRADE).
- Post-deploy 14 d OPPORTUNITY → PAPER_TRADE conversion rate lifts above 3.4 % (the post-MATCH-001 baseline). **Closure threshold: ≥ 5 % over 14 d** — same as the umbrella Lever A spec.

## 7. Rollback

Rollback at three layers:

| trigger | revert mechanism | speed |
|---|---|---|
| Feed produces no Kalshi-relevant ingestion in 24 h | Remove the URL from `RSS_FEEDS`; redeploy | minutes |
| Feed fires high-volume noise (false-positive matches) | Remove the URL from `RSS_FEEDS`; redeploy. Or add the source string to a feed-suppression list | minutes |
| Feed crashes the bot (parse error, auth wall, infinite loop) | `launchctl bootout` per the post-soak rollback runbook §2; revert URL; redeploy | < 30 min |

No env-side fast revert — feed URL list is hardcoded in `config.py`. If the operator needs a kill switch, add an `EXTRA_RSS_FEEDS_DISABLED` env-var pattern in a follow-up commit.

## 8. Closure logic for EDGE-004

If A.1+1 (this feed) lifts conversion to ≥ 5 % over 14 d post-deploy: **EDGE-004 closes against Lever A.** Stop here.

If A.1+1 stalls (conversion below 5 %), proceed to A.1+2 (specialist analyst feed). After 2 honest A.1+ feed attempts, if conversion remains below 5 %:

- EDGE-004 escalates to **PROFIT-LLM-001** (signal-analyzer LLM unification, gated behind GOV.P4) or
- **P4-GATE Appendix A** market-mix work — both already ROADMAP-tracked outside EDGE-004 scope.

Per the lever menu §5: don't burn unbounded calendar time on a hypothesis the data didn't support. Two A.1+ feed attempts is the cap.

## 9. Risks

- **Feed URL drift.** Government feed URLs can be retired or restructured between this draft and 2026-05-22 deploy. Re-verify at landing time. Document live URL + feed title in the deploy commit message.
- **Auth / rate-limit operational debt.** Specialist analyst feeds may be paywalled, login-gated, or rate-limited (think tanks more than government bulletins). The MacBook + Mac Studio dual-host pattern (per project CLAUDE.md "concurrent Mac + Windows instances on the same network trigger Reddit 403s") makes shared-IP rate-limiting a known failure mode for high-frequency feeds. Specialist analyst feeds are typically lower-frequency than mainstream news so this risk is bounded; verify per-feed during the 24 h ingestion audit.
- **Latency tail.** Government RSS often emits hours-or-days after the underlying event. Per §4 step 4, latency-fail feeds get killed before deploy. But a borderline-latency feed (50-60 % within 24 h) may technically pass §4 and produce mostly-stale evidence in production. Mitigate with `EARLY_MAX_NEWS_AGE_BY_SOURCE` overrides if the post-deploy data shows G6 attrition.
- **Source-class classification gap.** If the feed's `<title>` string doesn't match A.1's token list, ingestion succeeds but the records bucket as `other` — defeating the purpose. Mitigate by §4 step 5 verification + amending A.1's token list at landing time.
- **Marginal trade-rate impact.** A.1+ feeds add new evidence; whether the LLM produces directional verdicts on those events is the open empirical question. The `evidence_scorer.source_quality(analysis) = 0.60` weight is solid, so each `analysis` event contributes meaningfully — but the LLM still has to call directionally on the event. Codex's empirics show specialist analyst sources DID produce 3/3 historical PAPER_TRADE on the archive (Kyiv Post / etc), so the directional-verdict pattern is empirically supported for this class — unlike government bulletin (0/0). **Still cannot fully size pre-deploy** because adding *more* specialist analyst feeds may not multiply linearly (some headlines may already be picked up by existing specialist feeds).

## 10. Soak-window contract

This spec is documentation only. No code changes pre-deploy. The 24 h ingestion / match-score / latency audits run during the post-soak base-stack validation windows (i.e., between Wave-1 deploys), giving the operator time to verify candidate feeds before the first A.1+ deploy on day 2026-05-22 at the earliest.

## 11. xfail harness pre-load decision

Defer until pre-deploy URL probe (§4 step 1) confirms the chosen URL is live. The harness shape:

- 1 positive-control test: feed `<title>` string classifies as `analysis` post-A.1+1 (per the §3.2 token-list expansion).
- 1 strict-xfail test: the feed URL appears in `config.py:RSS_FEEDS`. xfails today (URL not yet present); xpasses on deploy commit.

Land both as a follow-up commit once the URL is verified.

## 12. Out of scope

- **A.1+2 (specialist analyst feed) detailed spec.** Conditional on A.1+1's verdict; separate spec when needed.
- **Market microstructure feed (A.1+3).** Different pipeline shape (no RSS); separate spec when needed.
- **`PROFIT-LLM-001`** signal-analyzer LLM unification. Outside EDGE-004 scope entirely.
- **Refactor of `_source_class_for_evidence` to URL-based mapping.** Per A.1 spec §2.3, deferred to a follow-up.
- **Cross-feed deduplication.** A new feed firing on the same headline as an existing feed produces 2 ingestion events; existing `feeds/dedup.py` handles this. Out of scope.
