# Pre-Wave-1 source-of-truth doc index

**Type:** operator quick-reference (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-06.
**Audience:** operator at Day-7 close + Wave-1 deploy fire-time. **One-page lookup of "where is X?"** to reduce fire-time doc-search latency.

## TL;DR

100+ governance + spec docs across cycles 1-7. This doc maps "I need to know X" to the canonical source-of-truth file. Sorted by fire-time relevance.

## Day-7 close (fire 2026-05-08T19:01Z+)

| operator question | source-of-truth file |
|---|---|
| What are the §8.5.1 close gates? | `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` |
| What's the 11-step close playbook? | `docs/governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` |
| Compact 30-45min checklist? | `docs/governance/2026-05-05-day-7-fire-time-compact-checklist.md` |
| Pre-staged attestation values? | `docs/governance/2026-05-05-day-7-attestation-prestage.md` |
| Attestation template? | `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` |
| Pre-stage attestation file (cycle-11) | `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` |
| Day-7 mid-soak confirmation skeleton (cycle-10) | `docs/governance/2026-05-07-day-7-pre-soak-confirmation.md` |
| Decision flowchart? | `docs/governance/PROFIT-PHASE2-001-close-day-decision-flow.md` |
| Pre-fire dry-run report? | `docs/governance/2026-05-06-pre-day7-dry-run-rehearsal.md` |
| §8.5.2 carve-out invocation table? | `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` (§"§8.5.2 policy-equivalence carve-outs invoked") |
| Day-7 → Wave-1-commit-1 hand-off? | `docs/governance/2026-05-05-day-7-to-wave-1-handoff-procedure.md` |

## Wave-1 deploy (≥ 2026-05-08T20:00Z+)

| operator question | source-of-truth file |
|---|---|
| Locked commit order? | `docs/governance/wave-1-deploy-commit-order-decision.md` |
| Per-commit acceptance + hand-off? | `docs/governance/2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md` |
| Compact 30min per-commit playbook? | `docs/governance/2026-05-05-wave-1-fire-time-per-commit-checklist.md` |
| Cadence + UTC windows? | `docs/governance/2026-05-05-wave-1-deploy-day-timing.md` |
| 14-row regression watch? | `docs/governance/wave-1-post-deploy-observation-plan.md` |
| Pre-staged CHANGELOG block? | `docs/governance/wave-1-changelog-entry-prestaged.md` |
| Pre-staged per-commit messages (cycle-10) | `docs/governance/wave-1-commit-messages-prestaged.md` |
| Pre-Wave-1 deploy dry-run rehearsal (cycle-10) | `docs/_archive/governance/2026-05-06-pre-wave1-deploy-dry-run-rehearsal.md` (ARCHIVED Stream G R5) |
| CHANGELOG drift-check refresh (cycle-10/-11) | `docs/_archive/governance/2026-05-06-changelog-drift-check-cycle-10.md` (ARCHIVED Stream G R5) |
| Cycle-10 blocker resolution | `docs/_archive/governance/2026-05-06-cycle-10-blocker-resolution.md` (ARCHIVED Stream G R5) |
| Smoke wrapper? | `scripts/wave1_post_deploy_smoke.sh` (cycle 2) |
| Per-commit smoke? | `scripts/wave1_fire_time_smoke.sh` (cycle 4) |
| Pre-deploy state baseline (4 reports)? | `docs/governance/2026-05-05-pre-wave1-{skipped-rate,opportunity-age,cooldown,trade-rate}-*.md` |
| Baseline interpretation? | `docs/governance/2026-05-05-pre-wave1-baseline-interpretation.md` |
| Per-commit acceptance audit? | `scripts/wave1_commit_chain_acceptance_audit.sh` (cycle 7) |
| Per-commit strict-xfail sentinels? | `tests/test_wave1_commit{1-6}_*_post_deploy.py` (cycle 7) |

## Wave-2 deploy (≥ 2026-05-18+ Branch A start; ≥ 2026-06-02 Branch C deploy)

| operator question | source-of-truth file |
|---|---|
| Branch A → C → option-A sequence? | `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` |
| Decision flowchart? | `docs/governance/2026-05-05-wave-2-decision-flow.md` |
| Compact per-commit playbook? | `docs/governance/2026-05-05-wave-2-fire-time-per-commit-checklist.md` |
| Locked commit order? | `docs/governance/wave-2-deploy-commit-order-decision.md` |
| Cadence + UTC windows? | `docs/governance/2026-05-05-wave-2-deploy-day-timing.md` |
| Branch C feed-selection rubric? | `docs/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` |
| Smoke wrapper? | `scripts/wave2_fire_time_smoke.sh` (cycle 6) |
| Pre-staged CHANGELOG block? | `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` |

## Wave-3 deploy (≥ 2026-06-17+ if Wave-2 stalls)

| operator question | source-of-truth file |
|---|---|
| Lever B G1=0.04 lock? | `docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` |
| Lever C v1 lock? | `docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` |
| Decision flowchart? | `docs/governance/2026-05-05-wave-3-decision-flow.md` |
| Compact per-commit playbook? | `docs/governance/2026-05-05-wave-3-fire-time-per-commit-checklist.md` |
| Cadence + UTC windows? | `docs/governance/2026-05-05-wave-3-deploy-day-timing.md` |
| Smoke wrapper? | `scripts/wave3_fire_time_smoke.sh` (cycle 6) |
| Lever B-2 (0.03 floor) follow-up stub? | `docs/superpowers/specs/2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` |

## Branch D escalation (if Wave-2 + Wave-3 both stall)

| operator question | source-of-truth file |
|---|---|
| Triggers? | `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` |
| Fire procedure runbook? | `docs/governance/2026-05-05-branch-d-fire-procedure-runbook.md` |
| PROFIT-LLM-001 sizing scope? | `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md` |
| P4-GATE Appendix A sizing scope? | `docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` |

## Closure-path TLDR

| operator question | source-of-truth file |
|---|---|
| Current EDGE-004 closure path overview? | `docs/governance/edge-004-closure-path-tldr-v3.md` |

## Failure-mode runbooks

| operator question | source-of-truth file |
|---|---|
| KILL_SWITCH fire? | `docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md` |
| Mac Studio dead-bot / reboot? | `docs/governance/2026-05-05-mac-studio-dead-bot-reboot-runbook.md` |
| Network/Kalshi-API/Ollama outage? | `docs/governance/2026-05-05-network-api-outage-runbook.md` |
| Rollback (per-commit)? | `docs/governance/post-soak-rollback-runbook.md` |

## Architecture / contract

| operator question | source-of-truth file |
|---|---|
| Implementation Contract (binding)? | `docs/IMPLEMENTATION_CONTRACT.md` |
| Multi-cycle contract adherence audits? | `docs/governance/2026-05-05-cross-cycle-contract-adherence-review.md` + `2026-05-05-implementation-contract-cycle-4-5-review.md` + `docs/_archive/governance/2026-05-06-implementation-contract-cycle-6-7-review.md` (ARCHIVED Stream G R5) |
| Production config capture invariants (cycle-8 incident codified)? | `docs/IMPLEMENTATION_CONTRACT.md` §15 |
| Cycle-10 capture-incident decision + commit | `docs/governance/2026-05-05-launchd-plist-consolidation-decision.md` + commit `96e2995` (byte-faithful rewrite) |
| Lever menu (post-2026-05-05 locks)? | `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` (§5.1 + §5.2) |
| Closure-path TLDR? | `docs/governance/edge-004-closure-path-tldr-v3.md` |
| Unified debt log? | `docs/profit_path_debt_log.md` |
| ROADMAP? | `docs/ROADMAP.md` |
| README + status? | `README.md` |

## Operator scripts

| operator action | script |
|---|---|
| Soak invariant audit (gate 7) | `scripts/check_soak_invariant.sh --json` |
| Manual review tool (gate 6) | `scripts/governance_decision_review.py --bulk-mode` |
| Pre-soak close branch backup | `scripts/pre_soak_close_branch_backup.sh` |
| Bot health check | `scripts/bothealth.sh` |
| Wave-1 post-deploy smoke | `scripts/wave1_post_deploy_smoke.sh` |
| Wave-1 fire-time smoke | `scripts/wave1_fire_time_smoke.sh` |
| Wave-1 commit-chain acceptance audit | `scripts/wave1_commit_chain_acceptance_audit.sh` |
| Wave-2 fire-time smoke | `scripts/wave2_fire_time_smoke.sh` |
| Wave-3 fire-time smoke | `scripts/wave3_fire_time_smoke.sh` |
| DB backup health audit | `scripts/db_backup_health_audit.sh --json` |
| DB snapshot backup | `scripts/db_snapshot_backup.sh` (auto-fired by launchd at 06:00 MDT daily) |
| LLM throughput audit | `scripts/llm_throughput_audit.py` |
| LLM call budget projection | `scripts/pre_wave1_llm_call_budget_projection.py` |
| Source-class evolution audit | `scripts/source_class_evolution_audit.py` |
| PARSE_ERROR retroactive audit | `scripts/parse_error_retroactive_audit.py` |
| Distribution analysis (close-day) | `scripts/distribution_analysis_v2.py --daily-trend` |
| Calibration drift wiring audit | `scripts/calibration_drift_wiring_audit.py --json` |
| Operator alert routing audit | `scripts/operator_alert_routing_audit.sh --json` |
| Pre-commit hook health audit | `scripts/precommit_hook_health_audit.sh --json` |
| Test coverage audit | `scripts/test_coverage_audit.py` |
| Release tag inventory | `scripts/release_tag_inventory.sh` + `scripts/release_tag_inventory_snapshot.py` |
| launchd plist drift audit (legacy) | `scripts/launchd_plist_drift_audit.sh --json` |
| launchd template equivalence audit (cycle-10 IC §15 Rule 3 oracle) | `scripts/launchd_template_equivalence_audit.py [--installed \| --fixtures] [--json]` |
| Doc xref audit (markdown link + version-drift in prestaged blocks) | `scripts/doc_xref_audit.py [--include-archive]` |
| MacBook import disposition | `scripts/macbook_import_disposition_audit.sh` |
| MacBook import archive migration | `scripts/macbook_import_archive_migration.sh` |
| Doc cross-link audit | `scripts/doc_xref_audit.py` |

## Out of scope

- Per-spec deep-dives. Operator reads the spec at the file path; this index just maps question→file.
- ROADMAP-tracked Phase 4 (live trading) docs. Out of EDGE-004 scope.

## Cross-links (sibling-index purposes)

- `docs/governance/README.md` — directory-level inventory
- `docs/governance/2026-05-05-doc-index-audit.md` — cycle-2 categorical inventory
- `docs/governance/2026-05-05-doc-index-cleanup-execution-plan.md` — post-close cleanup procedure
- `docs/governance/2026-05-05-doc-cross-link-integrity-audit.md` — cycle-7 link audit
