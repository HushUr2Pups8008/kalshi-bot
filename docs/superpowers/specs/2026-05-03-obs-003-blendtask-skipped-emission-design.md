# OBS-003 — BlendTask SKIPPED emission for blocked-reason path

**Status:** design (post-soak implementation; do not land before PROFIT-PHASE2-001 closes ≥ 2026-05-09)
**Tracker:** `PROFIT-OBS-003` in `docs/profit_path_debt_log.md`
**Owner:** Claude
**Severity:** HIGH (per the 2026-05-01 promotion; the 2026-05-02 forensic addendum reframed scope but kept severity)
**Dependencies:** none

## 1. Problem

`OPPORTUNITY` events are logged at `main.py:772` *upstream* of `BlendTask.process_fast_lane_result`. When the blender or readiness gate produces a non-None `trade_blocked_reason`, the candidate is dropped at `tasks/blend_task.py:204` with a `BlendTaskResult(enqueued=False)` and **no SKIPPED record is emitted** — only a `BLEND_DECISION` record carrying the kill reason in its `trade_blocked_reason` field.

Empirical impact, per the Codex 2026-05-03 attribution audit (`docs/_archive/governance/2026-05-03-obs003-kill-attribution.md`):

- 264 OPPORTUNITY total → 23 accounted exits (20 SKIPPED + 3 PAPER_TRADE) + **240 silent exits** (100% attributed via BLEND_DECISION) + 1 unattributed
- Silent-exit decomposition:

| `trade_blocked_reason` | count | % of silent exits |
|---|---:|---:|
| `G1_blended_confidence` | 197 | 82.1% |
| `G6_recency_score` | 31 | 12.9% |
| `G2_evidence_source_class_diversity` | 12 | 5.0% |

Trade-log consumers that key off the `SKIPPED` event stream (governance Phase 2 reasoning, `bothealth.sh` daily aggregator, future readiness-gate calibration runs) currently see only the 20 executor-emitted SKIPPED records over the lifetime audit window, missing the 240 BlendTask-blocked candidates entirely. The fix is **log-stream consolidation**: emit a SKIPPED record from BlendTask whenever a candidate is blocked, carrying the blender or readiness-gate reason in the `reason` field.

## 2. The fix

At `tasks/blend_task.py:204`, when `trade_blocked_reason is not None`, emit a SKIPPED record before the early return:

```python
# tasks/blend_task.py — current (lines 195–212)
await self._emit_blend_decision(
    ticker=ticker,
    blend_result=blend_result,
    regime_weights=regime_weights,
    regime_confidence=regime_confidence,
    trade_blocked_reason=trade_blocked_reason,
    evidence_ids=evidence_ids,
)

if trade_blocked_reason is not None:
    return BlendTaskResult(
        market_ticker=ticker,
        blend_result=blend_result,
        readiness_decision=readiness,
        trade_blocked_reason=trade_blocked_reason,
        candidate=None,
        enqueued=False,
    )
```

becomes:

```python
await self._emit_blend_decision(
    ticker=ticker,
    blend_result=blend_result,
    regime_weights=regime_weights,
    regime_confidence=regime_confidence,
    trade_blocked_reason=trade_blocked_reason,
    evidence_ids=evidence_ids,
)

if trade_blocked_reason is not None:
    await self._emit_skipped(
        ticker=ticker,
        blend_result=blend_result,
        readiness=readiness,
        trade_blocked_reason=trade_blocked_reason,
        fast_lane_result=fast_lane_result,
    )
    return BlendTaskResult(
        market_ticker=ticker,
        blend_result=blend_result,
        readiness_decision=readiness,
        trade_blocked_reason=trade_blocked_reason,
        candidate=None,
        enqueued=False,
    )
```

The `_emit_skipped` helper composes the same payload shape the executor uses at `trading/executor.py:137–152` and writes via `write_trade_log_async(trade_log.log_skipped, ...)`. Reason is the BlendTask-side `trade_blocked_reason` value (e.g. `"G1_blended_confidence"`, `"G2_evidence_source_class_diversity"`, blender-side reasons set inside `_blender(...)`).

## 3. Components touched

- `tasks/blend_task.py` — add `_emit_skipped` method (analogous to `_emit_blend_decision`); call it before the blocked-reason early return.
- `tests/test_blend_task.py` — new test fixtures covering the SKIPPED emission for each `trade_blocked_reason` value.
- `tests/test_governance_monitor.py` — sanity-check that the new SKIPPED stream doesn't double-count BLEND_DECISION + SKIPPED for the same kill (the monitor should still report the same kill count; just under a different event type).

No production-code change to `trading/executor.py` — its existing SKIPPED emission for `_validate()` rejections stays unchanged.

## 4. Data flow

Pre-fix:

```
OPPORTUNITY (logged) → BlendTask → blender → readiness gate
                      ↓
                BLEND_DECISION (always, with trade_blocked_reason)
                      ↓
   if blocked: silent return; OPPORTUNITY → BLEND_DECISION join is the only audit lever
   else:       enqueue → executor → _validate → SKIPPED or PAPER_TRADE
```

Post-fix:

```
OPPORTUNITY (logged) → BlendTask → blender → readiness gate
                      ↓
                BLEND_DECISION (always, with trade_blocked_reason)
                      ↓
   if blocked: SKIPPED (NEW; reason=trade_blocked_reason); return
   else:       enqueue → executor → _validate → SKIPPED or PAPER_TRADE
```

The accounting becomes `OPPORTUNITY = SKIPPED + PAPER_TRADE` exactly (modulo at-the-moment in-flight). Both BlendTask-blocked and executor-rejected candidates appear in the SKIPPED stream with distinct `reason` strings.

## 5. SKIPPED payload shape

The executor's existing SKIPPED payload at `trading/executor.py:137–151` carries:

- `reason` (str)
- `ticker`, `headline`, `source`
- `method` (`"llm"` or `"keyword"`)
- `llm_direction`, `llm_magnitude`
- `model_probability`, `market_price`
- `edge`, `min_edge_threshold`
- `signal_meta` (optional)

BlendTask's SKIPPED emission should match this shape, sourcing fields from `fast_lane_result` (the `SignalAnalysis` upstream of the blender), `blend_result` (for the blended `model_probability` and `edge`), and the readiness-gate output (for `min_edge_threshold` if a `readiness_gate_min_edge_override` is active). Specifically:

- `reason`: `trade_blocked_reason` value (the G1–G6 enum or blender-side reason)
- `ticker`, `headline`, `source`: from `fast_lane_result.market.ticker`, `fast_lane_result.news_item.headline[:80]`, `fast_lane_result.news_item.source`
- `method`: `"llm"` if the upstream signal had any `llm_*` field set, else `"keyword"`
- `llm_direction`, `llm_magnitude`: from `fast_lane_result`
- `model_probability`: `blend_result.blended_p` (the post-blend value, not the fast-lane raw `estimated_probability`)
- `market_price`: `fast_lane_result.market_yes_price`
- `edge`: `blend_result.blended_p - fast_lane_result.market_yes_price/100.0` (the post-blend edge; matches the executor's convention)
- `min_edge_threshold`: `readiness.readiness_gate_min_edge_override` if non-None, else the blender's `default_min_edge`
- `signal_meta`: forwarded if present on `fast_lane_result.signal_meta`

This is the load-bearing payload-shape decision: the BlendTask-emitted SKIPPED's `model_probability` and `edge` reflect the *blended* values, not the fast-lane raw values, because the blender is what produced the blocked-reason verdict. Audit consumers that joined OPPORTUNITY's raw edge against SKIPPED's edge previously saw discontinuity (OPPORTUNITY emits raw fast-lane edge at `main.py:772`); with this fix they now see the post-blend edge that actually drove the decision. Document the convention in the SKIPPED log schema notes.

## 6. Risk

**Volume jump in SKIPPED stream.** Pre-fix lifetime SKIPPED count: 20. Post-fix expected: 20 + 240 = 260 over the same 13-day window (~20×). Downstream tooling that assumed "SKIPPED is rare" needs review:

- `scripts/bothealth.sh` daily aggregator (per recent commit `9023561`) — already groups by `reason`; the histogram just becomes richer. Verified soak-safe.
- `scripts/governance_monitor.py` — reads `decisions.jsonl` (governance-side), not the trade log. Unaffected.
- Future readiness-gate calibration scripts — will benefit from the richer attribution.
- Operator dashboards (if any) that surface raw SKIPPED counts as "executor rejections" — those counts will jump and the operator needs to know the SKIPPED stream now means "blocked at any pipeline stage."

The fix is observability consolidation; no decision-path semantics change. But the volume scaling is real and a few downstream consumers may need to update their narrative.

**Soak invariant:** the change touches `tasks/blend_task.py` which is on the decision-path file list. Even though the change is purely additive (emits an additional log record; doesn't alter any control flow or returned values), the conservative reading of `decision consistency = high-risk during soak` covers any edit to that file. Post-soak landing is the right cadence.

**Backward compatibility:** the `BLEND_DECISION` records retain their `trade_blocked_reason` field. The new SKIPPED emission is additive. Existing audit consumers that key off `BLEND_DECISION.trade_blocked_reason` continue to work; new consumers can key off `SKIPPED.reason` for a unified accounting view.

## 7. Implementation plan

1. **Source change** (`tasks/blend_task.py`):
   - Add `_emit_skipped(ticker, blend_result, readiness, trade_blocked_reason, fast_lane_result)` method composing the payload per §5.
   - Call `_emit_skipped` immediately before the blocked-reason early return at line 204.
   - Forward the `trade_log` logger via the existing `BlendDecisionLogger` injection point (the BlendTask already takes `logger` in its constructor for `log_blend_decision`; reuse the same handle for `log_skipped`).
2. **Test additions** (`tests/test_blend_task.py`):
   - `test_blocked_blend_emits_skipped_record_with_g1_reason` — synthetic BlendResult with G1_blended_confidence trade_blocked_reason; assert SKIPPED record emitted with `reason="G1_blended_confidence"`.
   - Mirror tests for G2, G3, G4, G5, G6 reasons.
   - `test_blocked_blender_side_reason_emits_skipped_record` — synthetic BlendResult with a blender-side trade_blocked_reason; assert SKIPPED record carries that reason.
   - `test_unblocked_blend_does_not_emit_blendtask_skipped_record` — happy-path: BlendTask enqueues the candidate; no SKIPPED record from BlendTask (the executor still emits its own if `_validate` rejects; that's a separate codepath).
   - `test_skipped_payload_carries_blended_edge_not_fast_lane_edge` — pin the §5 payload-shape decision.
3. **Schema documentation** (`utils/logger.py` or wherever the SKIPPED schema is documented — locate during implementation):
   - Add a one-paragraph note to the SKIPPED schema docstring explaining that BlendTask-emitted SKIPPED records carry post-blend `model_probability` / `edge` while executor-emitted SKIPPED records carry the executor's own pre-trade values. The `reason` field disambiguates: G1–G6 / blender-side reasons → BlendTask; everything else (cooldown, opposing-position, capped_dollars, etc.) → executor.
4. **Bothealth aggregator validation** (`scripts/bothealth.sh`):
   - Run the aggregator against a synthetic post-fix decisions log to confirm the per-day skip-reason histogram renders correctly with the new high-volume reasons.
   - No source-change expected; just validation.
5. **Closure**:
   - Update PROFIT-OBS-003 entry: status OPEN → COMPLETE, citing the BlendTask emission + 7 new tests + bothealth validation.
   - Top-of-file counters: Open HIGH 4 → 3; Items COMPLETE += 1.
   - Cross-reference the closure from PROFIT-EDGE-004's notes (EDGE-004's audit can now quote per-gate kill counts directly from the SKIPPED stream rather than the BLEND_DECISION join).

## 8. Acceptance criteria

- `tasks/blend_task.py:204` blocked-reason path emits a SKIPPED record with `reason=trade_blocked_reason` before the early return.
- 7 new tests in `tests/test_blend_task.py` cover the per-gate emission and the unblocked-path no-emission case.
- Post-deploy 24-hour audit confirms `OPPORTUNITY = SKIPPED + PAPER_TRADE` within ±N for at-the-moment in-flight (N small, < 5).
- Distinct SKIPPED `reason` values include at least: `G1_blended_confidence` (the dominant kill per the 2026-05-03 attribution), plus the executor's pre-existing reason set.
- Full pytest suite green.

## 9. Rollback

Revert is one method addition + one call-site insertion. Trivial.

Trigger to revert: post-deploy `OPPORTUNITY = SKIPPED + PAPER_TRADE` accounting drifts negative (i.e., the new SKIPPED emission is double-firing somehow), OR `bothealth.sh` aggregator chokes on the new reason values.

## 10. Soak-window contract

This spec is pre-loaded during PROFIT-PHASE2-001 soak (drafted 2026-05-03; do not implement before 2026-05-09 organic close or 2026-05-16 hard ceiling). The change is additive-logging on a decision-path file (`tasks/blend_task.py`), which falls under the conservative reading of the `decision consistency = high-risk during soak` rule. Post-soak landing is the right cadence.

## 11. Out of scope

- Executor-side SKIPPED behaviour — unchanged. The `_validate()` rejection path keeps emitting SKIPPED for cooldown / opposing-position / capped_dollars / etc.
- `BLEND_DECISION` schema or emission — unchanged. The fix is additive.
- G1 calibration tightening — separate concern. The 2026-05-03 attribution showed G1 dominates 82% of silent exits; whether 0.05 is the right floor is a calibration question that requires post-fix data to evaluate.
- G6 single-ticker concentration on KXTRUMPIRAN — a separate diagnostic question (does that ticker's evidence stop flowing well before market close?). Track inside PROFIT-OBS-003 closure; spawn a new debt entry only if the post-fix data confirms a ticker-specific pattern.
