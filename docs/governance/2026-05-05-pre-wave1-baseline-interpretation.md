# Pre-Wave-1 baseline interpretation summary

**Type:** synthesis (Claude task per Implementation Contract §9 — review/planning).
**Source:** Codex's 4 baseline reports + cycle-5 trade-log inspection.
**Audience:** operator before Wave-1 deploy.
**Drafted:** 2026-05-05.

## TL;DR

**Critical finding:** post-cutover Mac Studio has produced **0 OPPORTUNITY / 0 SKIPPED / 0 PAPER_TRADE** events through 4 days. All Wave-1-spec regression rules that compare to a "pre-Wave-1 baseline" need rethinking — the baseline IS zero. **Affected rules:**
- `wave-1-post-deploy-observation-plan.md` rule "trade-rate > 2× pre-deploy 24h baseline" — undefined when baseline = 0
- Same plan's "MATCH_DIAGNOSTIC suppression-rate falls > 30% vs pre-deploy" — suppression-rate IS measurable (Codex audit returned MATCH_DIAGNOSTIC: 450)
- Same plan's "0 SKIPPED records emit" — rule fires by default; would NOT regress to 0 since 0 is the starting state

**Recommended fix to observation plan:** rebase regression rules from "ratio vs pre-deploy" to "absolute thresholds + post-deploy growth detection" for the SKIPPED / OPPORTUNITY / PAPER_TRADE signals. Suppression-rate + classifier distribution rules remain ratio-based since their baselines are non-zero.

## What the trade-log actually contains

Mac Studio `logs/trades/live/trades.jsonl` at 2026-05-05T14:04Z = **27,809 events** across 4 days post-cutover:

| event type | count | % | what it means |
|---|---:|---:|---|
| `EARLY_STALE_DROP` | 26,711 | 96.0 % | feed yielded a stale headline; pre-matcher kill |
| `MATCH_DIAGNOSTIC` | 450 | 1.6 % | match-score gate evaluation |
| `EARLY_FRESH_PASS` | 265 | 0.95 % | feed yielded a fresh headline; passed staleness gate |
| `MATCH_SUPPRESSED` | 175 | 0.63 % | match suppressed (matcher kill) |
| `NEW_MARKET` | 110 | 0.40 % | new Kalshi market discovered |
| `ANALYSIS_REJECTED` | 76 | 0.27 % | LLM rejected the analysis |
| `SIGNAL_ANALYSIS_DETAIL` | 22 | 0.08 % | LLM emitted a directional view but didn't survive readiness gate |

Notable: **0 OPPORTUNITY / 0 SKIPPED / 0 PAPER_TRADE / 0 BLEND_DECISION events.** The pipeline is firing through the matcher + LLM, but no candidate has reached BlendTask → readiness-gate evaluation in 4 days.

## Per-baseline interpretation

### Codex baseline 1: SKIPPED-rate (`docs/_archive/governance/2026-05-05-pre-wave1-skipped-rate-baseline.md`)

**Reported:** OPPORTUNITY=0; SKIPPED=0; PAPER_TRADE=0; SKIPPED rate = 0.0%.

**Interpretation:** The `OBS-003` Wave-1 commit emits SKIPPED records when a candidate fails the readiness gate. The Mac Studio post-cutover state has produced **zero readiness-gate evaluations** in 4 days, so SKIPPED-rate is undefined (0/0).

**Wave-1 deploy expectation:** post-OBS-003 deploy, the FIRST candidate that reaches BlendTask will produce either a PAPER_TRADE OR a SKIPPED record. Because the pre-deploy baseline is 0, the post-deploy regression rule "0 SKIPPED records emit in 24h post-deploy" is the default state — a non-fire is NOT a regression.

**Recommended replacement rule:** "Within 7 d post-Wave-1 commit 6 (Lever A.1 deploy), AT LEAST ONE BLEND_DECISION event emits in trades.jsonl. If 7 d passes with 0 BLEND_DECISIONs: investigate per the cycle-5 baseline interpretation — likely an upstream pipeline issue, not a SKIPPED issue."

### Codex baseline 2: OPPORTUNITY age proxy (`docs/_archive/governance/2026-05-05-pre-wave1-opportunity-age-distribution.md`)

**Reported:** 700 age samples; median 421.945s; p90 1669.0s; max 2610.2s. Source: 252 EARLY_FRESH_PASS + 448 MATCH_DIAGNOSTIC events.

**Interpretation:** even though no OPPORTUNITY events were emitted, the "age" of fresh-passed headlines + matched candidates IS measurable. Median 422s = 7 minutes between feed-discovery and matcher-evaluation. p90 1669s = 28 min. Max 2610s = 43 min.

**Wave-1 deploy expectation:** OBS-005 cooldown sentinel default fix doesn't change age distribution; it changes how `_cooldown_remaining()` reports for first-time-trade keys. **Age distribution should be unchanged post-OBS-005.** Any meaningful shift in p50/p90/max age post-Wave-1 deploy = upstream regression NOT caused by Wave-1 changes; investigate feed sources.

**Recommended replacement rule:** "Post-Wave-1, MATCH_DIAGNOSTIC + EARLY_FRESH_PASS age distribution stays within ±20% of baseline (median 422s ± 84s)."

### Codex baseline 3: cooldown distribution (`docs/_archive/governance/2026-05-05-pre-wave1-cooldown-distribution-audit.md`)

**Reported:** 0 cooldown SKIPPED rows.

**Interpretation:** because there have been 0 SKIPPED events, there are 0 cooldown-related SKIPPED events. Pre-OBS-005 baseline is unmeasurable from this signal.

**Wave-1 deploy expectation:** post-OBS-005, cooldown signals will appear in OPPORTUNITY records (as `cooldown_state` field) when first BlendTask candidate fires. The OBS-005 fix changes `_cooldown_remaining()` from returning `0.0` to returning `None` for first-time keys. **Detection rule:** post-OBS-005, first OPPORTUNITY event with `cooldown_state == "0.0"` for a never-traded ticker = sentinel-leak regression.

**Recommended replacement rule (per existing observation plan but enforced manually):** "First OPPORTUNITY in trades.jsonl post-OBS-005 deploy: confirm `cooldown_state` field uses None semantics, not 0.0 sentinel. If 0.0 leaks: rollback per `post-soak-rollback-runbook.md` §4.1."

### Codex baseline 4: per-ticker trade-rate (`docs/_archive/governance/2026-05-05-pre-wave1-trade-rate-per-ticker-baseline.md`)

**Reported:** 0 PAPER_TRADE rows.

**Interpretation:** Mac Studio post-cutover has produced 0 paper trades. The MacBook archive's 3 PAPER_TRADE events (KXFISAEXTEND series, all VitalLaw.com, all losses) are not in this trade-log; they live in `mac_archive/macbook_2026-05-01_import/`.

**Wave-1 deploy expectation:** Wave-1 commit 6 (Lever A.1 classifier patch) is archive-replay-confirmed neutral on trade-rate. No post-deploy increase expected. **The "trade-rate > 2× pre-deploy baseline" rule is undefined** because baseline = 0; any post-deploy paper-trade IS a >2× lift trivially.

**Recommended replacement rule:** "Post-Wave-1 commit 6, paper-trade rate over 7d window: target ≥ 1 paper-trade per 7d AT THE NEW A.1 classifier rate. Wave-2 Branch C deploy is what's expected to actually produce trades; Wave-1 Lever A.1 alone is hygiene. If > 5 paper-trades per 7d post-Lever-A.1: investigate — that's a 5× lift from a classifier-only change which the archive replay said wouldn't happen."

## What this means for Wave-1 deploy

**The 0-baseline reality changes the deploy posture.** Wave-1 commit 6 (Lever A.1) was already framed as "archive-neutral hygiene" per the EDGE-004 closure-path TLDR. The 0-baseline post-cutover confirms this framing: there is nothing for the classifier patch to revert because there are no trades-in-flight to disturb.

**Affected docs that reference the pre-Wave-1 baseline as "non-zero":**
- `wave-1-post-deploy-observation-plan.md` (already noted; cycle-5 follow-up)
- `2026-05-05-day-7-attestation-prestage.md` (mentions "267 decisions; 0 KILL_SWITCH/etc" — those are governance-side baselines and ARE non-zero; not affected)
- `2026-05-05-wave-1-deploy-day-timing.md` (timing recommendations — not affected by 0-baseline; agnostic to volume)

**Recommended cycle-6 follow-up:** apply the replacement rules from this doc back into `wave-1-post-deploy-observation-plan.md` as a §3.7 update. **Not blocking Wave-1 deploy** — the existing rules are not actively wrong, just degenerately satisfied.

## Why post-cutover has 0 OPPORTUNITY events

Honest read: the Mac Studio post-cutover has been running v0.29.58 + the cycle-2 + cycle-3 doc/script artifacts (which don't touch runtime). The bot is firing through the pipeline (27,809 events in 4 days), but the matcher + LLM combination isn't producing OPPORTUNITY events at the current `PAPER_MIN_MATCH_SCORE = 0.06` threshold + post-EDGE-001/002/003 readiness-gate semantics.

This is **consistent with the pre-soak forecast** — PROFIT-EDGE-004 was registered specifically because the matcher signal-quality / market-mix combination wasn't producing edge. The cycle-1 to cycle-5 work (Wave-1/2/3 specs + harnesses + baselines) is precisely the response to this: Wave-1 ships the OBS / MATCH / EXEC plumbing fixes; Wave-2 ships the new feed onboarding to LIFT the OPPORTUNITY rate; Wave-3 + Branch D are escalation if Wave-2 stalls.

**The 0-OPPORTUNITY baseline is a feature of the pre-Wave-1 state, not a bug to fix pre-Wave-1.** The deploys themselves are designed to lift the count.

## Cross-links

- `docs/_archive/governance/2026-05-05-pre-wave1-skipped-rate-baseline.md` — Codex baseline 1
- `docs/_archive/governance/2026-05-05-pre-wave1-opportunity-age-distribution.md` — Codex baseline 2
- `docs/_archive/governance/2026-05-05-pre-wave1-cooldown-distribution-audit.md` — Codex baseline 3
- `docs/_archive/governance/2026-05-05-pre-wave1-trade-rate-per-ticker-baseline.md` — Codex baseline 4
- `wave-1-post-deploy-observation-plan.md` — affected by 0-baseline rules (cycle-6 follow-up)
- `2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md` — governance-side baseline (not affected; non-zero)
- `docs/profit_path_debt_log.md` PROFIT-EDGE-004 entry — pre-soak no-edge framing (consistent)
