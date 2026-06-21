# Paper-Mode Rapid-Learning Framework — Design Proposal

**Version:** v3 (2026-05-23 — amended per Codex independent review; see §10 for v1→v2→v3 changelog)
**Status:** Design only, no code. Operator decision required before any infrastructure build.
**Author:** architect agent (ECC), 2026-05-23; v2 amendments by main thread incorporating ECC `code-reviewer` adversarial review; v3 amendments by main thread incorporating Codex independent review.
**Reviewer status:**
- v1 reviewed adversarially by ECC `code-reviewer` (Claude lineage) — verdict APPROVED WITH AMENDMENTS (3 blockers + 2 must-resolve concerns). All 5 incorporated into v2.
- v2 reviewed independently by Codex (different model lineage) — verdict APPROVED WITH AMENDMENTS, **not safe to merge/activate**. Found 5 BLOCK-severity items (B, C, D, E, F) the prior Claude-lineage reviewer missed. All 5 blockers + 3 concerns incorporated into v3 per Codex's specific must-fix text. v3 is the candidate for Phase 0 operator approval.

---

## 1. Problem framing

**Operator constraint:** "we cannot continue to waste time at a 7-day clip... soak has its time and its place, but while in paper mode" — rapid learning while paper.

**Current cycle is broken for paper-mode because:**
- §8.5.1 calendar floors (7d early-close / 14d default) treat wall-clock time as a proxy for evidence, but paper-mode has zero money at risk — the calendar floor's job is to give the live-money system time to surface ordering / latency / safety regressions, not to validate edge.
- Bot rate is ~0.8 paper trades/day. POST_FIX_NEW gate requires ≥200 rows post-2026-05-13T00:02:37Z; earliest natural unlock is 2026-06-14. That is wall-clock-bound, not learning-bound.
- 26+ cycle-specific replay scripts at `scripts/edge_replay/` (manual assembly per cycle: `build_cycle17d_corpus.py`, `build_cycle17d_broader_corpus.py`, `cycle15b_common.py`, etc.) — no general harness, every cycle pays the assembly tax.
- Wave-1 observation windows (24h/48h/72h/7d in `tests/test_wave1_postdeploy_validation_windows.py`) are **manual contract pins**: operator writes PASS line into observation plan doc, then removes xfail-strict decorator. There is no automated closure.

**IC §16 legitimate scope (do not erode):** IC §16 (`docs/IMPLEMENTATION_CONTRACT.md:862-928`) exists because 11 cycles of safety/observability work shipped while the bot accumulated 3 lifetime paper trades and lost all 3. Its tripwire is "behavioral changes that affect money outcomes deploy only with replayed-EV evidence." That tripwire is correct for live-mode and stays untouched. The amendment opportunity is narrow: **paper-mode behavioral changes** where the cost of a wrong call is "wasted paper data" not "lost dollars."

---

## 2. Risk Tier Matrix

Four tiers. Tier assignment is by **blast radius**, not by code location.

> **v2 STAMP — Max-wins rule (per reviewer blocker B):** For any PR touching files mapped to multiple tiers, the **highest tier wins, no exceptions**. The tier classifier (I-5) computes `max(tier_of(p) for p in changed_paths)`. Unknown paths default to T3. **OVERRIDE: not allowed** — safety invariant.
>
> **v3 STAMP — Semantic classifier scope (per Codex blocker B):** The classifier consumes changed files PLUS config/env, model/dependency, DB schema migrations, prompt-template changes, runbook changes, and generated artifacts. Any unclassified runtime-affecting artifact is T3. The path-based max-wins rule is NECESSARY-BUT-NOT-SUFFICIENT; semantic detection is the second layer.

| Tier | Name | Trigger | Pre-deploy gate | Post-deploy observation | Rollback |
|------|------|---------|-----------------|-------------------------|----------|
| **T0** | Observability / safety / mechanical bug | IC §16 Rule 2 categories: SKIPPED-emission, log fields, kill switch, launchd, install scripts, cooldown sentinel-type bug fixes. No path to a paper or live trade decision changes. | Unit tests + lint. No replay needed. | 24h smoke (existing observation-canary mechanism). | Revert commit. |
| **T1** | Paper-mode behavioral, replay-decidable | Touches `/feeds`, classifier, blender, Trade Readiness Gate G1-G6 thresholds, sizing formula. Effect on edge is **measurable on existing replay corpora** deterministically. | (a) Replay-as-CI gate run against **≥1 pre-registered holdout corpus from a different market regime AND ≥2 market families** (see I-1 v3 amendment), producing per-trade EV + 95% CI table (IC §16 Rule 4 schema). Corpora not meeting the regime/family standard remain `IN_PERIOD_VALIDATION_ONLY` and CANNOT alone gate a T1 deploy. (b) Scenario suite green. (c) **Pre-gate cache coverage check passes** (see I-2 v3). (d) Verdict: positive replayed EV OR explicit "negative-evidence acceptance" memo per IC §16 Rule 5. **No calendar floor.** | 72h paper observation — variance gate, not calendar gate (see §5). Bot trades during window are flagged with a `contamination_window` cohort marker (I-10) and retained as a **separate OOS evaluation corpus** (not silently excluded). | Revert + corpus-rerun confirms reversion restored prior EV signature. |
| **T2** | Paper-mode behavioral, replay-indeterminate | Behavioral change whose effect depends on real-time signals not present in corpora (e.g., new feed source with no historical capture; new LLM prompt where cached outputs don't exist). | (a) Synthetic event corpus + cached-LLM stub gate (see §3). (b) Scenario suite green. (c) Pre-gate cache coverage check passes. (d) Operator approval memo naming which evidence gap forced T2 routing. | **Variance gate + calendar floor of 5d** (compressed from 14d; rationale: paper-mode no-money-risk + active replay infrastructure shortens the "have we surprised ourselves yet" half-life). Bot trades during window flagged with `contamination_window` cohort marker, retained as separate OOS eval corpus. | Revert + 24h sanity smoke. |
| **T3** | Live-mode transition OR sizing/capital change OR runtime-affecting infrastructure change | Paper→live cutover; Kelly fraction change; hard-cap change; **first live order on any new ticker class**; bankroll mutation logic; any change to config/env/model/dependency/schema/prompt that classifier I-5 cannot definitively map to T0/T1/T2; any "unknown" path. (Per Codex concern A: T3 trigger row is canonical and supersedes any narrower wording in §8 Q5.) | **IC §16 unchanged, in full.** ≥30 resolved markets, 95% CI per-trade EV positive, Rule 4 table, Rule 6 cohort separation. Dual-agent audit (`~/.claude/rules/agent_collaboration.md` high-assurance workflow). Operator gate. | §8.5.1 floors apply unchanged: 7d early-close minimum, 14d default. Wave-1 windows (24h/48h/72h/7d) unchanged. | Live kill switch + recorded incident; full IC §16 re-gate before next attempt. |

**Tier assignment is adversarial.** When in doubt between T1 and T3, route to T3. When in doubt between T1 and T2, route to T2. The default direction of doubt is **more gate, not less.**

**Calendar-floor substitution rationale (T1/T2 only):** `feedback_soak_acceleration_split.md` permits calendar-floor cuts when "volume gate cleared, safety zero." T1 satisfies both: pre-deploy replay gate is the evidence substitute; T0-categorized safety changes are explicitly excluded from T1.

---

## 3. Infrastructure pieces required

| # | Deliverable | File path (estimated) | Complexity | Dependency |
|---|-------------|----------------------|------------|------------|
| **I-1** | **General replay corpus builder** with **v3 regime/family OOS standard.** Inputs: date range, market-family filter, cohort tag, regime label. Output: JSONL with cohort flag per IC §16 Rule 6 + `corpus_window_start_utc` / `corpus_window_end_utc` + `market_regime` + `market_families` fields. **v3 STAMP — corpus diversity standard (per Codex blocker C):** "T1/T2 gates require **at least one pre-registered holdout corpus from a different market regime AND at least two market families**; adjacent calendar-month slices from the same news cycle remain `IN_PERIOD_VALIDATION_ONLY` and cannot alone gate a T1 deploy." Regime labels are pre-registered (operator declares them in `docs/governance/corpus-regimes.md` before the corpus is built) — examples: `pre_p0`, `post_p0_hotfix`, `wave_1_post_deploy`, `post_OBS_005_cooldown_fix`. May 31 + June 1 from the same news cycle is one regime, not two. All 6 existing corpora (2026-05-06→2026-05-10) are labeled `IN_PERIOD_VALIDATION_ONLY` until at least one regime-distinct holdout exists. **OVERRIDE ALLOWED:** operator may relax to "≥2 corpora regardless of regime" only with explicit memo at `docs/governance/<date>-corpus-diversity-override-memo.md` citing why genuine OOS coverage is not yet available. Override memo is itself gated by 30-day framework review per §5. | `scripts/edge_replay/build_corpus.py` (replaces `build_cycle17d*.py` lineage) | M | **Hard** |
| **I-2** | **Cached LLM-output store** with **v3 extended cache key (per Codex blocker D).** Cache key: `(row_id, prompt_template_hash, prompt_filled_hash, model_id, model_digest, ollama_version, endpoint_type, seed, temperature, num_ctx, sampler_options_hash, hardware_backend_class, response_hash)`. Temperature=0 alone is INSUFFICIENT for determinism on Ollama+qwen3 — operator-confirmed nondeterminism reports exist for seed+temperature=0 paths on multi-slot GPU configurations. Read-through cache: on cache miss, log + fail. **Seed runs require repeat verification:** each capture is verified by repeating once and comparing hashes; mismatch flags the row as `poisoned` and excludes it from any gate. Pre-gate coverage check ≥95% scans corpus rows for the full extended-key tuples and fails BEFORE observation window opens. **OVERRIDE: temperature pin not allowed; cache-key extension not allowed** (correctness invariants). Schema migration: current corpus rows lack these fields, so I-1 first-pass populates them from production capture going forward; pre-deploy corpora are marked `LLM_CACHE_INCOMPLETE` and cannot gate LLM-touching T1 changes until reseeded. | `scripts/edge_replay/llm_cache.py` + sqlite at `logs/edge_replay/llm_cache.sqlite` | M | **Hard** |
| **I-3** | **LLM-output capture hook** with **v3 expanded field set.** Production code path captures every LLM response (signal_analyzer + governance) into I-2 store. Each capture records the full I-2 cache key tuple. **Additive** — production behavior unchanged on capture failure (write-best-effort, log on miss). **v3 STAMP:** capture writes include the full `(seed, num_ctx, sampler_options, model_digest, ollama_version, endpoint_type, prompt_template_hash, hardware_backend_class, response_hash)`. Repeat-verification: each capture is re-run once with identical inputs; on hash divergence the row is marked `nondeterministic` and excluded from cache. Endpoint constraints preserved: signal_analyzer uses `endpoint_type="openai_compat"`; governance uses `endpoint_type="native"` with `think: False` per PROFIT-GOV-001 / CLAUDE.md gotcha. | `analysis/signal_analyzer.py` + `governance/llm.py` (additive call sites; do not refactor) | M | **Hard** (complexity upgraded S→M due to repeat-verification + 13-field cache key) |
| **I-4** | **Replay-as-CI runner.** Single entry point invoked from `pytest` or CI. Inputs: changed-file list, corpus list (filtered to non-`IN_PERIOD_VALIDATION_ONLY` and non-`LLM_CACHE_INCOMPLETE`), gate config. Output: pass/fail + Rule 4 table written to `logs/edge_replay/ci_runs/<commit>/`. Uses all eligible corpora by default; opt-out requires memo. | `scripts/edge_replay/replay_gate.py` + `tests/test_replay_gate_smoke.py` | M | **Hard for T1; soft for T0** |
| **I-5** | **Tier classifier** with **v3 expanded semantic scope (per Codex blocker B).** Consumes changed files PLUS config/env edits, prompt-template hash diffs, model/dependency manifest diffs (pinned model_digest changes, ollama version changes), DB schema migration files, generated artifact diffs (e.g., `requirements.txt`, `pyproject.toml`, `governance/prompts.py` constants, `*.plist.template`), and operator runbook changes that affect runtime behavior. Rule: any unclassified runtime-affecting artifact → T3. Path-based max-wins still applies as the inner reduction; semantic detection adds inputs to the union. Signature: `classify_tier(changed_paths, config_diff, prompt_template_diff, model_manifest_diff, schema_migrations) -> Literal["T0","T1","T2","T3"]`. Append-only ledger at `logs/edge_replay/tier_classifications.jsonl` records the full input fingerprint, not just file paths. **OVERRIDE: not allowed** for the routing rule. | `scripts/edge_replay/tier_classifier.py` + `tests/test_tier_classifier.py` | M | **Hard** (complexity upgraded S→M due to semantic-scope expansion) |
| **I-6** | **Scenario suite — fixed corpus of adversarial events.** Hand-curated JSONL with rows for: FISA-burst, suppression negation/hedging, keyword-direction flip, qwen3 anchor-rate polarity (`governance/prompts.py:27-31` gotcha). Append-only per Q3 default. | `tests/scenarios/*.jsonl` + `tests/test_scenario_suite.py` | M | **Hard** |
| **I-7** | **Variance gate calculator** with v2 stamped metric. Primary signal: decision-rate stability + unexpected-SKIPPED-bucket (NOT EV CI half-width in 72h windows; resolution lag makes EV unworkable). | `tasks/stats/variance_gate.py` | M | **Hard for T1/T2** |
| **I-8** | **Wave-1 automated closure** — Phase 2 (per v2). Consumes I-7 variance gate output to flip xfail-strict markers in `tests/test_wave1_postdeploy_validation_windows.py`. | `tests/conftest.py` plugin + edits to existing wave-1 test module | M | **Hard** |
| **I-9** | **Synthetic market-resolution harness.** | — | L | **EXPLICIT DECISION: DO NOT BUILD.** |
| **I-10** | **Corpus contamination-window marker (v3 — apples-to-apples retention per Codex blocker E).** During T1/T2 observation windows, bot continues paper-trading. Those rows are flagged with `cohort_extension="contamination_window:<change_id>:<window_id>"`. **v3:** contamination-window rows are **excluded from pre-deploy gates** for OTHER changes BUT are **retained as a separate OOS evaluation corpus** (`logs/edge_replay/contamination_corpora/<change_id>.jsonl`) for the T1 retrospective (I-11). They are NOT silently discarded — that would create the sampling bias Codex flagged. The contamination corpus IS the gold-standard "this is what the bot did under candidate code" measurement. | DB schema migration in `data/paper_trades.db` + write-side in `tasks/blend_task.py` + read-side in I-1 + retention-side new module `scripts/edge_replay/contamination_corpus_manager.py` | M | **Hard** (complexity upgraded S→M due to retention requirement) |
| **I-11** | **T1 retrospective hook (v3 — baseline-vs-candidate delta EV per Codex blocker F).** 7 days after a T1 deploy, the framework runs the retrospective on the **same 7-day flagged contamination-window rows AFTER they resolve** (or as resolution arrives, with explicit `unresolved_at_retrospective_time=N` count). **Replays BOTH the baseline code path AND the candidate code path on identical input rows**, then compares delta EV/sign. The original pre-deploy replay-CI verdict's predicted EV is NOT the apples-to-apples comparator (different population). The apples-to-apples comparator is `baseline(rows) - candidate(rows)` on the same row set. Systematic sign divergence (>25% of T1 cycles) suspends framework per §5. | `scripts/edge_replay/t1_retrospective.py` + cron entry | M | **Hard** |
| **I-12** | **Cache integrity check (v2).** On every cache read from I-2, verify `sha256(canonical_json(response)) == stored_response_hash`. Mismatch → `CacheCorruptionError`, T1 gate fails-closed. **v3 extension:** I-12 also runs the repeat-verification check at capture-time (per I-3 v3) and flags rows that diverge as `poisoned`. | Read-path + capture-path additions to `scripts/edge_replay/llm_cache.py` | S | **Hard** |
| **I-13** | **Framework readiness integration test.** Single test that imports I-1, I-2, I-3, I-4, I-5, I-6, I-7, I-8, I-10, I-11, I-12 and asserts importability + signature presence + fail-safe behavior. **IC §16 amendment merge is BLOCKED until this test passes in CI.** | `tests/test_framework_readiness.py` | S | **Hard — gates IC §16 amendment merge** |

**Build order (v3):** I-5 (tier classifier with semantic scope) → I-3 (LLM capture with extended fields + repeat-verification) → I-1 (corpus builder with regime/family fields) → I-2 (LLM cache with extended key) → I-12 (cache integrity + repeat check) → I-6 (scenario suite, parallel) → I-10 (contamination retention, parallel) → I-4 (replay-as-CI gate) → I-7 (variance gate) → I-8 (Wave-1 automated closure) → I-11 (T1 retrospective with baseline-vs-candidate replay) → I-13 (integration test, last).

---

## 4. IC §16 amendment proposal (v3)

**Amend `docs/IMPLEMENTATION_CONTRACT.md:862-928`. Specific edits:**

**v2 amendment to the Scope block (line 916):**

> **APPEND to existing Scope text:**
> "For paper-mode behavioral changes, blast-tier routing per §16.7 governs. Rule 1's ≥30-markets threshold applies only to T3 (live-mode/sizing/capital/runtime-infrastructure). T1/T2 changes use the replay-as-CI gate threshold defined in §16.7. The Scope block continues to apply to all behavioral changes — §16.7 determines WHICH threshold applies per tier, not whether IC §16 applies."

**At line 870 (Rule 1), replace:**
> Original Rule 1 text.

**With:**
> "Behavioral changes deploy only after a replayed-EV harness shows positive expected value on the relevant feature. The confidence threshold depends on the change's blast tier (see §16.7):
> - **T3 (live-mode, sizing, capital, runtime-infrastructure):** 95% CI on per-trade EV across ≥30 resolved markets. Unchanged.
> - **T1/T2 (paper-mode behavioral):** Replay-as-CI gate verdict against ≥1 pre-registered holdout corpus from a different market regime AND ≥2 market families (per §16.7). Negative-evidence acceptance memo permitted per Rule 5. Pre-gate LLM cache coverage check (extended-key) must pass before observation window opens."

**Insert new section §16.7 (v3):**

> ### §16.7 — Blast-tier routing (added 2026-05-XX)
>
> Behavioral changes route by tier per the Risk Tier Matrix (`docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md` §2). The tier classifier (`scripts/edge_replay/tier_classifier.py`) consumes changed files **plus config/env, model/dependency manifests, DB schema migrations, prompt-template diffs, and any other runtime-affecting artifact**. Any unclassified runtime-affecting artifact routes to T3. The path-based max-wins reduction is the inner step; the semantic input scope is the outer step.
>
> - **T0** (observability/safety/mechanical): Rule 2 exempt as today.
> - **T1** (paper-mode behavioral, replay-decidable): Replay-as-CI gate against ≥1 pre-registered holdout corpus from a different market regime AND ≥2 market families. Pre-gate LLM cache coverage ≥95% for the extended-key tuples. No calendar floor. 72h variance-gate observation. Contamination-window rows retained as separate OOS evaluation corpus.
> - **T2** (paper-mode behavioral, replay-indeterminate): Replay-as-CI + scenario suite + 5d calendar floor + pre-gate cache coverage check.
> - **T3** (live-mode/sizing/capital/runtime-infrastructure): Full Rule 1 (≥30 markets, 95% CI), §8.5.1 floors unchanged, Wave-1 windows unchanged, dual-agent audit, operator gate.
>
> **T1 retrospective (I-11):** 7 days after a T1 deploy, baseline code and candidate code are BOTH replayed on the same flagged contamination-window rows AFTER they resolve. The retrospective measures `baseline(rows) - candidate(rows)` delta EV/sign. The pre-deploy replay-CI prediction is NOT the comparator (different population). Systematic sign divergence >25% across T1 cycles suspends the framework per §5.
>
> **Cache determinism (I-2/I-3):** Temperature=0 alone is NOT accepted as proof of determinism. Cache key includes seed, num_ctx, full sampler options, model digest, Ollama version, endpoint type, prompt template hash, hardware/backend class, and response hash. Each capture is repeat-verified; mismatch poisons the row. Pre-deploy corpora without populated extended keys are marked `LLM_CACHE_INCOMPLETE` and cannot gate LLM-touching T1 changes until reseeded.
>
> **Tier downgrades require operator memo.** Tier upgrades are automatic on classifier ambiguity. The max-wins rule, semantic-scope expansion, temperature=0 pin, cache-key extension, and repeat-verification requirement are not overridable. Other defaults (regime/family standard relaxation, variance metric) carry an "OVERRIDE ALLOWED" annotation per §3 and require a dated memo in `docs/governance/`.

**Rules 3, 5, 6: NO CHANGES.** All three apply across all tiers. Rule 6 cohort field is extended by I-10 (contamination_window marker) — schema additive.

---

## 5. Acceptance criteria — "the framework is working"

Operator verifies after 30 days:

| Signal | Target | Source |
|--------|--------|--------|
| T1 cycle time (commit → deploy) | ≤ 48h median, ≤ 72h p95 | `logs/edge_replay/ci_runs/*/` |
| T1 deploys with replay-as-CI verdict | 100% | Verdict file per deploy |
| T3 deploys in 30d | ≤ 2 (forces selectivity) | Commit log + IC §16 evidence pack |
| T0 work as % of total | ≤ 40% | Commit categorization |
| Paper trades / day | ≥ 2.0 | Bot stats |
| Replay corpus reuse | Each non-`IN_PERIOD_VALIDATION_ONLY` corpus used by ≥3 T1 gates | `replay_gate.py` audit log |
| LLM cache hit rate (extended key) | ≥ 95% | I-2 metrics |
| Negative-evidence memos | ≥ 1 | `docs/governance/*negative-evidence*.md` |
| **T1 retrospective sign-divergence (v3 — baseline-vs-candidate)** | ≤ 25% (predicted vs measured delta-EV sign mismatch) | I-11 |
| **`IN_PERIOD_VALIDATION_ONLY` corpora — trending down** | Trending down as out-of-regime corpora arrive | I-1 metadata |
| **Cache integrity + nondeterminism failures (v3)** | 0 (any failure = corpus poisoning incident or sampler non-determinism breakthrough) | I-12 |
| **(v3) Repeat-verification hit rate** | ≥99% (low non-determinism on Ollama+qwen3 at temperature=0+seed) | I-3 capture log |

**30-day review owner:** operator (not delegated).
**Red-signal threshold:** ANY single metric outside target = framework suspension; pre-amendment IC §16 resumes; operator memo required at `docs/governance/<date>-framework-suspension-memo.md`.

**The framework FAILS its own test if:** T3 deploys outnumber T1, OR 30 days pass with zero T1 deploys, OR cycle time median > 7d, OR T1 retrospective sign-divergence > 25%, OR repeat-verification hit rate < 99% (meaning LLM nondeterminism leaks past cache, making replay gate unreliable).

---

## 6. Regression-into-wheel-spinning safeguards (v3)

1. **T0-budget cap.** No more than 40% of merged commits in any 14-day window may be T0-classified. CI-enforced; deadlock path = staleness alarm + escalation memo (NOT hard block).
2. **T1 staleness alarm.** >14d without a T1 deploy → operator warning at session start.
3. **Negative-evidence cycles count as T1 progress.** Memos must name the hypothesis, the corpus rows tested, and the EV signature observed. Cosmetic T1s detectable via memo content review.
4. **Replay-as-CI verdict immutable post-merge.** Stored at `logs/edge_replay/ci_runs/<commit>/`.
5. **Tier classifier audit trail.** Append-only `logs/edge_replay/tier_classifications.jsonl` records full input fingerprint per classification.
6. **30-day framework review.** Owner = operator. Threshold = any single signal red = suspension. Resume requires fresh design pass + reviewer cycle.
7. **(v2) T1 retrospective check (I-11).** v3 amendment: retrospective uses baseline-vs-candidate replay on identical contamination-window rows, not predicted-vs-actual.
8. **(v3) Concern G acknowledgement (per Codex):** Several safeguards (operator memo for overrides, staleness alarm, session-start warning) devolve to operator discretion / memo traffic. This is acknowledged as a residual risk that v3 does NOT fully solve. The 30-day review IS the forcing function; if memo fatigue is observed, the framework is suspended per §5 acceptance criteria. Future framework revision (post-30d-review) should consider automated tier-classifier-override budgets.

---

## 7. Migration path (v3 — realistic timeline)

**Phase 0 — Decision (operator)**
- Approve / reject v3 proposal.
- Approve IC §16 amendment language.
- Approve build order in §3.
- Approve OVERRIDE-ALLOWED defaults or override with explicit alternatives.

**Phase 1 — Blockers before activation (~3 weeks, was ~2 per v2; expanded per Codex H)**
- I-5 tier classifier with **semantic scope** (M, upgraded from S)
- I-3 LLM capture with **extended fields + repeat-verification** (M, upgraded from S)
- I-1 corpus builder with **regime/family fields** (M)
- I-2 LLM cache with **extended key + repeat-verification** (M)
- I-12 cache integrity + repeat-verification (S)
- I-10 contamination-window marker **with retention** (M, upgraded from S)

**Phase 2 — Activation prerequisites (~3 weeks)**
- I-6 scenario suite (M)
- I-4 replay-as-CI gate runner (M)
- I-8 Wave-1 automated closure (M)
- I-13 framework readiness integration test (S) — BLOCKS IC §16 amendment merge
- IC §16 amendment merged (depends on I-13)
- T0-budget check live in CI

**Phase 3 — Activation**
- Framework operates for T0/T1/T2.
- T3 paths unchanged.
- I-11 T1 retrospective cron live (baseline-vs-candidate replay).

**Phase 4 — Deferred**
- I-7 variance gate (M) — until then, T1 uses fixed 72h paper observation with stamped variance metric (decision-rate stability + unexpected-SKIPPED-bucket).
- 30-day framework review (day 30 after activation).
- First **out-of-regime** corpus built (rationale: post-Wave-1 paper-soak data accumulated; clears `IN_PERIOD_VALIDATION_ONLY` flag for one corpus). Realistic timing: 60-90 days post-activation given bot rate.

**Total realistic timeline:** ~6 weeks combined for Phase 1+2, not the v2 estimate of ~4. Plus 60-90 days organic accumulation for the first out-of-regime corpus to qualify under the new diversity standard.

**v3 Q9 reconciliation (per Codex H):** OOS corpus building is a Phase 4 deliverable, NOT Phase 2. The Phase 2 corpus builder (I-1) ships with the diversity gate in place; the qualifying corpora arrive organically over 60-90 days of bot operation. Activation does not block on having qualifying OOS corpora — activation enables T0/T1 cycles immediately; T1 cycles return verdicts labeled `IN_PERIOD_VALIDATION_ONLY` until a regime-distinct corpus exists.

---

## 8. Risks and open questions (v3)

| # | Risk / question | v3 stamped default | Override path |
|---|-----------------|-------------------|---------------|
| Q1 | Corpus row minimum. | **STAMPED:** ≥500 rows across the corpora used in a gate, combined with I-1 regime/family standard. | Operator memo. |
| Q2 | LLM cache backfill. | **STAMPED:** one-time deterministic seed run per corpus row (temperature=0, seed pinned). Output frozen. Capture must pass repeat-verification per I-3 v3. Pre-deploy corpora that cannot be reseeded are marked `LLM_CACHE_INCOMPLETE` and ineligible to gate LLM-touching T1 changes. | Operator memo to defer seed-run; degrades corpus to T2-only usage. |
| Q3 | Scenario suite curation. | **STAMPED:** append-only with operator memo per addition; never deleted (only deprecated with a reason). | Operator memo. |
| Q4 | Variance metric. | **STAMPED:** decision-rate stability + unexpected-SKIPPED-bucket. EV CI half-width explicitly NOT used in 72h windows. | Operator override memo. |
| Q5 | T1→T3 promotion triggers. | **v3 STAMPED — reconciled with §2 T3 row per Codex A:** the §2 T3 row is canonical and broader than the v2 Q5. T3 triggers include paper-to-live flip, `trading/executor.py` Kelly/cap/bankroll edits, first live order on any new ticker class, AND any classifier-undecidable runtime-affecting artifact (config/env/model/deps/schema/prompts). | Override not allowed (safety invariants). |
| Q6 | 5 consecutive negative-evidence cycles. | **STAMPED:** strategic-pivot conversation per IC §16 Rule 5. Not another T1. | Operator memo to extend to 7. |
| Q7 | Concurrent T1 deploys. | **STAMPED:** T1 deploys serialized. | Operator memo. |
| Q8 | Independent review. | **DONE in v3:** Codex independent review verdict logged. | — |
| Q9 | Out-of-regime corpus building. | **v3 STAMPED — moved to Phase 4 per Codex H:** Phase 2 ships I-1 with diversity gate; qualifying corpora accumulate organically over 60-90 days. T1 verdicts labeled `IN_PERIOD_VALIDATION_ONLY` until then. Activation does NOT block on OOS corpus availability. | Operator memo. |
| Q10 | T1 retrospective alert routing. | **STAMPED:** session-start operator warning + entry in `logs/edge_replay/t1_retrospective.log`. | Operator memo to wire external. |
| Q11 | Cache integrity failure response. | **STAMPED:** batch invalidation if ≥3 rows fail; full capture batch re-captured. | Operator memo. |
| **(v3) Q12** | Operator memo fatigue (per Codex G). | **STAMPED:** acknowledged residual risk; 30-day review is the forcing function. Future revision considers automated override budgets. | n/a — limitation, not a decision. |
| **(v3) Q13** | Pre-registered regime labels — who declares them? | **STAMPED:** operator declares regime labels in `docs/governance/corpus-regimes.md` BEFORE the corpus is built; corpus builder I-1 reads from that doc. Adding a regime requires the same memo cadence as scenario additions (Q3). | Operator memo. |
| **(v3) Q14** | Repeat-verification capture cost (per Codex D). | **STAMPED:** capture takes 2x LLM invocation latency. Production capture is best-effort; replay-time re-capture for poisoned rows runs offline. If repeat-verification rate < 99% at 30-day review, framework suspends per §5. | n/a — surfacing only. |

---

## 9. Codex review — explicit threat-model items v3 still surfaces (per Codex I)

These are documented as known limitations, not solved by v3:

1. **Semantic tier bypass** — even with expanded I-5 scope, a sufficiently novel change (new file type, new artifact category) could bypass classification. Mitigation: T3 default + audit trail.
2. **Corpus selection bias** — operator memo can override default corpus selection. Mitigation: memo content reviewable; 30-day review catches drift.
3. **Regime overfit** — even regime-labeled corpora may share latent confounders (same Kalshi market liquidity, same news-cycle structural features). Mitigation: this is the deepest open problem and likely unsolvable without long-run forward operation. Framework acknowledges.
4. **Cache/model drift** — Ollama updates, model version bumps, GPU/hardware changes can shift cache validity. Mitigation: cache key includes ollama_version + model_digest + hardware_backend_class.
5. **Prompt-template drift** — prompt changes detected by classifier I-5 semantic scope; prompt_template_hash in cache key.
6. **Leakage from resolved outcomes into replay features** — replay rows include `resolved_yes`; care needed so candidate code does not transitively use it. Mitigation: I-4 replay-CI runner runs candidate code with `resolved_yes` masked at decision-time; revealed only at EV scoring.
7. **Operator memo fatigue** — see Q12.
8. **Negative-evidence gaming** — see §6.3.
9. **Concurrent T0 baseline drift** — T0 deploys parallelize; their cumulative effect on the bot's behavior between T1 cycles is uncharacterized. Mitigation: T0-budget cap limits accumulation rate; 30-day review catches systemic drift.
10. **"No trades" misread as safe** — if v0.30.2 fixes cause the bot to trade ZERO times in a 30-day window, no T1 cycle can produce evidence. Mitigation: §5 "paper trades / day ≥ 2.0" target catches this.

---

## 10. Document History

**v1 (2026-05-23, ~14:00 UTC):** Initial proposal by ECC `architect` agent. 8 open questions, 9 infrastructure deliverables.

**v2 (2026-05-23, ~22:00 UTC):** Amendments per ECC `code-reviewer` adversarial review (APPROVED WITH AMENDMENTS, 3 blockers + 2 must-resolve). Added I-10/I-11/I-12/I-13. Stamped defaults Q1-Q11.

**v3 (2026-05-23, ~22:30 UTC):** Amendments per **Codex independent review** (APPROVED WITH AMENDMENTS — NOT SAFE TO MERGE/ACTIVATE; 5 blockers + 3 concerns). Specific changes:

- **§2 T3 row:** broadened to include "first live order on any new ticker class" and "any classifier-undecidable runtime-affecting artifact" (per Codex A reconciliation with Q5).
- **§2 T1/T2 corpus standard:** "≥2 calendar months" replaced with "≥1 pre-registered holdout corpus from a different market regime AND ≥2 market families" (per Codex C).
- **§3 I-1:** corpus diversity standard rewrite (regime/family) + IN_PERIOD_VALIDATION_ONLY semantics (per Codex C).
- **§3 I-2:** cache key extended to 13 fields including seed, num_ctx, sampler_options, model_digest, ollama_version, endpoint_type, prompt_template_hash, hardware_backend_class, response_hash (per Codex D). Temperature=0 explicitly stated as insufficient.
- **§3 I-3:** capture hook extended fields + repeat-verification requirement (per Codex D). Complexity upgraded S→M.
- **§3 I-5:** semantic scope expansion — consumes config/env, model/deps, schema, prompts, runbooks (per Codex B). Complexity upgraded S→M.
- **§3 I-10:** contamination-window rows RETAINED as separate OOS eval corpus, not silently excluded (per Codex E). Complexity upgraded S→M.
- **§3 I-11:** T1 retrospective uses baseline-vs-candidate replay on identical flagged rows after resolution, not predicted-vs-actual (per Codex F).
- **§4 §16.7:** all v3 amendments reflected.
- **§5:** added v3 signals (T1 retrospective sign-divergence, IN_PERIOD trend, repeat-verification hit rate).
- **§6:** added safeguard 8 acknowledging concern G; updated 7 to use baseline-vs-candidate.
- **§7:** timeline extended from ~4 weeks to ~6 weeks (per Codex H). Q9 OOS corpus moved Phase 2 → Phase 4.
- **§8:** Q5 reconciled with T3 row; added Q12 (memo fatigue), Q13 (regime labels), Q14 (repeat-verification cost).
- **§9 NEW:** threat-model items Codex surfaced as known limitations.

**v3 reviewer status:** Codex independent review — APPROVED WITH AMENDMENTS — all 5 blockers + 3 concerns incorporated. **Not safe to merge/activate until Phase 0 operator approval.**

**v4 (TBD):** any further iteration after operator decisions on Phase 0.

---

## Relevant file paths

- `/Users/jacobparenti/vscode/kalshi-bot/docs/IMPLEMENTATION_CONTRACT.md` (IC §16 at lines 862-928, amendment target)
- `/Users/jacobparenti/vscode/kalshi-bot/scripts/edge_replay/` (28 existing scripts; lineage to consolidate; v3 OOS seed extractor at `oos_corpus_seed.py`)
- `/Users/jacobparenti/vscode/kalshi-bot/logs/edge_replay/` (6 existing corpora — all `IN_PERIOD_VALIDATION_ONLY` per Codex C until regime-distinct OOS corpus built)
- `/Users/jacobparenti/vscode/kalshi-bot/tests/test_wave1_postdeploy_validation_windows.py` (manual-closure pattern automated via I-8)
- `/Users/jacobparenti/vscode/kalshi-bot/analysis/signal_analyzer.py` (I-3 capture hook site; OpenAI-compat endpoint)
- `/Users/jacobparenti/vscode/kalshi-bot/governance/llm.py` (I-3 capture hook site; native endpoint; `think: False` per PROFIT-GOV-001)
- `/Users/jacobparenti/vscode/kalshi-bot/governance/prompts.py:27-31` (anchor_rate polarity block; protected by I-6 scenario suite)
- `/Users/jacobparenti/vscode/kalshi-bot/tasks/stats/` (target dir for I-7 variance gate)
- `/Users/jacobparenti/vscode/kalshi-bot/data/paper_trades.db` (I-10 contamination-window column added via migration)
- `/Users/jacobparenti/vscode/kalshi-bot/docs/profit_path_debt_log.md` (framework activation + 30-day review entries)
- `/Users/jacobparenti/vscode/kalshi-bot/docs/governance/` (override-memo destination; review-suspension memos; regime-label declarations)
- **(v3) `docs/governance/corpus-regimes.md`** — operator-declared regime labels per Q13.
