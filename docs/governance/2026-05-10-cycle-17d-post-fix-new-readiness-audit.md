# Cycle-17D POST_FIX_NEW Corpus Readiness Audit

**Date:** 2026-05-10
**Author:** Claude (via ECC `code-explorer` subagent)
**Tracking:** PROFIT-EDGE-012
**Trigger:** Operator request after schema audit (`6e626ea`) declared PRE_FIX rows unusable. Before locking option (c) ("pause Cycle-17 entirely; wait for POST_FIX_NEW vintage to accumulate"), confirm whether current post-Wave-1-cleanup-ship production logs are accumulating natively schema-compatible rows.
**Strict scope:** read-only; no edge-quality verdict; corpus-readiness evidence only.

## 1 — TL;DR

**Data is too sparse to be useful on any short timeline.** At observed production rates, the POST_FIX_NEW corpus would not reach cycle-16D's 272-row mass for **~4-5 months**. Phase-2 closing in 5 days will produce ~33 cumulative post-Wave-1 corpus-eligible rows. Even +30 days post-soak projects only ~171 rows — still smaller than the cycle-16D cohort that produced the 12-row admission ceiling in E2 + E3.

**Plus: the corpus builder has two unmapped-field bugs** that would silently null-out production-proxy fields on future POST_FIX_NEW rows even if volume eventually materializes. These are tractable code fixes (charter-permitted scorer-tooling exception) and would be the right interim work if the operator picks pause-and-wait.

## 2 — Data shape (post-Wave-1 window = 2026-05-10T03:35Z onward)

Wave-1 cleanup-only ship landed 2026-05-09T21:35 MDT = 2026-05-10T03:35 UTC (commit `c9df364`). The 2026-05-09 UTC archive is pre-ship; the 2026-05-10 live file is the first POST_FIX_NEW vintage data.

Observed in `logs/trades/live/trades.jsonl` over ~6.5h post-ship:

- 7 BLEND_DECISIONs
- 7 OPPORTUNITYs
- 5 SKIPPEDs (reasons: 2 G2_evidence_source_class_diversity, 2 G1_blended_confidence, 1 G6_recency_score)
- 2 PAPER_TRADEs (KXTRUMPCHINA-26MAY09-MAY13 / MAY14; not yet resolved)

**OBS-003 emission shape working correctly:** 7 OPPORTUNITY = 5 SKIPPED + 2 PAPER_TRADE (zero silent exits, invariant exact).

## 3 — Schema compatibility (sampled)

The Cycle-17D production-proxy admission requires 4 fields per row: `market_yes_price`, `confidence`, `edge`, `model_prob`. Source pipelines for `build_replay_dataset.py`:

| Pipeline | Source | Schema-compatible? |
|----------|--------|--------------------|
| `_paper_trade_rows()` | `data/paper_trades.db` | **PARTIAL** — `market_yes_price`, `model_prob` (`estimated_prob`), `edge` present; **`confidence` field NOT mapped** (DB has `llm_confidence` but builder doesn't read it); `news_class` absent from DB schema. |
| `_evidence_store_rows()` | `data/evidence_store.db` (dossier_updates) | **PARTIAL** — `model_prob` (`new_estimate`), `confidence` (`confidence_after`), `signal_source`, `news_class`, `headline` present; **`market_yes_price` hardcoded `None` by design**. Same gap as cycle-13 PRE_FIX rows. |
| `_log_rows()` | `logs/trades/*/trades.jsonl` (BLEND_DECISION + OPPORTUNITY + SKIPPED events) | **BROKEN — key-name mismatches.** Builder reads `model_prob`/`estimated_prob` but logs emit `blended_p` (BD) / `model_probability` (OPP) / `estimated_probability` (alt). Builder reads `market_yes_price` but SKIPPED logs emit `market_price`. Builder reads `ticker` but BD emits `market_ticker`. **Most production-proxy fields silently map to `None` on log-sourced rows.** |

**Implication:** the only schema-complete-enough rows for production-proxy admission today are `_paper_trade_rows()` outputs that have `confidence` filled — and the builder currently does not fill `confidence` from `paper_trades.llm_confidence`. So **0 currently-accumulating rows would clear the Cycle-17D Clause-A 4-field admission filter as-is**, even after Wave-1 ship. Two code bugs gate this.

## 4 — Volume + accumulation rate

Pre-Wave-1 daily rate (May 2-8 mean, excluding anomalous May-2 re-run spike): **~7.8 BLEND_DECISIONs/day**. Today (May 10 partial ~6.5h, 7 BDs): extrapolated **~26 BD/day** — likely China-news-cluster-driven spike, not steady-state.

**Corpus-eligible accumulation (the binding rate):**

| Source | Rate (post-Wave-1) | Notes |
|--------|--------------------|-------|
| Paper trades | **~0.8 PT/day** (7 PT total / 9 days of Phase-2 soak) | The only source with `market_yes_price + resolved_yes` — both required for IC §16 win evaluation |
| Dossier updates | ~1.8 DU/day | Lacks `market_yes_price` by design; not production-proxy-eligible |

**Projections (assuming current rate holds + corpus-builder bugs fixed):**

| Window | Days | Projected corpus-eligible rows |
|--------|------|--------------------------------|
| Today → 2026-05-15 (Phase-2 close) | 5 | ~33 cumulative post-Wave-1 |
| Post-soak → +14 days | 14 | ~97 |
| Post-soak → +30 days | 30 | ~171 |
| Post-soak → +60 days | 60 | ~298 |
| Post-soak → +90 days | 90 | ~424 |

**At ~0.8 PT/day, reaching cycle-16D-equivalent paper-trade mass (~272 rows) takes ~340 days.** Reaching a useful 30-paper-trade slice mass takes ~37 days. The admission ceiling math from E2/E3 says even 30 paper trades may not clear the 4-axis trades≥10 floor due to source/series/type/news-class slicing.

## 5 — Bottleneck inference

- **Admission funnel:** OBS-003 closed the silent-exit gap. Every OPPORTUNITY now produces a SKIPPED or PAPER_TRADE. No structural opportunity-loss.
- **Rate constraint:** `paper_price_sanity` dominance from pre-cutover archive (119 of 260 production-proxy skips on cycle-16D) is NOT visible in today's 5-skip sample (reasons split G1/G2/G6). Sample too small to draw structural conclusions — China-news cluster on the 50-cent KXTRUMPCHINA series may not trigger `paper_price_sanity` because prices aren't extreme.
- **Resolution constraint:** the 2 paper trades from today expire 2026-05-13/14 (within Phase-2 window). Resolution flips `resolved_yes` from `None` to `True/False`, making them IC §16-eligible. Without resolution, they're admission-counted but not win-evaluable.

## 6 — Two interim code bugs (filable as new debt or rolled into Cycle-17 pause work)

**Bug 1: `_paper_trade_rows()` doesn't map `llm_confidence`.**

`paper_trades.db` carries `llm_confidence` per row. `scripts/edge_replay/build_replay_dataset.py::_paper_trade_rows()` does NOT read it into the corpus row's `confidence` field. Result: every PT-sourced corpus row has `confidence=None`. Clause-A production-proxy admission requires `confidence`. Fix is a single line in the builder mapping `row["confidence"] = row["llm_confidence"]`.

**Bug 2: `_log_rows()` has multiple key-name mismatches.**

| Builder reads | Log key emitted | Affected event |
|---------------|----------------|----------------|
| `model_prob`/`estimated_prob` | `blended_p` | BLEND_DECISION |
| `model_prob`/`estimated_prob` | `model_probability` | OPPORTUNITY |
| `model_prob`/`estimated_prob` | `estimated_probability` | alt OPP variant |
| `market_yes_price` | `market_price` | SKIPPED |
| `ticker` | `market_ticker` | BLEND_DECISION |

Result: BLEND_DECISION + OPPORTUNITY + SKIPPED log-sourced corpus rows have null `model_prob` + null `market_yes_price` + (sometimes) null `ticker`. Fix is an additive mapping in `_log_rows()` to fall through these alternate keys.

**Charter compliance for both bug fixes:** scorer-tooling-fix exception per Cycle-17C charter §"Sole exception to single variable per experiment". No behavioral change; mechanical normalization. Fits cleanly into the Cycle-17D schema-audit / wrapper / sweep work batch as a precursor.

**Strategic value:** even if operator picks pause-and-wait, fixing these in the pause window ensures the eventual POST_FIX_NEW corpus is schema-complete from day one. Without the fixes, the +30-90 day accumulated rows would be silently degenerate (Clause A null-out).

## 7 — Verdict

**Corpus-readiness verdict for Cycle-17 resume after Phase-2 close (2026-05-15):**

| Window | Mass | Useful for cycle resume? |
|--------|------|--------------------------|
| 2026-05-15 (Phase-2 close) | ~33 rows | **NO** — too sparse; insufficient to admit any IC §16-eligible slice |
| +14 days post-soak (2026-05-29) | ~97 rows | **NO** — still below cycle-16D mass; paper-trade count ~14 = unlikely to clear trades≥10 floor on 4-axis grouping |
| +30 days post-soak (2026-06-14) | ~171 rows | **MAYBE** — paper trades ~30; first window where a single concentrated slice could plausibly clear trades≥10. **Data too sparse to project confidently.** |
| +60 days post-soak (2026-07-14) | ~298 rows | **MAYBE — likely useful** — paper trades ~50; comparable to cycle-16D admitted-row mass; matches E2/E3 cohort scale |
| +90 days post-soak (2026-08-13) | ~424 rows | **LIKELY USEFUL** — paper trades ~75; surpasses cycle-16D scale |

**Honest framing:** option (c) is **not a 5-day wait**. It is a **30-90 day wait** for useful corpus mass, contingent on production-rate assumptions holding. If the operator anchored on "wait 5 days for Phase-2" → that framing was over-optimistic. If the operator anchored on "wait until POST_FIX_NEW is genuinely viable" → that's 30-90 days.

## 8 — Out of scope (confirmed)

- Did NOT evaluate win/loss rates or edge quality.
- Did NOT propose corpus-builder fixes as commits in this audit (filing recommendation only).
- Did NOT modify any file.
- Did NOT read `.env` contents.

## 9 — Cross-references

- Charter: `docs/governance/2026-05-10-cycle-17d-charter-amendment.md`
- Schema audit (Codex commit `6e626ea`): `logs/edge_replay/cycle17d/schema_compatibility_audit.json`
- Predecessor structural findings: `docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md` + `docs/governance/2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md`
- Corpus builder source: `scripts/edge_replay/build_replay_dataset.py`
- Production logs: `logs/trades/live/trades.jsonl` + `logs/trades/archive/2026/05/`
- DBs: `data/paper_trades.db`, `data/evidence_store.db`
- Tracking: `PROFIT-EDGE-012`
