# Post-EDGE-004 escalation paths

**Drafted:** 2026-05-04 (during PROFIT-PHASE2-001 soak; pre-staged operator decision aid)
**Status:** ROADMAP-tracked; activates only if Wave-1 + Wave-2 + Wave-3 all stall against the EDGE-004 closure target (≥ 5 % conversion over 14 d)
**Companion docs:**

- `docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` — pre-deploy expected state across all waves
- `docs/governance/edge-004-closure-path-tldr.md` (v2.1) — operator-facing TL;DR
- `docs/profit_path_debt_log.md` — unified ROADMAP / debt tracker

## When this doc fires

Activates if the modal scenario from the unified Wave-1+2 forecast plays out:

1. Wave-1 base stack lands (Day 13). Conversion drops 3.4 % → 1.1 % by design (post-MATCH-001 + EXEC-002 tightening).
2. Wave-2 Lever A.1 lands (Day 13). Silent; no archive lift.
3. Wave-2 Lever A.1+ lands (Day 14+). Either:
   - Branch A (passive Google News observation): no VitalLaw / legal-niche surfacing in 14 d
   - Branch B (active VitalLaw direct RSS): probe fails or paywall-locks
   - Branch C (open-RSS legal analogues): deploy completes but conversion < 5 % over 14 d
   - Option-A parallel (specialist-geopolitics): deploy completes but conversion < 5 % over 14 d
4. Wave-3 Lever B (G1=0.04) lands (Day 28+). Predicted lift +1-2 PAPER_TRADE / 14 d; insufficient to reach 5 %.
5. Wave-3 Lever C (cross-series) lands (Day 35+). Risk-control only; +0 PAPER_TRADE.

After Day 35 with all of the above, EDGE-004 is **unclosable through intake-side levers**. This doc enumerates what comes next.

## Escalation Path 1: PROFIT-LLM-001

**Tracker:** `PROFIT-LLM-001` (ROADMAP-tracked outside EDGE-004)
**Owner:** TBD (operator + Claude)
**Soft-deadline:** 2026-07-15 (60 d post-EDGE-004 close-fail)
**Entry criterion:** all EDGE-004 intake levers (A.1 / A.1+ / B / C) deployed and not closing in 14 d each.

### Scope

Replace the qwen3:14b governance LLM with a stronger model OR change the LLM prompt structure to extract a sharper probability signal. The current LLM produces:

- Direction labels: `up / down / none` (fine)
- Magnitude labels: `none / small / medium / large` (coarse — most decisions land on `small` or `none`)
- Confidence: a 0-1 float, but empirically clusters in [0.3, 0.5] — too many low-confidence verdicts get killed at G1

PROFIT-LLM-001 re-tunes one of:

1. **Prompt restructure**: ask for explicit probability-shift estimates (e.g., "from p=0.20 to p=0.30") rather than coarse magnitudes. Tighter probability output → larger blender-side EV for the same headline.
2. **Model swap**: try `qwen3:32b` / `mixtral:8x22b` / `gpt-4o-mini` (operator preference). Larger model may output more diverse magnitude/confidence pairs.
3. **Few-shot calibration**: add 5-10 worked examples of historical PAPER_TRADE-producing headlines into the system prompt as anchors.

### Risks

- **Cost / latency increase**: larger models slow per-headline LLM call. May break the rolling-cooldown / throughput contract.
- **Validation requires another shadow-soak**: PROFIT-LLM-001 modifies decision shape, so 14 d shadow-soak is required (rerun PROFIT-PHASE2-001 with the new LLM).
- **Cannot run in parallel with EDGE-004 levers** (would confound attribution). Operator must close EDGE-004 first (formally STALLED, not COMPLETE).

### Soft-deadline rationale

60 d post-EDGE-004 close gives the operator time to evaluate Wave-3 outcomes and pick a PROFIT-LLM-001 starting point with full context. Earlier escalation = wasted prompt-tuning effort if Wave-3 already produced lift.

## Escalation Path 2: P4-GATE Appendix A market-mix expansion

**Tracker:** `P4-GATE` (ROADMAP-tracked; Appendix A is the post-EDGE-004 expansion path)
**Owner:** TBD
**Soft-deadline:** 2026-08-15 (90 d post-EDGE-004 close-fail)
**Entry criterion:** PROFIT-LLM-001 has either (a) closed EDGE-004 itself, freeing capacity, OR (b) been formally stalled.

### Scope

Expand the bot's market mix beyond the current geopolitical / sports / financial-headline focus. P4-GATE Appendix A enumerates the candidate market families:

- Cultural / entertainment markets (Kalshi `KXAWARDS`, `KXFILM` series)
- Sports prop markets (currently blocklisted via `SPORTS_PREFIX_BLOCKLIST`)
- Macroeconomic markets (`KXFEDFUNDS`, `KXCPI`, `KXNFP`)
- Election / political markets (post-2026 mid-term cycle)

Each market family requires:

- New keyword set (`KEYWORD_RULES`)
- New source-class allow-list (likely `mainstream_news` heavy, less specialist-analyst)
- New EV calibration (different volatility regime per family)

### Risks

- **Series-classifier creep**: each new family adds tokens to the keyword matcher, increasing false-positive risk on the existing geopolitical surface.
- **Multi-regime calibration**: the existing G1-G6 thresholds were tuned on geopolitical signals; a sports-prop or macro-data signal may have a different EV distribution shape.
- **Sports prefix unblocking risk**: `SPORTS_PREFIX_BLOCKLIST` exists for a reason (off-topic noise floods); removal must be empirically gated.

### Soft-deadline rationale

90 d gives time to fully exercise PROFIT-LLM-001 first. If LLM-tuning closes EDGE-004 or unlocks more EV from existing markets, market-mix expansion is the next logical step. If LLM-tuning fails too, market-mix is the last operational lever before declaring the bot's intake structurally ill-suited and pivoting to a different trading strategy entirely.

## Escalation Path 3: Strategy pivot (last resort)

**Tracker:** none yet (would create `PROFIT-PIVOT-001` if/when fired)
**Owner:** Operator only
**Soft-deadline:** 2026-12-01 (decoupled from EDGE-004 timeline; operator-discretion)
**Entry criterion:** PROFIT-LLM-001 + P4-GATE Appendix A both deployed and EDGE-004 still not closed.

### Scope

The bot's intake-side approach (poll RSS feeds → keyword-match → LLM verdict → blender → paper trade) may simply not have edge against Kalshi's market efficiency. Strategy-pivot options:

1. **Market-making** (provide liquidity rather than directional bets)
2. **Cross-market arbitrage** (within Kalshi or across Kalshi / Polymarket / PredictIt)
3. **Calendar / time-decay strategies** (close-to-expiry pricing inefficiencies)
4. **Closure** (formal end of the bot project; document lessons learned)

This path is operator-discretion only. Claude / Codex roles in this path are limited to research + scaffolding; strategy decisions are operator's alone.

## Cross-links

- `docs/profit_path_debt_log.md` — primary ROADMAP tracker (will reference this doc when escalation fires)
- `docs/governance/post-soak-rollback-runbook.md` — incident-response runbook (covers Wave-1/2/3 rollbacks; does NOT cover escalation)
- `docs/governance/post-soak-close-rehearsal-checklist.md` — operator deploy guide for Wave-1/2/3
- `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` — full lever menu (5-revision history)
