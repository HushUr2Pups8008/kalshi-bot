# Claude Commit Adversarial Review

Scope:

- `7774cd5` — mid-soak Phase 2 health report.
- `85875fd` — PROFIT-OBS-003 strict-xfail SKIPPED-emission harness.
- `77b3838` — post-soak landing-order design.

## Executive Read

The commits are useful and mostly safe during the soak. The highest-risk gap is the OBS-003 xfail harness: it tests the target behavior, but it does not yet pin the async logger call path (`write_trade_log_async(logger.log_skipped, ...)`) or the exact logger schema contract. The landing-order note also still treats MATCH-001 B′ as a 600-1,300 flip based on the spec estimate; the archive counterfactual in `docs/governance/2026-05-03-post-soak-landing-simulation.md` now estimates 1,076 suppressed match keys and a much larger OPPORTUNITY drop (260 → 87) than the sequence note implies.

## Findings

### F1: OBS-003 xfail harness does not prove the injected logger path

Severity: medium.

`SpyLogger` now has `log_skipped`, and the tests assert `logger.skipped_records`. That catches a clean implementation that calls the injected logger. It does not catch an implementation that bypasses the injected logger and calls module-level `trade_log.log_skipped` directly; such a patch would be harder to test and could still mutate production logs in tests if patching is wrong.

Landing-time addition:

- Patch or spy `tasks.blend_task.write_trade_log_async` and assert its first positional argument is `logger.log_skipped`.
- Keep the existing `logger.skipped_records` checks, but make them evidence of the async write target rather than only a fake logger side effect.

### F2: OBS-003 xfail reason names drift from the real readiness enum

Severity: low-medium.

The parametrized xfail list includes `G4_recency_floor` and `G5_structural_tier`, while the real readiness enum in `tasks/trade_readiness_gate.py` is `G4_regime_confidence` and `G5_dossier_drift_suspect`. The tests still serve as generic blocked-reason coverage because `trade_blocked_reason` is a string, but they fail to pin two load-bearing real enum names.

Landing-time correction:

- Replace `G4_recency_floor` with `G4_regime_confidence`.
- Replace `G5_structural_tier` with `G5_dossier_drift_suspect`.
- Keep one explicit blender-side arbitrary string case.

### F3: OBS-003 payload pin lacks required-key completeness

Severity: medium.

The payload-shape test pins blended `model_probability` / `edge`, but the review did not find an assertion that the SKIPPED payload has the full executor-compatible key set. OBS-003's central value is unifying downstream trade-log consumers; key completeness matters as much as the post-blend values.

Landing-time addition:

- Assert at least this key set: `reason`, `ticker`, `headline`, `source`, `method`, `llm_direction`, `llm_magnitude`, `model_probability`, `market_price`, `edge`, `min_edge_threshold`, `signal_meta`.
- Add one assertion that `headline` follows the executor's truncation convention if the implementation truncates.

### F4: Mid-soak report is correct to distrust `governance_monitor.py`, but not a reproducible tool

Severity: low.

The report includes a reproduction snippet, but it is embedded in markdown and uses `python3.14`. That is fine for human review, but it does not give the operator a committed, tested replacement command during the remainder of soak.

Follow-up:

- Claude's separate `governance_monitor.py` fix design should include a small xfail harness that reproduces both bugs: env path fallback and target extraction from actual `GOVERNANCE_DECISION` rows.
- Until then, daily checks should use the exact snippet from the report, not `scripts/governance_monitor.py`.

### F5: Landing-order spec underestimates MATCH-001 B′ blast radius

Severity: high for post-soak sequencing.

The sequence note says MATCH-001's archive replay impact is "~600-1,300 records flip survived → suppressed" and expects OPPORTUNITY rate to stay within the 13-day band. The new counterfactual simulation estimates 1,076 suppressed match keys but OPPORTUNITY count falls from 260 to 87 if every downstream record attached to a B′-suppressed key is removed. That may be an overestimate because the simulation is key-based and archive-limited, but it is a red flag: MATCH-001 B′ may be more behaviorally material than the landing order assumes.

Landing-order edit:

- Before placing MATCH-001 second, require a pre-deploy replay that reports historical OPPORTUNITY retention and manually samples dropped OPPORTUNITY rows.
- Consider landing OBS-003 before MATCH-001 if the priority is attribution completeness before a matcher-volume change.

### F6: Landing-order spec's OBS-005 rollback criteria conflict with the earlier adversarial review

Severity: low-medium.

The sequence note's OBS-005 restart trigger mentions "cooldown-trip storm", which catches the old bug shape. It still does not include the likely bad-fix shape: repeated same-ticker trades inside the cooldown window because the finite `_last_traded` branch was broken.

Add:

- Rollback if any ticker emits two PAPER_TRADE records inside `paper_ticker_cooldown` without explicit override.

## Recommended Actions

1. Amend OBS-003 xfail harness before implementation to pin real G4/G5 reason names, async logger target, and full payload key set.
2. Amend landing-order spec with the post-soak simulation result: MATCH-001 B′ may reduce archive OPPORTUNITY from 260 to 87 under a strict key-based counterfactual.
3. Keep the mid-soak report as the source of truth until `governance_monitor.py` has an xfail reproduction and fix.
