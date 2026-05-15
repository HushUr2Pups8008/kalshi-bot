# Cycle-15B Claude independent trace read + same-class pathology cross-check (L4 + L5)

**Type:** verification artifact. Combines L4 (independent Lane B trace read) + L5 (PROFIT-GOV-001/002 same-class pathology cross-check) per `2026-05-06-cycle-15b-task-split.md`.
**Drafted:** 2026-05-06 post-Codex C3-C6 commit `851eb86`.
**Authority:** Cycle-15B charter §"Pre-stated decision criteria" (`2026-05-06-cycle-15b-charter-extraction-rebuild.md`).
**Gates:** L6 sub-fix verdict appendix consumes this.

## TL;DR

L4 independent read **concurs** with Codex C2 zero-collapse-step identification (`keyword_path`) and Codex C6 sub-fix surface (`per_keyword_direction_map` extension). Surface narrowed to cycle-14 trace sites 3 + 7. Sites 2 + 6 (LLM-path) RULED OUT for the zero-collapse phenomenon.

L5 cross-check finds **no same-class pathology** between Cycle-15B keyword-map gap and PROFIT-GOV-001 (qwen3 thinking-consumed-by-grammar) or PROFIT-GOV-002 (rubber-stamp bias). Caveat: C3 LLM audit ran with Ollama unavailable/circuit-open; production-LLM behavior on directional fixtures was NOT verified live. C2 already ruled out LLM-path for the zero-collapse phenomenon, so this caveat does not block C7.

**Key finding flagged for L6:** Codex's sub-fix addresses the 8 directional fixtures' zero-emission but does NOT address F8 NEUTRAL over-emission. F8 produces `|Δ|=0.12` via existing `"senate judiciary"` keyword (direction=yes, strength=0.12), which fails the charter Lane B clause "F8/F9 NEUTRAL fixtures stay within `expected_magnitude_max` (0.02 / 0.005 respectively)." Sub-fix is incomplete on this clause.

## L4 — Independent Lane B trace read

### Read sequence (without consulting Codex C6 proposal first)

Read C2-C5 outputs in order, formed independent diagnosis, then compared against Codex C6.

### Independent diagnosis

**Zero-collapse step:** `keyword_path`. Confirmed by:
- C2 `zero_collapse_step.json`: 8 fixtures (F1-F7 directional + F10 repetition) collapse at `keyword_path`. Single-step finding.
- C5 `suppression_trace.json`: pre_suppression_magnitude == post_suppression_magnitude for all 10 fixtures. Suppression is NOT the cause.
- C5 `keyword_estimate_reasoning` for F1-F7: `"Keyword analysis found 0 signal(s): []. Net shift: +0.000."` — no matching keywords means net_shift = 0 means kw_prob = base_probability = 0.50.

**Why keyword_path emits 0:** the per-keyword direction map (`config.GEOPOLITICAL_SIGNALS` and siblings) does not contain phrases matching the synthetic fixture vocabulary. Per C4 keyword_audit:
- F1 expected phrases (`reauthorization signed`, `signed into law`): NONE in map.
- F2 (`expires`, `fails to act`, `will not become law`): NONE.
- F3 (`issues pardons`, `signed pardons`, `pardons for`): map has `trump pardons`/`trump fires`/etc. but `matched_keywords=[]` per actual matcher → no production-level match.
- F4 (`no january 6 pardons`, `no pardons`): NONE; map has no negation handling.
- F5 (`sign nuclear deal`, `nuclear agreement`): NONE.
- F6 (`arrives in islamabad`, `landed in islamabad`): NONE.
- F7 (`cancels pakistan trip`): NONE.
- F10 = repetition of F1: same gap.

This is cycle-14 trace site 3 (per-keyword `net_shift` produces zero because the lookup yields no entries) + site 7 (per-keyword direction assignment for synthetic fixture vocabulary is absent). Sites 1, 4, 5 ruled out by Lane A pass (cycle-14). Sites 2, 6 (LLM-path) ruled out by C2 first-step-collapse rule.

**Match against Codex C6:** Codex selected `per_keyword_direction_map` at `config.py:734 / GEOPOLITICAL_SIGNALS` as the surface. Single-step. Concur on surface and step.

### Coverage notes (independent reading)

C4 `coverage_ok` is computed against `expected_phrases` only. NEUTRAL fixtures (F8, F9) have empty `expected_phrases` so `coverage_ok=true` automatically. This metric does NOT detect over-emission on NEUTRAL fixtures.

Inspecting C5 suppression_trace for F8: `keyword_estimate_keywords: ["senate judiciary"]`, `pre_suppression_magnitude: 0.12`, `keyword_estimate_side: "yes"`. F8 produces 0.12 YES-direction shift on a procedural-no-resolution fixture (per fixture rationale "Procedural movement, no resolution-affecting outcome. Model should move minimally |delta| < 0.02").

This is the F8 over-emission finding load-bearing for L6.

F9 OFF_TOPIC_NEUTRAL: 0 keywords match, magnitude 0.0 → passes `expected_magnitude_max=0.005`.

## L5 — PROFIT-GOV-001 / PROFIT-GOV-002 same-class pathology cross-check

### Pathology classes recap

- **PROFIT-GOV-001** (closed 2026-05-02): Ollama `format=json` + qwen3 thinking returned empty `{}`. Root cause: qwen3 chain-of-thought reasoning consumed by JSON grammar constraint. Fix: pass top-level `think: False` in Ollama generate-request payload (sibling of `format`, not nested). `governance/llm.py:LocalQwenLLM.complete` carries the fix.
- **PROFIT-GOV-002** (closed): rubber-stamp bias on `disable_source` decisions; LLM defaulted to acquiescent low-confidence approvals.

### Cycle-15B Lane B failure mode

Keyword-map vocabulary gap. Pure config / data layer issue. Not LLM behavior.

### Cross-check

| dimension | PROFIT-GOV-001/002 | Cycle-15B keyword-map gap |
|---|---|---|
| Layer | LLM inference + prompt convention | Config / per-keyword direction map |
| Symptom | LLM returns empty `{}` or rubber-stamp approvals | `keyword_estimate` returns net_shift=0 because no keywords match |
| Root cause | qwen3 thinking lost to grammar / prompt-engineering pathology | `GEOPOLITICAL_SIGNALS` config does not contain fixture vocabulary |
| Same-class? | NO | NO |

The Lane B failure mode is structurally orthogonal to the LLM-layer pathologies. The fix is a config/data extension, not a prompt or grammar change.

### C3 LLM audit caveat

C3 `llm_prompt_audit.json` shows all 10 fixtures with `status: "ollama_unavailable"` or `"ollama_circuit_open"`. Production qwen3 behavior on directional Lane B fixtures was not verified live during this run.

**Why this does NOT block C7:**
- C2 zero-collapse-step finding fires at `keyword_path`, which is the FIRST step in the trace per harness break-on-first-collapse rule.
- The harness traces `keyword_path` BEFORE `llm_path` (per `signal_analyzer.py` execution order: keyword_estimate runs first, LLM runs only if confidence threshold not met by keywords).
- Once `keyword_path` step output is `<0.01` on directional input, downstream LLM behavior is irrelevant for the zero-collapse finding.

**Forward concern (not Cycle-15B scope):** if production qwen3 emits `magnitude="none"` on directional input (PROFIT-GOV-002 same-class pathology at signal-analyzer prompt layer), that would be a SEPARATE Cycle-16+ issue surfacing only after Cycle-15B keyword extension repairs the keyword_path layer. Worth re-running C3 LLM audit live (Ollama available) when Cycle-15B C7 lands, OR as part of Cycle-16 if no positive-EV slice surfaces despite Lane B pass.

### L5 verdict

No same-class pathology between current Cycle-15B sub-fix and prior LLM-layer fixes. Codex's keyword-map extension surface is correct.

## What this read does NOT cover

- L6 sub-fix verdict appendix (separate doc / appendix to Codex's `edge-replay-cycle15b-sub-fix-proposal.md`).
- L7 re-ingestion atomicity review (post-C9, separate doc).
- C8 Lane B post-fix verification (Codex deliverable; consumes the C7 sub-fix and runs Lane B harness).
- C10 IC §16 acceptance (Codex deliverable; consumes C9 re-ingestion).

## Cross-links

- `docs/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` — locked criteria source.
- `docs/governance/2026-05-06-cycle-15b-task-split.md` — L4 + L5 task definitions.
- `docs/governance/2026-05-06-cycle-15b-pre-execution-criteria-verification.md` — L2 + L3 verifications.
- `docs/governance/edge-replay-cycle15b-sub-fix-proposal.md` — Codex C6 sub-fix proposal (L6 appends Claude verdict).
- `docs/_archive/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` — sites 3 + 7 narrowed via L4 read.
- `logs/edge_replay/cycle15b/zero_collapse_step.json` — C2 single-step finding.
- `logs/edge_replay/cycle15b/keyword_audit.json` — C4 coverage gaps.
- `logs/edge_replay/cycle15b/suppression_trace.json` — C5 (F8 over-emission surfaced here).
- `logs/edge_replay/cycle15b/llm_prompt_audit.json` — C3 (Ollama unavailable; verified non-blocking).
- `docs/profit_path_debt_log.md` `PROFIT-GOV-001` / `PROFIT-GOV-002` — orthogonal pathologies.
