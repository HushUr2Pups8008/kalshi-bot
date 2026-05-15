# PROFIT-EDGE-004 Lever A Stage A.1 — fix `_source_class_for_evidence` classifier (prerequisite, not standalone lever)

**Status:** design — **REVISED 2026-05-03 post-Codex archive replay** (Wave 2 of post-soak landing, earliest deploy ≥ 2026-05-22; lands as a *prerequisite* for A.1+ feed onboarding, not as a standalone edge-production lever)
**Tracker:** `PROFIT-EDGE-004` Lever A → Stage A.1
**Owner:** Claude (design) + Codex (post-fix counterfactual replay; landed `e5b7213` / `8001a16`)
**Severity:** MEDIUM (revised down from HIGH after the archive replay showed standalone lift ≈ 0; severity reflects "prerequisite hygiene" not "edge-producing lever")
**Drafted:** 2026-05-03
**Last revision:** 2026-05-03 (same day) — revised scope post-Codex archive replay
**Empirical basis:** `docs/governance/2026-05-03-source-class-diversification-audit.md` + `docs/_archive/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md` (Codex archive replay) + existing `main.py:_source_class_for_evidence` source.

## 0. Revised scope header (added 2026-05-03 post-Codex archive replay)

The original spec framed Lever A.1 as a "first concrete action that arithmetically lifts the official count using feeds the bot is already polling" — predicting **≥ 30/260 official** post-fix on the 13-day archive. **Codex's 2026-05-03 archive replay falsified that prediction.**

**Codex empirics** (`docs/_archive/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md`, commit `8001a16`):

| surface | rows | flips under post-A.1 classifier | post-fix `official` |
|---|---:|---|---:|
| EVIDENCE_INGESTION | 248 | **N/A — 0/248 carry raw source strings** | unmeasurable |
| OPPORTUNITY (surrogate) | 260 | 2 (`other → news`: Breaking Defense × 1, Defense News × 1) | **1/260 (unchanged from pre-fix)** |
| MATCH_DIAGNOSTIC (surrogate) | 2,838 | 8 (`other → news`) | 63/2,838 (unchanged) |

**The 6 misclassified canonical sources** (Department of War / UN News / EC press releases / IAEA / Defense News / Breaking Defense) **do not appear at the OPPORTUNITY or EVIDENCE_INGESTION surface in the 13-day archive.** Defense News and Breaking Defense appear at MATCH_DIAGNOSTIC but flip to `news` (not `official`); the four `official`-target sources are absent entirely.

**Why those sources don't appear:**

- The RSS feeds may not have fired on Kalshi-relevant headlines during the archive window, OR
- They fired but were filtered upstream (matcher score floor, sport-prefix blocklist, pre-LLM gate), OR
- The evidence-pipeline join doesn't preserve their `source` field through to OPPORTUNITY.

Whichever cause, the empirical fact is: **classifier patch alone produces ~0 lift on the archive's `official` count.**

**Revised role of Lever A.1:**

1. **Prerequisite, not lever.** A.1 is hygiene work — fix the classifier so when *new* sources land via A.1+ (feed onboarding) or *existing* sources begin firing during a post-soak window, they get bucketed correctly from day 0. Without A.1, every A.1+ feed addition risks landing as `other`.

2. **No standalone empirical target.** The "≥ 30/260 official" target is removed from acceptance criteria. The classifier patch is correct on a per-source basis (verified by `tests/test_lever_a1_classifier_counterfactual.py`); whether the archive distribution shifts is downstream of feed-firing patterns the classifier doesn't control.

3. **Edge-production lever is A.1+.** Lever A's edge-production hypothesis lives entirely in **A.1+ feed onboarding** — adding new non-news feeds whose source labels the post-A.1 classifier will correctly bucket. Codex's audit found 0 `other → official` flips at the OPP surface; the only path to `official > 1` is *new sources we don't currently poll.*

4. **EDGE-004 closure-path implication.** Lever A's standalone path was already speculative (Codex's Lever B counterfactual showed B doesn't produce edge; C is risk-control; E closed; D demoted). With A.1's archive replay showing zero standalone lift, **EDGE-004's edge-production hypothesis is now solely "intake diversification adds new sources that produce new edge."** That's an open-ended bet on feed-onboarding empirics yet to be done.

**Spec body below is preserved — most of it is still correct on a per-source basis** (the token-list patch, the test cases, the rollback story). The §1 "surprise finding" framing is now historical context, not a claim about empirical lift; the §4 sizing methodology + §5 acceptance criteria's "≥ 30/260 official" target are obsolete and superseded by this §0.

## 1. Original "surprise finding" framing (preserved as historical context)

The Lever A umbrella spec frames Stage A.1 as "add new non-news feed modules" (government bulletins, specialist analyst feeds, market microstructure). The naive read of the source-class audit's `official=9/260` is "we don't have enough official feeds."

That read is **wrong** under inspection. `config.py:RSS_FEEDS` already includes:

- `https://www.whitehouse.gov/news/feed` (source string: `"News – The White House"`)
- `https://www.defense.gov/.../RSS.ashx?...` (source string: `"Department of War News Feed"`)
- `https://news.un.org/feed/subscribe/en/news/all/rss.xml` (UN News)
- `https://ec.europa.eu/commission/presscorner/api/rss?language=en` (European Commission)
- `https://www.iaea.org/feeds/news` (IAEA)
- Several others (Defense News, Breaking Defense — industry but defense-aligned)

Six+ official feeds wired. Yet the audit reports `official` on only 9/260 OPPORTUNITY events. Where does the loss happen?

**`main.py:_source_class_for_evidence` (lines 372-411) is the bottleneck.** The classifier maps runtime source-label strings to one of `{social, market, official, news, other}` via substring tests against a hand-coded token list. The list is incomplete:

| Source string | Token list match | Resulting class |
|---|---|---|
| `"News – The White House"` | `"white house"` ✓ | `official` |
| `"Department of War News Feed"` | none of `.gov` / `white house` / `state department` / `defense department` / `federal reserve` / `supreme court` / `congress` / `parliament` / `ministry` / `official` | **`other`** ❌ |
| `"UN News - Global perspective Human stories"` | none of the official tokens | **`other`** ❌ |
| `"Press releases - RSS"` (European Commission) | none | **`other`** ❌ |
| `"Top Stories From the International Atomic Energy Agency"` | none | **`other`** ❌ |
| `"Defense News"` | none of the official tokens | **`news`** (no — also no news token; falls through to `"other"`) ❌ |

At least 5 of the 6+ wired official-class feeds are bucketed as `"other"`. **The classifier is silently demoting government / institutional / IAEA traffic to the lowest-quality bucket.**

This explains the `official=9/260` audit result. The fix is not a new feed; it's a 5-line classifier patch that recovers feeds already in production.

## 2. The fix

Two complementary improvements to `main.py:_source_class_for_evidence`:

### 2.1 Expand the `official` token list

Add tokens for already-wired feeds:

```python
# main.py: _source_class_for_evidence — expanded official tokens
if any(token in lower for token in (
    ".gov",
    "white house",
    "state department",
    "defense department",
    "department of war",        # NEW — covers Department of War News Feed
    "department of defense",    # NEW — covers DoD variants
    "federal reserve",
    "supreme court",
    "congress",
    "parliament",
    "ministry",
    "official",
    "un news",                  # NEW — covers UN News
    "united nations",           # NEW — covers UN variants
    "european commission",      # NEW — covers EC press releases
    "press releases",           # NEW — broad catcher; positioned AFTER more-specific
    "international atomic energy agency",  # NEW — IAEA
    "iaea",                     # NEW — IAEA short form
)):
    return "official"
```

**Caveat for `"press releases"`:** the token is broad and may catch industry press too. Position it carefully so industry feeds (`"Reuters press releases"` etc.) still hit the `news` branch first. Verify during implementation by running the classifier against the union of all distinct source strings observed in the 13-day archive.

### 2.2 Add `"defense news"` / `"breaking defense"` to the `news` branch

Currently neither matches any branch, falling through to `"other"`. Defense industry press is `news` (analyst-adjacent, but emits market-relevant headlines). Add to the existing news token list:

```python
if any(token in lower for token in (
    "reuters",
    "associated press",
    ...
    "defense news",       # NEW
    "breaking defense",   # NEW
)):
    return "news"
```

### 2.3 Optional: replace token-substring with feed-URL-based classification

The existing classifier infers source-class from the runtime source-label string (RSS feed `<title>`), which is fragile (feed publishers can change the title and silently re-bucket the feed). A more robust pattern: maintain a `RSS_FEED_SOURCE_CLASS` mapping in `config.py` keyed by feed URL, and consult it in `_source_class_for_evidence`. Falls back to the existing token-substring inference if no mapping exists.

**Defer this refactor to a follow-up.** The 5-line token patch in §2.1 + §2.2 captures 80%+ of the lift; the URL-based refactor is a different shape of risk (config layout change) and needs its own validation cycle.

## 3. Components touched

Single function: `main.py:_source_class_for_evidence`. Token-list edits only.

Plus tests:

- `tests/test_main_pipeline.py` — pin the new classifications. Specifically: `"News – The White House"` → `official` (already covered), `"Department of War News Feed"` → `official` (NEW), `"UN News - Global perspective Human stories"` → `official` (NEW), `"Press releases - RSS"` → `official` (NEW), `"Top Stories From the International Atomic Energy Agency"` → `official` (NEW), `"Defense News"` → `news` (NEW), `"Breaking Defense"` → `news` (NEW).
- `tests/test_main_pipeline.py:test_signal_to_evidence_preserves_source_class_diversity` — re-run; expect a wider distribution than today.

**No changes to** `analysis/`, `tasks/`, `trading/`, `feeds/`, or `config.py` (in §2.1/§2.2; §2.3 would touch `config.py` but is out of scope here).

## 4. Sizing methodology (OBSOLETE per §0 — Codex archive replay falsified the prediction)

> **Superseded.** Codex's 2026-05-03 archive replay (`docs/_archive/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md`) found EVIDENCE_INGESTION rows do not carry raw source strings (0/248) and OPPORTUNITY surrogate replay shows post-A.1 official count stays at 1/260 — same as pre-fix. The methodology and target below are preserved as historical context only.

Original methodology (preserved):

1. Read every `EVIDENCE_INGESTION` event from `logs/trades/archive/2026/04/*.jsonl` and `logs/trades/archive/2026/05/*.jsonl`.
2. For each event, recompute `source_class` under the **post-fix** classifier.
3. Compare distribution: how many events flip `other → official`? `other → news`?
4. Re-run the source-class audit script (`scripts/simulations/source_class_audit.py` or whatever Codex used) against the post-fix classifier. Report:
   - ~~`official` count under the post-fix classifier (target: ≥ 30/260, i.e., 3.4× the current 9/260).~~ **Empirical actual: 1/260 — unchanged.**
   - `news` count change (likely small). **Empirical actual: 213 → 215 (+2).**
   - `other` count drop (the residual `other` bucket should shrink to truly-unknown sources). **Empirical actual: 46 → 44 (-2).**

Sizing-cost: LOW. The classifier is a pure function; replay is a single-pass over archived JSONL. No infrastructure required.

## 5. Acceptance criteria (REVISED post-Codex archive replay)

- `main.py:_source_class_for_evidence` updated per §2.1 + §2.2.
- New test cases in `tests/test_main_pipeline.py` pin the post-fix classifications (already pre-loaded as 6 strict-xfail tests + 1 positive control in `TestSourceClassClassifierLeverA1`, commit `7ff5a8b`).
- ~~Codex's counterfactual replay confirms ≥ 30/260 events flip `other → official`.~~ **Removed: empirically falsified per §0.**
- ~~Post-deploy 7 d EVIDENCE_INGESTION distribution shows `official` count rising in the predicted band.~~ **Removed: predicted band is empirically zero on archive data; no meaningful post-deploy lift expected from A.1 alone.**
- Per-source classification matches the reference classifier in `scripts/simulations/lever_a1_classifier_counterfactual.py:classify_post_fix`.
- Full pytest suite green; 6 strict-xfail markers in `TestSourceClassClassifierLeverA1` flip to xpass and are removed in the same commit as the classifier patch.
- A.1 is now a **prerequisite for A.1+** (feed onboarding) per §0; A.1's edge-production claim moves to A.1+'s acceptance criteria.

## 6. Rollback

Two-hunk single-file revert. Trivial. No persistent state to migrate.

Trigger to revert: the post-deploy 7 d EVIDENCE_INGESTION shows `official` count *exceeding* expected (e.g., 80 % of all events become `official` because `"press releases"` over-matches industry feeds). That's a token-list calibration issue; revert + tighten the offending token + re-deploy.

## 7. Soak-window contract

Documentation only pre-deploy. The fix is a decision-path-adjacent edit (the classifier feeds `evidence_scorer` weights, which feed blender confidence, which gates trading). Cannot land mid-soak. Wave 2 of post-soak landing order; earliest deploy ≥ 2026-05-22 (after the 4-item base stack stabilises).

## 8. Why this lands BEFORE intake-side feed additions

The Lever A umbrella spec listed three candidate feed-class additions. The classifier fix lands first because:

1. **Smaller blast radius.** Token-list edit vs new feed module + new auth + new poll loop.
2. **Cheaper sizing.** Single classifier replay vs full feed-onboarding audit (ingestion rate / match score / latency × N candidate feeds).
3. **Higher ROI per line of code.** Recovers existing official feeds the bot is *already polling* — pure measurement-side win, no operational debt added.
4. **De-risks the next A.1 step.** If the classifier fix alone lifts `official` to ≥ 30/260 and conversion rate climbs above the EDGE-004 closure threshold (5 % per the lever-A spec §4), Lever A closes without any feed-onboarding work. Saves 2-3 weeks of feed-validation cycles.
5. **Codex's audit predicts existing-feed-recovery is part of the lever surface.** The audit's read explicitly notes 142/260 single-class events — many of those are likely under-classified rather than truly mono-class.

Sequence within Lever A Stage A.1:

1. **Step A.1.0 — classifier fix (this spec).** Earliest deploy 2026-05-22. 7 d post-deploy validation.
2. **Step A.1.1 — first new feed (TBD: government bulletin or analyst feed).** Only if A.1.0 doesn't lift conversion to ≥ 5 % over its 14 d post-deploy window. Earliest deploy ~2026-06-05.
3. **Step A.1.2 — second new feed.** Only if A.1.0 + A.1.1 stalls. Per the umbrella spec's §4, abandon Lever A and proceed to Lever B after 2 new feeds.

## 9. Risks

- **Over-broad `"press releases"` token.** Catches industry press too. Verify against the union of source-string distinct values in the 13-day archive before deploy. If false-positive rate > 10 %, drop the token and rely on the more-specific tokens.
- **Token-substring ordering.** The current classifier orders branches: social → market → official → news → other. Adding `"un news"` to the official list AFTER the `news` token list would be wrong (UN News strings would hit `news` first). The fix preserves the existing branch order; verify token-list order is preserved during implementation.
- **Source-string drift.** RSS feed publishers can rename their `<title>`. The 5-line token patch is brittle to such drift. The §2.3 URL-based classifier is the durable fix; defer to a follow-up.
- **Source-class weight ladder interaction.** `evidence_scorer._SOURCE_CLASS_QUALITY` weights `official=0.85` vs `other=0.25`. Reclassifying 30+ events from `other → official` raises their weight ~3.4×. The downstream effect on blended confidence is positive but bounded; G2 (source-class diversity) becomes easier to pass; G1 (blended confidence) sees marginal lift. **No expected over-admittance** — the gates absorb the increase.

## 10. xfail harness pre-load

The classifier change is a small isolated function edit; harness pre-load is *cheap* and *valuable*:

- 7 strict-xfail tests pinning the new classifications:
  - `"Department of War News Feed"` → `official`
  - `"UN News - Global perspective Human stories"` → `official`
  - `"Press releases - RSS"` → `official`
  - `"Top Stories From the International Atomic Energy Agency"` → `official`
  - `"Defense News"` → `news`
  - `"Breaking Defense"` → `news`
  - `"News – The White House"` → `official` (regression guard for the existing case; not xfail, just a positive control alongside)

Add as a follow-up commit. Same pattern as OBS-005 / MATCH-001 / EXEC-002.

## 11. Out of scope

- **Feed-URL-based classification refactor (§2.3).** Larger config layout change; defer.
- **Source-class taxonomy redesign.** The 6-class enum is fine.
- **`evidence_scorer` weight tuning.** Lever A's umbrella spec §3 explicitly argues against weight-only changes. The classifier fix is volume-side; weights stay at current values.
- **New feed onboarding (Step A.1.1 / A.1.2).** Conditional on A.1.0's verdict; separate specs when needed.
- **`PROFIT-LLM-001`** signal-analyzer LLM unification. Outside EDGE-004 scope.
