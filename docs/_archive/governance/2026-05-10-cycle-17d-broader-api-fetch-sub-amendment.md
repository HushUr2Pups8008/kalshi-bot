# Cycle-17D Broader-API-Fetch Sub-Amendment

**Date:** 2026-05-10
**Author:** Claude
**Parent Charter:** `docs/_archive/governance/2026-05-10-cycle-17d-charter-amendment.md`
**Trigger:** Cycle-17D amendment §4 Delta 5 + operator directive 2026-05-10. Merged-corpus approach halted at step 3 (schema audit) with structural blocker: cycle-13 PRE_FIX rows lack `market_yes_price` and Kalshi API returns 404 on settled-market trade endpoint. POST_FIX_NEW corpus-readiness audit confirmed produced data too sparse for any short timeline (~33 rows at Phase-2 close, ~171 at +30 days). Operator picks option (d) staged.
**Authority:** PROFIT-EDGE-012
**Status:** HALTED 2026-05-10 at commit `aeef26a`; Stage 4/5/6 intentionally not executed. See `2026-05-10-cycle-17d-halt-on-historical-corpus-degeneracy.md`.

---

## §1 — Corpus Scope

**Source.** Re-run `scripts/edge_replay/fetch_resolved_markets.py` (or equivalent live-API fetcher from the Kalshi public API) to retrieve all settled markets (status = `"settled"`) in a pre-registered time window.

**Time window (pre-registered; locked at sub-amendment land):**
- **START:** `2026-01-01T00:00:00Z` (after most of cycle-13 PRE_FIX window; far enough back to amass volume; avoids pre-Wave-1-cleanup production logs)
- **END:** `2026-05-10T00:00:00Z` (audit date; locked to prevent corpus drift post-land)
- **Search filter:** `close_time IN [START, END]`

**Filters applied (in order):**

1. **Ticker intersection:** retain only markets whose ticker is present in the evidence_store ticker set (the bot never saw evidence for excluded tickers; no point replaying blind).
2. **Sports-prefix blocklist:** apply the existing bot-side sports-prefix blocklist to exclude markets the bot does not trade (e.g., `KX*SXDOW*`, `KX*SXNFL*`, `KX*SXMLB*`). Rationale: the corpus must be marketable under the bot's existing categorical scope.
3. **Historical-price coverage:** require that the Kalshi API still serves a non-empty trades endpoint for the market (i.e., historical prices are available). Markets where Kalshi has retired the endpoint are excluded.

**Output location:** `logs/edge_replay/cycle17d-broader/replay_dataset_broader.jsonl` (NEW subdirectory; distinct from the halted `cycle17d/` merged-corpus path to avoid collision).

**Build script:** `scripts/edge_replay/build_cycle17d_broader_corpus.py` (NEW; lands in a SEPARATE commit after this sub-amendment per baton handoff to Codex).

**Single-cohort structure:** This corpus is a **single fresh-fetched cohort, NOT a multi-vintage merge**. All rows draw from the live Kalshi API at the same fetch timestamp, avoiding the schema-incompatibility problem that killed the merged corpus.

---

## §2 — Cohort Labels

**Single cohort label for all rows:** `POST_FIX_REBUILT`

**Justification:** The broader-fetch corpus uses fresh price + market data from the live Kalshi API, but is fed through the SAME `signal_analyzer.py` extraction prompt (no prompt-behavior change between cycle-16D and now). All rows share identical extraction provenance — the same LLM extraction regime that produced the original 272-row cycle-16D `POST_FIX_REBUILT` cohort. The broader-fetch corpus does NOT represent a new extraction vintage; it is a market-mix expansion on the same extraction baseline.

**Future-proofing:** If a future patch to `signal_analyzer.py` introduces behavior-changing extraction improvements, subsequent broader-fetch corpora would inherit a NEW cohort label (e.g., `POST_FIX_NEW` if that vintage becomes available, or a new label per the time-based naming convention). For THIS sub-amendment, all rows are `POST_FIX_REBUILT`.

**Clause E (cohort-drift disqualification) compliance:** Carries forward from the parent charter but is trivially satisfied for this single-cohort corpus (one cohort = zero cross-cohort confound). The cohort field is retained in the row schema for forward compatibility with multi-cohort future corpora.

---

## §3 — SHA Pin Process

**Build manifest:** The `build_cycle17d_broader_corpus.py` script writes a manifest file at `logs/edge_replay/cycle17d-broader/build_manifest.json` capturing:
- Row count (pre-filter and post-filter)
- Time window (START, END)
- Blocklist version (reference the bot's current sports-prefix blocklist commit)
- Scorer schema-compatibility result (status: ready-for-audit or needs-remediation)
- SHA-256 digest of `replay_dataset_broader.jsonl`

**Charter SHA pin (two possible SHAs):**

The broader-fetch corpus may require a schema-normalization patch (similar to the `market_family` field added in commit `6e626ea` for the merged corpus). Two SHAs may be pinned:

1. **Pre-normalization SHA:** raw fetch output, before any schema-fix patch.
2. **Post-normalization SHA:** after any schema-remediation, if needed.

Both are recorded in this sub-amendment §3 placeholder below, with explicit marking of which is which.

**Pin timing:** Charter pins the SHA in a **follow-up Claude commit** (mirror the `ca0ddb7` pattern: pin post-build, not pre-build). This allows the build script (`Codex baton`) to land first, then Claude immediately pins the SHA in an amendment follow-up commit, preserving the immutability guarantee.

**Corpus immutability:** Once pinned, the corpus is frozen. Any re-fetch or schema change requires an explicit unlock commit signed by the operator.

**Pinned SHAs (2026-05-10):**

> **Pre-normalization SHA-256** (raw fetch from Codex build commit `15f3d47`): `917d04abcf8d4d45615ce2d328c164bcb98f4075a78ef26bd22c2b56fd32c102`
>
> **Post-normalization SHA-256:** not produced. Cycle-17D halted before Stage 4 formal schema audit / normalization work. **Pre-audit field-completeness data reported by the build script (`15f3d47` commit message)** was accepted as decisive per the halt record: `confidence` 43.64% present; `model_prob` 45.94%; `edge` 45.94%; `headline` 92.76%.

**Build manifest summary** (per `logs/edge_replay/cycle17d-broader/build_manifest.json`):

- 566 replay rows
- 24 distinct markets with historical-price coverage
- Single cohort: POST_FIX_REBUILT
- Build script reused Stage-1-fixed `build_replay_dataset` (`a23d473`); field completeness is genuine historical-data sparsity, NOT a re-introduced mapping bug
- Reviewer fix landed before commit: fallback reconciles partial list-endpoint underfetch by probing missing evidence tickers individually

---

## §4 — Admissibility Requirements

**Per-row requirements for production-proxy eligibility (Cycle-17D Clause A):**

Every row in the broader-fetch corpus must carry the following fields for admission to the Clause-A production-proxy gate:

- `ticker` — market identifier (required)
- `decision_ts` — timestamp of the decision / evidence event (required)
- `signal_source` — source name for 4-axis grouping (required)
- `market_family` — market category for 4-axis grouping (required)
- `signal_type` — signal type (e.g., "news", "event") for 4-axis grouping (required)
- `news_class` — news classification for 4-axis grouping (required)
- `cohort` — cohort label per §2 (required for schema; single value `POST_FIX_REBUILT` for this corpus)
- `headline` — news text (required)
- `market_yes_price` — market YES price at decision time, in cents (0–10000 range; required for IC §16 evaluation)
- `confidence` — LLM confidence estimate (0–1 range; required)
- `edge` — computed edge (float; required)
- `model_prob` — LLM probability estimate (0–1 range; required)
- `resolved_yes` — resolution outcome, boolean (required for post-resolution admission to win evaluation)

**Schema-audit verification:** Extend `scripts/edge_replay/cycle17d_schema_audit.py` (built by Codex in commit `6e626ea` for the merged corpus) to also audit the broader-fetch corpus shape. Re-use the existing audit tool; do not reimplement. Audit writes `logs/edge_replay/cycle17d-broader/schema_compatibility_audit.json` with the same shape as the merged-corpus audit.

**Admission threshold:** ≥95% of rows must have ALL production-proxy fields populated (no nulls or missing keys for any of the 13 required fields above). If the audit reports <95% field completeness, the sub-amendment halts before the GO/NO-GO sweep (§5 halt criteria #1).

The formal broader-corpus schema-audit artifact was intentionally not produced after operator halt pick β because the Stage 2 pre-audit completeness failure was already decisive.

---

## §5 — Halt Criteria

Three independent halt triggers (any one halts the sub-amendment sequence and escalates to operator decision):

### Halt Criterion 1 — Schema-audit fail

**Trigger:** `schema_compatibility_audit.json` reports `production_proxy_ready: false` OR field-completeness < 95% on ANY required production-proxy field.

**Action:** Halt before GO/NO-GO sweep. Operator decides next step:
- Re-fetch with different filters (e.g., relax the sports-prefix blocklist, or tighten historical-price coverage requirement).
- Accept the gap and proceed to sweep (operator override required; documents the override).
- Escalate to a different corpus shape altogether.

### Halt Criterion 2 — GO/NO-GO admission-count sweep fail

**Trigger:** GO/NO-GO admission-count sweep (§6, sequencing step 6 below) returns NO-GO.

**Definition of NO-GO:** no single 4-axis intersected bin (`signal_source × market_family × signal_type × news_class`) has ≥10 admitted rows post-sweep, using the identical production-proxy gates as cycle-16E (price floor/ceiling 2¢/98¢, ticker cooldown 14400s, paper-duplicate prob/price 0.07/5.0, same-signal prob/price 0.02/2.0, PAPER_MIN_EDGE 0.02).

**Action:** Halt before any redesign experiment. Operator decides escalation:
- Re-fetch with adjusted filters.
- Proceed with post-sweep baselines even if NO-GO (operator override required).
- Pause Cycle-17 entirely.
- No redesign-axis experiment (E4+) lands until corpus scope is resolved.

### Halt Criterion 3 — Revert-budget exhaustion

**Trigger:** Three sequential post-sweep reverts burn the revert budget (per parent charter §4 Delta 6).

**Action:** Architectural-rethink rule fires (identical to parent charter). Cycle-17D halts; operator picks next cycle or axis direction.

---

## §6 — Sequencing (Post-Sub-Amendment Land)

Strict sequencing under this sub-amendment:

1. **Sub-amendment lands** (this commit).

2. **Build script lands** — Codex implements `scripts/edge_replay/build_cycle17d_broader_corpus.py` (live-API fetch + filter + build + manifest). Separate commit. Output: `logs/edge_replay/cycle17d-broader/replay_dataset_broader.jsonl` + manifest.

3. **SHA pin** — Claude pins the broader-corpus SHA-256 in this sub-amendment §3 (follow-up commit), mirroring the `ca0ddb7` pattern. Pre-normalization SHA recorded; post-normalization (if schema remediation lands) recorded in a second pin commit.

4-11. **Not executed after halt.** Steps 4-11 were authorized at filing time but intentionally not executed after halt at commit `aeef26a`. Not produced: `logs/edge_replay/cycle17d-broader/schema_compatibility_audit.json`, `scripts/edge_replay/run_cycle17d_replay.sh`, `scripts/edge_replay/cycle17d_admission_sweep.py`, broader admission-sweep ledger, `E0''` baseline, E4 criteria-lock, E4 replay, post-verdict ledger row.

12. **3-revert architectural-rethink rule** — preserved but not engaged. The halt occurred pre-criteria-lock; no revert budget was consumed.

---

## §7 — Cross-References

- **Parent charter amendment:** `docs/_archive/governance/2026-05-10-cycle-17d-charter-amendment.md` (authorizes the merger approach; this sub-amendment is a pivot option per that parent)
- **Cycle-17D halt record:** `docs/_archive/governance/2026-05-10-cycle-17d-halt-on-historical-corpus-degeneracy.md` (commit `aeef26a`; supersedes this sub-amendment operationally)
- **Schema audit blocker:** `logs/edge_replay/cycle17d/schema_compatibility_audit.json` (commit `6e626ea`) — the finding that motivated this sub-amendment (PRE_FIX rows structurally unusable)
- **POST_FIX_NEW readiness audit:** `docs/_archive/governance/2026-05-10-cycle-17d-post-fix-new-readiness-audit.md` — volume projection showing 30–90 day wait for useful POST_FIX_NEW mass
- **Cycle-17D corpus-builder bugs:** mentioned in readiness audit §6 (unmapped `llm_confidence` in `_paper_trade_rows()`; key-name mismatches in `_log_rows()`); fixable as Charter-permitted scorer-tooling-fix
- **Stage 1 corpus-builder fixes:** commits `dcaa7c3` + `a23d473` (landed before this sub-amendment)
- **Criteria-lock template (E4 clones from):** `docs/_archive/governance/2026-05-10-cycle-17d-criteria-lock-template.md` (includes Clause E cohort-drift disqualification)
- **Parent charter sections referenced:** §3(b) E4 axis-pick deferral, §3(c) revert-budget reset, §4 Delta 6 sequencing, §5 revert-budget tracker
- **Tracking:** `PROFIT-EDGE-012`

---

## §8 — Operator-Locked Invariants (Verbatim from Parent Charter)

All five invariants from the parent Cycle-17D charter apply unchanged:

1. **IC §16 acceptance criteria.** ≥1 (`signal_source × market_family × signal_type × news_class`) slice with `ev_ci_95_lo > 0` AND `trades >= 10`. Verbatim from `docs/IMPLEMENTATION_CONTRACT.md` §16.

2. **Market-implied baseline as null model.** NOT 50% coin-flip. Per memory `feedback_market_implied_baseline.md`. Each experiment reports actual wins vs `Σ p_yes_at_decision_time` for the trades it produces.

3. **PAPER-ONLY capital posture.** Hard guardrail per Cycle-14 charter §5. NO experiment in Cycle-17D flips this. Live trading authorization remains gated behind Cycle-17 §A deploy-candidate review.

4. **Fixed pre-registered corpus discipline.** Corpus locked at build-script-land time (before each experiment), not amendable mid-experiment. The broader-fetch corpus is locked at step 2 (build-script land) and pre-registered by window [START, END].

5. **Single-variable rule, revert default, criteria-lock-before-replay.** One hypothesis per experiment; only pre-declared changes with locked acceptance threshold; revert is the default outcome; criteria-lock commit lands before replay runs.

---

**Operator sign-off:** TBD by operator workflow.
