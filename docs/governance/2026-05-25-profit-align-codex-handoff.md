# PROFIT-ALIGN Codex Follow-up Handoff

Date: 2026-05-25
Author: Codex independent review follow-up
Scope: Fixes for the five Codex verdict items after PR #54. No runtime restart,
no launchd mutation, no env mutation, no readiness threshold changes, no
paper-to-live transition.

## Summary

Codex agreed with the conservative direction of the PROFIT-ALIGN cluster but
found several over-claims:

- Floor-clamp halving was final-probability-based and could false-positive on
  legitimate exact-boundary arithmetic.
- LLM dedup keyed too narrowly and cached final probability instead of the LLM
  verdict.
- PROFIT-MATCH-DYNAMIC seed weights were treated as pinned despite weak audit
  volume.
- Test isolation was scattered across individual tests.
- Three PROFIT-ALIGN scaffold items shipped surfaces but did not yet have
  runtime emission paths.

This follow-up converts those findings into code, tests, and tracker docs.

## Code Changes

1. `main.py`
   - `_is_floor_clamp_suspected` now accepts `market_probability` and
     `llm_confidence`.
   - It reconstructs raw pre-clamp probability from cfg magnitude shifts and
     only fires when raw probability crosses below `0.05` or above `0.95`.
   - Open-position drift logging now uses
     `cfg.position_drift_alert_threshold` as a fraction of entry price. Values
     `>= 1.0` disable emission.

2. `analysis/llm_dedup_cache.py` and `analysis/signal_analyzer.py`
   - Cache key is now `sha256(full_prompt_text)`.
   - Cache values are verdict fields: `(confidence, reasoning, direction,
     magnitude)`.
   - Cache hits recompute final probability against the current market via
     `_probability_from_llm_verdict`.
   - Source/body/resolution/close-time changes now miss the cache.

3. `analysis/market_matcher.py` and `data/matcher_token_weights.json`
   - New `_combined_token_downweight` averages overlap-token weights, counting
     missing or malformed entries as `1.0`.
   - One generic downweighted token no longer min-dominates a multi-token
     overlap.
   - Audit seed entries are now `pinned: false` with
     `_seed_status: provisional`.

4. `tasks/blend_task.py` and `utils/logger.py`
   - Added `LANE_SKIPPED` writer and BlendTask emission when
     `cfg.enable_lane_skip_when_no_data` is enabled.
   - Added BlendTask call to `log_gate_summary` immediately after readiness
     evaluation.
   - Binding priority reports `G4_regime_low` before G1 when both fail.

5. `tests/conftest.py`
   - Global autouse reset for `analysis.llm_dedup_cache`.
   - Shared `isolated_match_feedback_weights` fixture for matcher/simulation
     baseline tests.

## Tests Added Or Changed

- `tests/test_main_pipeline.py`
  - Exact 0.05 / 0.95 boundary without raw crossing no longer triggers
    floor-clamp halving.
  - Position drift test patches `cfg.position_drift_alert_threshold` instead
    of legacy `DRIFT_ALERT_CENTS`.

- `tests/test_signal_analyzer.py`
  - Same prompt and new market price reuses verdict but recomputes probability.
  - Same headline/market with different source/body triggers fresh LLM call.

- `tests/test_market_matcher.py`
  - Matcher downweight tests use production `_combined_token_downweight`.
  - Supporting tokens dilute one generic downweight.

- `tests/test_match_feedback.py`
  - Seed file expectations now require provisional, recoverable entries.

- `tests/test_blend_task.py`
  - `LANE_SKIPPED` emission when no-data lane skip is enabled.
  - `GATE_SUMMARY` emission identifies G4 as binding when G4 and G1 both fail.

- `tests/test_align_remaining.py`
  - LLM dedup unit tests updated from price-bucket identity to prompt identity.

## Claude Review Notes

Recommended Claude review posture:

1. Check that the new floor-clamp signature has no stale call sites.
2. Confirm the LLM prompt still excludes market price. If price is ever
   reintroduced to the prompt, prompt hashing remains safe but recompute-on-hit
   should be revisited.
3. Decide whether provisional seed weights should have explicit expiry metadata
   beyond `_seed_status: provisional`.
4. Check whether `LANE_SKIPPED` should emit for fallback neutral lanes as well
   as missing lanes. Current follow-up only emits for missing accumulation or
   structural inputs.
5. Keep PROFIT-PHASE3-003 volume gating intact. These changes improve safety
   and observability; they do not prove profitability.

## Verification Commands

Focused command used during development:

```bash
.venv/bin/python -m pytest \
  tests/test_main_pipeline.py::TestFloorClampSuspected \
  tests/test_signal_analyzer.py::TestEstimateProbability::test_llm_dedup_recomputes_probability_for_current_market_price \
  tests/test_signal_analyzer.py::TestEstimateProbability::test_llm_dedup_key_includes_prompt_body_and_source \
  tests/test_market_matcher.py::TestMatcherDownweightApplication \
  tests/test_match_feedback.py::TestSeedWeightsFile \
  tests/test_blend_task.py::test_lane_skip_flag_emits_no_data_lane_events \
  tests/test_blend_task.py::test_readiness_gate_summary_logs_g4_as_binding_constraint \
  tests/test_main_pipeline.py::test_on_price_update_logs_position_drift_from_entry_price_cents \
  tests/test_align_remaining.py::TestLlmDedupCache \
  -q
```

Expected focused result after this follow-up: `26 passed`.

Final verification from Codex pass:

- Focused regression set: `26 passed`.
- Affected test files:
  `tests/test_main_pipeline.py tests/test_signal_analyzer.py tests/test_market_matcher.py tests/test_match_feedback.py tests/test_blend_task.py tests/test_align_remaining.py tests/test_simulations_smoke.py`:
  `367 passed, 13 xfailed`.
- Ruff affected Python files: `All checks passed!`.
- Full suite: `2389 passed, 4 skipped, 71 xfailed, 7 warnings`.
