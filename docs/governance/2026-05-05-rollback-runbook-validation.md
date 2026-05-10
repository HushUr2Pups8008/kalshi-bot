# 2026-05-05 — `post-soak-rollback-runbook.md` dry-trace validation

**Type:** read-only review (Claude task per Implementation Contract §9 — "Reviewing implementations").
**Scope:** trace every command path / file ref / env var / line ref in `docs/governance/post-soak-rollback-runbook.md` against `main` HEAD (`9f8deef`).
**Audience:** operator at Day-7 close before Wave-1 deploy starts.
**No edits to runbook applied** — findings only. Operator decides which to fix.

## TL;DR

**1 HIGH drift finding, 0 MEDIUM, 1 LOW.** Runbook is otherwise faithful to current HEAD. Recommend fixing F1 before Wave-1 first deploy because it could cause a 5-15 min response lag during a live incident if operator follows the false env-revert path.

## Findings

### F1 (HIGH) — `G1_CONFIDENCE_THRESHOLD` env-revert row is fictional

**Runbook §3 line 68:**
> `Lever B G1 calibration | G1_CONFIDENCE_THRESHOLD (if env-driven post-landing) | original 0.05 | restores the floor`

**Reality at HEAD:**
- `tasks/trade_readiness_gate.py:69` — `G1_CONFIDENCE_THRESHOLD = 0.05` (hardcoded module constant; not env-driven).
- `tasks/trade_readiness_gate.py:70` — `G1_FAILSAFE_CONFIDENCE_THRESHOLD = 0.10` (same).
- `analysis/decision_blender.py` — no G1 constants present (the spec docstring lives there per Lever B spec §2; the actual readiness gate constants are in `tasks/`).
- Lever B spec (`2026-05-03-edge-004-lever-b-g1-calibration-design.md`) §7: "Two-line constant revert. Trivial. Operator-side fast revert: **no env-var**; revert + redeploy is the only path."

The runbook's hedging clause "(if env-driven post-landing)" hints at this, but the row's presence in §3 (env-driven fast reverts) **misleads** the operator into looking for an env-var that doesn't and won't exist (per Lever B spec §7 + §11 explicit deferral of env-var override).

**Recommended fix:**

Remove the row from §3 entirely. Add a sentence to §3's note paragraph: "Lever B G1 calibration has no env-revert by design (see Lever B spec §7); revert via §4 code revert."

OR, if Codex / operator lands an env-var override in the Lever B deploy commit (separate from the spec's current §7): land the env-var first, then leave the row.

**Severity HIGH because:** the runbook gates incident-response speed. A 5-15 min false-trail during a live regression is meaningful when the bot is bleeding paper P&L. Lever B is Wave-3 (≥ 2026-06-06), so there is time to fix this before it matters — but the longer it sits, the more likely a future operator follows the false trail under stress.

### F2 (LOW) — EXEC-002 env-var name will only exist post-deploy

**Runbook §3 line 65:**
> `EXEC-002 series-correlation guard | SERIES_CORRELATION_WINDOW_SECONDS | 0 | the guard fires never`

**Reality at HEAD:**
- `config.py` — no `SERIES_CORRELATION_WINDOW_SECONDS` env-var read at HEAD. (Worktree at `.claude/worktrees/agent-a6863af4/config.py:1043` has it; that's the unmerged dry-run state.)
- EXEC-002 spec (`2026-05-03-exec-002-series-correlation-guard-design.md`) §6 introduces the env-var as part of the deploy.

This is **correct usage** by the runbook (post-deploy operator state), but it can confuse a reader who tries to verify the env-var on pre-deploy main and finds nothing. **No action required**; the runbook's "Wave-1 / Wave-2 deploy" framing makes the post-deploy assumption explicit.

Same dynamic for `CROSS_SERIES_CORRELATION_WINDOW_SECONDS` (Wave-3 Lever C) and `MULTI_SOURCE_CORROBORATION_MIN_N` (Lever E — closed; the runbook's parenthetical "should not have landed; if it did somehow" is correct caution).

### F3 (LOW) — `MULTI_SOURCE_CORROBORATION_MIN_N` row is dead code

**Runbook §3 line 70:**
> `Lever E (CLOSED — should not have landed; if it did somehow) | MULTI_SOURCE_CORROBORATION_MIN_N | 1 | gate fires never`

Lever E is closed (per `docs/governance/edge-004-closure-path-tldr.md` v2.2). The row is defensive dead-code in the runbook. Safe to leave; consider removing at the next runbook revision pass to reduce operator noise.

**No action required.**

## Confirmed clean (faithful to HEAD)

| runbook section | HEAD reality | verdict |
|---|---|---|
| §2 emergency revert: `launchctl bootout/bootstrap` | `~/Library/LaunchAgents/com.jake.kalshi-bot.plist` exists (1.1 KB) | ✅ |
| §2 plist names: `com.jake.kalshi-bot`, `com.kalshi.governance.fast`, `com.kalshi.governance.deep` | all 3 plists exist on disk | ✅ |
| §4.1 OBS-005 revert hunk | `trading/executor.py:208,276` matches the runbook diff (current pre-deploy = `0.0`; revert from post-deploy `float("-inf")` back to `0.0`) | ✅ |
| §4.2 MATCH-001 (B') revert hunk | `analysis/market_matcher.py:628` is at the pre-deploy predicate (`_token_not_in_ticker = not any(...)`); runbook shows revert from post-deploy substring (option a) back to current state | ✅ |
| §4.3 OBS-003 revert | spec exists (`2026-05-03-obs-003-blendtask-skipped-emission-design.md` 13.3 K); revert is additive removal | ✅ |
| §4.4 EXEC-002 spec ref | spec exists (`2026-05-03-exec-002-series-correlation-guard-design.md` 13.7 K); env-var exposed per F2 | ✅ |
| §4.5 governance_monitor revert | spec exists (`docs/_archive/specs/2026-05-03-governance-monitor-fix-design.md` 8.9 K, ARCHIVED Stream G R19); two-hunk single-file revert intact | ✅ |
| §4.6 Lever A.1 revert | spec exists (`2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` 17.3 K); two-hunk single-file revert intact | ✅ |
| §6 post-revert checklist refs `bothealth.sh` | `scripts/bothealth.sh` exists (11 K) | ✅ |
| §7 full rollback refs `pre_soak_close_branch_backup.sh` | `scripts/pre_soak_close_branch_backup.sh` exists (4.5 K) | ✅ |
| §7 force-push reset path | flagged in runbook as destructive; correct caution | ✅ |
| §8 out-of-scope refs `PHASE2_RUNBOOK.md`, `replay_dossier.py` | not validated in this pass; out of scope per runbook §8 itself | n/a |

## Cross-link integrity

All explicit cross-links in the runbook (§0 trigger conditions, §3 spec refs, §4.x spec refs, §7 rollback-anchor script, §8 out-of-scope refs) point at files that exist on `main` HEAD. No dead links.

## Recommended action

1. **Apply F1 fix before Day-7 close attestation lands** (zero cost; trivial doc edit). Either remove the G1 row or replace its env-revert column with "code revert only — see §4 (Lever B)."
2. F2 + F3: defer to a future runbook revision; not blocking.
3. No other changes.

## Out of scope

- Re-validating the EXEC-002 / OBS-003 / MATCH-001 (B') revert hunks against future deployed state. The dry-run report (`2026-05-05-wave-1-deploy-dry-run-report.md`) already exercised these in an isolated worktree.
- Validating `PHASE2_RUNBOOK.md` (governance soak runbook) — separate runbook, separate validation pass.
- Operator UI / Grafana panel references — non-runbook surface.

## Cross-links

- `docs/governance/post-soak-rollback-runbook.md` — the runbook under review
- `docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md` §7 + §11 — Lever B revert path
- `docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md` §6 — EXEC-002 env-var introduction
- `tasks/trade_readiness_gate.py:69-70` — G1 constant location of record
