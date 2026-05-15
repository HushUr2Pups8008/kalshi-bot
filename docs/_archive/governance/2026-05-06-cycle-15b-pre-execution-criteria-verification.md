# Cycle-15B pre-execution criteria-lock + trace harness review (L2 + L3)

**Type:** verification artifact. Combines L2 (sub-fix-criteria-lock verification) + L3 (Codex per-step trace code review) per `2026-05-06-cycle-15b-task-split.md`.
**Drafted:** 2026-05-06 post-Codex C1+C2 commit `e4296fc`.
**Authority:** Cycle-15B charter §"Pre-stated decision criteria" (`2026-05-06-cycle-15b-charter-extraction-rebuild.md`).
**Gates:** Codex C6 sub-fix selection does NOT proceed until this verification passes.

## TL;DR

L2: Codex C2 zero-collapse-step identification matches the locked charter criterion. **PASS** — no drift. C6 authorized.
L3: Codex C1 trace harness + `signal_analyzer.py` instrumentation reviewed. **PASS** — read-only under unflagged runs, criterion implementation matches charter, schema stable. No findings blocking C3-C6.

## L2 — sub-fix-criteria-lock verification

### Locked charter criterion

Per `2026-05-06-cycle-15b-charter-extraction-rebuild.md` §"Zero-collapse-step identification":

```
A step "zeroes magnitude" iff:
  step input has |signal_magnitude| > 0.05
  step output has |signal_magnitude| < 0.01
If multiple steps each individually meet the collapse criterion, record the FIRST.
If no single step zeros alone but cumulative effect collapses, that is a SECOND-CLASS
multi-step finding requiring operator scope-extension BEFORE C7.
```

### Codex C2 output

`logs/edge_replay/cycle15b/zero_collapse_step.json`:

```json
{
  "criterion": {"input_abs_gt": 0.05, "output_abs_lt": 0.01},
  "finding_type": "single_step",
  "zero_collapse_step": "keyword_path",
  "count": 8,
  "counts": {"keyword_path": 8},
  "examples": ["F1_FISA_REAUTHORIZED_YES", "F2_FISA_LAPSED_NO",
               "F3_PARDONS_ISSUED_YES", "F4_PARDONS_NOT_ISSUED_NO",
               "F5_TRUMP_IRAN_DEAL_YES", "F6_VANCE_PAKISTAN_VISIT_YES",
               "F7_VANCE_PAKISTAN_CANCELED_NO", "F10_REPETITION_DAMPING"]
}
```

### Drift check

| charter requirement | Codex output | match |
|---|---|---|
| input criterion `|magnitude| > 0.05` | `input_abs_gt: 0.05` | ✓ |
| output criterion `|magnitude| < 0.01` | `output_abs_lt: 0.01` | ✓ |
| FIRST step recorded if multi-step | `per_step_extraction_trace.py:169` `break` after first-collapse step in iteration order | ✓ |
| single_step vs multi_step finding type reported | `finding_type: "single_step"` (line 183 logic: `single_step if len(ordered)==1 OR top_count > second_count`) | ✓ |
| NEUTRAL fixtures excluded from collapse criterion | F8 + F9 absent from examples list (their `expected_signal=0` → `|input|<0.05` → criterion never fires) | ✓ |

**Verdict: PASS — no drift.**

### Coverage

8 of 10 fixtures (F1-F7 directional + F10 repetition) consistently identify `keyword_path` as the zero-collapse step. F8 + F9 NEUTRAL fixtures correctly excluded by criterion (zero expected signal, criterion floor not met).

Single_step finding is unambiguous: only `keyword_path` appears in `counts`. No multi-step ambiguity. No SECOND-CLASS finding. C6 sub-fix selection proceeds against `keyword_path` as the named step.

### Cross-reference to cycle-14 sign-error candidate trace

`docs/_archive/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` listed sites 2/3/6/7 as not-ruled-out by Lane A:
- Site 2 — LLM-path probability shift application (`signal_analyzer.py:578-581`).
- Site 3 — keyword-path `net_shift` (`signal_analyzer.py:326`).
- Site 6 — LLM `direction` field convention (PROMPT layer).
- Site 7 — per-keyword direction assignment.

Codex C2 narrows the surface to **keyword_path** = sites 3 + 7. Sites 2 + 6 (LLM-path) are RULED OUT for the zero-collapse phenomenon (LLM-path step did not collapse in any fixture). C3 LLM prompt audit is now lower-priority confirmatory; C4 per-keyword direction map dump becomes the primary discriminator between site 3 (net_shift computation) and site 7 (per-keyword direction map).

## L3 — Codex C1 trace harness code review

### Read-only behavior

`analysis/signal_analyzer.py` `_emit_extraction_trace_step` (line 16-17):

```python
if os.environ.get("KALSHI_EXTRACTION_TRACE") != "1":
    return
```

✓ Production unflagged runs are unaffected. Instrumentation hooks no-op without explicit env opt-in. Matches charter requirement "do not modify extraction behavior."

### Instrumentation completeness

Three production hooks added in `signal_analyzer.py`:
- `keyword_path` after `keyword_estimate` (line ~466 in diff).
- `llm_path` after LLM probability resolution (line ~1129).
- `final_estimate` at two terminal branches (line ~1178 + ~1221: `no_keywords_no_llm_estimate` and `keyword_only`).

Harness (`per_step_extraction_trace.py`) emits four step records per fixture:
- `fixture_expected_signal` (synthetic ground-truth anchor).
- `keyword_path` (calls `keyword_estimate` directly).
- `llm_path` (calls `estimate_probability` end-to-end; final_prob - base = LLM-effective signal).
- `final_estimate` (final probability - base).

✓ ≥ 4 step records per fixture. Aligns with charter pattern.

### Discrepancy noted (non-blocking)

`per_step_extraction_trace.py:138`: `_step("llm_path", expected, llm_signal, llm_state)` uses `expected` (signed fixture truth) as the step's `input_signal_magnitude`, not `keyword_signal` (the in-production upstream value).

Production instrumentation `signal_analyzer.py:1131` uses `input_signal_magnitude=kw_prob - base_probability` (the chained upstream value).

These differ. The harness scores each step against synthetic ground truth ("given crystal-clear input, what does this step output?") rather than chained input→output. For the locked criterion (input>0.05 AND output<0.01) the harness's choice is OK because:
- Directional fixtures: forced `expected=±0.051` (floor at line 36-37) → `|input|>0.05` always holds → criterion fires whenever any step's output drops below 0.01.
- NEUTRAL fixtures: `expected=0.0` → `|input|<0.05` → criterion never fires (correct).

For C2 zero-collapse identification, the harness's choice produces correct results. Documenting the discrepancy here so future trace reviews don't read the harness as production-data-flow modeling. Not blocking.

### Schema stability

`{step_name, input_signal_magnitude, output_signal_magnitude, intermediate_state}` schema consistent across all 4 step types. JSON output sorted (line 209: `sort_keys=True`). ✓

### Test coverage

`tests/test_edge_replay_per_step_extraction_trace.py` (41 lines) — focused suite. Default `--no-llm` path supported (line 204 `--no-llm` flag, line 132-137 conditional skip). Allows CI without Ollama dependency. ✓

### Findings

No findings blocking C3-C6. The discrepancy in §"Discrepancy noted" is documented for future reviewers; it does not affect L2 verification or C6 sub-fix selection.

## What this verification does NOT cover

- C3 LLM prompt convention audit (separate Codex deliverable; reviewed in L4 + L5).
- C4 per-keyword direction map dump (separate Codex deliverable; primary discriminator for site 3 vs site 7).
- C5 suppression-logic trace (separate Codex deliverable).
- L4 independent Lane B trace read (lands after C3-C5 land, with full diagnostic context).
- Sub-fix selection content — that is C6's deliverable, gated by this verification + L4 + L5 + L6 verdict appendix.

## Cross-links

- `docs/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` — locked criteria source.
- `docs/governance/2026-05-06-cycle-15b-task-split.md` — L2 + L3 task definitions.
- `docs/governance/cycle-15b-post-verdict-action-checklist.md` — L9 checklist (this is the L2 + L3 deliverable referenced there).
- `docs/_archive/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` — sites 2/3/6/7 narrowed to keyword_path = sites 3 + 7.
- `scripts/edge_replay/per_step_extraction_trace.py` — Codex C1 harness.
- `analysis/signal_analyzer.py` — Codex C1 instrumentation hooks.
- `logs/edge_replay/cycle15b/zero_collapse_step.json` — Codex C2 output verified.
- `logs/edge_replay/cycle15b/per_step_trace.json` — Codex C2 per-fixture trace records.
