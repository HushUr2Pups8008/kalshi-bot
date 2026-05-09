# Consolidation Plan — docs/ Directory

**Phase:** 4 — Design (code-architect agent output)
**Date:** 2026-05-09
**Inputs:** classification.md (Phase 2), tracking-sources-analysis.md (Phase 3), one-doc-design.md (Phase 4 Step 1)
**User decisions applied:** Q1 (EDGE_STATUS MERGE+DELETE), Q2 (wave-* KEEP until Wave-3), Q3 (IC out of scope), Q4 (5 soak placeholders ARCHIVE), Q5 (TLDR files ARCHIVE), Q6 (12 MIXED KEEP)

---

## Batch Legend

| Batch | Label | Trigger |
|-------|-------|---------|
| B1 | Tracking merge + structure | Merge EDGE_STATUS, fold TLDR content, restructure debt log preamble |
| B2 | Soak placeholder archive | Move 5 Q4 placeholder files to archive |
| B3 | TLDR archive | Move 2 TLDR files to archive after B1 content fold |
| B4 | Nothing (KEEP as-is) | All KEEP files; no mutation |
| B5 | Out-of-scope | IMPLEMENTATION_CONTRACT, wave-* prestaged — excluded until post-Wave-3 |

Batches B1→B2→B3 are ordered by dependency. B4 and B5 require no execution.

---

## Per-File Action Table

| path | classification | action | target | rationale | batch |
|------|---------------|--------|--------|-----------|-------|
| `docs/_archive/README.md` | ARCHIVE | KEEP | — | Already in archive; no change | B4 |
| `docs/_archive/plans/v0.20.0_source_quality_feedback_loop.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.21.0_subreddit_discovery_loop.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.22.0_feedback_loop_closure.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.23.0_production_grade_fixes.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.24.0_live_safety_and_tests.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.26.0_fast_lane_source_prioritization.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.26.3_phase6b_shadow_eval_scoring.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.27.1_ollama_http_error_fix.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.27.2_llm_model_mismatch_fix.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.27.3_ollama_error_audit_script.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.27.4_llm_latency_observability.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.29.2_execution_mode_safety.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/plans/v0.29.4_test_log_isolation.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/studies/future_plans.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/studies/news_sources_evaluation.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/studies/polymarket_venue_integration_investigation.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/studies/profit_cal_001_calibration_wiring.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/_archive/studies/websocket_fix.md` | ARCHIVE | KEEP | — | Already archived | B4 |
| `docs/EDGE_STATUS.md` | TRACKING_STATUS | MERGE-INTO-ONE-DOC | `docs/profit_path_debt_log.md` §Current Status | Q1 decision: full content merges into new §Current Status, then `git rm`. Eliminates parallel tracking surface. | B1 |
| `docs/evidence_store_schema.md` | SCHEMA | KEEP | — | Schema definition; not tracking; out of scope | B4 |
| `docs/IMPLEMENTATION_CONTRACT.md` | CONTRACT | KEEP | — | Q3: out of scope entirely; IC §10 rules are distinct from debt log application | B5 |
| `docs/ROADMAP.md` | TRACKING_ROADMAP | KEEP | — | Canonical forward horizon; debt log references, does not duplicate | B4 |
| `docs/profit_path_debt_log.md` | TRACKING_DEBT | RESTRUCTURE | self | B1 target: add §Current Status, restructure preamble, add Consolidated-From row, eliminate zero-content separator, append R-10 | B1 |
| `docs/governance/2026-05-03-claude-commit-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence; evidence != tracking | B4 |
| `docs/governance/2026-05-03-claude-commits-4a7cc38-fee5003-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-claude-commits-56d641e-9e2fffa-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-claude-commits-728f3bd-cfc0b60-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-claude-commits-c5cbc6f-90c26cf-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-claude-commits-cdbf6ef-f786246-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-claude-latest-commits-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-cross-series-headline-overlap-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-cumulative-call-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-day-3-pending-mid-soak-confirmation.md` | TRACKING_STATUS | ARCHIVE | `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/` | Q4: superseded soak placeholder; live state now tracked in debt log PROFIT-PHASE2-001 | B2 |
| `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation.md` | TRACKING_STATUS | ARCHIVE | `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/` | Q4: superseded soak placeholder | B2 |
| `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-2.md` | TRACKING_STATUS | ARCHIVE | `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/` | Q4: superseded soak placeholder | B2 |
| `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-3.md` | TRACKING_STATUS | ARCHIVE | `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/` | Q4: superseded soak placeholder | B2 |
| `docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-4.md` | TRACKING_STATUS | ARCHIVE | `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/` | Q4: superseded soak placeholder | B2 |
| `docs/governance/2026-05-03-edge004-lever-d-pre-llm-gate-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-edge004-wave2-expected-state-ladder.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-empty-response-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-g1-admittance-counterfactual.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-h5-semantic-bisection.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-lever-e-source-corroboration-sizing.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-match001-bprime-anchor-sizing.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-match001-bprime-false-negative-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-match001-bprime-false-suppression-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-match001-tokenization-equivalence-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-matcher-jaccard-threshold-bisection.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-mid-soak-health-report.md` | TRACKING_STATUS | KEEP | — | Historical soak evidence; not a live tracking surface | B4 |
| `docs/governance/2026-05-03-mid-soak-snapshot-2.md` | TRACKING_STATUS | KEEP | — | Historical soak evidence | B4 |
| `docs/governance/2026-05-03-mid-soak-snapshot-3.md` | TRACKING_STATUS | KEEP | — | Historical soak evidence | B4 |
| `docs/governance/2026-05-03-mid-soak-snapshot-4.md` | TRACKING_STATUS | KEEP | — | Historical soak evidence | B4 |
| `docs/governance/2026-05-03-mid-soak-snapshot-5.md` | TRACKING_STATUS | KEEP | — | Historical soak evidence | B4 |
| `docs/governance/2026-05-03-obs003-kill-attribution.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-obs003-skipped-synthesizer-reality-check.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-post-soak-landing-simulation.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-post-soak-spec-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-source-class-diversification-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-03-wave1-acceptance-ladder-forecast.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-claude-commits-681ceb9-2bf3da1-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-claude-latest-five-commits-adversarial-review-legal-cycle.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-day-4-mid-soak-confirmation.md` | TRACKING_STATUS | KEEP | — | Completed canonical soak confirmation (not a placeholder); retain as evidence | B4 |
| `docs/governance/2026-05-04-day-4-mid-soak-sanity-check.md` | TRACKING_STATUS | KEEP | — | Completed canonical soak evidence | B4 |
| `docs/governance/2026-05-04-day-5-cycle-sanity-check-pending.md` | TRACKING_STATUS | KEEP | — | Historical soak state evidence | B4 |
| `docs/governance/2026-05-04-legal-niche-probe-order-domain-overlap.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-lever-a1-plus-specialist-analyst-domain-normalized-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-match001-bprime-spec-parity-verification.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-pre-wave1-source-class-ev-baseline.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-vitallaw-aggregator-path-forensics.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-04-vitallaw-archive-forensics.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` | PLAN | KEEP | — | Active forward-looking spec; Wave-2 dependency | B4 |
| `docs/governance/2026-05-05-branch-d-fire-procedure-runbook.md` | RUNBOOK | KEEP | — | Operational runbook; not tracking | B4 |
| `docs/governance/2026-05-05-changelog-drift-check.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-claude-latest-six-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-cross-cycle-contract-adherence-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-cross-set-adversarial-review-legal-doc-cycle.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-day-5-cycle-sanity-check-canonical.md` | TRACKING_STATUS | KEEP | — | Completed canonical soak evidence | B4 |
| `docs/governance/2026-05-05-day-5-cycle-sanity-check-refresh.md` | TRACKING_STATUS | KEEP | — | Historical soak evidence | B4 |
| `docs/governance/2026-05-05-day-5-cycle-sanity-check.md` | TRACKING_STATUS | KEEP | — | Historical soak evidence | B4 |
| `docs/governance/2026-05-05-day-7-attestation-prestage.md` | MIXED | KEEP | — | Q6: pre-staged attestation; active until soak closes | B4 |
| `docs/governance/2026-05-05-day-7-fire-time-compact-checklist.md` | RUNBOOK | KEEP | — | Operational runbook; not tracking | B4 |
| `docs/governance/2026-05-05-day-7-to-wave-1-handoff-procedure.md` | RUNBOOK | KEEP | — | Operational runbook; not tracking | B4 |
| `docs/governance/2026-05-05-day-7-walkthrough-dry-trace.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-db-backup-gap-resolution.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-debt-log-toc-navigation-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-doc-cross-link-integrity-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-doc-index-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-doc-index-cleanup-execution-plan.md` | PLAN | KEEP | — | Point-in-time plan; historical | B4 |
| `docs/governance/2026-05-05-edge004-tldr-doc-quality-review.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-gate6-manual-review-tool-status.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-governance-monitor-preload-verification.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-implementation-contract-cycle-4-5-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md` | RUNBOOK | KEEP | — | Operational runbook; not tracking | B4 |
| `docs/governance/2026-05-05-latest-5plus5-adversarial-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-launchd-plist-consolidation-decision.md` | PLAN | KEEP | — | Point-in-time decision; historical | B4 |
| `docs/governance/2026-05-05-launchd-plist-drift-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-legal-aggregator-coverage-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-lever-d-nomenclature-cleanup-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-mac-studio-dead-bot-reboot-runbook.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-macbook-import-archive-procedure.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-memory-hygiene-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-network-api-outage-runbook.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-next-soak-readiness-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-openclaw-orchestrator-integration-note.md` | PLAN | KEEP | — | Integration note; historical | B4 |
| `docs/governance/2026-05-05-pre-wave1-baseline-interpretation.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-pre-wave1-cooldown-distribution-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-pre-wave1-opportunity-age-distribution.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-pre-wave1-skipped-rate-baseline.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-pre-wave1-trade-rate-per-ticker-baseline.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-pre-wave1-version-bump-dry-run.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-daily-trend-baseline.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-llm-throughput-baseline.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-parse-error-retroactive-findings.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-PROFIT-PHASE2-001-source-class-evolution-baseline.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-readme-drift-check.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-roadmap-drift-check.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-rollback-runbook-validation.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-vitallaw-direct-rss-probe.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-wave-1-deploy-day-timing.md` | PLAN | KEEP | — | Deploy timing spec; active until Wave-1 deploys | B4 |
| `docs/governance/2026-05-05-wave-1-deploy-dry-run-report.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-wave-1-fire-time-per-commit-checklist.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` | PLAN | KEEP | — | Operator decision input; active until Cycle-17 decision | B4 |
| `docs/governance/2026-05-05-wave-2-decision-flow.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-wave-2-deploy-day-timing.md` | PLAN | KEEP | — | Deploy timing spec | B4 |
| `docs/governance/2026-05-05-wave-2-fire-time-per-commit-checklist.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-wave-3-decision-flow.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-wave-3-deploy-day-timing.md` | PLAN | KEEP | — | Deploy timing spec | B4 |
| `docs/governance/2026-05-05-wave-3-fire-time-per-commit-checklist.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-05-wave1-commit-order-risk-audit.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-05-wave1-six-commit-dryrun-review-status.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-changelog-drift-check-cycle-10.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-10-blocker-resolution.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-11-pre-wave1-final-cleanup.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-12-replay-readiness-inventory.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-13-live-api-coordination.md` | PLAN | KEEP | — | Active coordination note for live-API work | B4 |
| `docs/governance/2026-05-06-cycle-13-replay-harness-code-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-13-replay-scope-expansion-charter.md` | CHARTER | KEEP | — | Active-cycle charter; stays orthogonal until cycle close | B4 |
| `docs/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md` | CHARTER | KEEP | — | Closed cycle charter; retain as evidence | B4 |
| `docs/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` | CHARTER | KEEP | — | Closed cycle charter; retain as evidence | B4 |
| `docs/governance/2026-05-06-cycle-15b-claude-independent-trace-read.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-15b-l7-reingestion-atomicity-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-15b-pre-execution-criteria-verification.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-cycle-15b-task-split.md` | MIXED | KEEP | — | Q6: cycle task split + plan; closed cycle evidence | B4 |
| `docs/governance/2026-05-06-cycle-7-cross-cycle-contract-adherence-update.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-doc-xref-audit-report.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-doc-xref-audit-triage.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-gate-6-capacity-resolution-plan.md` | PLAN | KEEP | — | Active plan; Gate-6 capacity still open | B4 |
| `docs/governance/2026-05-06-gate-6-path-1-fire-time-template.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/2026-05-06-implementation-contract-cycle-6-7-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-post-obs003-skipped-attribution-audit-refresh.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-pre-cycle-14-codex-queue.md` | PLAN | KEEP | — | Historical codex queue; closed | B4 |
| `docs/governance/2026-05-06-pre-day7-dry-run-rehearsal.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-pre-wave1-deploy-dry-run-rehearsal.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-pre-wave1-risk-register.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-pre-wave1-source-of-truth-doc-index.md` | README | KEEP | — | Pre-wave1 doc index; historical | B4 |
| `docs/governance/2026-05-06-soak-runtime-characterization-summary.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` | PLAN | KEEP | — | Strategic decision record; referenced from EDGE_STATUS cross-links | B4 |
| `docs/governance/2026-05-06-wave-2-deploy-ready-state-assessment.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-07-cycle-16d-charter-price-reconstruction.md` | CHARTER | KEEP | — | Closed cycle charter; retain as evidence | B4 |
| `docs/governance/2026-05-07-cycle-16d-m3-fetch-code-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-07-cycle-16d-m7-coverage-acceptance.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-07-cycle-16d-pre-execution-criteria-verification.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-07-cycle-16d-price-source-inventory.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-07-cycle-16d-task-split.md` | MIXED | KEEP | — | Q6: cycle task split + plan; closed cycle evidence | B4 |
| `docs/governance/2026-05-07-cycle-16e-claude-rescope-and-review.md` | AUDIT_REVIEW | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-07-cycle-16e-scorer-forensics-charter.md` | CHARTER | KEEP | — | Closed cycle charter; retain as evidence | B4 |
| `docs/governance/2026-05-07-cycle-16e-task-split.md` | MIXED | KEEP | — | Q6: cycle task split + plan; closed cycle evidence | B4 |
| `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` | CHARTER | KEEP | — | Active cycle charter; stays orthogonal until cycle close | B4 |
| `docs/governance/2026-05-07-cycle-17c-e1-claude-verdict-appendix.md` | MIXED | KEEP | — | Q6: verdict appendix + E2 plan; active cycle evidence | B4 |
| `docs/governance/2026-05-07-cycle-17c-e1-criteria-lock-bayesian-log-odds.md` | CHARTER | KEEP | — | Active cycle criteria lock | B4 |
| `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` | SCHEMA | KEEP | — | Schema definition; not tracking | B4 |
| `docs/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-07-day-7-pre-soak-confirmation.md` | TRACKING_STATUS | KEEP | — | Active pre-soak confirmation pre-stage; soak still open | B4 |
| `docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md` | AUDIT_REPORT | KEEP | — | Point-in-time evidence | B4 |
| `docs/governance/2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md` | PLAN | KEEP | — | Active cycle planning doc | B4 |
| `docs/governance/cycle-14-post-verdict-action-checklist.md` | MIXED | KEEP | — | Q6: post-verdict checklist; closed cycle evidence | B4 |
| `docs/governance/cycle-15-conditional-charter-skeletons.md` | CHARTER | KEEP | — | Closed cycle skeletons; retain as evidence | B4 |
| `docs/governance/cycle-15b-post-verdict-action-checklist.md` | MIXED | KEEP | — | Q6: post-verdict checklist; closed cycle evidence | B4 |
| `docs/governance/cycle-16-conditional-charter-skeletons.md` | CHARTER | KEEP | — | Closed cycle skeletons; retain as evidence | B4 |
| `docs/governance/cycle-16d-post-verdict-action-checklist.md` | MIXED | KEEP | — | Q6: post-verdict checklist; closed cycle evidence | B4 |
| `docs/governance/cycle-17-conditional-charter-skeletons.md` | CHARTER | KEEP | — | Active cycle skeletons; stays until Cycle-17 closes | B4 |
| `docs/governance/edge-004-closure-path-tldr-v3.md` | TRACKING_STATUS | ARCHIVE | `docs/_archive/2026-05-09-docs-consolidation/` | Q5: key content folded into debt log §Current Status §2.3 in B1; then archive | B3 |
| `docs/governance/edge-004-closure-path-tldr.md` | TRACKING_STATUS | ARCHIVE | `docs/_archive/2026-05-09-docs-consolidation/` | Q5: superseded by v3; no content absorption needed | B3 |
| `docs/governance/edge-replay-cycle12-report.md` | AUDIT_REPORT | KEEP | — | Evidence; referenced from EDGE_STATUS §Replay verdict log | B4 |
| `docs/governance/edge-replay-cycle13-report.md` | AUDIT_REPORT | KEEP | — | Evidence; referenced from EDGE_STATUS §Replay verdict log | B4 |
| `docs/governance/edge-replay-cycle14-diagnosis.md` | AUDIT_REPORT | KEEP | — | Evidence; referenced from EDGE_STATUS §Replay verdict log | B4 |
| `docs/governance/edge-replay-cycle15b-report.md` | AUDIT_REPORT | KEEP | — | Evidence; referenced from EDGE_STATUS §Replay verdict log | B4 |
| `docs/governance/edge-replay-cycle15b-sub-fix-proposal.md` | PLAN | KEEP | — | Point-in-time plan; historical | B4 |
| `docs/governance/edge-replay-cycle16d-report.md` | AUDIT_REPORT | KEEP | — | Evidence; referenced from EDGE_STATUS §Replay verdict log | B4 |
| `docs/governance/edge-replay-cycle16e-scorer-forensics.md` | AUDIT_REPORT | KEEP | — | Evidence; referenced from EDGE_STATUS §Replay verdict log | B4 |
| `docs/governance/edge-replay-cycle17c-e1-report.md` | AUDIT_REPORT | KEEP | — | Evidence; current active cycle | B4 |
| `docs/governance/edge-replay-pivot-playbook.md` | RUNBOOK | KEEP | — | Operational runbook; referenced from EDGE_STATUS cross-links | B4 |
| `docs/governance/PHASE2_RUNBOOK.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/post-edge-004-escalation-paths.md` | PLAN | KEEP | — | Active escalation planning | B4 |
| `docs/governance/post-soak-close-rehearsal-checklist.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/post-soak-rollback-runbook.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/PROFIT-PHASE2-001-close-day-decision-flow.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` | RUNBOOK | KEEP | — | Operational runbook | B4 |
| `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` | MIXED | KEEP | — | Q6: reusable template; active until soak closes | B4 |
| `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` | TRACKING_STATUS | KEEP | — | Completed attestation document; soak evidence | B4 |
| `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` | CHARTER | KEEP | — | Active soak close-criteria charter | B4 |
| `docs/governance/PROFIT-PHASE2-002-onboarding.md` | DESIGN_SPEC | KEEP | — | Active design spec; Wave-2 dependency | B4 |
| `docs/governance/README.md` | README | KEEP | — | Governance operator manual index | B4 |
| `docs/governance/wave-1-changelog-entry-prestaged.md` | MIXED | KEEP | — | Q2 + Q6: wave-* prestaged; exclude until Wave-3 deploys | B5 |
| `docs/governance/wave-1-commit-messages-prestaged.md` | MIXED | KEEP | — | Q2 + Q6: wave-* prestaged; exclude until Wave-3 deploys | B5 |
| `docs/governance/wave-1-deploy-commit-order-decision.md` | PLAN | KEEP | — | Wave-1 deploy decision; active until Wave-1 ships | B4 |
| `docs/governance/wave-1-post-deploy-observation-plan.md` | PLAN | KEEP | — | Active observation plan | B4 |
| `docs/governance/wave-2-deploy-commit-order-decision.md` | PLAN | KEEP | — | Wave-2 deploy decision; active until Wave-2 ships | B4 |
| `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` | MIXED | KEEP | — | Q2 + Q6: wave-* prestaged; exclude until Wave-3 deploys | B5 |
| `docs/housekeeping/2026-05-08/architecture.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-08/claude-md-audit.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-08/comment-health.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-08/dead-code-inventory.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-08/open-questions-resolution.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-08/python-quality.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-08/security-audit.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-08/SUMMARY.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-08/workspace-surface.md` | AUDIT_REPORT | KEEP | — | Housekeeping audit evidence | B4 |
| `docs/housekeeping/2026-05-09/docs-consolidation/inventory.md` | AUDIT_REPORT | KEEP | — | This consolidation initiative evidence | B4 |
| `docs/housekeeping/2026-05-09/docs-consolidation/tracking-purpose.md` | AUDIT_REPORT | KEEP | — | This consolidation initiative evidence | B4 |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/context-cost.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/drift-detection.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/gap-analysis.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/reference-integrity.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/foundational-docs-audit/SUMMARY.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/guidance-conflicts/code-vs-doc-drift.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/guidance-conflicts/intra-cluster-conflicts.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/guidance-conflicts/SUMMARY.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/guidance-consolidation-plan.md` | AUDIT_REPORT | KEEP | — | Active guidance consolidation plan | B4 |
| `docs/housekeeping/2026-05-09/guidance-discovery/auto-memory-extract.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/guidance-discovery/classification.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/guidance-discovery/inventory.md` | AUDIT_REPORT | KEEP | — | Audit evidence | B4 |
| `docs/housekeeping/2026-05-09/phase-3-design/README.md` | AUDIT_REPORT | KEEP | — | Phase-3 design evidence | B4 |
| `docs/housekeeping/2026-05-09/phase-3-design/oq1-analysis-purity-rename.md` | AUDIT_REPORT | KEEP | — | Phase-3 design evidence | B4 |
| `docs/housekeeping/2026-05-09/phase-3-design/p1-02-signal-analyzer-decomposition.md` | AUDIT_REPORT | KEEP | — | Phase-3 design evidence | B4 |
| `docs/housekeeping/2026-05-09/phase-3-design/p1-03-logger-typed-params.md` | AUDIT_REPORT | KEEP | — | Phase-3 design evidence | B4 |
| `docs/superpowers/plans/2026-04-24-governance-agent-phase-1-plan.md` | PLAN | KEEP | — | Historical plan; evidence of v1 implementation | B4 |
| `docs/superpowers/plans/2026-04-25-governance-agent-phase-2-plan.md` | PLAN | KEEP | — | Historical plan; evidence of v2 implementation | B4 |
| `docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md` | PLAN | KEEP | — | Historical plan | B4 |
| `docs/superpowers/plans/2026-05-09-docs-directory-consolidation.md` | PLAN | KEEP | — | Active: parent plan for this initiative | B4 |
| `docs/superpowers/plans/2026-05-09-guidance-consolidation-initiative.md` | PLAN | KEEP | — | Active: parallel consolidation initiative | B4 |
| `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-a-source-class-diversification-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` | DESIGN_SPEC | KEEP | — | Active design spec; Wave-2 dependency | B4 |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-e-multi-source-corroboration-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-match-001-tokenization-option-b-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-03-post-soak-landing-order-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` | DESIGN_SPEC | KEEP | — | Active design spec stub | B4 |
| `docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-05-next-soak-cadence-tune-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |
| `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md` | DESIGN_SPEC | KEEP | — | Active design spec | B4 |

---

## Batches

### Batch 1 — Tracking merge + debt log restructure

**Dependency:** None (first batch; all others depend on B1 completing EDGE_STATUS deletion)

Steps (follow migration sequence in `one-doc-design.md` §5):
1. Add `## Current Status` skeleton to `profit_path_debt_log.md` at line 44
2. Merge full `docs/EDGE_STATUS.md` content into `## Current Status`; `git rm docs/EDGE_STATUS.md`
3. Fold lever map + closure criteria + honest-read from `docs/governance/edge-004-closure-path-tldr-v3.md` into `§2.3 EDGE-004 Closure Path`
4. Apply preamble restructure (replace lines 1-7 with verbatim header from `one-doc-design.md §4`)
5. Add `Consolidated From` row to `## Header / Metadata` table
6. Eliminate `## Full Technical Debt Log` separator (lines 45-48 of original file)
7. Append R-10 to `## Operating Rules`

**Files mutated:** `docs/profit_path_debt_log.md` (restructured), `docs/EDGE_STATUS.md` (deleted)
**Net lines delta:** ~+144 lines to debt log; -104 lines (EDGE_STATUS deleted)

### Batch 2 — Soak placeholder archive

**Dependency:** None (independent of B1)

Move Q4 files using `git mv`:
```
docs/governance/2026-05-03-day-3-pending-mid-soak-confirmation.md
docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation.md
docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-2.md
docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-3.md
docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-4.md
```
Target: `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/`
Add archive pointer in PROFIT-PHASE2-001 entry in `profit_path_debt_log.md`.

**Files mutated:** 5 files moved; 1 line added to debt log

### Batch 3 — TLDR archive

**Dependency:** B1 must complete (content fold into §2.3 must happen before archive)

```
git mv docs/governance/edge-004-closure-path-tldr-v3.md docs/_archive/2026-05-09-docs-consolidation/
git mv docs/governance/edge-004-closure-path-tldr.md docs/_archive/2026-05-09-docs-consolidation/
```

**Files mutated:** 2 files moved

### Batch 4 — No-op (KEEP)

258 files with action KEEP require no mutation. This batch exists only to confirm the scope boundary.

### Batch 5 — Out-of-scope (deferred)

3 wave-* prestaged files excluded per Q2 until Wave-3 deploys:
- `docs/governance/wave-1-changelog-entry-prestaged.md`
- `docs/governance/wave-1-commit-messages-prestaged.md`
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md`

`docs/IMPLEMENTATION_CONTRACT.md` excluded per Q3.

---

## Action Summary

| Action | Count | Bytes removed from active docs/ | Notes |
|--------|-------|--------------------------------|-------|
| MERGE-INTO-ONE-DOC + DELETE | 1 | 10,277 (EDGE_STATUS) | Absorbed into debt log |
| MERGE-INTO-ONE-DOC (content fold only) | 1 | ~4,000 (TLDR v3 key content) | File moves to archive in B3 |
| ARCHIVE | 7 | ~18,127 (5 soak placeholders + 2 TLDRs) | Moved to `_archive` |
| RESTRUCTURE | 1 | net +13,450 | debt log gains §Current Status |
| KEEP | 258 | — | No change |
| KEEP (B5 deferred) | 4 | — | Wave-* + IC excluded |
| **Total mutated** | **10 files** | **~28,000 bytes net removed from active tracking** | |
