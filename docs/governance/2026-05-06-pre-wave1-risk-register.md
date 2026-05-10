# Pre-Wave-1 risk register

**Type:** consolidated risk inventory (Claude task per Implementation Contract §9 — review).
**Drafted:** 2026-05-06.
**Audience:** operator pre-Day-7-close + pre-Wave-1-deploy.
**Source:** synthesizes risks identified across cycles 1-7 docs + cycle-2/3 LOCK addenda + failure-mode runbooks.

## TL;DR

**11 risks identified across deploy + soak + post-deploy surfaces.** All have documented mitigations; 0 require pre-Wave-1 operator action beyond what's already planned. **3 HIGH-impact / LOW-likelihood (require playbook readiness; covered).** **1 LOW-impact / MED-likelihood (gate-7 surface drift; closed via cycle-5 invocation table refresh).**

## Risk matrix

### HIGH impact / LOW likelihood (operator-playbook required)

#### R1: KILL_SWITCH fires during Day-7 close window or Wave-1 deploy

| dimension | value |
|---|---|
| Likelihood | LOW (0 in 4d 5h soak) |
| Impact | HIGH (close aborts; deploys halt) |
| Detection | `logs/governance/decisions.jsonl` `KILL_SWITCH` event |
| Mitigation | `2026-05-05-kill-switch-fire-procedure-runbook.md` — STOP bot first; REVERT vs QUARANTINE decision matrix |
| Residual | playbook well-rehearsed; ~15-30 min wall-clock to bot-stable or quarantined |

#### R2: Mac Studio reboots / dies mid-deploy

| dimension | value |
|---|---|
| Likelihood | LOW (Mac Studio uptime: stable; KeepAlive=SuccessfulExit:false in plist) |
| Impact | HIGH (deploy state mid-flight; possible partial commit; ambiguous resume) |
| Detection | `launchctl list | grep com.jake.kalshi-bot` shows PID=0 + non-zero exit OR fully unloaded |
| Mitigation | `2026-05-05-mac-studio-dead-bot-reboot-runbook.md` — 4-category triage |
| Residual | playbook covers all 4 categories; ~10-20 min wall-clock |

#### R3: Network/Kalshi-API/Ollama outage during Day-7 close

| dimension | value |
|---|---|
| Likelihood | LOW (no current signs) |
| Impact | HIGH (close defers; cadence gate may fail if outage > 3h) |
| Detection | `tail logs/app/bot.log` shows network errors; `bothealth.sh` flags |
| Mitigation | `2026-05-05-network-api-outage-runbook.md` — 5-category triage; Day-7 special case in §4.1 |
| Residual | If outage during close: defer fire-time per playbook |

### MED impact / LOW-MED likelihood

#### R4: Wave-1 commit-1 (OBS-005) sentinel-leak post-deploy

| dimension | value |
|---|---|
| Likelihood | LOW (Codex archive replay confirmed); MED if test coverage gap |
| Impact | MED (silent observability data corruption; trade-rate unaffected) |
| Detection | First OPPORTUNITY post-deploy: `cooldown_state == "0.0"` for never-traded ticker |
| Mitigation | `post-soak-rollback-runbook.md` §4.1 code revert; `tests/test_wave1_commit1_obs005_post_deploy.py` strict-xfail sentinel (cycle 7) |
| Residual | sentinel test fails fast on regression; rollback is single-commit revert |

#### R5: Wave-1 commit-2 (MATCH-001 B') over-/under-suppression

| dimension | value |
|---|---|
| Likelihood | LOW (Codex archive replay; B' orthogonality finding) |
| Impact | MED (suppression-rate drift > ±30%) |
| Detection | `MATCH_DIAGNOSTIC` records over 24h |
| Mitigation | `post-soak-rollback-runbook.md` §4.2; `tests/test_wave1_commit2_match001_post_deploy.py` strict-xfail sentinel |
| Residual | observability-only impact pre-Wave-2 (no PAPER_TRADE upstream) |

#### R6: Wave-1 commit-3 (OBS-003) SKIPPED reason missing

| dimension | value |
|---|---|
| Likelihood | LOW |
| Impact | MED (gate-attribution coverage gap) |
| Detection | `SKIPPED` records with `reason=null` or empty |
| Mitigation | `post-soak-rollback-runbook.md` §4.3; `tests/test_wave1_commit3_obs003_post_deploy.py` strict-xfail sentinel; baseline interpretation rebases rule from "≥ 1 SKIPPED in 24h" to "≥ 1 BLEND_DECISION in 7d" |
| Residual | passive 0-OPPORTUNITY-baseline means SKIPPED requires BLEND_DECISION which requires OPPORTUNITY which is currently 0 |

#### R7: Wave-1 commit-4 (EXEC-002) env-var revert path lost

| dimension | value |
|---|---|
| Likelihood | LOW |
| Impact | MED (rollback path regresses from env-revert to code-revert) |
| Detection | `cfg.series_correlation_window_seconds` doesn't read from env |
| Mitigation | env-revert via `launchctl setenv SERIES_CORRELATION_WINDOW_SECONDS 0`; `tests/test_wave1_commit4_exec002_post_deploy.py` strict-xfail sentinel |
| Residual | low because cfg.field is well-tested + cycle-3 lock attestation |

#### R8: Wave-1 commit-6 (Lever A.1) trade-rate explosion (≥ 5×)

| dimension | value |
|---|---|
| Likelihood | LOW (Codex archive replay confirmed neutral) |
| Impact | MED (overtrading cascade per INV-6) |
| Detection | OPPORTUNITY count over 24h > 5× pre-deploy baseline (currently 0; any post-deploy count is a feature, not bug per `2026-05-05-pre-wave1-baseline-interpretation.md`) |
| Mitigation | `post-soak-rollback-runbook.md` §4.6; `tests/test_wave1_commit6_levera1_post_deploy.py` strict-xfail sentinel |
| Residual | post-cutover baseline is 0; rule rebased to absolute thresholds |

### LOW impact / various likelihoods

#### R9: Doc cross-reference drift (cosmetic)

| dimension | value |
|---|---|
| Likelihood | MED (cycle-7 audit found 2 broken refs in wave-1-changelog-entry-prestaged.md) |
| Impact | LOW (cosmetic CHANGELOG entry; not blocking) |
| Detection | `scripts/doc_xref_audit.py` (Codex cycle 7) |
| Mitigation | cycle-8 fix applied (this cycle); future drift caught by audit script |
| Residual | minimal; audit run pre-Wave-1-deploy as hygiene |

#### R10: launchd plist drift (operator-managed plists)

| dimension | value |
|---|---|
| Likelihood | LOW (currently no drift per cycle-7 audit) |
| Impact | LOW (3 of 6 plists lack repo source-of-truth; recovery requires Time Machine OR re-derivation) |
| Detection | `scripts/launchd_plist_drift_audit.sh` |
| Mitigation | cycle-7 consolidation decision doc; cycle-8 Codex tasks 2-5 capture remaining 3 plists into `ops/launchd/` |
| Residual | minimal post-cycle-8; pre-Wave-2 recommendation |

#### R11: §8.5.2 gate-7 surfaces NEW commit pre-fire-time

| dimension | value |
|---|---|
| Likelihood | LOW (no expected commits between now and 2026-05-08T19:01Z; only doc/script work) |
| Impact | LOW (carve-out invocation requires fresh §8.5.2 evidence-coverage analysis OR fall to default 14d) |
| Detection | `bash scripts/check_soak_invariant.sh --json` at fire-time |
| Mitigation | criteria runbook §8.5.2 invocation table updated cycle 5 to cover doc/script artifacts as OUT-OF-SCOPE-for-§8.5.2 |
| Residual | minimal; new doc/script commits auto-qualify as OUT-OF-SCOPE per the table's note |

## Risks NOT in this register (out of pre-Wave-1 scope)

- Wave-2 Branch C feed RSS-probe failures (deploy-time risk; covered in `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` §"Tie-breakers")
- Wave-3 Lever B P&L negative outcome (post-deploy 14d window risk; covered in Lever B parent §6 acceptance)
- Branch D PROFIT-LLM-001 sizing inadequate (post-Wave-2-stall risk; covered in `2026-05-05-profit-llm-001-pre-sizing-scope-design.md` §3)
- DEFERRED-CEILING outcome (terminal risk; covered in TLDR v3 honest-read)
- Calibration drift detection wiring failure (post-Wave-1 observability risk; Codex `calibration_drift_wiring_audit.py` covers)

## Operator pre-Wave-1 action checklist

Per this risk register:

- [ ] Read `2026-05-05-kill-switch-fire-procedure-runbook.md` once (R1)
- [ ] Read `2026-05-05-mac-studio-dead-bot-reboot-runbook.md` once (R2)
- [ ] Read `2026-05-05-network-api-outage-runbook.md` once (R3)
- [ ] Confirm `2026-05-05-day-7-fire-time-compact-checklist.md` is at-hand at fire-time (procedural readiness)
- [ ] Confirm `2026-05-05-wave-1-fire-time-per-commit-checklist.md` is at-hand at each commit (procedural readiness)
- [ ] No pre-Wave-1 code changes, ever, until Day-7 close attestation pushes (R11 mitigation)

## Cross-links

- `docs/_archive/governance/2026-05-06-pre-day7-dry-run-rehearsal.md` — gate-state evidence (ARCHIVED Stream G R14)
- `docs/_archive/governance/2026-05-06-soak-runtime-characterization-summary.md` — soak-state evidence (ARCHIVED Stream G R11)
- `docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md` (R1)
- `docs/governance/2026-05-05-mac-studio-dead-bot-reboot-runbook.md` (R2)
- `docs/governance/2026-05-05-network-api-outage-runbook.md` (R3)
- `docs/governance/post-soak-rollback-runbook.md` (R4-R8 mitigation)
- `docs/governance/2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md` (per-commit risk surface)
- `docs/governance/2026-05-05-pre-wave1-baseline-interpretation.md` (R8 0-baseline rebase)
- `docs/governance/2026-05-05-launchd-plist-consolidation-decision.md` (R10 mitigation plan)
