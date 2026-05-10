# PROFIT-LLM-001 pre-sizing scope spec

**Status:** design (defines the bounded sizing surface for Branch D first-handoff). NO code change.
**Authority:** Implementation Contract §11 — out-of-scope-of-EDGE-004 escalation requires explicit redesign discussion. This spec defines the redesign-discussion scope.
**Drafted:** 2026-05-05.
**Audience:** operator + Codex when Branch D fires (per `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §2).
**Companion:** `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §3.1 (Branch D handoff to PROFIT-LLM-001).

## TL;DR

When Branch D fires, operator runs the bounded sizing audit defined in this spec. Audit produces a verdict: **"land PROFIT-LLM-001"** OR **"PROFIT-LLM-001 inadequate; size P4-GATE Appendix A."** Sizing surface is constrained to 4 axes (prompt template, model swap, context window, batch coherence) — not open-ended LLM redesign.

## 1. What PROFIT-LLM-001 means in this codebase

PROFIT-LLM-001 is the registered debt entry for **signal-analyzer LLM unification** — the question of whether `analysis/signal_analyzer.py`'s LLM call (the per-headline directional-view emitter that drives OPPORTUNITY events) is producing the maximum signal it can.

Currently:
- Single LLM call per matched headline
- Prompt template fixed (per `analysis/signal_analyzer.py`)
- Model = whatever Ollama serves locally (currently qwen3:14b or thereabouts)
- Context window = headline + market title + minimal pre-context

The hypothesis: a different LLM configuration (different prompt / model / context shape) could produce **more directional views on the same headline corpus**, lifting the OPPORTUNITY count beyond the post-Wave-1 base of ~1 % conversion.

## 2. Sizing surface (4 axes)

Sizing audit constrains to these 4 dimensions:

### 2.1 Prompt template

**Question:** does the current SYSTEM_PROMPT + USER_PROMPT shape produce maximum directional-view yield?

**Audit:** Codex re-runs the post-Wave-1 OPPORTUNITY corpus (≥ 30 d) against 3-5 alternative prompt templates:
- A1 (current baseline) — locked
- A2 — explicit chain-of-thought guidance ("reason step by step before emitting magnitude")
- A3 — explicit calibration anchor ("if uncertain, prefer magnitude=none over hedged guess")
- A4 — explicit worked example (1-2 ICL examples in the prompt)

**Sizing output:** per-template magnitude!=none yield rate over the same corpus.

**Decision criterion:** if any non-baseline template produces > 1.5× the directional-view yield WITHOUT calibration drift, PROFIT-LLM-001 lands as a prompt-template change. ~3-5 day Codex audit cost.

### 2.2 Model swap

**Question:** does a different LLM model produce more directional views?

**Audit:** Codex runs the same corpus against 2-3 alternative models available via Ollama:
- qwen3:14b (baseline)
- qwen3:30b-q4 (larger; possibly more directional)
- llama3:8b (different model family; sanity-check)

**Sizing output:** per-model directional-view yield + p50/p90 latency.

**Decision criterion:** if a different model produces > 1.3× directional-view yield with ≤ 2× latency, model swap lands as PROFIT-LLM-001. **Caveat: §8.5.2 decision-policy boundary** — model swap requires a fresh governance shadow-soak before promoting from shadow to real. Wall-clock cost: ~1-2 weeks of post-LLM-swap soak before Wave-N deploy.

### 2.3 Context window

**Question:** does the LLM see enough context to make a directional call?

**Audit:** Codex re-runs the corpus with extended context:
- Baseline = headline + market title (current)
- Extended = + recent 1-2 articles from same source class within 24 h
- Full = + market resolution criteria + last 3 BLEND_DECISIONs for ticker

**Sizing output:** per-context-shape directional-view yield + per-call token cost.

**Decision criterion:** if extended context produces > 1.5× directional-view yield with ≤ 2× token cost, context expansion lands. **Caveat: feeds into the LLM call budget pressure** — higher per-call token cost reduces total possible calls per UTC day.

### 2.4 Batch coherence

**Question:** does batching headlines for the same ticker improve directional-view yield?

**Audit:** Codex compares per-headline calls vs per-ticker batch calls (3-5 headlines per call).

**Sizing output:** per-call directional-view yield + per-trade attribution.

**Decision criterion:** if batch yields > 1.5× per-call directional views with attribution clarity preserved, batch coherence lands as PROFIT-LLM-001. **Caveat: BlendTask + Executor flow** — batch shape may require BlendTask refactor; out of PROFIT-LLM-001 scope.

## 3. Sizing audit procedure

When Branch D §2 fires, operator runs in this order:

1. **Step A — Codex audits prompt-template axis (§2.1)** — quickest; ~3-5 days. Lowest deploy effort.
2. **Step B — IF prompt-template insufficient, Codex audits model-swap axis (§2.2)** — ~1 week including soak.
3. **Step C — IF model-swap insufficient, Codex audits context-window axis (§2.3)** — ~1-2 weeks including budget impact analysis.
4. **Step D — Last resort: batch coherence (§2.4)** — ~3-4 weeks including BlendTask refactor scope analysis.

**At any step:** if the audit shows the axis is sufficient (criterion in §2.x), STOP audit. Land PROFIT-LLM-001 as that axis change.

If all 4 audits return "inadequate": PROFIT-LLM-001 verdict = **inadequate**. Operator escalates to P4-GATE Appendix A per `2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md`.

## 4. What's IN scope for PROFIT-LLM-001

Limited to the 4 axes in §2. Each axis change is a **single config / template / model edit** in `analysis/signal_analyzer.py` — not a full LLM-pipeline rewrite.

## 5. What's OUT of scope (escalates to P4-GATE Appendix A or beyond)

- **Multi-model ensemble.** Combining qwen3 + llama3 outputs is too complex; if PROFIT-LLM-001 4 axes inadequate, the right answer is market-mix work, not ensemble engineering.
- **LLM fine-tuning.** Training a Kalshi-specific LLM is out of project scope.
- **Streaming inference.** Per-call streaming doesn't change the directional-view yield; out of scope.
- **Per-market-class LLM specialization.** Different LLM per market class is over-engineering pre-evidence.

## 6. Sizing output format

Codex audit produces `docs/governance/[date]-profit-llm-001-sizing-report.md` with:

- Audit window dates
- Per-axis yield numbers (baseline + 3-5 alternatives per axis)
- Per-axis latency / token-cost numbers
- Per-axis decision-criterion verdict
- Final recommendation: which axis (or "inadequate; escalate")

**Length target:** ≤ 2,000 words. Audit-output should be operator-readable in one sitting.

## 7. Branch D fire-time procedure

When operator fires Branch D per Lever-D escalation criteria spec §2:

1. **Document the trigger** in `docs/profit_path_debt_log.md` PROFIT-EDGE-004 entry.
2. **Tag the moment:** `git tag -a edge-004-branch-d-fired-${UTC_DATE}`.
3. **Bump TLDR to v4:** create `docs/governance/edge-004-closure-path-tldr-v4.md` reflecting Branch D fired status.
4. **Open PROFIT-LLM-001 sizing.** Codex starts Step A (§3.1). Operator monitors progress; expects audit report within 5 days.
5. **Read audit report.** Operator verdicts which axis (or escalates to P4-GATE Appendix A).
6. **If axis-change recommended:** PROFIT-LLM-001 lands as a separate Wave (Wave 4+). Spec authoring + deploy timing TBD per axis.

## 8. Acceptance criteria (this spec)

This spec is satisfied when:

1. `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §3.1 cross-references this spec.
2. `docs/profit_path_debt_log.md` PROFIT-LLM-001 entry exists (registered; not yet sized).
3. The 4-axis surface is the canonical scope reference for any future PROFIT-LLM-001 sizing audit.

## 9. Out of scope (this spec)

- Sizing audit execution. Triggers when Branch D fires; not pre-emptive.
- Per-axis implementation specs. Drafted post-sizing-verdict.
- P4-GATE Appendix A scope (separate spec at `2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md`).

## 10. Cross-links

- `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §3.1 — Branch D handoff to PROFIT-LLM-001
- `docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` — second-handoff target (this cycle)
- `docs/_archive/2026-05-09-docs-consolidation/edge-004-closure-path-tldr-v3.md` — closure-path-TLDR (archived 2026-05-09; v4 fires post-Branch-D)
- `docs/profit_path_debt_log.md` PROFIT-LLM-001 entry — receives sizing-report cross-link
- `analysis/signal_analyzer.py` — implementation surface
- `docs/IMPLEMENTATION_CONTRACT.md` §11 — authority basis for redesign-discussion scope
