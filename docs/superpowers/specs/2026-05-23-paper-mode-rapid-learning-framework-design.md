# Paper-Mode Rapid-Learning Framework — Design Proposal

**Version:** v2 (2026-05-23 — amended per adversarial review; see §10 for v1→v2 changelog)
**Status:** Design only, no code. Operator decision required before any infrastructure build.
**Author:** architect agent (ECC), 2026-05-23; v2 amendments by main thread incorporating ECC `code-reviewer` adversarial review.
**Reviewer status:** v1 reviewed adversarially by independent ECC `code-reviewer` (verdict: APPROVED WITH AMENDMENTS — 3 blockers + 2 must-resolve concerns). All 5 must-fix items incorporated below. **Pending:** independent Codex review per `~/.claude/rules/agent_collaboration.md` high-assurance workflow (Q8). v2 is NOT safe to activate until Codex pass and operator approval of stamped defaults below.

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

> **v2 STAMP — Max-wins rule (per reviewer blocker B):** For any PR touching files mapped to multiple tiers, the **highest tier wins, no exceptions**. The tier classifier (I-5) computes `max(tier_of(p) for p in changed_paths)`. Unknown paths default to T3. This rule is the canonical resolution for cross-tier PRs and is enforced in CI before the gate runs. **OVERRIDE: not allowed** — this is a safety invariant.

| Tier | Name | Trigger | Pre-deploy gate | Post-deploy observation | Rollback |
|------|------|---------|-----------------|-------------------------|----------|
| **T0** | Observability / safety / mechanical bug | IC §16 Rule 2 categories: SKIPPED-emission, log fields, kill switch, launchd, install scripts, cooldown sentinel-type bug fixes. No path to a paper or live trade decision changes. | Unit tests + lint. No replay needed. | 24h smoke (existing observation-canary mechanism). | Revert commit. |
| **T1** | Paper-mode behavioral, replay-decidable | Touches `/feeds`, classifier, blender, Trade Readiness Gate G1-G6 thresholds, sizing formula. Effect on edge is **measurable on existing replay corpora** (the change can be re-scored on a row in `logs/edge_replay/*/replay_dataset*.jsonl` deterministically). | (a) Replay-as-CI gate run against ≥2 corpora **meeting the temporal-diversity standard** (see §3 I-1 v2 amendment) produces per-trade EV + 95% CI table (IC §16 Rule 4 schema). (b) Scenario suite green. (c) **Pre-gate cache coverage check passes** (see I-2 v2). (d) Verdict: positive replayed EV OR explicit "negative-evidence acceptance" memo per IC §16 Rule 5. **No calendar floor.** | 72h paper observation — variance gate, not calendar gate (see §5). Bot trades during this window are flagged with a `contamination_window` cohort marker (I-10). | Revert + corpus-rerun confirms reversion restored prior EV signature. |
| **T2** | Paper-mode behavioral, replay-indeterminate | Behavioral change whose effect depends on real-time signals not present in corpora (e.g., new feed source with no historical capture; new LLM prompt where cached outputs don't exist). | (a) Synthetic event corpus + cached-LLM stub gate (see §3). (b) Scenario suite green. (c) Pre-gate cache coverage check passes. (d) Operator approval memo naming which evidence gap forced T2 routing. | **Variance gate + calendar floor of 5d** (compressed from 14d; rationale: paper-mode no-money-risk + active replay infrastructure shortens the "have we surprised ourselves yet" half-life). Bot trades during window flagged with `contamination_window` cohort marker. | Revert + 24h sanity smoke. |
| **T3** | Live-mode transition OR sizing/capital change | Paper→live cutover; Kelly fraction change; hard-cap change; first live order on any new ticker class; bankroll mutation logic. | **IC §16 unchanged, in full.** ≥30 resolved markets, 95% CI per-trade EV positive, Rule 4 table, Rule 6 cohort separation. Dual-agent audit (`~/.claude/rules/agent_collaboration.md` high-assurance workflow). Operator gate. | §8.5.1 floors apply unchanged: 7d early-close minimum, 14d default. Wave-1 windows (24h/48h/72h/7d) unchanged. | Live kill switch + recorded incident; full IC §16 re-gate before next attempt. |

**Tier assignment is adversarial.** When in doubt between T1 and T3, route to T3. When in doubt between T1 and T2, route to T2. The default direction of doubt is **more gate, not less.** This is the primary anti-wheel-spinning lever (§6).

**Calendar-floor substitution rationale (T1/T2 only):** `feedback_soak_acceleration_split.md` permits calendar-floor cuts when "volume gate cleared, safety zero" — i.e., when evidence is sufficient and no safety surface was touched. T1 satisfies both: pre-deploy replay gate is the evidence substitute; T0-categorized safety changes are explicitly excluded from T1. Decision-policy cuts mid-soak remain forbidden — variance gates (§5) compute on completed observation windows, not partial ones.

---

## 3. Infrastructure pieces required

| # | Deliverable | File path (estimated) | Complexity | Dependency |
|---|-------------|----------------------|------------|------------|
| **I-1** | **General replay corpus builder** with v2 **temporal diversity enforcement.** Replace ad-hoc `build_cycle*_corpus.py` scripts. Inputs: date range, market-family filter, cohort tag. Output: JSONL with cohort flag per IC §16 Rule 6 AND `corpus_window_start_utc` / `corpus_window_end_utc` fields. **v2 STAMP — corpus diversity standard:** corpora used in a T1/T2 replay-as-CI gate must collectively span **≥2 distinct calendar months** OR the verdict is labeled `IN_PERIOD_VALIDATION_ONLY` (a Rule 5 negative-evidence-style annotation). All 6 existing corpora (2026-05-06→2026-05-10) are flagged as in-period; framework launch must build at least one out-of-period corpus before producing any non-flagged T1 verdict. Signature: `build_corpus(start_utc, end_utc, market_families, cohort_tag) -> Path`. **OVERRIDE ALLOWED:** operator may temporarily downgrade to "≥2 corpora regardless of period" with explicit memo citing why OOS coverage is not yet available — memo file required at `docs/governance/<date>-corpus-diversity-override-memo.md`. | `scripts/edge_replay/build_corpus.py` (replaces `build_cycle17d*.py` lineage) | M | **Hard** |
| **I-2** | **Cached LLM-output store** with v2 **endpoint-aware key.** Per-`(row_id, prompt_hash, model_id, endpoint_type, temperature)` → JSON-decoded response. Read-through cache: on cache miss during replay, log + fail (do NOT call live LLM during replay — non-determinism re-enters). **v2 STAMP:** `endpoint_type: Literal["native","openai_compat"]` is part of the cache key. `temperature=0` is pinned during all capture runs (signal_analyzer + governance) so cached outputs are deterministic. Pre-gate coverage check fires BEFORE the observation window opens, scanning corpus rows and verifying cache hit rate ≥95% for the specific `(row_id, prompt_hash, model_id, endpoint_type, temperature)` tuples — failure aborts the T1 cycle at gate-open, not at gate-close. **OVERRIDE: temperature pin not allowed** (correctness invariant). | `scripts/edge_replay/llm_cache.py` + sqlite at `logs/edge_replay/llm_cache.sqlite` | M | **Hard** |
| **I-3** | **LLM-output capture hook.** Production code path captures every LLM response (signal_analyzer + governance) into I-2 store, keyed by row_id. Captures populate the cache so future replays are deterministic. **Must be additive** — production behavior unchanged on capture failure (write-best-effort, log on miss). **v2 STAMP:** capture hook records `endpoint_type` per call. signal_analyzer captures use `endpoint_type="openai_compat"`; governance captures use `endpoint_type="native"` (per CLAUDE.md gotcha — `think: False` only works on Ollama-native). Cache writes include `value_hash` (sha256 of JSON-canonicalized response) for **I-12 integrity-check support.** | `analysis/signal_analyzer.py` + `governance/llm.py` (add capture call sites; do not refactor; preserve `think: False` per PROFIT-GOV-001) | S | **Hard** |
| **I-4** | **Replay-as-CI runner.** Single entry point invoked from `pytest` or CI. Inputs: changed-file list, corpus list, gate config. Output: pass/fail + Rule 4 table written to `logs/edge_replay/ci_runs/<commit>/`. v2 amendment: runner ALWAYS uses **all available corpora meeting the diversity standard** by default — operator opt-out (cherry-pick a subset) requires a memo per **D** anti-gaming safeguard. Signature: `run_replay_gate(changed_files, corpora="all_diverse", gate_spec) -> GateVerdict`. | `scripts/edge_replay/replay_gate.py` + `tests/test_replay_gate_smoke.py` | M | **Hard for T1; soft for T0** |
| **I-5** | **Tier classifier** with v2 **max-wins rule.** Maps changed-file list to tier per §2. Rule-based, not ML. Signature: `classify_tier(changed_paths: list[Path]) -> Literal["T0","T1","T2","T3"]`. **v2 STAMP:** implementation returns `max(tier_of(p) for p in changed_paths)`. Explicit per-tier allowlist + denylist documented in module docstring. **Unknown paths default to T3** (fail-safe direction). Every classification logs `(commit_sha, changed_files, classified_tier, max_tier_file, rule_matched)` to append-only ledger at `logs/edge_replay/tier_classifications.jsonl`. **OVERRIDE: not allowed for the max-wins rule** (safety invariant); operator may override per-PR classification only via signed memo. | `scripts/edge_replay/tier_classifier.py` + `tests/test_tier_classifier.py` | S | **Hard** |
| **I-6** | **Scenario suite — fixed corpus of adversarial events.** Hand-curated JSONL with rows for: FISA-burst (cooldown drift), suppression edge cases (negation, hedging), keyword-direction flip, qwen3 anchor-rate polarity (governance regression check, see CLAUDE.md `governance/prompts.py:27-31` gotcha). Each scenario has expected `(decision, side, magnitude)` triplet. **Append-only** per Q3 stamped default: scenarios are added with operator memo per addition; never deleted (only deprecated with a reason). | `tests/scenarios/*.jsonl` + `tests/test_scenario_suite.py` | M | **Hard** |
| **I-7** | **Variance gate calculator** with v2 **stamped metric.** Computes whether an observation window has surfaced "interesting deltas." **v2 STAMP — variance metric (per reviewer F + operator Q4 default):** primary signal is **decision-rate stability** (admission count + SKIPPED-by-bucket distribution stability) over the window. Secondary signal: **no unexpected SKIPPED bucket emerged** (relative to T1's stated scope — new gates in the T1 change ARE expected to add new buckets; the gate fails only when an UNEXPECTED bucket appears). EV CI half-width is explicitly NOT used in 72h windows (resolution lag makes it unworkable). Signature: `variance_gate_closed(window_start, baseline_metrics, t1_change_scope) -> (bool, reason)`. **OVERRIDE ALLOWED:** operator may select a different primary metric via memo at `docs/governance/<date>-variance-metric-override-memo.md` — recommend evaluating after 30-day framework review (§5). | `tasks/stats/variance_gate.py` (NOT `/analysis` — per domain-constraints rule, `/analysis` is pure analysis; operational trackers belong in `tasks/stats/`) | M | **Hard for T1/T2** |
| **I-8** | **Wave-1 automated closure** — **v2: PROMOTED FROM PHASE 4 TO PHASE 2** (per reviewer I). Replace manual "operator writes PASS line, removes xfail decorator" with automated closure that consumes I-7 variance gate output. xfail-strict markers in `tests/test_wave1_postdeploy_validation_windows.py` flip to passing when window closes naturally. Rationale: manual PASS-line writing reintroduces operator-latency bottleneck at cycle close — defeats the purpose of compressing the gate. | `tests/conftest.py` plugin + edits to existing wave-1 test module | M | **Hard** (was nice-to-have in v1; promoted to required) |
| **I-9** | **Synthetic market-resolution harness.** Generate synthetic resolution outcomes for un-resolved markets to expand corpus size beyond what natural resolutions provide. | — | L | **EXPLICIT DECISION: DO NOT BUILD.** Synthetic outcomes are not ground truth; corpora seeded with them will pass replay gates that real markets fail. Build I-1 + I-2 instead and live with corpus size constraint. |
| **I-10** | **Corpus contamination-window marker (v2 — added per reviewer J).** During T1/T2 observation windows, the bot continues paper-trading. Those trade rows are flagged with `cohort_extension="contamination_window:<change_id>:<window_id>"` on persistence. Corpus builder I-1 reads this field; rows from a contamination window are excluded from any T1 gate that gates a different change. Prevents the next T1's corpus from being polluted by the prior T1's in-window behavior. | DB schema migration in `data/paper_trades.db` + write-side in `tasks/blend_task.py` + read-side in I-1 | S | **Hard** |
| **I-11** | **T1 retrospective hook (v2 — added per reviewer J).** 7 days after a T1 deploy, the framework auto-runs a retrospective comparing the original replay-as-CI verdict's predicted EV vs the bot's actual paper EV over the 7-day window. Systematic divergence (predicted positive, actual negative — or vice versa beyond a threshold) emits an operator-visible alert and counts against the framework's 30-day review acceptance criteria (§5). Prevents replay-vs-reality drift from accumulating invisibly. | `scripts/edge_replay/t1_retrospective.py` + cron entry per `~/.claude/rules/agent_collaboration.md` | M | **Hard** |
| **I-12** | **Cache integrity check (v2 — added per reviewer J).** On every cache read from I-2, verify `sha256(canonical_json(response)) == stored_value_hash`. Mismatch raises `CacheCorruptionError` and the T1 gate fails-closed. Prevents corpus poisoning via corrupted LLM outputs being silently cached and read back as ground truth. | Read-path addition to `scripts/edge_replay/llm_cache.py` | S | **Hard** |
| **I-13** | **Framework readiness integration test (v2 — added per reviewer G).** Single test that imports I-1, I-2, I-3, I-4, I-5, I-6, I-7, I-8, I-10, I-11, I-12 and asserts: (a) each module is importable; (b) each public entry point exists with the documented signature; (c) the tier classifier returns T3 for an empty/unknown path (fail-safe verification). **IC §16 amendment merge is BLOCKED until this test passes in CI.** Makes the all-or-nothing requirement machine-checkable rather than prose-only. | `tests/test_framework_readiness.py` | S | **Hard — gates IC §16 amendment merge** |

**Build order (v2):** I-5 (tier classifier with max-wins) → I-3 (LLM capture, additive, populates cache immediately) → I-1 (general corpus builder with diversity flag) → I-2 (LLM cache reader with endpoint-aware key) → I-12 (cache integrity check, integrates into I-2 read path) → I-6 (scenario suite, parallel) → I-10 (contamination marker, parallel) → I-4 (replay-as-CI gate, depends on I-1/I-2/I-6) → I-7 (variance gate) → I-8 (Wave-1 automated closure) → I-11 (T1 retrospective) → I-13 (framework readiness integration test, last).

---

## 4. IC §16 amendment proposal (v2)

**Amend `docs/IMPLEMENTATION_CONTRACT.md:862-928`. Specific edits:**

**v2 amendment to the Scope block (line 916) — added per reviewer A:**

> **APPEND to existing Scope text:**
> "For paper-mode behavioral changes, blast-tier routing per §16.7 governs. Rule 1's ≥30-markets threshold applies only to T3 (live-mode/sizing/capital). T1/T2 changes use the replay-as-CI gate threshold defined in §16.7. The Scope block continues to apply to all behavioral changes — §16.7 determines WHICH threshold applies per tier, not whether IC §16 applies."

This closes the textual contradiction reviewer A flagged: without the Scope-block amendment, the old broad Rule 1 language remains the standing text and §16.7 is an unresolvable addendum.

**At line 870 (Rule 1), replace:**
> "Behavioral changes deploy only after a replayed-EV harness shows positive expected value on the relevant feature, with operator-stated confidence threshold (default: 95 % CI on per-trade EV across at least the last 30 resolved markets in the evidence window)."

**With:**
> "Behavioral changes deploy only after a replayed-EV harness shows positive expected value on the relevant feature. The confidence threshold depends on the change's blast tier (see §16.7):
> - **T3 (live-mode, sizing, capital):** 95% CI on per-trade EV across ≥30 resolved markets. Unchanged.
> - **T1/T2 (paper-mode behavioral):** Replay-as-CI gate verdict against ≥2 corpora meeting the temporal-diversity standard (§16.7). Negative-evidence acceptance memo permitted per Rule 5. **Pre-gate LLM cache coverage check must pass before observation window opens.**"

**Insert new section §16.7 after line 922 ("Scope"):**

> ### §16.7 — Blast-tier routing (added 2026-05-XX)
>
> Behavioral changes route by tier per the Risk Tier Matrix (`docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md` §2). Tier classifier (`scripts/edge_replay/tier_classifier.py`) assigns tier from changed-file list using the **max-wins rule** — for any PR touching files mapped to multiple tiers, the highest tier wins, no exceptions. Unknown paths default to T3.
>
> - **T0** (observability/safety/mechanical): Rule 2 exempt as today.
> - **T1** (paper-mode behavioral, replay-decidable): Replay-as-CI gate against ≥2 corpora meeting the temporal-diversity standard (≥2 distinct calendar months, OR verdict labeled `IN_PERIOD_VALIDATION_ONLY`). Pre-gate LLM cache coverage ≥95% for the corpus tuples being gated. No calendar floor. 72h variance-gate observation window.
> - **T2** (paper-mode behavioral, replay-indeterminate): Replay-as-CI + scenario suite + 5d calendar floor (compressed from §8.5.1's 14d) + pre-gate cache coverage check.
> - **T3** (live-mode/sizing/capital): Full Rule 1 (≥30 markets, 95% CI), §8.5.1 floors unchanged, Wave-1 windows unchanged, dual-agent audit, operator gate.
>
> **Tier downgrades require operator memo.** Tier upgrades are automatic on classifier ambiguity. The max-wins rule and the temperature=0 cache invariant are not overridable. Other defaults (corpus diversity, variance metric) carry an "OVERRIDE ALLOWED" annotation per §3 and require a dated memo in `docs/governance/` per override.

**Rules 3, 5, 6 (Rule 3 "may increase trades is NOT enough"; Rule 5 negative-evidence; Rule 6 cohort separation): NO CHANGES.** All three apply across all tiers. **Rule 6 cohort field is extended** by I-10 to include `contamination_window` markers — schema additive, not contradictory.

---

## 5. Acceptance criteria — "the framework is working"

Operator can verify after 30 days by checking:

| Signal | Target | Source |
|--------|--------|--------|
| T1 cycle time (commit → deploy) | ≤ 48h median, ≤ 72h p95 | `logs/edge_replay/ci_runs/*/` timestamps |
| T1 deploys with replay-as-CI verdict attached | 100% | Verdict file present per deploy commit |
| T3 deploys (live/sizing) in 30d | ≤ 2 (forces selectivity) | Commit log + IC §16 evidence pack |
| Cycles spent on T0 work as % of total | ≤ 40% (was effectively 100% during the 11-cycle incident) | Commit categorization |
| Paper trades / day | ≥ 2.0 (was 0.8) | Bot stats |
| Replay corpus reuse count | Each corpus used by ≥3 distinct T1 gates | `replay_gate.py` audit log |
| LLM cache hit rate during replays | ≥ 95% | I-2 metrics |
| Negative-evidence memos | ≥ 1 (proves Rule 5 is actually exercisable, not just documented) | `docs/governance/*negative-evidence*.md` |
| **T1 retrospective divergence rate (v2)** | ≤ 25% (i.e., predicted-vs-actual EV signs match in ≥75% of T1 cycles) | I-11 retrospective log |
| **`IN_PERIOD_VALIDATION_ONLY` verdicts (v2)** | Trending down (proves out-of-sample corpus building is happening) | I-1 corpus-window metadata |
| **Cache integrity failures (v2)** | 0 (any failure = corpus poisoning incident) | I-12 |

**v2 30-day review owner + threshold (per reviewer D):**
- **Owner:** operator. Not delegated.
- **Red-signal threshold:** ANY SINGLE metric in the table above outside target = framework suspension. Pre-amendment IC §16 resumes immediately. Suspension requires operator memo at `docs/governance/<date>-framework-suspension-memo.md` documenting which signals failed.

**The framework FAILS its own test if:** T3 deploys outnumber T1, or if 30 days pass with zero T1 deploys, or if cycle time exceeds 7d median, or if T1 retrospective divergence exceeds 25% (then replay predictions are systematically wrong and the framework is fast but unreliable).

---

## 6. Regression-into-wheel-spinning safeguards (v2 hardened)

The 11-cycle incident pattern: shipped safety/observability while edge stayed at zero. This framework re-enables that pattern if T0 becomes the path of least resistance.

**Concrete safeguards:**

1. **T0-budget cap.** No more than 40% of merged commits in any 14-day window may be T0-classified. Enforced by CI check (`scripts/edge_replay/tier_budget_check.py`) counting tier classifier outputs over rolling window. **v2 deadlock path (per reviewer D):** if T0-cap breach persists >7d with no T1 attempt, trigger the §6.2 staleness alarm AND require an explicit operator escalation memo at `docs/governance/<date>-t1-deadlock-escalation-memo.md` before any further T0 merges. Hard-block is replaced by alarm-plus-memo to avoid framework deadlock with no T1-ready work.

2. **T1 staleness alarm.** If >14d elapse with no T1 deploy attempt, emit operator-visible warning at session start. Forces explicit "we're spinning wheels" recognition.

3. **Negative-evidence cycles count as T1 progress.** A Rule 5 negative-evidence memo IS a deploy in the budget — explicitly. Otherwise the framework rewards avoiding tests that might say "no edge." **v2 anti-gaming (per reviewer D):** negative-evidence memos must include a paragraph naming the specific hypothesis being tested, the corpus rows the test ran against, and the EV signature observed. A negative-evidence memo whose hypothesis is "tested log-label change for behavioral impact" is reviewable for content; deliberately weak T1s are visible to operator review.

4. **Replay-as-CI verdict is immutable post-merge.** Stored under `logs/edge_replay/ci_runs/<commit>/`. Operator cannot retroactively re-classify a T1 deploy as T0 to dodge the budget check.

5. **Tier classifier audit trail.** Every classification logs `(commit_sha, changed_files, classified_tier, max_tier_file, rule_matched)` to append-only `logs/edge_replay/tier_classifications.jsonl`. Reviewable by independent agent; lets the operator catch drift where "behavioral" changes get reclassified as "observability."

6. **Mandatory 30-day framework review.** At day 30, operator runs §5 acceptance-criteria table. Any single signal in the red = framework suspended; full IC §16 (pre-amendment) resumes. **Owner is operator; threshold is any-single-metric-out-of-target.** Resume requires fresh design pass + reviewer cycle.

7. **(v2) T1 retrospective check (I-11).** 7 days after every T1 deploy, framework auto-runs predicted-vs-actual EV comparison. Systematic divergence (>25% of T1 cycles have wrong-sign predictions) is itself a framework-suspension signal per §5.

---

## 7. Migration path (v2)

**Phase 0 — Decision (operator)**
- Approve / reject this proposal v2.
- Approve IC §16 amendment language (including Scope-block amendment).
- Approve build order in §3.
- Approve OVERRIDE-ALLOWED defaults (corpus diversity standard, variance metric, T0-cap deadlock path) or override with explicit alternatives.
- Schedule independent Codex review (Q8).

**Phase 1 — Blockers before activation (hard dependencies, realistic estimate: ~2 weeks per reviewer G)**
- I-5 tier classifier with max-wins rule (S)
- I-3 LLM capture hook with endpoint+temperature recording, additive, deployed to production to start populating cache (S)
- I-1 general corpus builder with diversity flag (M)
- I-2 LLM cache reader with endpoint-aware key + pre-gate coverage check (M)
- I-12 cache integrity check (S)
- I-10 corpus contamination-window marker — DB schema migration + writer + reader (S)

**Phase 2 — Activation prerequisites (~2 weeks)**
- I-6 scenario suite (M) — must include FISA-burst, suppression negation, anchor_rate polarity (`governance/prompts.py:27-31` regression check)
- I-4 replay-as-CI gate runner (M)
- I-8 Wave-1 automated closure (M) — **promoted from Phase 4 per reviewer I**
- I-13 framework readiness integration test (S) — IC §16 amendment merge BLOCKED until this passes
- IC §16 amendment merged (depends on I-13 passing)
- T0-budget check (`tier_budget_check.py`) live in CI

**Phase 3 — Activation**
- Framework operates for T0/T1/T2 paper-mode changes.
- T3 paths unchanged.
- I-11 T1 retrospective cron live from day 1 of activation.

**Phase 4 — Deferred / nice-to-have**
- I-7 variance gate (M) — until then, T1 uses fixed 72h paper observation with the stamped variance metric (decision-rate stability + unexpected-SKIPPED-bucket) computed at window close.
- 30-day framework review per §5 (day 30 after activation).
- Build at least one out-of-period corpus (covering June 2026+) before any T1 verdict drops the `IN_PERIOD_VALIDATION_ONLY` label.

**What CANNOT be deferred (v2 reaffirmed):** I-1, I-2, I-3, I-4, I-5, I-6, I-8 (now), I-10, I-11, I-12, I-13, IC §16 amendment (Scope + Rule 1 + §16.7), T0-budget check. Without any one of these, the framework is not safe to activate.

---

## 8. Risks and open questions (v2 — defaults stamped per operator directive)

| # | Risk / question | v2 stamped default | Override path |
|---|-----------------|-------------------|---------------|
| Q1 | **Corpus diversity (combined row minimum).** | **STAMPED:** ≥500 rows across the corpora used in a gate. Combined with §3 I-1 temporal diversity (≥2 calendar months). | Operator memo at `docs/governance/<date>-corpus-min-rows-memo.md`. |
| Q2 | **LLM cache backfill** for pre-deploy corpora. | **STAMPED:** one-time deterministic seed run (temperature=0) per corpus row before that corpus is used as gating input. Output frozen, cache read-only for replay thereafter. Cost: one LLM invocation per row per model, paid once at framework launch. | Operator memo if seed-run is deferred or skipped — degrades that corpus to T2-only usage (cannot gate LLM-touching T1 changes). |
| Q3 | **Scenario suite hand-curated** risk. | **STAMPED:** append-only with operator memo per addition; never deleted (only deprecated with a reason). New scenarios from incident response or reviewer findings are first-class additions. | Same — operator memo required for any deletion/deprecation. |
| Q4 | **Variance gate metric.** | **STAMPED (per reviewer F):** decision-rate stability + unexpected-SKIPPED-bucket — NOT EV CI half-width (unworkable in 72h). | Operator override memo at `docs/governance/<date>-variance-metric-override-memo.md`; revisit after 30-day framework review. |
| Q5 | **T1→T3 promotion triggers.** | **STAMPED:** paper-to-live flip AND any edit to `trading/executor.py` Kelly logic / hard cap / bankroll mutation are the ONLY T3 triggers from below. Tier classifier I-5 hard-codes these paths to T3. | Override not allowed — these are safety invariants. |
| Q6 | **5 consecutive negative-evidence cycles** — when does framework declare wheel-spinning? | **STAMPED:** if 5 consecutive T1 cycles return negative-evidence memos, framework triggers strategic-pivot conversation per IC §16 Rule 5 (calibration / sample-size / information-frontier diagnosis), NOT another T1. Operator memo at `docs/governance/<date>-strategic-pivot-memo.md`. | Operator memo can defer to 7 consecutive (max 7) if a specific calibration cycle is in progress. |
| Q7 | **Concurrent T1 deploys.** | **STAMPED:** T1 deploys are serialized — one observation window must close before the next T1 deploys. T0 deploys may parallelize. T2 deploys may parallelize with at most 1 T1. | Operator override memo. |
| Q8 | **Independent Codex review.** | **STAMPED:** required before Phase 1 begins. Operator schedules. | Override not allowed for high-assurance gate work. |
| **Q9 (v2)** | **Out-of-period corpus building** — who builds the June 2026+ corpus to clear `IN_PERIOD_VALIDATION_ONLY` flag? | **STAMPED:** framework launch includes building one OOS corpus (June 2026 window) as a Phase 2 deliverable. Bot trades during June feed it. After Wave-1 fixes settle (next 30d), assemble first OOS corpus. | Operator memo. |
| **Q10 (v2)** | **T1 retrospective alert routing** — where does I-11's divergence alert go? | **STAMPED:** session-start operator warning + entry in `logs/edge_replay/t1_retrospective.log`. No external paging (paper-mode, not on-call surface). | Operator may wire to external alert via memo. |
| **Q11 (v2)** | **Cache integrity failure response** — when I-12 fires, what happens beyond fail-closed? | **STAMPED:** mark corpus row poisoned, alert operator, run cache audit on all rows from that capture batch. If ≥3 rows fail, the whole capture batch (typically a single bot session) is invalidated and re-captured. | Operator memo. |

---

## 9. Document History

**v1 (2026-05-23, ~14:00 UTC):** Initial proposal by ECC `architect` agent. 8 open questions (Q1-Q8), 9 infrastructure deliverables (I-1 through I-9).

**v2 (2026-05-23, ~22:00 UTC):** Amendments incorporated per ECC `code-reviewer` adversarial review verdict APPROVED WITH AMENDMENTS. Operator directive: stamp reviewer-recommended defaults with OVERRIDE ALLOWED markers.

Changes from v1 → v2:
- **§2 Risk Tier Matrix:** added max-wins rule stamp at top (per reviewer B). Updated T1/T2 pre-deploy gate cells to reference cache coverage check + contamination_window marker.
- **§3 Infrastructure:** added I-10 (contamination marker), I-11 (T1 retrospective), I-12 (cache integrity), I-13 (framework readiness integration test). Updated I-1 with temporal diversity standard (per reviewer C). Updated I-2 with endpoint_type + temperature=0 (per reviewer E). Updated I-5 with max-wins rule (per reviewer B). Updated I-7 with stamped variance metric (per reviewer F). Promoted I-8 from Phase 4 to Phase 2 (per reviewer I).
- **§4 IC §16 amendment:** added Scope-block amendment (per reviewer A) to close textual contradiction.
- **§5 Acceptance criteria:** added T1-retrospective-divergence-rate signal, `IN_PERIOD_VALIDATION_ONLY` trend signal, cache-integrity-failure signal. Defined 30-day review owner (operator) + red-signal threshold (any single metric out-of-target) per reviewer D.
- **§6 Anti-wheel-spin safeguards:** added v2 deadlock-path memo route (alarm-plus-memo, not hard-block). Added v2 anti-gaming requirement for negative-evidence memos (must name hypothesis + corpus rows + EV signature). Added T1 retrospective as safeguard 7.
- **§7 Migration path:** realistic timeline updated from "~1 week each" to "~2 weeks each" per reviewer G. Phase 2 expanded with I-8, I-13. I-13 explicitly gates IC §16 amendment merge.
- **§8 Open questions:** Q1-Q8 stamped with defaults + override paths. Added Q9 (OOS corpus building), Q10 (T1 retrospective alert routing), Q11 (cache integrity failure response).
- **Adversarial review status:** APPROVED WITH AMENDMENTS verdict logged at top. Codex review still pending per Q8.

**v3 (TBD):** Pending Codex independent review per `~/.claude/rules/agent_collaboration.md` high-assurance workflow.

---

## Relevant file paths

- `/Users/jacobparenti/vscode/kalshi-bot/docs/IMPLEMENTATION_CONTRACT.md` (IC §16 at lines 862-928, amendment target; Scope block at line 916, also amended)
- `/Users/jacobparenti/vscode/kalshi-bot/scripts/edge_replay/` (28 existing scripts; lineage to consolidate)
- `/Users/jacobparenti/vscode/kalshi-bot/logs/edge_replay/` (6 existing corpora — all flagged as `IN_PERIOD_VALIDATION_ONLY` until OOS corpus built per Q9)
- `/Users/jacobparenti/vscode/kalshi-bot/tests/test_wave1_postdeploy_validation_windows.py` (manual-closure pattern automated via I-8, now Phase 2)
- `/Users/jacobparenti/vscode/kalshi-bot/analysis/signal_analyzer.py` (I-3 capture hook site; OpenAI-compat endpoint per CLAUDE.md gotcha; `endpoint_type="openai_compat"` in cache key)
- `/Users/jacobparenti/vscode/kalshi-bot/governance/llm.py` (I-3 capture hook site; preserves `think: False` per PROFIT-GOV-001; `endpoint_type="native"` in cache key)
- `/Users/jacobparenti/vscode/kalshi-bot/governance/prompts.py:27-31` (anchor_rate polarity block; protected by scenario suite I-6)
- `/Users/jacobparenti/vscode/kalshi-bot/tasks/stats/` (target dir for I-7 variance gate per domain-constraints rule)
- `/Users/jacobparenti/vscode/kalshi-bot/data/paper_trades.db` (I-10 contamination-window column added via schema migration)
- `/Users/jacobparenti/vscode/kalshi-bot/docs/profit_path_debt_log.md` (where framework activation + 30-day review entries land per project tracking-system policy)
- `/Users/jacobparenti/vscode/kalshi-bot/docs/governance/` (override-memo destination; review-suspension memos; strategic-pivot memos)
