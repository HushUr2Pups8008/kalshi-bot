# Cycle-17D Halt — Historical-Corpus Degeneracy

**Date:** 2026-05-10
**Author:** Claude (per operator-confirmed halt decision)
**Tracking:** PROFIT-EDGE-012
**Halt scope:** Cycle-17D charter amendment (`docs/governance/2026-05-10-cycle-17d-charter-amendment.md`) AND broader-API-fetch sub-amendment (`docs/governance/2026-05-10-cycle-17d-broader-api-fetch-sub-amendment.md`). Both halted pre-experiment. **NO revert burned** — this is a pre-criteria-lock halt analogous to E2 axis-abandoned-before-criteria-lock outcome per cycle-17C charter.

## 1 — Operator decision (verbatim)

Halt recorded at commit `aeef26a`.

> Pick β. Accept the Stage 2 pre-audit finding as decisive and halt the broader-fetch sub-amendment before Stage 4/5/6. Do not lower the 95% threshold. Do not run the formal schema audit or GO/NO-GO sweep just to confirm an already decisive field-completeness failure.
>
> Record Cycle-17D as halted on historical-corpus degeneracy: merged corpus failed on PRE_FIX price coverage; broader-fetch failed on confidence/model_prob/edge completeness; all historical shapes converge to roughly the Cycle-16D effective admissible cohort.
>
> Preserve Stage 1 fixes and Stage 2 builder artifacts for future reuse. Primary path becomes POST_FIX_NEW accumulation with clean fields, then resume when enough native rows exist to test a materially broader corpus.

## 2 — Structural finding

Cycle-17D tested three historical-corpus shapes; all three converged on essentially the same effective production-proxy admissible cohort (~237-247 rows) — comparable to or smaller than the cycle-16D corpus that E2 + E3 already proved unable to clear IC §16:

| Corpus shape | Total rows | Effective admissible | Binding gap | Source |
|--------------|-----------:|---------------------:|-------------|--------|
| Merged (cycle-13_live + cycle-13_local + cycle-15B + cycle-16D) | 513 | 237 (POST_FIX_REBUILT only) | PRE_FIX rows lack `market_yes_price` (98.8%); Kalshi API 404 on retired trade endpoint | `6e626ea` schema audit |
| Broader-API fetch [2026-01-01 → 2026-05-10] | 566 | ≤247 | `confidence` 43.64% present; `model_prob`/`edge` 45.94% — bot never produced full evidence for many markets in fetch window | `15f3d47` pre-audit |
| Cycle-16D alone (reference) | 272 | 237 | (matches merged POST_FIX_REBUILT subset) | E2 + E3 |

**Different gaps, different root causes, convergent effective cohort.** Stage 1's Codex `build_replay_dataset` bug fixes (`dcaa7c3` + `a23d473`) are working correctly — the broader-fetch build used Stage-1-fixed helpers, so field-mapping is correct. The gaps in the broader-fetch corpus are genuine historical-data sparsity: for ~54% of the fetched markets, the bot never produced a full BLEND_DECISION (`confidence` + `model_prob` + `edge` together). This is upstream of corpus shape.

**Generalization:** any frozen-historical-data corpus the bot has accumulated to date will produce a similar effective admissible cohort. The structural blocker is the bot's pre-Wave-1 evidence/decision logging — not the corpus construction.

## 3 — Halt rationale

**Why not run Stage 4 formal schema audit:** Stage 2 step 2 build commit (`15f3d47`) emitted pre-audit field-completeness numbers that are not borderline. 43.64% `confidence` is far below the 95% sub-amendment §4 threshold; outcome of the formal Stage 4 audit is foregone. Running it consumes Codex effort to confirm a result already evident in the build manifest. **Operator override of §6 step 4 is a defensible discipline choice** when the pre-audit data is decisive (not when it's borderline).

**Why not run Stage 6 GO/NO-GO sweep:** Even if Stage 4 had been waived without operator override (i.e., if we accepted the 43.64%-completeness rows into the sweep), the sweep would run on ≤247 admitted rows distributed across multiple 4-axis intersected bins — structurally equivalent to cycle-16D's 12-row admission ceiling. Sweep would almost certainly NO-GO; same shape as letting E3 burn revert budget against a known structural ceiling. Saving the sweep run.

**Why not lower the 95% threshold:** The threshold exists to prevent silently degenerate corpora from being admitted to a sweep. Lowering it to 40-50% allows the broader-fetch corpus to proceed at the cost of reintroducing the cycle-16D-class anti-pattern risk (false-positive slices driven by missing-field cohort imbalance). The discipline of pre-registered admissibility thresholds is exactly what cycle-17D was supposed to formalize.

## 4 — Revert-budget impact

**Zero.** Cycle-17D never reached criteria-lock; no redesign experiment ran. Halt is analogous to cycle-17C E2 `axis_abandoned_before_criteria_lock` per the charter's pre-lock exception (no implementation commit landed for a redesign hypothesis; no revert counts).

Cycle-17D revert tracker (final state): **0/3 fresh start preserved; no reverts consumed.** When cycle resumes (under either Cycle-17D resume or a successor cycle), the revert budget is available untouched.

## 5 — Preserved artifacts (no rollback)

The following artifacts are retained for future reuse. **Do not delete or rebase out:**

### Code (Stage 1 fixes)

- `dcaa7c3` — `_paper_trade_rows()` maps `llm_confidence` → `confidence`; `_log_rows()` adds key-name aliases (`blended_p`, `model_probability`, `estimated_probability`, `market_price`, `market_ticker`)
- `a23d473` — canonical-key precedence + `_first_present()` None-aware fallthrough (replaces falsy-skipping `or`-chain on the two new alias paths)

**Future value:** Future POST_FIX_NEW corpus builds (production data after 2026-05-09T03:35Z Wave-1 ship) inherit these fixes automatically when `build_replay_dataset.py` is run. Without them, POST_FIX_NEW rows would silently null-out production-proxy fields on log-sourced events.

### Build scripts + tests

- `scripts/edge_replay/build_cycle17d_corpus.py` (`1336fe2`) — merged-vintage corpus builder
- `scripts/edge_replay/build_cycle17d_broader_corpus.py` (`15f3d47`) — live-API-fetch broader corpus builder
- `scripts/edge_replay/cycle17d_schema_audit.py` (`6e626ea`) — schema-compatibility audit tool
- `tests/test_cycle17d_corpus.py`, `tests/test_cycle17d_broader_corpus.py`, `tests/test_cycle17d_schema_audit.py`, `tests/test_build_replay_dataset_field_mapping.py` (12 tests across the fix + builder commits)

**Future value:** When POST_FIX_NEW corpus mass is sufficient (per resume conditions §7 below), reuse `build_replay_dataset.py` directly OR adapt `build_cycle17d_broader_corpus.py` to a fresh time window. Schema audit + field-mapping tests provide regression coverage.

### Corpora (artifacts on disk; not committed to repo)

- `logs/edge_replay/cycle17d/replay_dataset_merged.jsonl` — 513-row merged corpus (SHA `a0f5401b65acd9592e2dcc1c34bb0b9d0c76fe4718a2d714a9bc29160244f913` post-normalization)
- `logs/edge_replay/cycle17d-broader/replay_dataset_broader.jsonl` — 566-row broader-fetch corpus (SHA `917d04abcf8d4d45615ce2d328c164bcb98f4075a78ef26bd22c2b56fd32c102`)

**Future value:** If a successor cycle wants to test a specific hypothesis on a narrow slice that DOES have field completeness across these corpora (e.g., the 237 POST_FIX_REBUILT subset of the merged corpus), the data is on-disk and ready. Each corpus's SHA is pinned in its respective amendment for traceability.

### Governance documentation

- `docs/governance/2026-05-10-cycle-17d-charter-amendment.md` — parent charter amendment
- `docs/governance/2026-05-10-cycle-17d-broader-api-fetch-sub-amendment.md` — broader-fetch sub-amendment
- `docs/governance/2026-05-10-cycle-17d-criteria-lock-template.md` — 5-clause criteria-lock template
- `docs/governance/2026-05-10-cycle-17d-post-fix-new-readiness-audit.md` — POST_FIX_NEW readiness audit
- This halt-record doc

**Future value:** When cycle resumes, charter + sub-amendment + criteria-lock template are reusable as-is. The readiness audit establishes the rate baseline (~0.8 PT/day) for projecting when resume conditions §7 are met.

### Artifacts intentionally not produced after halt

- No broader-corpus formal schema-audit output at `logs/edge_replay/cycle17d-broader/schema_compatibility_audit.json`
- No `scripts/edge_replay/run_cycle17d_replay.sh`
- No `scripts/edge_replay/cycle17d_admission_sweep.py`
- No broader admission-sweep governance ledger
- No `E0''` broader-corpus baseline or downstream replay artifacts

## 6 — Primary path forward

**POST_FIX_NEW accumulation.** Phase-2 soak ends ~2026-05-15; POST_FIX_NEW rows accumulate at ~0.8 PT/day (per readiness audit). Stage 1 corpus-builder bug fixes ensure these rows are field-complete on arrival. No code work needed during accumulation — the pipeline is self-running.

Background activity during the wait:
- Phase-2 soak monitoring per `docs/governance/PHASE2_RUNBOOK.md` (operator daily check; closes 2026-05-15)
- PROFIT-PHASE2-001 acceptance target 2026-05-15
- PROFIT-CUTOVER-001 closes on first trade or 2026-05-15
- No Cycle-17 experiments authorized; no E4/E5/etc. criteria-lock attempts

## 7 — Resume conditions (operator-decision authority; quantitative threshold)

Cycle-17 may resume (under Cycle-17D charter, sub-amendment, or successor) when **all three** conditions hold simultaneously:

1. **POST_FIX_NEW row mass ≥ 200 production-proxy-complete rows.** Verifiable via `scripts/edge_replay/build_replay_dataset.py` + `cycle17d_schema_audit.py` against current production logs + DBs. Projected timeline per readiness audit: **+30 to +60 days post-Phase-2 close** (i.e. 2026-06-14 to 2026-07-14). "Production-proxy-complete" = all four fields (`market_yes_price`, `confidence`, `model_prob`, `edge`) populated.
2. **Outcome-blind admission-count sweep finds ≥1 4-axis intersected bin with ≥10 admitted rows.** Same threshold as Cycle-17D §6 step 5 GO/NO-GO. This is the genuine ceiling-lift check.
3. **Schema audit confirms ≥95% field completeness** on the operative corpus. Same threshold as the sub-amendment §4 admissibility requirement.

If all three hold, resume proceeds via: (a) operator picks corpus shape — single fresh-fetch, single POST_FIX_NEW snapshot, or POST_FIX_NEW + POST_FIX_REBUILT joint cohort with Clause E disqualifier; (b) Codex builds the corpus per the picked shape; (c) Claude pins SHA + updates whichever active charter applies; (d) Codex runs admission-count sweep + schema audit; (e) if GO, operator picks first experiment axis post-sweep; (f) E4 criteria-lock per existing template.

If condition (1) is met but (2) is NO-GO at sweep, the cycle remains halted pending more accumulation (operator can re-check at intervals).

**Resume timing:** earliest reasonable resume check = 2026-06-14 (+30 days post-soak). Operator runs a no-cost readiness check at any time (re-run `cycle17d_schema_audit.py` on current logs + DBs to count production-proxy-complete rows).

## 8 — Capital posture

PAPER-ONLY. Hard guardrail per Cycle-14 charter §5. Halt does NOT change posture. No experiment or sweep ran; nothing was authorized for live trading at any stage of Cycle-17D.

## 9 — Cross-references

- Cycle-17D parent charter: `docs/governance/2026-05-10-cycle-17d-charter-amendment.md` (superseded operationally by this halt record)
- Broader-API-fetch sub-amendment: `docs/governance/2026-05-10-cycle-17d-broader-api-fetch-sub-amendment.md` (superseded operationally by this halt record)
- Criteria-lock template: `docs/governance/2026-05-10-cycle-17d-criteria-lock-template.md`
- POST_FIX_NEW readiness audit: `docs/governance/2026-05-10-cycle-17d-post-fix-new-readiness-audit.md`
- Stage 1 bug-fix commits: `dcaa7c3` + `a23d473`
- Stage 2 build commits: `15f3d47` (broader-fetch) + `1336fe2` (merged-vintage)
- Schema audit (merged corpus): `6e626ea`
- Cycle-17C predecessor verdicts: E1 (`edge-replay-cycle17c-e1-report.md`), E2 (`2026-05-08-cycle-17c-e2-g1-admission-sweep.md`), E3 (`2026-05-10-cycle-17c-e3-flip-sign-counterfactual.md`)
- Tracking: `PROFIT-EDGE-012`

## 10 — Operator note

This halt is the second consecutive structural finding that the bot's pre-Wave-1 evidence/decision data does not support a production-proxy replay at IC §16 scale. The first was the within-cycle-16D admission ceiling (E2 + E3). The second is the historical-corpus-shape degeneracy (this halt). Both point to the same diagnostic: the only path to a workable cycle-17-class redesign is forward-going production data with clean field mappings (which Stage 1 has now ensured).

This is operationally a "wait for more data" decision, not a "cycle-17 is wrong" decision. The cycle-17 framework (single-variable redesign with IC §16 + market-implied baseline + cohort discipline + GO/NO-GO admission gates) remains the operative discipline when resume conditions are met.
