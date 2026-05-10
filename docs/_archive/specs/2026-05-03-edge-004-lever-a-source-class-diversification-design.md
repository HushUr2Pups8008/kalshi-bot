# PROFIT-EDGE-004 Lever A — source-class diversification (revised post-2026-05-03 audit)

**Status:** design (Wave 2 of post-soak landing — earliest deploy ≥ 2026-05-22 after the 4-item base stack stabilises)
**Tracker:** `PROFIT-EDGE-004` (Lever A entry from `2026-05-03-edge-004-lever-menu-design.md`)
**Owner:** Claude (design) + Codex (sizing once OBS-003 is live)
**Severity:** HIGH (parent EDGE-004 closure path)
**Drafted:** 2026-05-03
**Source empirics:** `docs/governance/2026-05-03-source-class-diversification-audit.md`

## 1. What changed since the lever menu

The EDGE-004 lever menu (`2026-05-03-edge-004-lever-menu-design.md`) listed Lever A as "if a single source-class accounts for >60 % of zero-edge events, weighting that class down lifts conversion materially." Codex's 2026-05-03 source-class audit complicates that hypothesis:

- OPPORTUNITY source-class distribution (joined via `BLEND_DECISION.evidence_ids_contributing` → `EVIDENCE_INGESTION.source_class`):

| source_class | count | share |
|---|---:|---:|
| `news`     | 238 | 91.5 % |
| `other`    | 122 | 47 % |
| `official` |   9 |  3.5 % |
| `unknown`  |   9 |  3.5 % |

(Sums > 260 because 109/260 OPPORTUNITY records are multi-class; 142 single-class; 9 unknown.)

- Edge-sign breakdown (the 6 OPPORTUNITY events with non-zero edge): positive on `other` × 2, `news` × 1; negative on `other` × 3. Zero positive-edge events were `official`-driven.

Codex's read (verbatim): *"merely adding official sources is not the direct EDGE-004 unlock. The better next hypothesis is source-mix plus readiness/blending interaction: broaden non-news classes enough to improve G2/structural context, then verify whether G1/G6 still dominate after OBS-003 makes those kills visible in SKIPPED."*

**Implication for Lever A:** the simple "tweak `_SOURCE_CLASS_QUALITY` weights" interpretation is insufficient. The data does not support a thesis that an existing source-class is over- or under-weighted. The real Lever A is upstream — broaden the non-news intake mix so the `evidence_ingestion` log surface is less news-saturated.

## 2. Two-stage lever

### Stage A.0 — Calibration sanity-check (cheap, near-zero risk)

Before re-shaping intake, confirm the existing weight ladder is internally consistent.

- **Action:** verify `_SOURCE_CLASS_QUALITY` (`analysis/evidence_scorer.py:24-32`) reflects the operator's current beliefs about source quality. The existing values are: `official=0.85, news=0.70, analysis=0.60, market=0.55, social=0.35, other=0.25`, with a `_DEFAULT_QUALITY=0.25` fallback for `unknown`.
- **Expected outcome:** likely no change. The weights look defensible. This stage exists to *rule out* a stale-calibration regression (the file hasn't been touched since the original BSR-5 landing).
- **Cost:** ~30 minutes operator review of the constants. No code change unless the review surfaces an obvious inconsistency.
- **Outcome triggers Stage A.1.**

### Stage A.1 — Intake diversification (the actual lever)

The lever Codex's audit points at: broaden the non-news source-class mix so `evidence_ingestion` produces more `official`, `analysis`, and `market`-class records relative to `news`.

- **Mechanism:** add or unblock additional non-news feed sources in `feeds/`. Candidate categories (each requires its own narrow audit and config addition):
  - **Government / regulatory bulletins** (FBI press, DOJ press, State Department, Treasury OFAC, court PACER feeds) → `source_class="official"`. Currently 9/260 OPP touch official; pre-fix target ≥ 30/260.
  - **Specialist analyst feeds** (think-tank trackers, sectoral newsletters) → `source_class="analysis"`. Currently absent from the audit's table → either zero ingestion or sub-threshold matching.
  - **Market microstructure** (Kalshi orderbook deltas, related-prediction-market quotes) → `source_class="market"`. Already wired but light volume.
- **Expected impact:** moves G2 (`evidence_source_class_diversity` ≥ 2 distinct classes) from a kill gate (5/240 silent exits) to a routine pass. Combined with OBS-003's attribution clarity, this should compress the G1-dominant kill mass by lifting blended confidence on cases where structural / official corroboration appears.
- **Blast radius:** new feed modules in `/feeds`, new keyword / source allowlists in `config.py`, possibly new `evidence_ingestion` test fixtures. **No change to `analysis/`, `tasks/`, or `trading/`.** The fix is purely intake-side.
- **Sizing cost:** MED. Each new feed needs:
  1. A 24 h ingestion-rate audit (does the feed produce > 5 events/day?).
  2. A keyword-match audit (do the events match against current Kalshi market titles at score ≥ 0.06?).
  3. A latency audit (does the feed surface news within the 24 h G6 recency floor?).
  Reusable harness for all three: Codex's `match_score_audit` + a simple `feed_latency_probe` script.
- **Risk shape:** LOW-MED. Intake additions don't change any decision-path file. The risk is operational: more feeds = more rate limits / auth rotation surface area.
- **Dependencies:** Stage A.0 (cheap pre-check). Also benefits from OBS-003 already-landed because the pre/post comparison needs OBS-003's full SKIPPED stream to attribute kill-rate changes by gate.
- **Closure criterion:** OPPORTUNITY → PAPER_TRADE conversion rate lifts to ≥ 5 % over a 14-day post-deploy window (vs the post-MATCH-001 baseline of 3.4 % per the landing-order spec's Codex simulation), AND `G2_evidence_source_class_diversity` SKIPPED count drops to < 2 % of total SKIPPED.

## 3. Why not just bump the weights anyway?

Tempting but unsupported by the data:

- **Bumping `news` down (0.70 → 0.50)** would reduce news-only OPPORTUNITY confidence enough to push more candidates below G1's 0.05 floor. That trades a no-trade pattern for a different no-trade pattern.
- **Bumping `official` up (0.85 → 0.95)** has no leverage when only 9/260 events touch the class. The arithmetic ceiling is ~3.5 % conversion lift even if every official-touching event became a trade.
- **Bumping `other` (0.25)** is an under-determined call without first classifying *what* `other` actually contains. The audit groups `other = 122` events with no taxonomy — likely a mix of subreddits, blog feeds, and unclassified sources.

A weight tweak in isolation is a calibration fiction. The intake mix is what determines effective signal quality at the readiness gate; weights only modulate how the available mix flows through.

## 4. Sequencing

1. **Stage A.0** lands as a documentation-only commit (or a no-op constants commit if review surfaces a tweak). Day 0 of Lever A.
2. **Stage A.1 sizing** runs in parallel with the existing post-soak base stack (Steps 1–4). Codex sizes each candidate feed against the 13-day archive before any new feed code lands.
3. **Stage A.1 deploy** lands one new feed at a time, each with its own 7 d validation window. Wave 2 of the post-soak landing order. Earliest first-feed deploy: 2026-05-22 (after 2026-05-15 + 7 d base-stack validation).
4. **Lever A closure:** EDGE-004 closes against Lever A only after the 14 d post-deploy window confirms the 5 % conversion-rate target.

If after 2 new feeds the conversion rate has not lifted, **abandon Lever A and proceed to Lever D** (pre-LLM gate re-enablement) per the lever menu's sequence. Don't burn unbounded calendar time on a hypothesis the data didn't support.

## 5. Risks

- **Data-driven self-deception.** Codex's read is right that *intake* is the lever; my framing risk is that "broaden non-news" turns into "add three more news sources with different domain names" without genuinely reaching new source classes. Each new feed requires its `source_class` to be explicitly set in the feed module — verify on PR review, not just on commit.
- **Feed authentication / rate-limit operational debt.** Government feeds often require headers, polling discipline, or signed user-agent strings. The current MacBook + Mac Studio dual-host pattern (per CLAUDE.md "concurrent Mac + Windows instances on the same network trigger Reddit 403s") makes shared-IP rate-limiting a known failure mode.
- **Latency vs G6 recency floor.** Many `official` sources are slower than news (regulatory bulletins lag the news cycle). G6 floors evidence at recency_score ≥ 0.30 — too-slow sources never reach the readiness gate. The latency audit in §2 Stage A.1 sizing is the gate that filters candidate feeds.
- **Calibration knob abuse.** Stage A.0's "rule out stale calibration" might *tempt* the operator into a weight tweak as a quick fix. **Resist.** §3 covers why the math doesn't work in isolation.

## 6. Soak-window contract

This spec is documentation only and lands during the active `PROFIT-PHASE2-001` soak. No code changes pre-deploy. Lever A becomes operationally active in Wave 2 of the post-soak landing order, after the 4-item base stack stabilises.

## 7. Out of scope

- **All other levers from the EDGE-004 menu** (B, C, D, E, F). Each gets its own design when its turn arrives.
- **Appendix A market-mix work (P4-GATE).** Distinct from Lever A's intake-side broadening — Appendix A changes the *market* surface, not the *source* surface. Tracked separately in `docs/ROADMAP.md`.
- **Source-class taxonomy refactor.** The existing 6-class enum (`official` / `news` / `analysis` / `market` / `social` / `other`) is fine; "broadening" means re-balancing the *populations within those classes*, not adding new classes.
- **Calibration framework changes.** The G1 floor is Lever B territory.
- **`PROFIT-LLM-001` signal-analyzer LLM unification.** Out of EDGE-004 scope entirely.
