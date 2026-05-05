# Post-soak-close rehearsal checklist (operator playbook for 2026-05-15 close → Wave-1 deploys)

**Status:** procedural (operator-facing). Pre-staged during the active `PROFIT-PHASE2-001` soak so the day the soak closes the operator works from a single document, not a multi-spec tour.
**Audience:** the operator on the day of and immediately after the 2026-05-15 organic close (or 2026-05-16 hard ceiling) of `PROFIT-PHASE2-001`.
**Drafted:** 2026-05-03
**Companions:**
- `docs/superpowers/specs/2026-05-03-post-soak-landing-order-design.md` (sequencing spec; the *what* of each step)
- `docs/governance/post-soak-rollback-runbook.md` (incident-response only)

This document is the *day-by-day operator script* — actions, validation checks, smoke-tests for the 13-day Wave-1 deploy window. Use it linearly; do not skip steps without explicit go-arounds.

## 0. T-minus-1 day (the day BEFORE soak close)

The day before the planned close, run pre-deploy hygiene:

- [ ] Confirm `PROFIT-PHASE2-001` close criteria are actually met. Re-run the day-N mid-soak report; verify §8.5 acceptance criteria status.
- [ ] Re-run the latest mid-soak snapshot script. Confirm trailing-window PARSE_ERROR rate is 0 %, no new safety events.
- [ ] Run `scripts/bothealth.sh` and confirm no current operational red flags.
- [ ] Snapshot the pre-Wave-1 SHA and write it down somewhere durable (e.g., a sticky note OR a debt-log entry). This is the rollback anchor for §7 of the post-soak rollback runbook.
- [ ] Verify all 4 base-stack specs still resolve to the latest current-state version (per the post-soak landing-order spec §0). If any was updated between draft and close, re-read its acceptance + restart-trigger sections.
- [ ] Re-run `pytest -q` at HEAD. Document the strict-xfail count. After Wave-1 deploys, this number drops monotonically.

## 1. Day 0 — soak closes; OBS-005 deploys

OBS-005 is a 1-line sentinel patch — the smallest possible Wave-1 deploy. Use it to smoke-test the deploy pipeline post-soak.

### 1.1 Pre-deploy check

- [ ] Pull latest `main`; verify HEAD is at the pre-Wave-1 SHA recorded yesterday.
- [ ] Re-run `pytest tests/test_executor.py::TestCooldownSentinelOBS005 -q`. Confirm 5 strict-xfail tests fail today (xfailed). Two of them (`test_paper_never_traded_runtime_behavior_under_small_monotonic` + the source-inspection pins) document the bug shape.
- [ ] Inspect `tests/conftest.py:_ci_stub_env`. Confirm `PAPER_TICKER_COOLDOWN=0` / `LIVE_TICKER_COOLDOWN=0` stubs are still in place — these get removed in the OBS-005 deploy commit.

### 1.2 Deploy

- [ ] Apply the OBS-005 source change per `2026-05-03-obs-005-cooldown-sentinel-fix-design.md` §2:
  - `trading/executor.py:208` (paper) sentinel `0.0 → float("-inf")`.
  - `trading/executor.py:276` (live) sentinel `0.0 → float("-inf")`.
- [ ] Remove the 5 `pytest.mark.xfail` markers from `TestCooldownSentinelOBS005` in the same commit. CI must verify the tests xpass cleanly.
- [ ] Remove the `PAPER_TICKER_COOLDOWN=0` / `LIVE_TICKER_COOLDOWN=0` stubs from `tests/conftest.py:_ci_stub_env` in the same commit (per spec §7 acceptance #3).
- [ ] Bump `VERSION` (patch bump). Update `CHANGELOG.md`. Tag the commit `vX.Y.Z`.
- [ ] Push to origin; confirm CI passes.
- [ ] Restart the bot per the OBS-005 deploy runbook in the spec.

### 1.3 24 h smoke window (Day 0 → Day 1)

Watch for restart-trigger conditions per spec §1 step 1 of the landing-order spec:

- [ ] No new exception in `bot.log`.
- [ ] No `OPPORTUNITY` count drop vs the pre-deploy baseline.
- [ ] No `SKIPPED` reason regression.
- [ ] No cooldown-trip storm (every market trips within first cycle post-restart — would mean the sentinel direction is inverted).

If any restart-trigger fires, halt Wave-1 and follow the post-soak rollback runbook §4.1.

## 2. Day 1 — MATCH-001 (B') deploys

After OBS-005's 24 h smoke clears.

### 2.1 Pre-deploy check

- [ ] Re-run Codex's MATCH-001 anchor sizing audit (`scripts/simulations/match001_bprime_anchor_sizing.py`) against the live archive. Confirm:
  - 0 / 5 canonical headlines suppressed.
  - Suppressed-key count in the 600–1,300 range expected by spec §1 (Codex's 2026-05-03 measurement was 1,076).
- [ ] Read the MATCH-001 spec §5.1 implementation gotcha addendum. Confirm option (a) substring containment is the chosen path. (Option (b) hyphen-splitting tokenizer per `2026-05-03-match-001-tokenization-option-b-design.md` is the alternative if option (a) is rejected.)

### 2.2 Deploy (option (a) recommended)

- [ ] Apply the MATCH-001 source change per spec §2 + §5.1 option (a):
  ```python
  ticker_lower = market.ticker.lower()
  _has_supporting_non_ticker_token = any(token not in ticker_lower for token in overlap)
  _meets_suppression_criteria = (
      bool(heuristic_flags)
      and not _has_supporting_non_ticker_token
      and (_near_threshold_weak or _pure_single_entity)
  )
  ```
- [ ] Remove the 2 `pytest.mark.xfail` markers from `TestSuppressionTokenGuardMATCH001` in the same commit.
- [ ] Update existing `TestLowQualityMatchSuppression` cases that relied on pre-fix predicate behaviour (per spec §6 step 2).
- [ ] Bump `VERSION`. Update `CHANGELOG.md`. Tag.
- [ ] Push; verify CI passes.

### 2.3 72 h validation window (Day 1 → Day 4)

Per landing-order spec Step 2:

- [ ] Post-deploy 7-day OPPORTUNITY count lands in [20 %, 50 %] of pre-deploy 13-day baseline (target: ~30 % retention; allow ±20 percentage points).
- [ ] No canonical-event headline appears in `MATCH_SUPPRESSED` for its canonical ticker (headline-level guard, not ticker-level — bare canonical tickers WILL appear in `MATCH_SUPPRESSED` for non-canonical headlines and that is correct behaviour).
- [ ] `MATCH_SUPPRESSED` count rises by ≥ 80 % of the simulation-estimated 1,076 keys (prorated for the 72 h elapsed time).

Restart trigger if OPP retention < 15 % of baseline OR any canonical-event headline appears in `MATCH_SUPPRESSED`.

## 3. Day 4 — OBS-003 deploys

After MATCH-001's 72 h validation closes.

### 3.1 Pre-deploy check

- [ ] Pre-deploy synthesizer fixture: `python scripts/simulations/obs003_skipped_stream_synthesis.py --emit /tmp/obs003_fixture.jsonl`.
- [ ] If `bothealth.sh` has been updated to support `SKIPPED_LOG_OVERRIDE`, run it against the synthetic fixture and verify the histogram matches the canonical reference output.
- [ ] Re-read OBS-003 spec §5 + §7 step 4 for the bothealth aggregator update requirement.

### 3.2 Deploy

- [ ] Apply the OBS-003 source change per spec §2 + §5:
  - Add `_emit_skipped(...)` method to `tasks/blend_task.py`.
  - Insert call before the blocked-reason early return.
  - Forward via `BlendDecisionLogger` injection point (the existing `logger` constructor arg).
- [ ] Update `bothealth.sh` to surface the Skipped trades section + accept `SKIPPED_LOG_OVERRIDE` env (per the F2 follow-up).
- [ ] Remove the 11 `pytest.mark.xfail` markers from the OBS-003 harness in the same commit.
- [ ] Bump `VERSION`. Update `CHANGELOG.md`. Tag.
- [ ] Push; verify CI passes.

### 3.3 48 h validation window (Day 4 → Day 6)

Per landing-order spec Step 3:

- [ ] `OPPORTUNITY = SKIPPED + PAPER_TRADE` accounting within ±3 % in-flight.
- [ ] SKIPPED stream contains G1 / G2 / G3 / G4 / G5 / G6 + blender-side reasons in addition to executor reasons.
- [ ] `bothealth.sh` aggregator renders correctly with the new high-volume reasons.
- [ ] `G1_blended_confidence` is the dominant reason (matches Codex's 59/78 simulation distribution).

Restart trigger if OPP > SKIPPED + PAPER_TRADE OR `bothealth.sh` chokes on a new reason value OR `BLEND_DECISION` count regresses.

## 4. Day 6 — EXEC-002 deploys

After OBS-003's 48 h validation closes.

### 4.1 Pre-deploy check

- [ ] Re-run the FISA replay test locally. Confirm 1 of 3 same-series candidates enqueues.
- [ ] Confirm cross-series + window-expiry + window-override tests all pass.
- [ ] Confirm `cfg.series_correlation_window_seconds=0` env override disables the guard cleanly.

### 4.2 Deploy

- [ ] Apply EXEC-002 source change per spec §2 + §5:
  - New `_recent_series_enqueues` instance attribute on `BlendTask.__init__`.
  - New `_series_prefix(ticker)` helper.
  - Series-prefix check inserted between readiness eval and `_emit_blend_decision`.
  - `cfg.series_correlation_window_seconds` config knob in `config.py`.
- [ ] Remove the 10 `pytest.mark.xfail` markers from the EXEC-002 harness in the same commit.
- [ ] Bump `VERSION`. Update `CHANGELOG.md`. Tag.
- [ ] Push; verify CI passes.

### 4.3 7 d validation window (Day 6 → Day 13)

Per landing-order spec Step 4 (longer because trade-rate impact is the primary signal and trade-rate is sparse):

- [ ] 24 h post-deploy SQL audit returns zero rows from the same-series-burst query.
- [ ] 7 d realized-P&L comparison vs the 13-day MacBook baseline is **flat or improved** (the simulation says the suppressed 2 FISA trades would have been losses).
- [ ] `series_correlation_in_window` SKIPPED count is non-zero only on cycles where a same-series candidate fires within the window.

Restart trigger if trade volume drops to zero across 7 d OR realized P&L worse than baseline OR `series_correlation_in_window` fires on tickers sharing no actual series prefix.

## 5. Day 13 — Wave-1 base stack closed; governance_monitor fix lands alongside

**Commit-order decision (LOCKED 2026-05-04):** per `docs/governance/wave-1-deploy-commit-order-decision.md`, Wave-1 lands as **6 per-feature commits**, NOT a single bundle. Bisect-friendly rollback granularity. See that doc for the recommended commit sequence + xfail-marker removal mapping.


The governance_monitor fix is independent of the Wave-1 base stack but lands the same day as OBS-005 (Day 0) per the landing-order spec. Document its closure here for completeness:

- [ ] If governance_monitor fix landed Day 0: confirm `python scripts/governance_monitor.py` against the live `decisions.jsonl` produces the correct 19-distinct-targets histogram + per-day error breakdown.
- [ ] If not yet landed: deploy now per spec §2 (KALSHI_HOME path fix + type-set membership fix). Remove the 5 strict-xfail markers in the same commit.

## 6. Day 13 — Wave-2 first item: Lever A.1 classifier patch

After Wave-1 base stack stabilises. **Lands as prerequisite hygiene only — do not expect standalone lift.** Per the Lever A.1 spec §0 (post-Codex archive replay).

### 6.1 Pre-deploy check

- [ ] Re-run `pytest tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1 -q`. Confirm 6 strict-xfail + 1 positive control.
- [ ] Re-run `pytest tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1PlusAnalysisBranch -q`. Confirm 6 strict-xfail + 1 positive control (analysis-class branch added in commit `87c3f15`; lands in same hunk as A.1+1 deploy, not A.1).
- [ ] Re-run `python scripts/simulations/lever_a1_classifier_counterfactual.py`. Confirm the canonical-source distribution lift is `official +4, news +2, other -6`.

### 6.2 Deploy

- [ ] Apply the A.1 classifier patch per `2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` §2.1 + §2.2.
- [ ] Remove the 6 `pytest.mark.xfail` markers from `TestSourceClassClassifierLeverA1` in the same commit.
- [ ] Bump `VERSION`. Update `CHANGELOG.md`. Tag.

### 6.3 Validation

- [ ] No archive-data lift expected per Codex's 2026-05-03 archive replay.
- [ ] Per-source classifications match the reference classifier in `scripts/simulations/lever_a1_classifier_counterfactual.py:classify_post_fix`.

A.1 lands silently; the real edge-production work is A.1+ feed onboarding (next).

## 7. Day 14+ — Wave-2 Lever A.1+ first feed

After A.1's verification clears (a few hours; A.1 is silent in production data).

**Decision point — REVISED post-aggregator-path forensics** (`docs/governance/2026-05-04-vitallaw-aggregator-path-forensics.md`): VitalLaw came via Google News RSS, NOT direct feed. Google News query family is **already active** in canonical config (`config.py:DISABLED_SOURCE_FAMILIES` — re-enabled 2026-04-23). Therefore Day-14 is a 4-branch tree:

1. **Branch A (DEFAULT, passive — no code change):** observe Wave-1 close for 14 d. Watch `trades.jsonl` for VitalLaw / legal-niche source-string surfacing. If ≥ 1 PAPER_TRADE materialises through the Google News query family, EDGE-004 closure path is intact via Branch A and no A.1+1.5 deploy required.
2. ~~**Branch B (active deploy if A fails):** probe `vitallaw.com` for direct RSS endpoint~~ — **DROPPED 2026-05-05** (Codex direct-RSS probe `2026-05-05-vitallaw-direct-rss-probe.md` confirmed no public feed endpoint).
3. **Branch C (fallback if A fails):** onboard open-RSS analogues per A.1+1.5 spec §2 (Lawfare / Just Security / SCOTUSblog / Politico Legal).
4. **option-A parallel:** specialist-geopolitics per A.1+ spec §3.1 (war on the rocks / CSIS / ISW / CFR / Atlantic Council). Operator may run option-A in parallel with C for breadth.

### 7.1 Pre-deploy check (both options)

- [ ] Pre-deploy verify each candidate feed URL with `curl -I`. Document live URL + feed `<title>` in deploy commit.
- [ ] Re-run `pytest tests/test_lever_a1plus_feed_config.py -q`. Confirm 1 passes (existing-domains positive control) + 2 strict-xfail (option-A specialist-analyst URL, option-B vital_law-niche URL). Whichever option deploys, the corresponding xfail flips xpass; remove that marker in the deploy commit. The OTHER xfail stays in place (it pins the option not yet deployed).
- [ ] Run the 24 h ingestion / match-score / latency / source-class verification per the A.1+ spec §4.
- [ ] If all 4 acceptance thresholds pass, deploy the feed. If any fail, kill the candidate and run §4 against the next candidate.
- [ ] Post-deploy 14 d validation: target is conversion ≥ 5 % over 14 d (same as Lever A umbrella spec §4 closure criterion).

## 8. Day 28+ — Wave-3 escalation if Lever A.1+ stalls

If A.1+1 (first feed) stalls at < 5 %:

- Spec the Lever A.1+2 candidate (specialist analyst feed). Repeat §7.

If A.1+1 + A.1+2 both stall at < 5 % over 14 d each:

- Wave-3 begins. Lever B (G1 calibration) at `min_n=0.04`. Lands ≥ 14 d after OBS-003 to ensure the attribution dataset is mature.
- After Lever B stalls or completes: Lever C (cross-series headline correlation, EXEC-002 Approach 2).
- After Lever C: escalate to PROFIT-LLM-001 or P4-GATE Appendix A. Both are out of EDGE-004 scope; tracked separately.

## 9. Per-day operator routine (during validation windows)

Every day during a validation window:

- Morning: read `bothealth_${DATE}.md` daily report. Triage any RED indicator.
- Run the daily-monitoring script per `docs/governance/PHASE2_RUNBOOK.md` §daily section.
- Spot-check `logs/trades/live/trades.jsonl` for the latest 10 OPPORTUNITY / BLEND_DECISION / PAPER_TRADE events.
- If on a validation window: re-check the spec's acceptance criteria against current data. If any criterion has flipped FAIL, halt the window and review.

## 10. When to abandon Wave-N and reset

Per the post-soak rollback runbook §7, full rollback to pre-Wave-1 SHA is the prescribed escalation if:

- Two or more Wave-1 items fail validation back-to-back.
- A Wave-1 revert produces unexpected secondary failures.
- Any safety event (KILL_SWITCH / batch_aborted) fires more than once in a single validation window.
- Operator is uncertain which deploy caused the regression and trade-log evidence is ambiguous.

Force-push reset to `main` is destructive — confirm explicit operator authorization before running.

## 11. Out of scope

- **Mid-soak rebooting.** The soak is in continuous shadow mode; no operator action mid-soak unless a PARSE_ERROR / VALIDATION_ERROR / KILL_SWITCH / batch_aborted event fires. See `docs/governance/PHASE2_RUNBOOK.md` §monitoring for those criteria.
- **GOV.P3 real-mode flip.** Different shape (governance soak / runbook). See `docs/governance/PHASE2_RUNBOOK.md` §next steps.
- **Codex-side empirics during deploys.** Codex's adversarial reviews + sizing audits happen in parallel with operator deploys; this checklist documents the operator's path only.
- **Wave-3+ details beyond the §8 escalation note.** Each Wave-3 lever has its own spec; this checklist is the Wave-1 + Wave-2 first-feed cycle.
- **`PROFIT-LLM-001` / P4-GATE.** Out of EDGE-004 scope; separate tracking.
