# News Sources — Comprehensive Evaluation

| Field | Value |
|-------|-------|
| Status | EVALUATION (analysis-only; no integration during active windows) |
| Date drafted | 2026-04-23 |
| Author | Claude |
| Companions | `docs/ROADMAP.md` Appendix A; `docs/profit_path_debt_log.md` |
| Execution window | Earliest post-P3-GATE for Tier 1 expansion; post-P4-GATE for Tier 2+ per Appendix A |

---

## 1. How news enters the bot today

### 1.1 Architecture at a glance

```
  feeds/
    __init__.py            — NewsItem contract (headline, url, source, published, body, item_id)
    rss_monitor.py         — 144 lines. Wire services + editorial RSS. feedparser-based.
    reddit_monitor.py      — 403 lines. Public-JSON polling; OAuth optional (not configured).
    gdelt_monitor.py       — 244 lines. DISABLED in DISABLED_NEWS_SOURCES.
    search_news_monitor.py — 298 lines. Per-market Google/Bing query lane. DISABLED
                             via DISABLED_SOURCE_FAMILIES ({google_news_query, bing_news_query}).
    google_news_monitor.py — 162 lines. *Not wired in main.py.* Dead or legacy.
    dedup.py               — 107 lines. Global HeadlineDedup across all sources.
    subreddit_selector.py  — 257 lines. Runtime subreddit selection + filtering.
    subreddit_discovery.py — 193 lines. Adaptive subreddit discovery.
```

All monitors share one contract: **emit `NewsItem` via an async `Callable[[NewsItem], Awaitable[None]]` enqueue callback** (`main.py::_enqueue_news`). Each is a separately-scheduled `asyncio.create_task` wired in [main.py:1750-1784](../../main.py#L1750-L1784).

### 1.2 Source declaration pattern

All declarative, in [config.py](../../config.py):

| Name | Purpose |
|---|---|
| `RSS_FEEDS` | List of URLs to poll on `RSS_POLL_INTERVAL_SECONDS` (60s) |
| `REDDIT_SUBREDDITS`, `REDDIT_CORE_SUBREDDITS`, `REDDIT_SUBREDDIT_TOPIC_MAP` | Reddit polling pool + topic mapping |
| `DISABLED_NEWS_SOURCES` | Exact-string blocklist on `NewsItem.source` |
| `DISABLED_SOURCE_FAMILIES` | Blocklist on the *family* axis (e.g. all `google_news_query` items) |
| `SOURCE_PRIORITY_TIERS` | Queue-priority override per source (currently populated with only 2 entries: Reuters, BBC) |
| `EARLY_MAX_NEWS_AGE_BY_SOURCE` | Per-source freshness-gate override (1800s for NYT / Guardian / AJ; default 300s) |

### 1.3 Current operational state (from 2026-04-22 bot.log)

| Source family | State | Notes |
|---|---|---|
| RSS | **Active** | 11 feeds watched; workhorse — 175 NEWS events/day observed |
| Reddit | **Degraded** | Public-JSON mode (no OAuth creds); 403 storm triggered the global circuit breaker on 2026-04-22 |
| GDELT | **Disabled** | In `DISABLED_NEWS_SOURCES` |
| Search news | **Disabled** | In `DISABLED_SOURCE_FAMILIES` |
| Fade-Kalshi-tweet | **Optional** | `FADE_TWEET_FEED_URLS` env var; defaults to empty |

Effective active sources: **8 RSS source organizations** (Reuters, AP, AJ, Guardian, Politico, NYT, RFERL, + any unconfirmed) and **0–1 subreddits** (when Reddit isn't circuit-broken).

---

## 2. Architectural assessment

### 2.1 What's working

- **`NewsItem` is deliberately narrow and sufficient.** Six fields cover every downstream consumer. No leakage of source-specific structure.
- **Monitor-per-family pattern is correct for diverse protocols.** RSS / Reddit JSON / GDELT API / search queries have genuinely different shapes; unifying them under a plugin system would be premature abstraction.
- **Disabled-set pattern preserves observability.** Entries aren't deleted; they sit with provenance, allowing one-at-a-time re-enable with measurement.
- **Async-generator + callback dispatch** fits INV-4 cleanly: `feeds/` owns I/O, `analysis/` stays pure.
- **Per-source freshness gates** (`EARLY_MAX_NEWS_AGE_BY_SOURCE`) let slow-cadence editorial sources in without lowering the wire-service bar.

### 2.2 Warts worth fixing

| # | Issue | Fix class |
|---|---|---|
| A1 | **Duplicate poll/dedup/error-isolation code** across 4 monitors. Rate-limit handling, backoff, circuit-breaker — all reimplemented per monitor. | Extract `feeds/base.py` with `PollingMonitor` mixin: backoff policy, per-source circuit breaker, error → health-event mapping. |
| A2 | **`feeds/google_news_monitor.py` (162 lines) is not wired in `main.py`.** Dead or legacy. | Audit and either delete or document. |
| A3 | **`SOURCE_PRIORITY_TIERS` is declared but barely populated.** Only 2 entries despite documenting 3 tiers. | Populate every active source; make missing-from-tiers a log warning. |
| A4 | **Reddit runs in "degraded mode" permanently.** 403 storms are structural without OAuth; the global circuit breaker papers over credential debt. | Obtain `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`; move to OAuth2 flow. This alone is the highest-leverage single change in this evaluation. |
| A5 | **Source-family blocklist is binary.** No throttling, no partial degradation. GDELT is "off" or "on" — nothing in between. | Introduce per-source rate-ceilings that short-circuit polling before the remote 429s. |
| A6 | **No live source-health telemetry.** Current health is reconstructed post-hoc via [scripts/source_scorecard.py](../../scripts/source_scorecard.py). | Emit `SOURCE_HEALTH` event (status changes, not per-poll). Match `MATCH_DIAGNOSTIC` pattern. |
| A7 | **`feeds/dedup.py` is global.** Cross-source dedup is semantically correct for identical headlines but can mask provenance differences that might matter for calibration. | Leave alone for now; revisit if PROFIT-CAL-001 exposes a per-source attribution need. |

### 2.3 Verdict on architecture

**Adjust, don't replace.** No foundational abstraction is broken. The monolithic-per-family pattern works, and the `NewsItem` contract is well-chosen. The fixes above are all additive (extract shared base, populate tier map, add OAuth, add health telemetry) — not a refactor.

The one structural decision worth revisiting is **A1 (base.py extraction)** if source count grows past ~8 families. Currently we have 4 active + 1 dead + helpers, and the per-file duplication is tolerable. At 8+ families it becomes painful.

---

## 3. Appendix A review and expansion

### 3.1 Appendix A validity check

Appendix A (post-OT&E news source options, investigated 2026-04-22) classifies candidates into three tiers with a defensible rationale. **All three tiers hold up under review.** Specifically:

- **Tier 1 (gov / NGO / wire breadth + Google Alerts + GDELT extended)** — correctly flagged as zero-cost, low-risk, drop-in additions.
- **Tier 2 (analytical depth, OSINT, cross-market, decentralized social)** — correctly flagged as higher integration work but justified for their signal niches.
- **Tier 3 (Telegram, ACLED, USGS/NOAA, Wikipedia)** — correctly flagged as either niche (Telegram for conflict reporting) or disaster-specific (USGS/NOAA).

The "explicitly skipped / deferred" section (X/Twitter, paid aggregators, freemium news APIs) is well-reasoned and should stand.

### 3.2 Additions / refinements I'd suggest

**Tier 1 candidates missing from Appendix A:**

| Source | Access | Rationale |
|---|---|---|
| **Kyiv Independent / Ukrainska Pravda (English)** | RSS | Fast Ukraine-specific reporting; directly supplements existing Guardian/AJ coverage on KXUKR markets |
| **Iran International (English)** | RSS | Fast Iran-specific reporting from diaspora press; directly supplements KXIRAN / KXTRUMPIRAN markets |
| **Times of Israel** | RSS | Direct Israel/Gaza/Lebanon coverage for KXMIDEAST markets |
| **Defense News / Breaking Defense (replace Defense One)** | RSS | Defense One is already in `DISABLED_NEWS_SOURCES`; the replacements have better signal-to-noise |
| **European Council / European Commission press feeds** | RSS | Complementary to existing US-centric gov sources; covers sanctions (relevant to Russia markets) |
| **Congress.gov daily digest** | RSS | Legislative action relevant to Trump-related markets (KXMOCTRUMP, KXTRUMPENDORSE, KXPARDONSTRUMP) |

**Tier 2 refinement:**

- **Polymarket cross-reference** deserves to be called out as a distinct **parallel-prediction-market lane**, not just another news source. The architectural home is probably a new monitor (`feeds/polymarket_monitor.py`) that emits a specialized `PredictionMarketQuote` item rather than `NewsItem`. This is a bigger architectural decision than "add a source." Flag for design-note discussion.

**Tier 3 refinement:**

- **Telegram** has a sub-component worth isolating: the open-source `BreakingNews` / `ReutersBreaking` / `ISW_Research` channels. These are bot-accessible via the Telegram Bot API without needing user authentication. Lower-risk than generic Telegram access; higher-signal than generic Wikipedia.

### 3.3 Skips I'd upgrade to "watch list"

- **X/Twitter via Playwright** was blanket-skipped in Appendix A citing ban risk. Realistic critique: if the bot needs breaking-news latency that RSS can't provide, there's no substitute — the wire services themselves post to X faster than they update their own feeds in some breaking cases. Don't *integrate* it, but track whether a latency gap appears post-CAL-001 that only X can close.

---

## 4. Process guardrails for future additions

### 4.1 Measurement protocol (expand Appendix A's A/B-per-source rule)

1. **Open a 24h baseline window** before the addition. Document: LLM-call volume, match rate, pre-LLM block rate, distinct dossier markets.
2. **Add exactly one source.** No bundling.
3. **Open a 24h post-add window.** Document the same four metrics.
4. **Compare.** Specifically: did block rate shift > ±5%? Did match rate shift > ±5%? Did LLM call volume exceed budget? Did any new parse errors emerge?
5. **Record in source scorecard + debt log.** If a source shifts key rates > ±5%, the next addition waits one full Section 13 window before proceeding.

### 4.2 Staging relative to Appendix A

Appendix A states: *"No integration until S4.5c COMPLETE and P4-GATE outcome known."* Per the operator-confirmed correctness-over-velocity priority (§6), that deferral stands as written. All additions — including zero-risk Tier 1 government RSS additions — wait until **P4-GATE outcome is known**.

Relative ordering within the post-P4-GATE queue remains:

- **Tier 1 government RSS** first (zero-risk, signal-predictable, drop-in).
- **Tier 1 Google Alerts per market** next, ideally paired with P3.2 (`market_specificity_score`) already COMPLETE.
- **Tier 2 Polymarket** after a dedicated design note (see §3.2); not a generic `NewsItem` source.
- **Tier 2 Bluesky / Mastodon** after Polymarket, lower priority.
- **Tier 3 Telegram** deferred indefinitely; revisit only if a latency gap emerges post-CAL-001.

*Prior draft of this section proposed unblocking Tier 1 gov RSS at P3-GATE PASS rather than P4-GATE. That shortcut is retracted per §6; retained here only as a note that the question was considered and rejected.*

---

## 5. Deliverables if this evaluation is adopted

None are execution-window-safe to perform now; all land post-window.

1. **Architectural prework** (post-S4.5c close, pre-expansion):
   - Audit and delete or document `feeds/google_news_monitor.py`.
   - Populate `SOURCE_PRIORITY_TIERS` fully.
   - Add `SOURCE_HEALTH` event type + emission (elevated priority per §7.3 — Reddit's permanent-degraded state makes partial-availability visibility more important).
   - Trim Reddit polling footprint to a curated subset (§7.2 step 1).
   - `feeds/base.py` extraction is **deferred** (see §8) — not pre-go-live.
   - `feeds/reddit_monitor.py` OAuth migration is **blocked externally** (Reddit app approval pending); no engineering effort until Reddit responds.

2. **Appendix A expansion** (update [docs/ROADMAP.md](../ROADMAP.md) Appendix A with the §3.2 additions).

3. **Post-P3-GATE source additions**, one at a time per the measurement protocol.

4. **Optional separate design note** for Polymarket cross-reference lane (if pursued).

---

## 6. Confirmed operator inputs (2026-04-23)

- **Reddit OAuth is externally blocked.** Operator submitted an app via Reddit's Responsible Builder Policy intake; no response received. Without OAuth the public JSON path is permanently rate-limited and the 403-storm / global-circuit-breaker behavior observed on 2026-04-22 is the expected steady state, not a transient bug. Treat Reddit as **degraded-permanent** and plan around it (see §7 below).
- **Go-live calendar priority: correctness > velocity.** Operator has explicitly deprioritized velocity — "done correctly and robustly before switching to real money" is the stated priority. This retracts the §4.2 staging shortcut suggestion (unblocking Tier 1 gov RSS at P3-GATE PASS). **Revert to Appendix A's original post-P4-GATE deferral for all additions.** Rigor > speed.
- **Gov-RSS API key pool** — still open. Most Tier 1 government feeds (State, White House, Treasury OFAC, DoD) are anonymous RSS with no registration barrier. The feeds that *do* require registration (e.g., some IMF/WTO endpoints) can be evaluated individually when the time comes.

## 7. Reddit strategy under the permanent-degraded constraint

The OAuth block is a hard external constraint, not a config issue. The question reframes: **what does the bot lose without Reddit, and should we replace it?**

### 7.1 What Reddit uniquely provided

| Reddit content type | Uniqueness | Replaceable? |
|---|---|---|
| Wire-service reposts (most of r/worldnews, r/news) | **Zero** — already in RSS directly | Yes — trivially; just use the upstream RSS |
| Firsthand ground-level reports (r/ukraine, r/iran natives) | Medium | Partial — Bluesky/Mastodon journalist timelines; Telegram conflict channels |
| Analytical discussion (r/CredibleDefense, r/geopolitics) | Low — too slow to be fast-lane signal | Yes — ISW / CSIS / CFR RSS cover this better |
| Upvote-curated "importance" signal | Unique but low-correlation with market-moving news | Not directly replaceable; not a clear loss |

### 7.2 Verdict

**The Reddit-unique signal was thin to begin with.** The degraded-permanent state is a mild coverage loss, not a crisis. Proposed course:

1. **Minimize Reddit polling footprint.** Drop from 20 subreddits/cycle to a small curated set (r/ArmedConflicts, r/CredibleDefense, plus 1–2 region-specific when relevant) to reduce the 403-storm attack surface without losing meaningful signal.
2. **Mark Reddit as degraded-permanent** in [docs/profit_path_debt_log.md](../profit_path_debt_log.md) so it's explicitly acknowledged and not rediscovered on every audit.
3. **Replace Reddit's "firsthand" content with Bluesky journalist timeline** once that integration is authorized (Tier 2 in Appendix A). Bluesky's open API has no equivalent gatekeeping to Reddit's.
4. **Replace "analytical" content with ISW/CSIS RSS** (Tier 2). Already planned.
5. **Do not spend further engineering effort trying to unblock Reddit OAuth.** If Reddit approves the app someday, re-enable is a config change, not a code change. Until then, don't wait.

### 7.3 Implication for architectural prework

The `feeds/base.py` extraction (§2.2 A1) becomes slightly less urgent — we're *shrinking* Reddit, not expanding it. But the `SOURCE_HEALTH` telemetry (§2.2 A6) becomes *more* urgent: with Reddit degraded-permanently, visibility into *partial* Reddit availability versus *full* circuit-open matters for knowing when we're actually missing something versus just absorbing a planned outage.

---

## 8. Revised priority summary (reflects confirmed operator inputs)

Given correctness-over-velocity priority, the post-window sequence should be:

1. **Architectural prework** (post-S4.5c close):
   - Audit/delete `feeds/google_news_monitor.py` (A2)
   - `SOURCE_HEALTH` event + emission (A6) — **bumped up** given Reddit's permanent-degraded state
   - Populate `SOURCE_PRIORITY_TIERS` fully (A3)
   - Trim Reddit to curated subset (§7.2 step 1)
   - Mark Reddit degraded-permanent in debt log (§7.2 step 2)

2. **PROFIT-CAL-001 execution** (independent, already designed).

3. **Observation + P2-GATE + P3 diagnostic tasks** proceed per the existing roadmap. No source changes.

4. **Appendix A integration** starts **only after P4-GATE outcome is known**, per the original deferral. Revert the P3-GATE shortcut proposal.

5. **`feeds/base.py` extraction (A1)** is deferred until either (a) a third architecturally-distinct source family is added, or (b) the per-monitor duplication produces an actual bug. Not a pre-go-live requirement.

The architectural adjustments have roughly equal priority to the source expansion — both serve the correctness objective.
