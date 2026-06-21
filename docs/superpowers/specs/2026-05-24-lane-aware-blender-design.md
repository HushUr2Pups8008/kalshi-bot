# Lane-aware blender — design spec (PROFIT-BLENDER-001)

**Status:** spec only. Implementation gated on operator approval after review.
**Date:** 2026-05-24
**Origin:** Operator question — "we're certainly not going to be looking at every new market that comes across to determine [the right prior]; how do we make this a structured process?" The answer has three options (A, B, C in this conversation); this is **Option B**, the architectural fix that makes the prior shape *non-load-bearing* for un-instrumented lanes.

---

## Problem

The current `decision_blender.blend()` blends across all lanes that pass non-None `LaneInput`. Live evidence from `KXUSAIRANAGREEMENT-27-26JUN` BD (2026-05-24):

```
fast_lane_p          = 0.05  confidence = 0.85   ← real LLM news signal
accumulation_p       = 0.50  confidence = 0.15   ← dossier neutral fallback (no real data)
structural_p         = 0.10  confidence = 0.24   ← IRON_CAP base rate
regime_weights       = (0.05, 0.55, 0.40)        ← pre-PR40 interp-heavy
regime_confidence    = 0.2307
→ blended_p          = 0.1109
→ blended_confidence = 0.1162  (diluted to ~14% of fast_lane_confidence)
```

The accumulation lane returned `p=0.5, conf=0.15` — a **neutral default** signal indicating "I have no data, falling back to uniform prior." The blender treated this as a real signal and weighted it in, pulling the blend toward 0.5 and capping blended_confidence well below fast_lane_confidence.

This is the operator-flagged systemic problem: **prior shape becomes load-bearing for the LLM's effective output**, requiring per-series manual tuning whenever a new Kalshi listing appears. The lanes-that-have-data don't get to express their confidence fully because lanes-that-have-no-data are still weighting in.

---

## Goal

The blender should produce a result that **matches the lanes that have actual signal**, automatically degrading gracefully when other lanes lack data. The prior weight shape stops dictating outcomes when only one lane is real.

After this change:
- A LLM-only signal on an uninstrumented market should produce `blended_confidence ≈ fast_lane_confidence × regime_factor` (not `× regime_factor × dilution_from_neutral_lanes`).
- The dossier and structural lanes contribute when they have a real signal, get *excluded* when they have only fallback.
- Per-series prior re-shaping becomes unnecessary for the prior-coverage problem the operator hit. (Other reasons to set series priors — e.g. real structural-data presence — remain.)

---

## Definition of "no real signal"

The hard problem: how does the blender know a lane returned a fallback vs. a real low-confidence signal?

Three candidate definitions, with tradeoffs:

| Definition | Pro | Con |
|---|---|---|
| **Explicit flag** on `LaneInput` (e.g. `is_fallback: bool`) | Unambiguous; caller knows the truth | Requires upstream caller changes (dossier system, structural service) to set the flag |
| **Heuristic threshold** — `is_fallback = (abs(p - 0.5) ≤ ε_p) AND (confidence ≤ ε_conf)` | No upstream changes | Conflates legitimate uncertainty with absent data |
| **Confidence-only threshold** — `is_fallback = confidence < ε_conf` | Simpler heuristic | Low-confidence-but-real signals still get dropped |

**Recommended: explicit flag**, with a default of `False` and a clearly-documented contract. Upstream callers set it when their internal "no data" branch triggers. Heuristic is a fallback for callers that haven't been updated.

`LaneInput` becomes:

```python
@dataclass(frozen=True)
class LaneInput:
    p: float
    confidence: float
    lane_id: str
    is_fallback: bool = False   # NEW: True when lane returned default-neutral, no real signal
```

---

## Algorithm change

`blend()` filters out fallback lanes before computing the weighted blend:

```python
def blend(*, fast, accumulation, structural, ...):
    raw_active = [ln for ln in (fast, accumulation, structural) if ln is not None]

    # PROFIT-BLENDER-001: exclude fallback lanes from weighting.
    # A fallback lane has no real signal — its inclusion would dilute
    # real signals from other lanes with no information gain.
    real_active = [ln for ln in raw_active if not ln.is_fallback]

    # If all lanes are fallback, degenerate to the original behavior
    # (uniform-weight equal blend). Better than crashing.
    active = real_active if real_active else raw_active

    # ... rest unchanged
```

**Single-line semantic change.** The rest of `_effective_confidences`, `_resolve_blend`, and downstream behavior stays identical — they just operate on the filtered list.

---

## Behavior matrix

For each scenario the blender outputs are predicted below. Concrete numbers compute against `regime_weights=(0.65, 0.25, 0.10)` (post-PROFIT-PRIORS-001 shape for event-driven markets) and `regime_confidence=0.22`.

| Scenario | Lanes active | Pre-fix `blended_conf` | Post-fix `blended_conf` | Pre-fix mode | Post-fix mode |
|---|---|---:|---:|---|---|
| LLM-only (fast=0.85, accum=fallback, struct=fallback) | 1 real | ~0.12 | ~0.20 (fast alone) | weighted_blend | dominant_lane |
| LLM + dossier (fast=0.85, accum=0.40 real, struct=fallback) | 2 real | similar to today | similar | weighted_blend | weighted_blend over 2 lanes |
| All three lanes have real data | 3 real | unchanged | unchanged | weighted_blend | weighted_blend (no change) |
| All lanes fallback | 0 real → degenerate to all | undefined | uniform blend (degradation) | weighted_blend | weighted_blend |

---

## Upstream caller obligations

Each lane producer must set `is_fallback=True` when its internal "no data" branch returns:

| Caller | When to set fallback |
|---|---|
| `tasks/blend_task._read_lane_context` accumulation path | when dossier is empty OR coverage below threshold (e.g. <2 evidence rows) |
| `tasks/blend_task._read_lane_context` structural path | when no `_SERIES_PRIORS` entry AND no external structural data |
| `tasks/blend_task._fast_lane_input` (LLM signal) | n/a — fast lane signal is always real when present (no fast signal → fast=None) |

This is the load-bearing part — the spec is only as good as the upstream caller's accuracy at flagging fallbacks. **A new caller test asserts each lane producer correctly emits `is_fallback`.**

---

## Acceptance criteria for implementation

1. `LaneInput.is_fallback: bool = False` added with documented contract.
2. `blend()` filters out fallback lanes before weighted-blend math.
3. `tasks/blend_task._read_lane_context` populates `is_fallback` on accumulation and structural lanes when their producers hit the "no data" branch.
4. New tests in `tests/test_decision_blender.py` for the four scenarios in the behavior matrix.
5. New test in `tests/test_blend_task.py` asserting `_read_lane_context` sets `is_fallback=True` when called with an empty-dossier market.
6. **Replay regression**: run the offline replay tool against a fixture matching the 2026-05-24 KXUSAIRANAGREEMENT BD (`fast=0.05/0.85, accum=0.5/0.15 fallback, struct=0.1/0.24 fallback`) and assert post-fix `blended_confidence ≥ 0.20` (clears G1=0.05 with `regime_confidence=0.22`).
7. CHANGELOG entry + VERSION bump.
8. **No G1/G4 threshold changes.** **No prior-shape changes.** **No env vars.** Behavior change is bounded to the blender's lane-filtering and the upstream callers that set the fallback flag.

---

## Test pinning the load-bearing invariant

```python
def test_lane_with_is_fallback_does_not_dilute_other_lanes():
    """Load-bearing PROFIT-BLENDER-001 contract: when a lane is marked
    is_fallback=True, it must NOT contribute to the blended output.
    A fallback lane carries no signal — including it in the weighted-
    blend math would dilute real signals from non-fallback lanes."""
    fast = LaneInput(p=0.05, confidence=0.85, lane_id="fast")
    accum_fallback = LaneInput(p=0.5, confidence=0.15, lane_id="accumulation", is_fallback=True)
    struct_fallback = LaneInput(p=0.1, confidence=0.24, lane_id="structural", is_fallback=True)

    result = blend(
        fast=fast, accumulation=accum_fallback, structural=struct_fallback,
        regime_weights={"fast": 0.65, "interpretation": 0.25, "structural": 0.10},
        regime_confidence=0.22,
    )

    # Post-fix: only fast lane contributes → dominant_lane mode.
    assert result.blend_mode == "dominant_lane"
    assert result.blended_p == pytest.approx(0.05)
    # blended_confidence approaches fast_lane_confidence × regime_factor (≥0.20).
    assert result.blended_confidence >= 0.20
```

---

## Risk assessment

**Blast radius:** signal-flow path. Per `~/.claude/rules/domain_constraints.md`, requires explicit operator approval before merge.

**Adversarial review focus:**
- Could excluding fallback lanes ever produce a WORSE decision than including them? (E.g., if structural fallback p=0.10 actually IS informative because base rates ARE low for this market class.) — Counter: the structural lane is the right place to express this, but should set `is_fallback=False` and let blender weight it normally. The fallback flag is for "I have NO data," not "I have weak data."
- Test #6 (replay regression) is the load-bearing validator. If the post-fix blended_confidence on the historical KXUSAIRANAGREEMENT BD does NOT clear G1 cleanly, the implementation has a bug.
- Backward compat: callers that don't set `is_fallback=True` still work exactly as before. No automatic migration risk.

---

## Sequencing

1. Spec review (this document) → operator approval gate
2. Implementation: `LaneInput.is_fallback` + `blend()` filter + caller flag-setting + tests
3. Replay regression run against fixture before merge
4. PR with operator gate (per domain_constraints.md)
5. Merge + bot restart → first natural BD on uninstrumented market should now clear G1 if the LLM is confident

Phase 3 framework activation remains paused.
