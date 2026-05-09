# docs/ Classification — 2026-05-09

**Phase:** 2 — Classification
**Scope:** All `.md` files under `docs/` as of 2026-05-09
**Total classified:** 275
**Sort order:** `find docs -name "*.md" | sort`

---

## Summary

| Classification | Count |
|---|---|
| AUDIT_REPORT | 107 |
| DESIGN_SPEC | 27 |
| AUDIT_REVIEW | 26 |
| PLAN | 24 |
| TRACKING_STATUS | 21 |
| RUNBOOK | 20 |
| ARCHIVE | 19 |
| MIXED | 12 |
| CHARTER | 11 |
| README | 3 |
| SCHEMA | 2 |
| CONTRACT | 1 |
| TRACKING_DEBT | 1 |
| TRACKING_ROADMAP | 1 |
| TRACKING_LEDGER | 0 |
| **Total** | **275** |

MIXED files requiring review: **12** — see secondary_classification column below.

---

## Classification Table

| path | classification | confidence | secondary_classification | rationale |
|------|---------------|-----------|--------------------------|-----------|
| `docs/_archive/plans/v0.20.0_source_quality_feedback_loop.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.21.0_subreddit_discovery_loop.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.22.0_feedback_loop_closure.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.23.0_production_grade_fixes.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.24.0_live_safety_and_tests.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.26.0_fast_lane_source_prioritization.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.26.3_phase6b_shadow_eval_scoring.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.27.1_ollama_http_error_fix.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.27.2_llm_model_mismatch_fix.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.27.3_ollama_error_audit_script.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.27.4_llm_latency_observability.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.29.2_execution_mode_safety.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/plans/v0.29.4_test_log_isolation.md` | ARCHIVE | H | — | Superseded version-tagged plan |
| `docs/_archive/README.md` | ARCHIVE | H | — | Archive directory index |
| `docs/_archive/studies/future_plans.md` | ARCHIVE | H | — | Superseded study |
| `docs/_archive/studies/news_sources_evaluation.md` | ARCHIVE | H | — | Superseded study |
| `docs/_archive/studies/polymarket_venue_integration_investigation.md` | ARCHIVE | H | — | Superseded study |
| `docs/_archive/studies/profit_cal_001_calibration_wiring.md` | ARCHIVE | H | — | Superseded study |
| `docs/_archive/studies/websocket_fix.md` | ARCHIVE | H | — | Superseded study |
| `docs/EDGE_STATUS.md` | TRACKING_STATUS | H | — | Operator-facing edge dashboard |
| `docs/evidence_store_schema.md` | SCHEMA | H | — | Evidence store schema definition |
| `docs/governance/2026-05-03-claude-commit-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-03-claude-commits-4a7cc38-fee5003-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-03-claude-commits-56d641e-9e2fffa-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-03-claude-commits-728f3bd-cfc0b60-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-03-claude-commits-c5cbc6f-90c26cf-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-03-claude-commits-cdbf6ef-f786246-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-03-claude-latest-commits-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-03-cross-series-headline-overlap-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-cumulative-call-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-day-3-pending-mid-soak-confirmation.md` | TRACKING_STATUS | H | — | Soak day status snapshot |
| `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-2.md` | TRACKING_STATUS | H | — | Soak day status snapshot |
| `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-3.md` | TRACKING_STATUS | H | — | Soak day status snapshot |
| `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-4.md` | TRACKING_STATUS | H | — | Soak day status snapshot |
| `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation.md` | TRACKING_STATUS | H | — | Soak day status snapshot |
| `docs/governance/2026-05-03-edge004-lever-d-pre-llm-gate-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-empty-response-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-g1-admittance-counterfactual.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-h5-semantic-bisection.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-lever-e-source-corroboration-sizing.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-match001-bprime-anchor-sizing.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-match001-bprime-false-negative-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-match001-bprime-false-suppression-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-match001-tokenization-equivalence-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-matcher-jaccard-threshold-bisection.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-mid-soak-health-report.md` | TRACKING_STATUS | H | — | Mid-soak health status report |
| `docs/governance/2026-05-03-mid-soak-snapshot-2.md` | TRACKING_STATUS | H | — | Soak snapshot |
| `docs/governance/2026-05-03-mid-soak-snapshot-3.md` | TRACKING_STATUS | H | — | Soak snapshot |
| `docs/governance/2026-05-03-mid-soak-snapshot-4.md` | TRACKING_STATUS | H | — | Soak snapshot |
| `docs/governance/2026-05-03-mid-soak-snapshot-5.md` | TRACKING_STATUS | H | — | Soak snapshot |
| `docs/governance/2026-05-03-obs003-kill-attribution.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-obs003-skipped-synthesizer-reality-check.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-post-soak-landing-simulation.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-post-soak-spec-adversarial-review.md` | AUDIT_REVIEW | H | — | Independent adversarial spec review |
| `docs/governance/2026-05-03-source-class-diversification-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-03-wave1-acceptance-ladder-forecast.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-04-claude-commits-681ceb9-2bf3da1-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-04-claude-latest-five-commits-adversarial-review-legal-cycle.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-04-day-4-mid-soak-confirmation.md` | TRACKING_STATUS | H | — | Soak day-4 confirmation snapshot |
| `docs/governance/2026-05-04-day-4-mid-soak-sanity-check.md` | TRACKING_STATUS | H | — | Soak day-4 sanity check |
| `docs/governance/2026-05-04-day-5-cycle-sanity-check-pending.md` | TRACKING_STATUS | H | — | Soak day-5 status placeholder |
| `docs/governance/2026-05-04-legal-niche-probe-order-domain-overlap.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-04-lever-a1-plus-specialist-analyst-domain-normalized-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-04-match001-bprime-spec-parity-verification.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-04-pre-wave1-source-class-ev-baseline.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-04-vitallaw-aggregator-path-forensics.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-04-vitallaw-archive-forensics.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` | PLAN | H | — | Forward-looking feed selection rubric |
| `docs/governance/2026-05-05-branch-d-fire-procedure-runbook.md` | RUNBOOK | H | — | Step-by-step fire procedure |
| `docs/governance/2026-05-05-changelog-drift-check.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-claude-latest-six-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-05-cross-cycle-contract-adherence-review.md` | AUDIT_REVIEW | H | — | IC adherence cross-cycle review |
| `docs/governance/2026-05-05-cross-set-adversarial-review-legal-doc-cycle.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-05-day-5-cycle-sanity-check-canonical.md` | TRACKING_STATUS | H | — | Soak day-5 canonical status |
| `docs/governance/2026-05-05-day-5-cycle-sanity-check-refresh.md` | TRACKING_STATUS | H | — | Soak day-5 status refresh |
| `docs/governance/2026-05-05-day-5-cycle-sanity-check.md` | TRACKING_STATUS | H | — | Soak day-5 sanity check |
| `docs/governance/2026-05-05-day-7-attestation-prestage.md` | MIXED | H | PLAN | Pre-staged attestation with embedded planning checklist |
| `docs/governance/2026-05-05-day-7-fire-time-compact-checklist.md` | RUNBOOK | H | — | Step-by-step fire-time procedure |
| `docs/governance/2026-05-05-day-7-to-wave-1-handoff-procedure.md` | RUNBOOK | H | — | Step-by-step handoff procedure |
| `docs/governance/2026-05-05-day-7-walkthrough-dry-trace.md` | AUDIT_REPORT | H | — | Dry-trace walkthrough audit |
| `docs/governance/2026-05-05-db-backup-gap-resolution.md` | AUDIT_REPORT | M | — | Gap resolution analysis |
| `docs/governance/2026-05-05-debt-log-toc-navigation-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-doc-cross-link-integrity-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-doc-index-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-doc-index-cleanup-execution-plan.md` | PLAN | H | — | Doc index cleanup execution plan |
| `docs/governance/2026-05-05-edge004-tldr-doc-quality-review.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-gate6-manual-review-tool-status.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-governance-monitor-preload-verification.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-implementation-contract-cycle-4-5-review.md` | AUDIT_REVIEW | H | — | IC adherence review cycles 4–5 |
| `docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md` | RUNBOOK | H | — | Step-by-step kill-switch procedure |
| `docs/governance/2026-05-05-latest-5plus5-adversarial-review.md` | AUDIT_REVIEW | H | — | Adversarial review of Claude commits |
| `docs/governance/2026-05-05-launchd-plist-consolidation-decision.md` | PLAN | H | — | Forward-looking consolidation decision |
| `docs/governance/2026-05-05-launchd-plist-drift-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-legal-aggregator-coverage-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-lever-d-nomenclature-cleanup-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-mac-studio-dead-bot-reboot-runbook.md` | RUNBOOK | H | — | Step-by-step reboot procedure |
| `docs/governance/2026-05-05-macbook-import-archive-procedure.md` | RUNBOOK | H | — | Step-by-step import procedure |
| `docs/governance/2026-05-05-memory-hygiene-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-network-api-outage-runbook.md` | RUNBOOK | H | — | Step-by-step outage procedure |
| `docs/governance/2026-05-05-next-soak-readiness-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-openclaw-orchestrator-integration-note.md` | PLAN | H | — | Forward-looking integration plan |
| `docs/governance/2026-05-05-pre-wave1-baseline-interpretation.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-pre-wave1-cooldown-distribution-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-pre-wave1-opportunity-age-distribution.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-pre-wave1-skipped-rate-baseline.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-pre-wave1-trade-rate-per-ticker-baseline.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-pre-wave1-version-bump-dry-run.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-daily-trend-baseline.md` | AUDIT_REPORT | M | — | Point-in-time trend measurement |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-llm-throughput-baseline.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-parse-error-retroactive-findings.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-source-class-evolution-baseline.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-readme-drift-check.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-roadmap-drift-check.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-rollback-runbook-validation.md` | AUDIT_REPORT | H | — | Runbook dry-trace validation |
| `docs/governance/2026-05-05-vitallaw-direct-rss-probe.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md` | RUNBOOK | H | — | Per-commit acceptance procedure |
| `docs/governance/2026-05-05-wave-1-deploy-day-timing.md` | PLAN | H | — | Deploy-day timing recommendation |
| `docs/governance/2026-05-05-wave-1-deploy-dry-run-report.md` | AUDIT_REPORT | H | — | Deploy dry-run measurement |
| `docs/governance/2026-05-05-wave-1-fire-time-per-commit-checklist.md` | RUNBOOK | H | — | Step-by-step per-commit checklist |
| `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` | PLAN | H | — | Operator decision input table |
| `docs/governance/2026-05-05-wave-2-decision-flow.md` | RUNBOOK | H | — | Step-by-step deploy decision flow |
| `docs/governance/2026-05-05-wave-2-deploy-day-timing.md` | PLAN | H | — | Deploy-day timing recommendation |
| `docs/governance/2026-05-05-wave-2-fire-time-per-commit-checklist.md` | RUNBOOK | H | — | Step-by-step per-commit checklist |
| `docs/governance/2026-05-05-wave-3-decision-flow.md` | RUNBOOK | H | — | Step-by-step deploy decision flow |
| `docs/governance/2026-05-05-wave-3-deploy-day-timing.md` | PLAN | H | — | Deploy-day timing recommendation |
| `docs/governance/2026-05-05-wave-3-fire-time-per-commit-checklist.md` | RUNBOOK | H | — | Step-by-step per-commit checklist |
| `docs/governance/2026-05-05-wave1-commit-order-risk-audit.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-05-wave1-six-commit-dryrun-review-status.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-06-changelog-drift-check-cycle-10.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-06-cycle-10-blocker-resolution.md` | AUDIT_REPORT | H | — | Blocker resolution analysis |
| `docs/governance/2026-05-06-cycle-11-pre-wave1-final-cleanup.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-06-cycle-12-replay-readiness-inventory.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-06-cycle-13-live-api-coordination.md` | PLAN | H | — | Live API coordination plan |
| `docs/governance/2026-05-06-cycle-13-replay-harness-code-review.md` | AUDIT_REVIEW | H | — | Independent code review |
| `docs/governance/2026-05-06-cycle-13-replay-scope-expansion-charter.md` | CHARTER | H | — | Cycle charter with acceptance criteria |
| `docs/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md` | CHARTER | H | — | Cycle charter with acceptance criteria |
| `docs/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` | AUDIT_REPORT | H | — | Candidate-site trace analysis |
| `docs/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` | CHARTER | H | — | Cycle charter with acceptance criteria |
| `docs/governance/2026-05-06-cycle-15b-claude-independent-trace-read.md` | AUDIT_REVIEW | H | — | Independent trace read review |
| `docs/governance/2026-05-06-cycle-15b-l7-reingestion-atomicity-review.md` | AUDIT_REVIEW | H | — | Independent atomicity review |
| `docs/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` | AUDIT_REPORT | H | — | Cohort analysis note |
| `docs/governance/2026-05-06-cycle-15b-pre-execution-criteria-verification.md` | AUDIT_REVIEW | H | — | Pre-execution criteria verification |
| `docs/governance/2026-05-06-cycle-15b-task-split.md` | MIXED | H | PLAN | Charter scope combined with task assignment |
| `docs/governance/2026-05-06-cycle-7-cross-cycle-contract-adherence-update.md` | AUDIT_REVIEW | H | — | IC adherence cross-cycle review update |
| `docs/governance/2026-05-06-doc-xref-audit-report.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-06-doc-xref-audit-triage.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-06-gate-6-capacity-resolution-plan.md` | PLAN | H | — | Gate-6 capacity resolution plan |
| `docs/governance/2026-05-06-gate-6-path-1-fire-time-template.md` | RUNBOOK | H | — | Fire-time operator template |
| `docs/governance/2026-05-06-implementation-contract-cycle-6-7-review.md` | AUDIT_REVIEW | H | — | IC adherence review cycles 6–7 |
| `docs/governance/2026-05-06-post-obs003-skipped-attribution-audit-refresh.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-06-pre-cycle-14-codex-queue.md` | PLAN | H | — | Pre-cycle Codex task queue |
| `docs/governance/2026-05-06-pre-day7-dry-run-rehearsal.md` | AUDIT_REPORT | H | — | Dry-run rehearsal analysis |
| `docs/governance/2026-05-06-pre-wave1-deploy-dry-run-rehearsal.md` | AUDIT_REPORT | H | — | Dry-run rehearsal analysis |
| `docs/governance/2026-05-06-pre-wave1-risk-register.md` | AUDIT_REPORT | H | — | Point-in-time risk analysis |
| `docs/governance/2026-05-06-pre-wave1-source-of-truth-doc-index.md` | README | M | — | Pre-wave1 source-of-truth doc index |
| `docs/governance/2026-05-06-soak-runtime-characterization-summary.md` | AUDIT_REPORT | M | — | Soak runtime characterization |
| `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` | PLAN | H | — | Strategic redirect decision |
| `docs/governance/2026-05-06-wave-2-deploy-ready-state-assessment.md` | AUDIT_REVIEW | H | — | Deploy-ready state assessment review |
| `docs/governance/2026-05-07-cycle-16d-charter-price-reconstruction.md` | CHARTER | H | — | Cycle charter with acceptance criteria |
| `docs/governance/2026-05-07-cycle-16d-m3-fetch-code-review.md` | AUDIT_REVIEW | H | — | Independent code review |
| `docs/governance/2026-05-07-cycle-16d-m7-coverage-acceptance.md` | AUDIT_REVIEW | H | — | Coverage acceptance review |
| `docs/governance/2026-05-07-cycle-16d-pre-execution-criteria-verification.md` | AUDIT_REVIEW | H | — | Pre-execution criteria verification |
| `docs/governance/2026-05-07-cycle-16d-price-source-inventory.md` | AUDIT_REPORT | H | — | Price source inventory |
| `docs/governance/2026-05-07-cycle-16d-task-split.md` | MIXED | H | PLAN | Charter scope combined with task assignment |
| `docs/governance/2026-05-07-cycle-16e-claude-rescope-and-review.md` | AUDIT_REVIEW | H | — | Consolidated rescope and review |
| `docs/governance/2026-05-07-cycle-16e-scorer-forensics-charter.md` | CHARTER | H | — | Cycle charter with acceptance criteria |
| `docs/governance/2026-05-07-cycle-16e-task-split.md` | MIXED | H | PLAN | Charter scope combined with task assignment |
| `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` | CHARTER | H | — | Cycle charter with acceptance criteria |
| `docs/governance/2026-05-07-cycle-17c-e1-claude-verdict-appendix.md` | MIXED | M | PLAN | Verdict appendix with E2 recommendation plan |
| `docs/governance/2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md` | CHARTER | H | — | Criteria lock charter |
| `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` | SCHEMA | H | — | Experiment ledger schema definition |
| `docs/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md` | AUDIT_REPORT | M | — | First-axis pick rationale analysis |
| `docs/governance/2026-05-07-day-7-pre-soak-confirmation.md` | TRACKING_STATUS | H | — | Day-7 pre-soak confirmation snapshot |
| `docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md` | AUDIT_REPORT | H | — | Point-in-time measurement output |
| `docs/governance/2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md` | PLAN | H | — | Hypothesis sketch and next-axis plan |
| `docs/governance/cycle-14-post-verdict-action-checklist.md` | MIXED | M | PLAN | Verdict outcomes with forward action checklist |
| `docs/governance/cycle-15-conditional-charter-skeletons.md` | CHARTER | H | — | Conditional charter skeletons |
| `docs/governance/cycle-15b-post-verdict-action-checklist.md` | MIXED | M | PLAN | Verdict outcomes with forward action checklist |
| `docs/governance/cycle-16-conditional-charter-skeletons.md` | CHARTER | H | — | Conditional charter skeletons |
| `docs/governance/cycle-16d-post-verdict-action-checklist.md` | MIXED | M | PLAN | Verdict outcomes with forward action checklist |
| `docs/governance/cycle-17-conditional-charter-skeletons.md` | CHARTER | H | — | Conditional charter skeletons |
| `docs/governance/edge-004-closure-path-tldr-v3.md` | TRACKING_STATUS | M | — | Closure-path status summary v3 |
| `docs/governance/edge-004-closure-path-tldr.md` | TRACKING_STATUS | M | — | Closure-path status summary |
| `docs/governance/edge-replay-cycle12-report.md` | AUDIT_REPORT | H | — | Edge replay cycle report |
| `docs/governance/edge-replay-cycle13-report.md` | AUDIT_REPORT | H | — | Edge replay cycle report |
| `docs/governance/edge-replay-cycle14-diagnosis.md` | AUDIT_REPORT | H | — | Edge replay diagnosis |
| `docs/governance/edge-replay-cycle15b-report.md` | AUDIT_REPORT | H | — | Edge replay cycle report |
| `docs/governance/edge-replay-cycle15b-sub-fix-proposal.md` | PLAN | H | — | Sub-fix proposal and action plan |
| `docs/governance/edge-replay-cycle16d-report.md` | AUDIT_REPORT | H | — | Edge replay cycle report |
| `docs/governance/edge-replay-cycle16e-scorer-forensics.md` | AUDIT_REPORT | H | — | Scorer forensics analysis |
| `docs/governance/edge-replay-cycle17c-e1-report.md` | AUDIT_REPORT | H | — | Edge replay cycle report |
| `docs/governance/edge-replay-pivot-playbook.md` | RUNBOOK | H | — | Strategic-pivot diagnostic playbook |
| `docs/governance/PHASE2_RUNBOOK.md` | RUNBOOK | H | — | Phase 2 operator runbook |
| `docs/governance/post-edge-004-escalation-paths.md` | PLAN | M | — | Post-EDGE-004 escalation planning |
| `docs/governance/post-soak-close-rehearsal-checklist.md` | RUNBOOK | H | — | Step-by-step close rehearsal |
| `docs/governance/post-soak-rollback-runbook.md` | RUNBOOK | H | — | Step-by-step rollback procedure |
| `docs/governance/PROFIT-PHASE2-001-close-day-decision-flow.md` | RUNBOOK | H | — | Step-by-step close-day flow |
| `docs/governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` | RUNBOOK | H | — | Step-by-step day-7 walkthrough |
| `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` | MIXED | H | TRACKING_STATUS | Reusable template with live tracking fields |
| `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` | TRACKING_STATUS | H | — | Completed early-close attestation |
| `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` | CHARTER | H | — | Early-close criteria charter |
| `docs/governance/PROFIT-PHASE2-002-onboarding.md` | DESIGN_SPEC | H | — | Phase 2 onboarding specification |
| `docs/governance/README.md` | README | H | — | Governance operator manual |
| `docs/governance/wave-1-changelog-entry-prestaged.md` | MIXED | H | TRACKING_LEDGER | Pre-staged changelog with deployment notes |
| `docs/governance/wave-1-commit-messages-prestaged.md` | MIXED | H | TRACKING_LEDGER | Pre-staged commit messages with deploy procedure |
| `docs/governance/wave-1-deploy-commit-order-decision.md` | PLAN | H | — | Commit order decision |
| `docs/governance/wave-1-post-deploy-observation-plan.md` | PLAN | H | — | Post-deploy observation plan |
| `docs/governance/wave-2-deploy-commit-order-decision.md` | PLAN | H | — | Commit order decision |
| `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` | MIXED | H | TRACKING_LEDGER | Pre-staged changelogs with deployment notes |
| `docs/housekeeping/2026-05-08/architecture.md` | AUDIT_REPORT | H | — | Architecture audit output |
| `docs/housekeeping/2026-05-08/claude-md-audit.md` | AUDIT_REPORT | H | — | CLAUDE.md rules audit output |
| `docs/housekeeping/2026-05-08/comment-health.md` | AUDIT_REPORT | H | — | Comment health audit output |
| `docs/housekeeping/2026-05-08/dead-code-inventory.md` | AUDIT_REPORT | H | — | Dead code inventory output |
| `docs/housekeeping/2026-05-08/open-questions-resolution.md` | AUDIT_REPORT | H | — | Open questions resolution output |
| `docs/housekeeping/2026-05-08/python-quality.md` | AUDIT_REPORT | H | — | Python quality audit output |
| `docs/housekeeping/2026-05-08/security-audit.md` | AUDIT_REPORT | H | — | Security audit output |
| `docs/housekeeping/2026-05-08/SUMMARY.md` | AUDIT_REPORT | H | — | Phase-1 housekeeping summary |
| `docs/housekeeping/2026-05-08/workspace-surface.md` | AUDIT_REPORT | H | — | Workspace surface audit output |
| `docs/housekeeping/2026-05-09/docs-consolidation/inventory.md` | AUDIT_REPORT | H | — | docs/ inventory measurement output |
| `docs/housekeeping/2026-05-09/docs-consolidation/tracking-purpose.md` | AUDIT_REPORT | H | — | Tracking purpose extraction analysis |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/context-cost.md` | AUDIT_REPORT | H | — | Context cost measurement output |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/drift-detection.md` | AUDIT_REPORT | H | — | Drift detection audit output |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/gap-analysis.md` | AUDIT_REPORT | H | — | Gap analysis audit output |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md` | AUDIT_REPORT | H | — | General quality audit output |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/reference-integrity.md` | AUDIT_REPORT | H | — | Reference integrity audit output |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/SUMMARY.md` | AUDIT_REPORT | H | — | Foundational docs audit summary |
| `docs/housekeeping/2026-05-09/guidance-conflicts/code-vs-doc-drift.md` | AUDIT_REPORT | H | — | Code-vs-doc drift audit output |
| `docs/housekeeping/2026-05-09/guidance-conflicts/intra-cluster-conflicts.md` | AUDIT_REPORT | H | — | Intra-cluster conflict audit output |
| `docs/housekeeping/2026-05-09/guidance-conflicts/SUMMARY.md` | AUDIT_REPORT | H | — | Guidance conflicts summary |
| `docs/housekeeping/2026-05-09/guidance-consolidation-plan.md` | PLAN | H | — | Guidance consolidation implementation plan |
| `docs/housekeeping/2026-05-09/guidance-discovery/auto-memory-extract.md` | AUDIT_REPORT | H | — | Auto-memory extraction audit output |
| `docs/housekeeping/2026-05-09/guidance-discovery/classification.md` | AUDIT_REPORT | H | — | Guidance file classification audit output |
| `docs/housekeeping/2026-05-09/guidance-discovery/inventory.md` | AUDIT_REPORT | H | — | Guidance file inventory audit output |
| `docs/housekeeping/2026-05-09/phase-3-design/oq1-analysis-purity-rename.md` | DESIGN_SPEC | H | — | OQ1 rename design specification |
| `docs/housekeeping/2026-05-09/phase-3-design/p1-02-signal-analyzer-decomposition.md` | DESIGN_SPEC | H | — | Signal analyzer decomposition design |
| `docs/housekeeping/2026-05-09/phase-3-design/p1-03-logger-typed-params.md` | DESIGN_SPEC | H | — | Logger typed-param refactor design |
| `docs/housekeeping/2026-05-09/phase-3-design/README.md` | README | H | — | Phase-3 design pass index |
| `docs/IMPLEMENTATION_CONTRACT.md` | CONTRACT | H | — | Binding multi-party implementation contract |
| `docs/profit_path_debt_log.md` | TRACKING_DEBT | H | — | Ongoing technical debt tracking log |
| `docs/ROADMAP.md` | TRACKING_ROADMAP | H | — | Versioned project roadmap |
| `docs/superpowers/plans/2026-04-24-governance-agent-phase-1-plan.md` | PLAN | H | — | Governance agent phase 1 plan |
| `docs/superpowers/plans/2026-04-25-governance-agent-phase-2-plan.md` | PLAN | H | — | Governance agent phase 2 plan |
| `docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md` | PLAN | H | — | Pipeline simulation buildout plan |
| `docs/superpowers/plans/2026-05-09-docs-directory-consolidation.md` | PLAN | H | — | docs/ directory consolidation plan |
| `docs/superpowers/plans/2026-05-09-guidance-consolidation-initiative.md` | PLAN | H | — | Guidance consolidation initiative plan |
| `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-a-source-class-diversification-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-e-multi-source-corroboration-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-match-001-tokenization-option-b-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-03-post-soak-landing-order-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-05-next-soak-cadence-tune-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` | DESIGN_SPEC | H | — | Technical design specification |
| `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md` | DESIGN_SPEC | H | — | Technical design specification |
