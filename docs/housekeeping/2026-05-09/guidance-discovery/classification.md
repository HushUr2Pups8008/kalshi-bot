# Guidance .md Classification — Phase 5 Agent 2

**Generated:** 2026-05-08
**Total files classified:** 321
**Cap applied:** NO (292 project + 22 global config + 7 auto-memory = 321; well under 500)

## Distribution

| Category | Count | Notes |
|---|---|---|
| RULE | 10 | all in ~/.claude/rules/ |
| GOTCHA | 2 | project CLAUDE.md + global CLAUDE.md (narrative sections) |
| WORKING_STYLE | 5 | AGENTS.md files, CLAUDE.md working-style preambles, RTK.md |
| DESIGN_SPEC | 58 | superpowers/specs, superpowers/plans, evidence_store_schema.md, cycle charters, conditional skeletons |
| RUNBOOK | 149 | governance/ operational docs (soak reports, runbooks, checklists, deploy artifacts, cycle audits, replay reports, adversarial reviews, baselines), PHASE2_RUNBOOK, README files, ops/launchd/README, scripts/README, PLATFORMS.md, CHANGELOG.md, housekeeping docs, logs |
| AGENT_DEF | 7 | agents/, commands/, skills/ |
| MEMORY_INDEX | 1 | MEMORY.md |
| MEMORY_ENTRY | 6 | individual feedback_*.md files |
| OTHER | 83 | _archive plans (closed), _archive studies (closed), windows_archive docs (historical/provenance), IMPLEMENTATION_CONTRACT.md (governance contract), ROADMAP.md (active task tracker), EDGE_STATUS.md (dashboard), profit_path_debt_log.md (debt tracker), CHANGELOG.md (release log), cache/changelog.md (tool changelog) |

> Note: "RUNBOOK" is the largest bucket because the governance/ directory (195 files) is dominated by
> operational cycle artifacts — soak snapshots, adversarial reviews, deploy checklists, replay reports,
> audit scans — all tied to the live system's operation rather than pre-implementation design.

---

## Per-file classification (3 clusters)

### Cluster A — Project repo (/Users/jacobparenti/vscode/kalshi-bot)

#### Root-level files

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| AGENTS.md | WORKING_STYLE | RULE | H | Global operating contract + precedence order; imperative one-liners but framed as behavioral norms |
| CHANGELOG.md | OTHER | | H | Release log; not a guidance or instruction file |
| CLAUDE.md | GOTCHA | WORKING_STYLE | H | "Critical Gotchas" section is bold-inline narrative (load-bearing WHY); top preamble is working-style |
| PLATFORMS.md | RUNBOOK | | H | Platform support matrix; operational reference |
| README.md | RUNBOOK | | H | Deployment/operational guide + system status |

#### docs/ (non-governance)

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/EDGE_STATUS.md | OTHER | | H | Live operator dashboard; neither guidance nor spec |
| docs/evidence_store_schema.md | DESIGN_SPEC | | H | Schema contract spec for future implementation |
| docs/IMPLEMENTATION_CONTRACT.md | DESIGN_SPEC | RULE | H | Binding architecture contract with locked invariants; functions as rules but framed as a design contract |
| docs/profit_path_debt_log.md | OTHER | | H | Technical debt tracker; not guidance |
| docs/ROADMAP.md | OTHER | | H | Active task tracker; not guidance |

#### docs/_archive/

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/_archive/README.md | OTHER | | H | Index of closed/non-load-bearing archive content |
| docs/_archive/plans/v0.20.0_source_quality_feedback_loop.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.21.0_subreddit_discovery_loop.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.22.0_feedback_loop_closure.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.23.0_production_grade_fixes.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.24.0_live_safety_and_tests.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.26.0_fast_lane_source_prioritization.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.26.3_phase6b_shadow_eval_scoring.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.27.1_ollama_http_error_fix.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.27.2_llm_model_mismatch_fix.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.27.3_ollama_error_audit_script.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.27.4_llm_latency_observability.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.29.2_execution_mode_safety.md | OTHER | | H | Closed plan; archived |
| docs/_archive/plans/v0.29.4_test_log_isolation.md | OTHER | | H | Closed plan; archived |
| docs/_archive/studies/future_plans.md | OTHER | | H | Archived investigation; historical |
| docs/_archive/studies/news_sources_evaluation.md | OTHER | | H | Archived study; historical |
| docs/_archive/studies/polymarket_venue_integration_investigation.md | OTHER | | H | Archived study; historical |
| docs/_archive/studies/profit_cal_001_calibration_wiring.md | OTHER | | H | Archived study; historical |
| docs/_archive/studies/websocket_fix.md | OTHER | | H | Archived study; historical |

#### docs/governance/ — runbooks, operator checklists, attestations

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/governance/README.md | RUNBOOK | | H | Governance operator manual; how-to-use for runtime overrides |
| docs/governance/PHASE2_RUNBOOK.md | RUNBOOK | | H | Phase 2 shadow-mode operational runbook |
| docs/governance/post-soak-rollback-runbook.md | RUNBOOK | | H | Incident-response rollback procedure |
| docs/governance/post-soak-close-rehearsal-checklist.md | RUNBOOK | | H | Operator close-day checklist |
| docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md | RUNBOOK | | H | KILL_SWITCH fire procedure |
| docs/governance/2026-05-05-mac-studio-dead-bot-reboot-runbook.md | RUNBOOK | | H | Dead-bot reboot procedure |
| docs/governance/2026-05-05-network-api-outage-runbook.md | RUNBOOK | | H | Network/API outage response |
| docs/governance/2026-05-05-branch-d-fire-procedure-runbook.md | RUNBOOK | | H | Branch-D escalation fire procedure |
| docs/governance/2026-05-05-macbook-import-archive-procedure.md | RUNBOOK | | H | MacBook import archive procedure |
| docs/governance/2026-05-05-rollback-runbook-validation.md | RUNBOOK | | H | Validation that rollback runbook is correct |
| docs/governance/PROFIT-PHASE2-001-close-day-decision-flow.md | RUNBOOK | | H | Close-day decision flowchart |
| docs/governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md | RUNBOOK | | H | Day-7 close-day linear playbook |
| docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md | RUNBOOK | | H | Attestation template for early-close |
| docs/governance/PROFIT-PHASE2-001-early-close-attestation.md | RUNBOOK | | H | Completed attestation for early-close |
| docs/governance/PROFIT-PHASE2-001-early-close-criteria.md | RUNBOOK | DESIGN_SPEC | H | Early-close criteria + operator runbook |
| docs/governance/2026-05-05-day-7-to-wave-1-handoff-procedure.md | RUNBOOK | | H | Day-7 → Wave-1 handoff steps |
| docs/governance/2026-05-05-day-7-fire-time-compact-checklist.md | RUNBOOK | | H | Compact fire-time checklist |
| docs/governance/2026-05-05-day-7-attestation-prestage.md | RUNBOOK | | H | Pre-staged attestation skeleton |
| docs/governance/2026-05-05-day-7-walkthrough-dry-trace.md | RUNBOOK | | H | Day-7 dry-trace walkthrough |
| docs/governance/2026-05-05-wave-1-fire-time-per-commit-checklist.md | RUNBOOK | | H | Wave-1 per-commit deploy checklist |
| docs/governance/2026-05-05-wave-2-fire-time-per-commit-checklist.md | RUNBOOK | | H | Wave-2 per-commit deploy checklist |
| docs/governance/2026-05-05-wave-3-fire-time-per-commit-checklist.md | RUNBOOK | | H | Wave-3 per-commit deploy checklist |
| docs/governance/2026-05-05-wave-1-deploy-day-timing.md | RUNBOOK | | H | Wave-1 deploy timing plan |
| docs/governance/2026-05-05-wave-2-deploy-day-timing.md | RUNBOOK | | H | Wave-2 deploy timing plan |
| docs/governance/2026-05-05-wave-3-deploy-day-timing.md | RUNBOOK | | H | Wave-3 deploy timing plan |
| docs/governance/2026-05-05-wave-1-deploy-dry-run-report.md | RUNBOOK | | H | Wave-1 deploy dry-run report |
| docs/governance/wave-1-deploy-commit-order-decision.md | RUNBOOK | DESIGN_SPEC | H | Commit-order decision for Wave-1; operator decision artifact |
| docs/governance/wave-2-deploy-commit-order-decision.md | RUNBOOK | DESIGN_SPEC | H | Commit-order decision for Wave-2 |
| docs/governance/wave-1-post-deploy-observation-plan.md | RUNBOOK | | H | Post-deploy observation plan |
| docs/governance/wave-1-changelog-entry-prestaged.md | RUNBOOK | | H | Pre-staged changelog entry |
| docs/governance/wave-1-commit-messages-prestaged.md | RUNBOOK | | H | Pre-staged commit messages |
| docs/governance/wave-2-wave-3-changelog-entries-prestaged.md | RUNBOOK | | H | Pre-staged changelog entries |
| docs/governance/2026-05-06-pre-day7-dry-run-rehearsal.md | RUNBOOK | | H | Pre-day-7 dry-run rehearsal |
| docs/governance/2026-05-06-pre-wave1-deploy-dry-run-rehearsal.md | RUNBOOK | | H | Pre-wave-1 deploy dry-run rehearsal |
| docs/governance/2026-05-06-gate-6-path-1-fire-time-template.md | RUNBOOK | | H | Gate-6 fire-time template |
| docs/governance/PROFIT-PHASE2-002-onboarding.md | RUNBOOK | | H | Phase-2 onboarding runbook |

#### docs/governance/ — design specs, charters, decision records

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md | DESIGN_SPEC | | H | Active redesign program charter |
| docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md | DESIGN_SPEC | | H | Experiment ledger schema spec |
| docs/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md | DESIGN_SPEC | | H | Axis-pick decision rationale |
| docs/governance/2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md | DESIGN_SPEC | | H | E1 acceptance criteria lock |
| docs/governance/cycle-15-conditional-charter-skeletons.md | DESIGN_SPEC | | H | Pre-staged charter scaffolds |
| docs/governance/cycle-16-conditional-charter-skeletons.md | DESIGN_SPEC | | H | Pre-staged charter scaffolds |
| docs/governance/cycle-17-conditional-charter-skeletons.md | DESIGN_SPEC | | H | Pre-staged charter scaffolds |
| docs/governance/2026-05-06-cycle-13-replay-scope-expansion-charter.md | DESIGN_SPEC | | H | Cycle-13 scope expansion charter |
| docs/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md | DESIGN_SPEC | | H | Cycle-14 charter/diagnosis |
| docs/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md | DESIGN_SPEC | | H | Cycle-15b charter rebuild |
| docs/governance/2026-05-07-cycle-16d-charter-price-reconstruction.md | DESIGN_SPEC | | H | Cycle-16d charter |
| docs/governance/2026-05-07-cycle-16e-scorer-forensics-charter.md | DESIGN_SPEC | | H | Cycle-16e scorer forensics charter |
| docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md | DESIGN_SPEC | | H | Strategic redirect decision record |
| docs/governance/edge-replay-pivot-playbook.md | DESIGN_SPEC | RUNBOOK | H | Pre-staged diagnostic playbook for IC §16 pivot |
| docs/governance/post-edge-004-escalation-paths.md | DESIGN_SPEC | | H | Post-EDGE-004 escalation path options |
| docs/governance/edge-004-closure-path-tldr.md | DESIGN_SPEC | | H | EDGE-004 closure path decision summary |
| docs/governance/edge-004-closure-path-tldr-v3.md | DESIGN_SPEC | | H | v3 of above |
| docs/governance/2026-05-05-doc-index-cleanup-execution-plan.md | DESIGN_SPEC | | H | Doc-index cleanup execution plan |
| docs/governance/2026-05-06-gate-6-capacity-resolution-plan.md | DESIGN_SPEC | | H | Gate-6 capacity resolution plan |
| docs/governance/2026-05-06-pre-cycle-14-codex-queue.md | DESIGN_SPEC | | M | Pre-cycle Codex task queue |
| docs/governance/2026-05-06-cycle-15b-task-split.md | DESIGN_SPEC | | H | Cycle-15b task split spec |
| docs/governance/2026-05-07-cycle-16d-task-split.md | DESIGN_SPEC | | H | Cycle-16d task split |
| docs/governance/2026-05-07-cycle-16e-task-split.md | DESIGN_SPEC | | H | Cycle-16e task split |
| docs/governance/cycle-14-post-verdict-action-checklist.md | RUNBOOK | | H | Post-verdict operator action checklist |
| docs/governance/cycle-15b-post-verdict-action-checklist.md | RUNBOOK | | H | Post-verdict operator action checklist |
| docs/governance/cycle-16d-post-verdict-action-checklist.md | RUNBOOK | | H | Post-verdict operator action checklist |

#### docs/governance/ — soak reports, snapshots, confirmations

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/governance/2026-05-03-mid-soak-health-report.md | RUNBOOK | | H | Soak health report |
| docs/governance/2026-05-03-mid-soak-snapshot-2.md | RUNBOOK | | H | Soak snapshot |
| docs/governance/2026-05-03-mid-soak-snapshot-3.md | RUNBOOK | | H | Soak snapshot |
| docs/governance/2026-05-03-mid-soak-snapshot-4.md | RUNBOOK | | H | Soak snapshot |
| docs/governance/2026-05-03-mid-soak-snapshot-5.md | RUNBOOK | | H | Soak snapshot |
| docs/governance/2026-05-03-day-3-pending-mid-soak-confirmation.md | RUNBOOK | | H | Day-3 confirmation placeholder |
| docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation.md | RUNBOOK | | H | Day-4 confirmation placeholder |
| docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-2.md | RUNBOOK | | H | Day-4 confirmation placeholder (duplicate cadence) |
| docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-3.md | RUNBOOK | | H | Day-4 confirmation placeholder (duplicate cadence) |
| docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-4.md | RUNBOOK | | H | Day-4 confirmation placeholder (duplicate cadence) |
| docs/governance/2026-05-04-day-4-mid-soak-confirmation.md | RUNBOOK | | H | Day-4 soak confirmation |
| docs/governance/2026-05-04-day-4-mid-soak-sanity-check.md | RUNBOOK | | H | Day-4 sanity check |
| docs/governance/2026-05-04-day-5-cycle-sanity-check-pending.md | RUNBOOK | | H | Day-5 sanity check placeholder |
| docs/governance/2026-05-05-day-5-cycle-sanity-check.md | RUNBOOK | | H | Day-5 cycle sanity check |
| docs/governance/2026-05-05-day-5-cycle-sanity-check-refresh.md | RUNBOOK | | H | Day-5 sanity check refresh |
| docs/governance/2026-05-05-day-5-cycle-sanity-check-canonical.md | RUNBOOK | | H | Day-5 sanity check canonical version |
| docs/governance/2026-05-07-day-7-pre-soak-confirmation.md | RUNBOOK | | H | Day-7 soak confirmation (skeleton) |
| docs/governance/2026-05-06-soak-runtime-characterization-summary.md | RUNBOOK | | H | Soak runtime summary |
| docs/governance/2026-05-05-next-soak-readiness-audit.md | RUNBOOK | | H | Pre-next-soak readiness audit |

#### docs/governance/ — analysis / audit reports / baselines

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/governance/2026-05-03-post-soak-landing-simulation.md | RUNBOOK | | H | Counterfactual replay simulation |
| docs/governance/2026-05-03-edge004-lever-d-pre-llm-gate-audit.md | RUNBOOK | | H | Read-only archive sweep / analysis |
| docs/governance/2026-05-03-cross-series-headline-overlap-audit.md | RUNBOOK | | H | Cross-series overlap audit |
| docs/governance/2026-05-03-cumulative-call-audit.md | RUNBOOK | | H | Cumulative API call audit |
| docs/governance/2026-05-03-empty-response-audit.md | RUNBOOK | | H | Empty-response audit |
| docs/governance/2026-05-03-source-class-diversification-audit.md | RUNBOOK | | H | Source class diversification audit |
| docs/governance/2026-05-03-g1-admittance-counterfactual.md | RUNBOOK | | H | G1 admittance counterfactual analysis |
| docs/governance/2026-05-03-h5-semantic-bisection.md | RUNBOOK | | H | H5 semantic bisection analysis |
| docs/governance/2026-05-03-matcher-jaccard-threshold-bisection.md | RUNBOOK | | H | Matcher threshold bisection |
| docs/governance/2026-05-03-match001-bprime-anchor-sizing.md | RUNBOOK | | H | Match-001 B' anchor sizing |
| docs/governance/2026-05-03-match001-bprime-false-negative-audit.md | RUNBOOK | | H | Match-001 B' false-negative audit |
| docs/governance/2026-05-03-match001-bprime-false-suppression-audit.md | RUNBOOK | | H | Match-001 B' false-suppression audit |
| docs/governance/2026-05-03-match001-tokenization-equivalence-audit.md | RUNBOOK | | H | Match-001 tokenization equivalence audit |
| docs/governance/2026-05-03-obs003-kill-attribution.md | RUNBOOK | | H | OBS-003 kill attribution analysis |
| docs/governance/2026-05-03-obs003-skipped-synthesizer-reality-check.md | RUNBOOK | | H | OBS-003 synthesizer reality check |
| docs/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md | RUNBOOK | DESIGN_SPEC | M | Feed sizing analysis (informs design) |
| docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md | RUNBOOK | DESIGN_SPEC | M | Analyst sizing analysis |
| docs/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md | RUNBOOK | | H | Source classifier counterfactual |
| docs/governance/2026-05-03-lever-e-source-corroboration-sizing.md | RUNBOOK | DESIGN_SPEC | M | Corroboration sizing analysis |
| docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md | RUNBOOK | | H | Trade rate forecast |
| docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md | RUNBOOK | | H | Wave-2 state ladder |
| docs/governance/2026-05-03-wave1-acceptance-ladder-forecast.md | RUNBOOK | | H | Wave-1 acceptance ladder forecast |
| docs/governance/2026-05-04-legal-niche-probe-order-domain-overlap.md | RUNBOOK | | H | Legal niche probe analysis |
| docs/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md | RUNBOOK | DESIGN_SPEC | M | Legal analyst sizing |
| docs/governance/2026-05-04-lever-a1-plus-specialist-analyst-domain-normalized-audit.md | RUNBOOK | | H | Domain-normalized analyst audit |
| docs/governance/2026-05-04-match001-bprime-spec-parity-verification.md | RUNBOOK | | H | Spec parity verification |
| docs/governance/2026-05-04-pre-wave1-source-class-ev-baseline.md | RUNBOOK | | H | Pre-wave-1 EV baseline |
| docs/governance/2026-05-04-vitallaw-aggregator-path-forensics.md | RUNBOOK | | H | VitalLaw path forensics |
| docs/governance/2026-05-04-vitallaw-archive-forensics.md | RUNBOOK | | H | VitalLaw archive forensics |
| docs/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md | RUNBOOK | DESIGN_SPEC | M | Feed selection rubric |
| docs/governance/2026-05-05-changelog-drift-check.md | RUNBOOK | | H | Changelog drift check |
| docs/governance/2026-05-05-PROFIT-PHASE2-001-daily-trend-baseline.md | RUNBOOK | | H | Daily trend baseline |
| docs/governance/2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md | RUNBOOK | | H | Decision distribution analysis |
| docs/governance/2026-05-05-PROFIT-PHASE2-001-llm-throughput-baseline.md | RUNBOOK | | H | LLM throughput baseline |
| docs/governance/2026-05-05-PROFIT-PHASE2-001-parse-error-retroactive-findings.md | RUNBOOK | | H | Parse error retroactive findings |
| docs/governance/2026-05-05-PROFIT-PHASE2-001-source-class-evolution-baseline.md | RUNBOOK | | H | Source class evolution baseline |
| docs/governance/2026-05-05-pre-wave1-baseline-interpretation.md | RUNBOOK | | H | Pre-wave-1 baseline interpretation |
| docs/governance/2026-05-05-pre-wave1-cooldown-distribution-audit.md | RUNBOOK | | H | Cooldown distribution audit |
| docs/governance/2026-05-05-pre-wave1-opportunity-age-distribution.md | RUNBOOK | | H | Opportunity age distribution |
| docs/governance/2026-05-05-pre-wave1-skipped-rate-baseline.md | RUNBOOK | | H | Skipped-rate baseline |
| docs/governance/2026-05-05-pre-wave1-trade-rate-per-ticker-baseline.md | RUNBOOK | | H | Trade rate per ticker baseline |
| docs/governance/2026-05-05-pre-wave1-version-bump-dry-run.md | RUNBOOK | | H | Version bump dry-run |
| docs/governance/2026-05-05-readme-drift-check.md | RUNBOOK | | H | README drift check |
| docs/governance/2026-05-05-roadmap-drift-check.md | RUNBOOK | | H | Roadmap drift check |
| docs/governance/2026-05-05-vitallaw-direct-rss-probe.md | RUNBOOK | | H | VitalLaw RSS probe |
| docs/governance/2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md | RUNBOOK | | H | Wave-1 acceptance matrix |
| docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md | RUNBOOK | DESIGN_SPEC | M | Wave-2 branch decision table |
| docs/governance/2026-05-05-wave-2-decision-flow.md | RUNBOOK | | H | Wave-2 decision flow |
| docs/governance/2026-05-05-wave-3-decision-flow.md | RUNBOOK | | H | Wave-3 decision flow |
| docs/governance/2026-05-05-wave1-commit-order-risk-audit.md | RUNBOOK | | H | Wave-1 commit-order risk audit |
| docs/governance/2026-05-05-wave1-six-commit-dryrun-review-status.md | RUNBOOK | | H | Wave-1 dry-run review status |
| docs/governance/2026-05-05-doc-cross-link-integrity-audit.md | RUNBOOK | | H | Doc cross-link integrity audit |
| docs/governance/2026-05-05-doc-index-audit.md | RUNBOOK | | H | Doc index audit |
| docs/governance/2026-05-05-edge004-tldr-doc-quality-review.md | RUNBOOK | | H | EDGE-004 TL;DR quality review |
| docs/governance/2026-05-05-gate6-manual-review-tool-status.md | RUNBOOK | | H | Gate-6 manual review tool status |
| docs/governance/2026-05-05-governance-monitor-preload-verification.md | RUNBOOK | | H | Governance monitor preload verification |
| docs/governance/2026-05-05-launchd-plist-drift-audit.md | RUNBOOK | | H | Launchd plist drift audit |
| docs/governance/2026-05-05-launchd-plist-consolidation-decision.md | RUNBOOK | DESIGN_SPEC | M | Plist consolidation decision |
| docs/governance/2026-05-05-legal-aggregator-coverage-audit.md | RUNBOOK | | H | Legal aggregator coverage audit |
| docs/governance/2026-05-05-lever-d-nomenclature-cleanup-audit.md | RUNBOOK | | H | Lever-D nomenclature cleanup audit |
| docs/governance/2026-05-05-memory-hygiene-audit.md | RUNBOOK | | H | Memory hygiene audit |
| docs/governance/2026-05-05-openclaw-orchestrator-integration-note.md | RUNBOOK | DESIGN_SPEC | M | OpenClaw integration note |
| docs/governance/2026-05-05-db-backup-gap-resolution.md | RUNBOOK | | H | DB backup gap resolution |
| docs/governance/2026-05-05-pre-wave1-source-of-truth-doc-index.md | RUNBOOK | | H | Pre-wave-1 source-of-truth doc index |
| docs/governance/2026-05-06-changelog-drift-check-cycle-10.md | RUNBOOK | | H | Changelog drift check |
| docs/governance/2026-05-06-cycle-10-blocker-resolution.md | RUNBOOK | | H | Cycle-10 blocker resolution |
| docs/governance/2026-05-06-cycle-11-pre-wave1-final-cleanup.md | RUNBOOK | | H | Cycle-11 final cleanup |
| docs/governance/2026-05-06-cycle-12-replay-readiness-inventory.md | RUNBOOK | | H | Cycle-12 replay readiness inventory |
| docs/governance/2026-05-06-cycle-13-live-api-coordination.md | RUNBOOK | | H | Cycle-13 live API coordination |
| docs/governance/2026-05-06-cycle-13-replay-harness-code-review.md | RUNBOOK | | H | Cycle-13 replay harness code review |
| docs/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md | RUNBOOK | | H | Cycle-14 sign-error candidate trace |
| docs/governance/2026-05-06-cycle-15b-claude-independent-trace-read.md | RUNBOOK | | H | Cycle-15b independent trace read |
| docs/governance/2026-05-06-cycle-15b-l7-reingestion-atomicity-review.md | RUNBOOK | | H | Cycle-15b L7 reingestion atomicity review |
| docs/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md | RUNBOOK | | H | Cycle-15b paper trades cohort note |
| docs/governance/2026-05-06-cycle-15b-pre-execution-criteria-verification.md | RUNBOOK | | H | Cycle-15b execution criteria verification |
| docs/governance/2026-05-06-cycle-7-cross-cycle-contract-adherence-update.md | RUNBOOK | | H | Cross-cycle contract adherence update |
| docs/governance/2026-05-06-doc-xref-audit-report.md | RUNBOOK | | H | Doc xref audit report |
| docs/governance/2026-05-06-doc-xref-audit-triage.md | RUNBOOK | | H | Doc xref audit triage |
| docs/governance/2026-05-06-implementation-contract-cycle-6-7-review.md | RUNBOOK | | H | Implementation contract review |
| docs/governance/2026-05-06-post-obs003-skipped-attribution-audit-refresh.md | RUNBOOK | | H | OBS-003 attribution audit refresh |
| docs/governance/2026-05-06-pre-wave1-risk-register.md | RUNBOOK | | H | Pre-wave-1 risk register |
| docs/governance/2026-05-06-wave-2-deploy-ready-state-assessment.md | RUNBOOK | | H | Wave-2 deploy ready state assessment |
| docs/governance/2026-05-07-cycle-16d-m3-fetch-code-review.md | RUNBOOK | | H | Cycle-16d M3 fetch code review |
| docs/governance/2026-05-07-cycle-16d-m7-coverage-acceptance.md | RUNBOOK | | H | Cycle-16d M7 coverage acceptance |
| docs/governance/2026-05-07-cycle-16d-pre-execution-criteria-verification.md | RUNBOOK | | H | Cycle-16d execution criteria verification |
| docs/governance/2026-05-07-cycle-16d-price-source-inventory.md | RUNBOOK | | H | Cycle-16d price source inventory |
| docs/governance/2026-05-07-cycle-16e-claude-rescope-and-review.md | RUNBOOK | | H | Cycle-16e rescope and review |
| docs/governance/2026-05-07-cycle-17c-e1-claude-verdict-appendix.md | RUNBOOK | | H | Cycle-17c E1 verdict appendix |
| docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md | RUNBOOK | | H | Cycle-17c E2 G1 admission sweep |
| docs/governance/2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md | DESIGN_SPEC | | H | E3 hypothesis sketch; forward-looking |
| docs/governance/2026-05-05-implementation-contract-cycle-4-5-review.md | RUNBOOK | | H | IC review cycle 4-5 |
| docs/governance/2026-05-03-post-soak-spec-adversarial-review.md | RUNBOOK | | H | Adversarial review of post-soak spec |

#### docs/governance/ — adversarial reviews

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/governance/2026-05-03-claude-commit-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-03-claude-commits-4a7cc38-fee5003-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-03-claude-commits-56d641e-9e2fffa-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-03-claude-commits-728f3bd-cfc0b60-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-03-claude-commits-c5cbc6f-90c26cf-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-03-claude-commits-cdbf6ef-f786246-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-03-claude-latest-commits-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-04-claude-commits-681ceb9-2bf3da1-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-04-claude-latest-five-commits-adversarial-review-legal-cycle.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-05-claude-latest-six-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |
| docs/governance/2026-05-05-cross-cycle-contract-adherence-review.md | RUNBOOK | | H | Cross-cycle contract adherence review |
| docs/governance/2026-05-05-cross-set-adversarial-review-legal-doc-cycle.md | RUNBOOK | | H | Cross-set adversarial review |
| docs/governance/2026-05-05-latest-5plus5-adversarial-review.md | RUNBOOK | | H | Commit adversarial review |

#### docs/governance/ — edge replay reports

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/governance/edge-replay-cycle12-report.md | RUNBOOK | | H | Cycle-12 replay report |
| docs/governance/edge-replay-cycle13-report.md | RUNBOOK | | H | Cycle-13 replay report |
| docs/governance/edge-replay-cycle14-diagnosis.md | RUNBOOK | | H | Cycle-14 replay diagnosis |
| docs/governance/edge-replay-cycle15b-report.md | RUNBOOK | | H | Cycle-15b replay report |
| docs/governance/edge-replay-cycle15b-sub-fix-proposal.md | DESIGN_SPEC | RUNBOOK | H | Sub-fix proposal (forward-looking) |
| docs/governance/edge-replay-cycle16d-report.md | RUNBOOK | | H | Cycle-16d replay report |
| docs/governance/edge-replay-cycle16e-scorer-forensics.md | RUNBOOK | | H | Cycle-16e scorer forensics |
| docs/governance/edge-replay-cycle17c-e1-report.md | RUNBOOK | | H | Cycle-17c E1 replay report |

#### docs/superpowers/

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/superpowers/plans/2026-04-24-governance-agent-phase-1-plan.md | DESIGN_SPEC | | H | Phase-1 governance agent plan |
| docs/superpowers/plans/2026-04-25-governance-agent-phase-2-plan.md | DESIGN_SPEC | | H | Phase-2 governance agent plan |
| docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md | DESIGN_SPEC | | H | Pipeline simulation buildout plan |
| docs/superpowers/plans/2026-05-09-guidance-consolidation-initiative.md | DESIGN_SPEC | | H | Guidance consolidation plan (this initiative) |
| docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md | DESIGN_SPEC | | H | LLM governance agent design spec |
| docs/superpowers/specs/2026-05-03-edge-004-lever-a-source-class-diversification-design.md | DESIGN_SPEC | | H | Lever-A design spec |
| docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md | DESIGN_SPEC | | H | Lever-A1 design spec |
| docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md | DESIGN_SPEC | | H | Lever-A1+ feed onboarding design |
| docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md | DESIGN_SPEC | | H | Lever-B G1 calibration design |
| docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md | DESIGN_SPEC | | H | Lever-C design spec |
| docs/superpowers/specs/2026-05-03-edge-004-lever-e-multi-source-corroboration-design.md | DESIGN_SPEC | | H | Lever-E design spec |
| docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md | DESIGN_SPEC | | H | EDGE-004 lever menu design |
| docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md | DESIGN_SPEC | | H | EXEC-002 design spec |
| docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md | DESIGN_SPEC | | H | Governance monitor fix design |
| docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md | DESIGN_SPEC | | H | MATCH-001 token guard design |
| docs/superpowers/specs/2026-05-03-match-001-tokenization-option-b-design.md | DESIGN_SPEC | | H | MATCH-001 option-B design |
| docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md | DESIGN_SPEC | | H | OBS-003 design spec |
| docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md | DESIGN_SPEC | | H | OBS-005 design spec |
| docs/superpowers/specs/2026-05-03-post-soak-landing-order-design.md | DESIGN_SPEC | | H | Post-soak landing order design |
| docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md | DESIGN_SPEC | | H | Lever-A1+1.5 legal analyst design |
| docs/superpowers/specs/2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md | DESIGN_SPEC | | H | Lever-B 0.03 floor follow-up stub |
| docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md | DESIGN_SPEC | | H | Lever-B G1 floor lock addendum |
| docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md | DESIGN_SPEC | | H | Lever-C v1 lock addendum |
| docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md | DESIGN_SPEC | | H | Lever-D escalation criteria design |
| docs/superpowers/specs/2026-05-05-next-soak-cadence-tune-design.md | DESIGN_SPEC | | H | Soak cadence tune design |
| docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md | DESIGN_SPEC | | H | P4 gate pre-sizing scope design |
| docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md | DESIGN_SPEC | | H | PROFIT-LLM-001 pre-sizing design |

#### docs/housekeeping/

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| docs/housekeeping/2026-05-08/SUMMARY.md | RUNBOOK | | H | Phase-1 housekeeping audit summary |
| docs/housekeeping/2026-05-08/architecture.md | RUNBOOK | | H | Architecture audit findings |
| docs/housekeeping/2026-05-08/claude-md-audit.md | RUNBOOK | | H | CLAUDE.md audit findings |
| docs/housekeeping/2026-05-08/comment-health.md | RUNBOOK | | H | Comment health findings |
| docs/housekeeping/2026-05-08/dead-code-inventory.md | RUNBOOK | | H | Dead code inventory |
| docs/housekeeping/2026-05-08/open-questions-resolution.md | RUNBOOK | | H | Open questions resolution |
| docs/housekeeping/2026-05-08/python-quality.md | RUNBOOK | | H | Python quality findings |
| docs/housekeeping/2026-05-08/security-audit.md | RUNBOOK | | H | Security audit findings |
| docs/housekeeping/2026-05-08/workspace-surface.md | RUNBOOK | | H | Workspace surface findings |
| docs/housekeeping/2026-05-09/foundational-docs-audit/SUMMARY.md | RUNBOOK | | H | Foundational docs audit summary |
| docs/housekeeping/2026-05-09/foundational-docs-audit/context-cost.md | RUNBOOK | | H | Context cost analysis |
| docs/housekeeping/2026-05-09/foundational-docs-audit/drift-detection.md | RUNBOOK | | H | Drift detection report |
| docs/housekeeping/2026-05-09/foundational-docs-audit/gap-analysis.md | RUNBOOK | | H | Gap analysis report |
| docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md | RUNBOOK | | H | General quality findings |
| docs/housekeeping/2026-05-09/foundational-docs-audit/reference-integrity.md | RUNBOOK | | H | Reference integrity findings |
| docs/housekeeping/2026-05-09/phase-3-design/README.md | RUNBOOK | DESIGN_SPEC | H | Phase-3 design pass index |
| docs/housekeeping/2026-05-09/phase-3-design/oq1-analysis-purity-rename.md | DESIGN_SPEC | | H | ADR-style design doc |
| docs/housekeeping/2026-05-09/phase-3-design/p1-02-signal-analyzer-decomposition.md | DESIGN_SPEC | | H | ADR-style design doc |
| docs/housekeeping/2026-05-09/phase-3-design/p1-03-logger-typed-params.md | DESIGN_SPEC | | H | ADR-style design doc |

#### ops/, scripts/, tests/, logs/, windows_archive/

| Path (relative to repo) | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| ops/launchd/README.md | RUNBOOK | RULE | H | Launchd maintainer rules + operational guide; contains load-bearing rules inline |
| scripts/README.md | RUNBOOK | | H | Scripts operational guide |
| scripts/simulations/README.md | RUNBOOK | | H | Simulations guide |
| tests/fixtures/installed_plists/README.md | RUNBOOK | | H | Fixture provenance + maintenance notes |
| logs/app/bothealth_2026-05-02.md | RUNBOOK | | H | Daily bot health report |
| logs/app/bothealth_2026-05-03.md | RUNBOOK | | H | Daily bot health report |
| logs/app/bothealth_2026-05-04.md | RUNBOOK | | H | Daily bot health report |
| logs/app/bothealth_2026-05-05.md | RUNBOOK | | H | Daily bot health report |
| logs/app/bothealth_2026-05-06.md | RUNBOOK | | H | Daily bot health report |
| logs/app/bothealth_2026-05-07.md | RUNBOOK | | H | Daily bot health report |
| logs/app/bothealth_2026-05-08.md | RUNBOOK | | H | Daily bot health report |
| logs/app/operator_alert_routing_20260505T134048Z.md | RUNBOOK | | H | Operator alert routing log |
| logs/app/operator_alert_routing_20260505T134131Z.md | RUNBOOK | | H | Operator alert routing log |
| logs/app/pre_day7_smoke_codex_cycle7.md | RUNBOOK | | H | Pre-day-7 smoke test log |
| logs/app/pre_day7_smoke_cycle11.md | RUNBOOK | | H | Pre-day-7 smoke test log |
| logs/app/soak_check_20260501_150756Z.md | RUNBOOK | | H | Soak check log |
| logs/app/soak_check_20260503_150700Z.md | RUNBOOK | | H | Soak check log |
| windows_archive/README.md | RUNBOOK | | H | Windows archive index + provenance |
| windows_archive/raw/2026-04-18_import/PROVENANCE.md | OTHER | | H | Data provenance record; not guidance |
| windows_archive/analysis/2026-04-18_import/consolidated/notes.md | OTHER | | H | Historical analysis notes; not guidance |
| windows_archive/analysis/2026-04-18_import/reports/7d_reporting_baseline/notes.md | OTHER | | H | Historical reporting baseline notes |

---

### Cluster B — Global config (~/.claude/)

| Path | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| ~/.claude/CLAUDE.md | WORKING_STYLE | | H | Top-level working style + tool preferences; no "Critical Gotchas" narrative blocks |
| ~/.claude/AGENTS.md | WORKING_STYLE | RULE | H | Global operating contract + precedence; imperative but framed as behavioral norms |
| ~/.claude/RTK.md | RUNBOOK | | H | RTK tool usage guide; how to run/invoke |
| ~/.claude/project/AGENTS.md | WORKING_STYLE | RULE | H | Project-scoped agent contract; architecture + rule boundaries |
| ~/.claude/cache/changelog.md | OTHER | | H | Claude Code app changelog; not guidance |
| ~/.claude/rules/documentation_format.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/rules/domain_constraints.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/rules/editing_safety.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/rules/git_workflow.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/rules/planning.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/rules/portability.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/rules/release_versioning.md | RULE | | H | Strict Trigger/Action format; project-local section is mixed |
| ~/.claude/rules/risk_review.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/rules/validation.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/rules/windows_local.md | RULE | | H | Strict Trigger/Action format |
| ~/.claude/agents/gitlab-investigator.md | AGENT_DEF | | H | Has frontmatter name/description/tools; agent persona |
| ~/.claude/commands/gl-status.md | AGENT_DEF | | H | Has frontmatter description; slash command definition |
| ~/.claude/commands/housekeeping-audit.md | AGENT_DEF | | H | Has frontmatter description; slash command definition |
| ~/.claude/skills/debug-issue.md | AGENT_DEF | | H | Has frontmatter name/description; skill definition |
| ~/.claude/skills/explore-codebase.md | AGENT_DEF | | H | Has frontmatter name/description; skill definition |
| ~/.claude/skills/refactor-safely.md | AGENT_DEF | | H | Has frontmatter name/description; skill definition |
| ~/.claude/skills/review-changes.md | AGENT_DEF | | H | Has frontmatter name/description; skill definition |

---

### Cluster C — Auto-memory

| Path | Primary | Secondary | Confidence | Notes |
|---|---|---|---|---|
| memory/MEMORY.md | MEMORY_INDEX | | H | Bullet-list index of feedback entries; no content of its own |
| memory/feedback_soak_confirmation_cadence.md | MEMORY_ENTRY | | H | Frontmatter type=feedback; narrative entry |
| memory/feedback_soak_acceleration_split.md | MEMORY_ENTRY | | H | Frontmatter type=feedback; narrative entry |
| memory/feedback_monitor_probe_vs_tasklist.md | MEMORY_ENTRY | | H | Frontmatter type=feedback; narrative entry |
| memory/feedback_edge_priority_over_deploy_safety.md | MEMORY_ENTRY | | H | Frontmatter type=feedback; narrative entry |
| memory/feedback_audit_scorer_before_verdict.md | MEMORY_ENTRY | | H | Frontmatter type=feedback; narrative entry |
| memory/feedback_market_implied_baseline.md | MEMORY_ENTRY | | H | Frontmatter type=feedback; narrative entry |

---

## Format consistency check

### RULE files — Trigger/Action format violations

All 10 files in `~/.claude/rules/` use correct Trigger/Action format. No violations found.

One note: `~/.claude/rules/release_versioning.md` contains a project-local "extends" section in CLAUDE.md (not in the rule file itself) that uses narrative format. The rule file itself is clean Trigger/Action. No violation.

### GOTCHA blocks in CLAUDE.md — sections that read like rules instead of narrative

The following blocks inside CLAUDE.md (project) are in Trigger/Action bullet format rather than bold-inline narrative, which violates the format mandated by `~/.claude/rules/documentation_format.md`:

1. **`/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` — "Release Versioning (project-local)" section**
   Uses Trigger/Action bullets (e.g., "Trigger: when bumping VERSION. Action: Stage VERSION first; ..."). This section is a mechanical rule list, not a GOTCHA narrative. Per `documentation_format.md`, if removing the explanation leaves it still useful, it should be in a rule file, not CLAUDE.md's narrative sections. However, this is intentionally in a mixed-format CLAUDE.md and is not under the "Critical Gotchas" heading — it's a separate standalone section. Low-severity discrepancy: the section belongs in a rule file or should be explicitly labeled as rules rather than presented inline with narrative gotchas.

2. **`/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` — "Continuous Improvement" and "Working Style" sections**
   These are narrative / preference prose, which is correct per the documentation_format rules. No violation.

3. **`/Users/jacobparenti/.claude/CLAUDE.md` — overall file**
   Uses prose narrative throughout. Correct. No violation.

### Cross-cluster format note for Phase 6

- `kalshi-bot/CLAUDE.md` mixes three formats in one file: GOTCHA narrative (Critical Gotchas section), RULE bullets (Release Versioning section), and WORKING_STYLE prose (top sections). The documentation_format rule says to split files that mix formats, but this specific file is cited as an intentional exception for "CLAUDE.md gotchas" per `documentation_format.md` rule 6. Flag for human review: the Release Versioning Trigger/Action block is the only format-ambiguous element — it could be extracted to `rules/release_versioning.md` or left as-is with explicit acknowledgment.

- No RULE files in `~/.claude/rules/` contain narrative prose — all are clean Trigger/Action. No violations there.

