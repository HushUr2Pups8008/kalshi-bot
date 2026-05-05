# P4-GATE Appendix A pre-sizing scope spec

**Status:** design (defines the bounded sizing surface for Branch D second-handoff). NO code change.
**Authority:** Implementation Contract §11 + ROADMAP P4-GATE explicit gating. This spec defines the Appendix A scope when PROFIT-LLM-001 audit returns inadequate.
**Drafted:** 2026-05-05.
**Audience:** operator + Codex when Branch D fires AND PROFIT-LLM-001 is also inadequate.
**Companion:** `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §3.2 (Branch D handoff to P4-GATE Appendix A); `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md`.

## TL;DR

When PROFIT-LLM-001 sizing returns inadequate per its 4-axis audit, operator runs the bounded sizing audit defined in this spec. Audit produces a verdict: **"land P4-GATE Appendix A"** (narrower-scope market integration) OR **"P4-GATE Appendix A inadequate; EDGE-004 closes DEFERRED-CEILING."** Sizing surface is constrained to 3 dimensions (market-scope filter, intake-path expansion, market-resolution cadence) — not open-ended re-architecture.

## 1. What P4-GATE Appendix A means

P4-GATE Appendix A is the post-OT&E News Source Options + market-mix integration surface defined in `docs/ROADMAP.md` Stage 4 P4-GATE Appendix A (line 402+). Its core thesis: **the LLM produces 0.5-anchored verdicts because the headline corpus + market mix is too broadly scoped.**

The 13-day MacBook archive showed:
- Most matched headlines came from broad geopolitical sources (NYT World News / Reuters / etc.)
- Most matched markets are open-ended event-resolution (Will X happen by Y date?)
- LLM often correctly returns magnitude=none — the headline doesn't shift its base rate on the open-ended resolution question

P4-GATE Appendix A's hypothesis: **narrower markets** (date-encoded budget markets per `KXSBUDGETRES`-style; specific decision-event markets) + **narrower-scope sources** (foreign MFA press; sovereign-action feeds; IAEA/OFAC) produce headlines whose directional implications are **clearly** above or below the market price.

## 2. Sizing surface (3 axes)

Sizing audit constrains to these 3 dimensions:

### 2.1 Market-scope filter

**Question:** does narrowing the active-markets set to date-encoded resolution criteria + specific decision-event markets shift the LLM's directional-view yield?

**Audit:** Codex queries the post-Wave-1 active-markets surface and segregates into:
- "broad scope" markets (open-ended geopolitical / political tension / etc.)
- "narrow scope" markets (date-encoded budgets; specific decision events; exchange-rate windows; IPO outcomes; etc.)

Codex re-runs `signal_analyzer.py` against the same headline corpus but filters to only narrow-scope markets:

**Sizing output:** OPPORTUNITY-rate per market-scope class; PAPER_TRADE-rate per class; per-class realized P&L.

**Decision criterion:** if narrow-scope markets produce > 2× the OPPORTUNITY-rate of broad-scope markets, market-scope filter lands as P4-GATE Appendix A. Wall-clock cost: ~1-2 weeks Codex audit + filter implementation.

### 2.2 Intake-path expansion

**Question:** does adding narrower-scope sources (foreign MFA press / IAEA / OFAC / specific government feeds) lift the headline corpus's directional density?

**Audit:** Codex per-source feasibility audit:
- IAEA RSS (`https://www.iaea.org/news/feed`)
- OFAC sanctions feed (`https://ofac.treasury.gov/...`)
- US State Department press feed
- Specific foreign MFA feeds (UK FCDO; Israeli MFA; Iranian gov press)

Per-source: D1 RSS feasibility; D2 paywall friction; D3 classifier bucket; D4 Kalshi market overlap; D5 historical archive evidence (likely 0; new source); D6 operational stability (per `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` rubric pattern).

**Sizing output:** per-source onboarding feasibility + per-source projected directional-view yield.

**Decision criterion:** if 2+ narrower-scope sources are RSS-feasible AND project ≥ 1 PAPER_TRADE / 14 d each, intake-path expansion lands as P4-GATE Appendix A. ~1 week per source onboarding.

### 2.3 Market-resolution cadence

**Question:** does the bot's poll cadence on market resolution timestamps miss the directional window?

**Audit:** Codex compares LLM call timing vs market resolution timing in the 13-day archive — does the bot's match-and-call happen significantly before/after the market price has already moved?

**Sizing output:** per-market average lag from headline-time to LLM call to market-price-move; identifies cadence gaps.

**Decision criterion:** if the lag distribution shows the bot is consistently calling markets AFTER the price has moved, cadence change lands as P4-GATE Appendix A. **Caveat: cadence change** = governance-soak territory; requires a fresh PHASE2-N soak before promoting to real.

## 3. Sizing audit procedure

When Branch D fires AND PROFIT-LLM-001 audit returns inadequate, operator runs:

1. **Step A — Codex audits market-scope filter axis (§2.1)** — ~1 week. Likely the highest-EV axis (operates on existing data; no new source onboarding).
2. **Step B — IF market-scope insufficient, Codex audits intake-path expansion axis (§2.2)** — ~1 week per candidate source; iterative.
3. **Step C — IF intake-path insufficient, Codex audits market-resolution cadence axis (§2.3)** — ~1 week + soak overhead.

**At any step:** if the audit shows the axis is sufficient, STOP. Land P4-GATE Appendix A as that axis change.

If all 3 axes return "inadequate": **EDGE-004 closes DEFERRED-CEILING** per `edge-004-closure-path-tldr-v3.md` honest-read §"What 'closure' looks like (v3)". Operator decision: live-trade at 1 % conversion (not viable); pivot venue (out of scope); reset strategic frame.

## 4. What's IN scope

- **Market-scope filtering** in `feeds/markets.py` or equivalent.
- **New RSS feeds** in `config.py:RSS_FEEDS` for narrower-scope sources.
- **Cadence config knobs** in `config.py` for market resolution polling.

## 5. What's OUT of scope

- **Multi-venue trading.** Adding non-Kalshi exchanges is out of project scope.
- **Custom market-creation tools.** Operator creating their own narrower-scope Kalshi markets is out of bot scope.
- **News-aggregator subscription paywalls.** Paid feeds out of scope (per project budget; operator-discretion).
- **Real-time WebSocket feed consumption from new sources.** Wave-N+ territory; out of P4-GATE Appendix A scope.

## 6. Sizing output format

Codex audit produces `docs/governance/[date]-p4-gate-appendix-a-sizing-report.md` with:

- Audit window dates
- Per-axis yield + cost numbers
- Per-axis decision-criterion verdict
- Final recommendation: which axis (or "inadequate; EDGE-004 DEFERRED-CEILING")

**Length target:** ≤ 2,500 words.

## 7. Branch-D-second-handoff fire-time procedure

When operator's PROFIT-LLM-001 audit returns inadequate per `2026-05-05-profit-llm-001-pre-sizing-scope-design.md` §3:

1. **Document the verdict** in `docs/profit_path_debt_log.md` PROFIT-EDGE-004 entry: "PROFIT-LLM-001 sizing returned inadequate; escalating to P4-GATE Appendix A."
2. **Tag the moment:** `git tag -a edge-004-p4-gate-handoff-${UTC_DATE}`.
3. **Open P4-GATE Appendix A sizing.** Codex starts Step A (§3.1). Operator monitors; expects audit report within 7 days.
4. **Read audit report.** Operator verdicts which axis (or escalates to DEFERRED-CEILING).
5. **If axis-change recommended:** P4-GATE Appendix A lands as Wave 5+. Spec authoring + deploy timing TBD per axis.

## 8. Acceptance criteria (this spec)

This spec is satisfied when:

1. `2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §3.2 cross-references this spec.
2. `2026-05-05-profit-llm-001-pre-sizing-scope-design.md` §3 cross-references this spec as the second-handoff target.
3. The 3-axis surface is the canonical scope reference for any future P4-GATE Appendix A sizing audit.

## 9. Out of scope (this spec)

- Sizing audit execution. Triggers when PROFIT-LLM-001 audit returns inadequate; not pre-emptive.
- Per-axis implementation specs. Drafted post-sizing-verdict.
- DEFERRED-CEILING declaration shape. Outside this spec's scope; covered in TLDR v3 honest-read.

## 10. Cross-links

- `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §3.2 — Branch D handoff to P4-GATE Appendix A
- `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md` — first-handoff (this cycle)
- `docs/governance/edge-004-closure-path-tldr-v3.md` — closure-path-TLDR
- `docs/ROADMAP.md` Stage 4 P4-GATE Appendix A (line 402+) — original P4-GATE definition
- `docs/profit_path_debt_log.md` PROFIT-EDGE-004 entry — receives audit report cross-link
- `docs/IMPLEMENTATION_CONTRACT.md` §11 — authority basis
