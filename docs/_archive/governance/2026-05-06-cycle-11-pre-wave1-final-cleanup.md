# Cycle-11 — pre-Wave-1 final cleanup audit

**Type:** read-and-fix audit. Closes cycle-10-surfaced gaps + completes pre-Wave-1 operator surface.
**Drafted:** 2026-05-06 (cycle 11).
**Companions:** cycle-10 commits `80d3e4f` + `53caa5c` + cycle-10.5 `bb77f8b`.

## TL;DR

10 Claude tasks executed. 3 produced fixes (class-name drift, README description drift, doc-index amendments); 4 produced verification reports (no fix needed); 3 produced new artifacts (attestation pre-stage, this summary, README cross-link). No blockers. Pre-Wave-1 deploy surface coherent.

## Per-task summary

### 1. Class-name drift fix upstream (FIX)

`docs/governance/wave-1-deploy-commit-order-decision.md` lines 45-50 + `docs/governance/wave-1-changelog-entry-prestaged.md` "Removed pytest.mark.xfail markers" section both referenced 5 non-existent test class names (`TestObs005CooldownSentinelDefault`, `TestMatch001TokenGuardRefinement`, `TestObs003SkippedEmission`, `TestExec002SeriesCorrelationGuard`, `TestGov003Fix`). Surfaced in cycle-10 `wave-1-commit-messages-prestaged.md` but not fixed upstream. Cycle-11 replaced with verified `_<SPEC>_XFAIL_REASON` constants + line numbers per cycle-10 verification.

Marker counts (verified 2026-05-06): OBS-005 = 5, MATCH-001 = 7, OBS-003 = 4, EXEC-002 = 3, GOV-003 = 7, Lever A.1 = 6. Total 32.

### 2. EXEC-002 env-var wiring verification (NO FIX REQUIRED)

`SERIES_CORRELATION_WINDOW_SECONDS` is NOT in `config.py` today. Spec `2026-05-03-exec-002-series-correlation-guard-design.md` §111 mandates the deploy hunk add it: "New `SERIES_CORRELATION_WINDOW_SECONDS` env var; load into `cfg.series_correlation_window_seconds`". Pre-deploy state correctly empty; rollback runbook §3 + Wave-1 commit-messages doc env-revert path is conditional on EXEC-002 deploy applying §111 alongside the BlendTask change.

Operator follow-up: at EXEC-002 deploy commit (Wave-1 commit 4), verify the patch includes the `config.py` env-var addition, not just the BlendTask change.

### 3. Pre-stage `PROFIT-PHASE2-001-early-close-attestation.md` (NEW ARTIFACT)

Populated from current soak counters (Day-4 baseline) with `<TBD>` placeholders for Day-7/8 fire-time fields. Sibling to `2026-05-07-day-7-pre-soak-confirmation.md`. §8.5.2 invocation table pre-staged for cycles 1-5 (per `PROFIT-PHASE2-001-early-close-criteria.md`); cycles 6-11 to be enumerated at fire-time per `bash scripts/check_soak_invariant.sh --json`.

### 4. Wave-1 commit-messages spec-§ refs verification (NO FIX REQUIRED)

Walked all 6 spec section references from `wave-1-commit-messages-prestaged.md`. All resolve:

| spec | referenced sections | resolves? |
|---|---|---|
| OBS-005 | §2 | ✅ "The fix" |
| MATCH-001 | §2 + §5.1 + §6 | ✅ "The fix (Option B′)" + "Implementation gotcha — `_tokenize` vs substring" + "Implementation plan" |
| OBS-003 | §2 + §5 | ✅ "The fix" + "SKIPPED payload shape" |
| EXEC-002 | §111 (env-var wiring) | ✅ in §"Implementation plan" body |
| GOV-003 | §2 (path resolution) | ✅ "The fix" |
| Lever A.1 | §2.1 + §2.2 | ✅ "Expand the official token list" + "Add defense news / breaking defense to the news branch" |

### 5. `ops/launchd/README.md` IC §15 cross-link + description drift fix (FIX)

Added a top-of-file "Maintainer rule" callout pointing at IC §15 + commit `96e2995` (byte-faithful rewrite). Catches future operators before they re-run the cycle-8 incident.

Also corrected 2 trigger descriptions that drifted from the byte-faithful templates:
- `com.jake.kalshi-bothealth`: was "every 15 minutes" (drifted from cycle-8 Codex StartInterval=900). Now "local 08:00 America/Denver daily" matching template.
- `com.jake.kalshi-soak-check`: was "local 09:07 calendar trigger" (incomplete). Now "one-shot pinned 2026-05-03 09:07 local" matching the Year/Month/Day pin.

Also documented the new `--allow-drift` install flag + pre-commit gate behavior.

### 6. Doc index audit refresh (FIX — amend Codex's index)

Codex's `2026-05-06-pre-wave1-source-of-truth-doc-index.md` already covers cycles 1-7. Cycle-11 amended with cycle-9/10/10.5/11 additions:
- IC §15 production-config-capture invariants cross-link
- `scripts/launchd_template_equivalence_audit.py` (cycle-10 IC §15 Rule 3 oracle; legacy `launchd_plist_drift_audit.sh` retained, marked legacy)
- `scripts/doc_xref_audit.py` (cycle-9/-10 markdown + prestaged-block version-drift)
- `PROFIT-PHASE2-001-early-close-attestation.md` (cycle-11 pre-stage)
- `2026-05-07-day-7-pre-soak-confirmation.md` (cycle-10 skeleton)
- `wave-1-commit-messages-prestaged.md` (cycle-10)
- `2026-05-06-pre-wave1-deploy-dry-run-rehearsal.md` (cycle-10)
- `2026-05-06-changelog-drift-check-cycle-10.md` (cycle-10/-11)
- `2026-05-06-cycle-10-blocker-resolution.md` (cycle-10.5)

### 7. `lever-b.patch` placement verification (NO FIX REQUIRED)

`docs/governance/wave-3-deploy-hunks/lever-b.patch` uses generic `@@` markers. Verified target placement: `tasks/trade_readiness_gate.py:69-70` contains exact pre-fix lines `G1_CONFIDENCE_THRESHOLD = 0.05` + `G1_FAILSAFE_CONFIDENCE_THRESHOLD = 0.10`. Patch will apply cleanly at Wave-3 deploy.

### 8. `.githooks/pre-commit` bypass review (NO FIX REQUIRED)

Hook honors git's standard `--no-verify` semantics (skips ALL pre-commit hooks). Project CLAUDE.md already documents emergency-bypass policy:

> **Bypass (emergency only):** `git commit --no-verify`. CI will still catch the drift; only use when the hook itself is broken.

Cycle-11 `ops/launchd/README.md` addition cross-references the same.

### 9. Memory hygiene check (NO FIX REQUIRED)

Cycles 9/10/10.5 produced 6+ governance docs. Per memory-hygiene principle "What NOT to save", code/conventions/architecture/file-paths/git-history are derivable from current state, NOT memory-worthy. Material cycle-10 lessons live in:
- IC §15 (production-config-capture invariants)
- Commit `96e2995` (byte-faithful rewrite)
- Cycle-9/-10 governance docs

Memory remains: 2 entries (`feedback_soak_confirmation_cadence` + `feedback_soak_acceleration_split`). Both load-bearing for soak operations; both verified current.

### 10. Cycle-9 → cycle-10.5 commit-chain drift audit (FINDING)

Walked commits `6221139` (cycle-9) → `80d3e4f` (cycle-10 Claude) → `53caa5c` (cycle-10 Codex) → `bb77f8b` (cycle-10.5). 23 files / +1342 / -21.

Findings:
- IC §15 references (cycle-10 Claude) coherent with commit `96e2995` + consolidation-decision doc.
- Codex install.sh `--allow-drift` + TTY confirmation match IC §15 Rule 5.
- Codex pre-commit gate matches IC §15 Rule 5.
- Cycle-10.5 fixtures captured via audit script's own `normalize()` — Rule 2 compliant.
- **One residual drift surfaced + fixed**: `ops/launchd/README.md` description text was authored cycle-8 against Codex's drifted templates ("every 15 minutes"). Cycle-8.5 reverted templates byte-faithfully but README description survived. Cycle-11 item 5 fixed.

Net: commit chain coherent post-cycle-11.

## Verification

- pytest: 1551 passed, 2 skipped, 111 xfailed
- ruff: clean
- doc_xref_audit: 0 dead (markdown links + prestaged-block version drift)
- launchd equivalence audit `--installed`: PASS (6/6 match)
- launchd equivalence audit `--fixtures`: PASS (6/6 match)

## Cross-links

- `docs/IMPLEMENTATION_CONTRACT.md` §15 — production-config-capture invariants (cycle-10)
- `docs/governance/2026-05-06-cycle-10-blocker-resolution.md` — cycle-10 blocker disposition
- `docs/governance/wave-1-commit-messages-prestaged.md` — cycle-10 deploy-day operator artifact (cycle-11 upstream class-name fix)
- `docs/governance/wave-1-deploy-commit-order-decision.md` — cycle-11 class-name fix
- `docs/governance/wave-1-changelog-entry-prestaged.md` — cycle-11 class-name fix
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` — cycle-11 pre-stage
- `docs/governance/2026-05-06-pre-wave1-source-of-truth-doc-index.md` — cycle-11 amendments
- `ops/launchd/README.md` — cycle-11 description fix + IC §15 cross-link
