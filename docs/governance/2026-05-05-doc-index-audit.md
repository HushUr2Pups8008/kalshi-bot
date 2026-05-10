# 2026-05-05 documentation index audit

**Type:** read-only inventory + cleanup recommendation (Claude task per Implementation Contract §9 — review).
**Scope:** `docs/governance/` (87 files) + `docs/superpowers/specs/` (20 files) + top-level `docs/` (4 files).
**Audience:** operator considering post-Wave-1 doc-housekeeping. **Not blocking; advisory.**

## TL;DR

Doc surface is **organic but not chaotic**. 6 superseded artifacts safely consolidatable; 12 mid-soak-confirmation reports retainable as audit-trail (don't delete); 5 adversarial-review docs are this-cycle-disposable. **Recommended cleanup: ~11 files moved to an `archive/` subdir; no deletions.**

## Inventory by category

### A. Operator-facing live docs (KEEP, all current)

- `governance/PHASE2_RUNBOOK.md` — daily monitoring runbook
- `governance/post-soak-close-rehearsal-checklist.md` — Wave-1 deploy plan
- `governance/post-soak-rollback-runbook.md` — incident response
- `governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` — close playbook
- `governance/PROFIT-PHASE2-001-close-day-decision-flow.md` — close flowchart
- `governance/PROFIT-PHASE2-001-early-close-criteria.md` — gate criteria
- `governance/PROFIT-PHASE2-001-early-close-attestation-template.md` — attestation template
- `governance/PROFIT-PHASE2-002-onboarding.md` — next-soak setup (this cycle)
- `governance/post-edge-004-escalation-paths.md` — Branch D companion
- `governance/wave-1-changelog-entry-prestaged.md` — Wave-1 changelog
- `governance/wave-2-wave-3-changelog-entries-prestaged.md` — Wave-2/3 changelog
- `governance/wave-1-deploy-commit-order-decision.md` — locked commit order
- `governance/wave-1-post-deploy-observation-plan.md` — 14-row regression watch (this cycle)
- `governance/edge-004-closure-path-tldr.md` — v2.2 (superseded; see §B)
- `governance/edge-004-closure-path-tldr-v3.md` — v3 (this cycle; current)
- `governance/README.md` — directory index

### B. Closure-path TLDR versioning — CONSOLIDATE

`edge-004-closure-path-tldr.md` (v2.2) is superseded by `edge-004-closure-path-tldr-v3.md` (this cycle). **Recommendation:** rename v2.2 → `archive/edge-004-closure-path-tldr-v2.2-2026-05-05.md`; rename v3 → `edge-004-closure-path-tldr.md` (drop the version suffix; only the latest gets the canonical name). Reduces operator confusion ("which one is current?"). Cross-link integrity: only the closure-path-TLDR is referenced from other files; redirect those references to the canonical name.

### C. Mid-soak confirmation reports (KEEP — audit trail)

12 files of pattern `2026-05-03-day-*-pending-mid-soak-confirmation*.md`, `2026-05-04-day-*-mid-soak-*.md`, `2026-05-03-mid-soak-snapshot-*.md`, `2026-05-04-day-5-cycle-sanity-check*.md`, `2026-05-05-day-5-cycle-sanity-check*.md`. These document the 1-per-day soak confirmation cadence (per memory `feedback_soak_confirmation_cadence.md`). 

**Recommendation:** retain as-is. They form the post-hoc evidence trail for the soak's gate-3/gate-4 cleanliness. Operator should NOT delete pre-Wave-1 close. Post-close, can be moved to `archive/mid-soak-confirmations/` as a folder.

### D. Adversarial-review docs (PARTIAL ARCHIVE)

| file | status | recommendation |
|---|---|---|
| `docs/_archive/governance/2026-05-03-claude-commit-adversarial-review.md` | superseded by later reviews | ARCHIVED Stream G R3 |
| `docs/_archive/governance/2026-05-03-claude-commits-4a7cc38-fee5003-adversarial-review.md` | resolved | ARCHIVED Stream G R3 |
| `docs/_archive/governance/2026-05-03-claude-commits-56d641e-9e2fffa-adversarial-review.md` | resolved | ARCHIVED Stream G R3 |
| `docs/_archive/governance/2026-05-03-claude-commits-728f3bd-cfc0b60-adversarial-review.md` | resolved | ARCHIVED Stream G R3 |
| `docs/_archive/governance/2026-05-03-claude-commits-c5cbc6f-90c26cf-adversarial-review.md` | resolved | ARCHIVED Stream G R17 |
| `docs/_archive/governance/2026-05-03-claude-commits-cdbf6ef-f786246-adversarial-review.md` | resolved | ARCHIVED Stream G R17 |
| `docs/_archive/governance/2026-05-03-claude-latest-commits-adversarial-review.md` | resolved | ARCHIVED Stream G R17 |
| `docs/_archive/governance/2026-05-03-post-soak-spec-adversarial-review.md` | resolved | ARCHIVED Stream G R3 |
| `docs/_archive/governance/2026-05-04-claude-commits-681ceb9-2bf3da1-adversarial-review.md` | resolved | ARCHIVED Stream G R3 |
| `docs/_archive/governance/2026-05-04-claude-latest-five-commits-adversarial-review-legal-cycle.md` | resolved | ARCHIVED Stream G R14 |
| `docs/_archive/governance/2026-05-05-claude-latest-six-adversarial-review.md` | resolved | ARCHIVED Stream G R3 |
| `docs/_archive/governance/2026-05-05-cross-set-adversarial-review-legal-doc-cycle.md` | resolved | ARCHIVED Stream G R3 |
| `docs/_archive/governance/2026-05-05-latest-5plus5-adversarial-review.md` | resolved | ARCHIVED Stream G R3 |

13 files, all post-resolution. **Recommendation:** move all to `archive/adversarial-reviews/`. Cross-link integrity: most adversarial-review docs aren't cross-referenced from operator-facing docs.

### E. Forensics + audit reports (KEEP — empirical evidence anchors)

50+ files of pattern `2026-05-0*-{forensics,audit,counterfactual,sizing,bisection}-*.md`. Each is a load-bearing empirical anchor cited by the lever specs / closure-path TLDR. **Recommendation:** retain in current location. These are the "why does Lever B's 0.04 floor make sense?" / "why does Lever C's §3.2 hash fire on 49.2 % of headlines?" evidence chain.

Examples NOT to archive:
- `2026-05-03-cross-series-headline-overlap-audit.md` — Lever C sizing anchor
- `2026-05-03-g1-admittance-counterfactual.md` — Lever B sizing anchor
- `2026-05-03-lever-a1-source-classifier-counterfactual.md` — Lever A sizing
- `2026-05-04-vitallaw-aggregator-path-forensics.md` — A.1+ Branch B kill anchor
- `2026-05-05-vitallaw-direct-rss-probe.md` — same
- `2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` — Wave-1 lift forecast

### F. Day-7-prep docs (THIS-CYCLE; KEEP)

- `2026-05-05-day-7-attestation-prestage.md` (this cycle)
- `2026-05-05-day-7-walkthrough-dry-trace.md` (this cycle)
- `2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md`
- `2026-05-05-rollback-runbook-validation.md` (this cycle)
- `2026-05-05-wave-2-a1plus-branch-decision-table.md` (this cycle)
- `docs/_archive/governance/2026-05-05-wave1-commit-order-risk-audit.md` (ARCHIVED Stream G R4)
- `docs/_archive/governance/2026-05-05-wave1-six-commit-dryrun-review-status.md` (ARCHIVED Stream G R4)
- `docs/_archive/governance/2026-05-05-pre-wave1-version-bump-dry-run.md` (ARCHIVED Stream G R14)
- `2026-05-05-wave-1-deploy-dry-run-report.md`
- `docs/_archive/governance/2026-05-05-next-soak-readiness-audit.md` (ARCHIVED Stream G R4)
- `docs/_archive/governance/2026-05-05-edge004-tldr-doc-quality-review.md` (ARCHIVED Stream G R4)

11 active artifacts; all referenced by Day-7 close playbook or this-cycle documentation. KEEP all.

### G. Specs (`docs/superpowers/specs/`) — all KEEP

20 files; all current. Lever spec lineage:
- Lever A umbrella + A.1 + A.1+ + A.1+1.5
- Lever B parent + 0.04 LOCK addendum (this cycle)
- Lever C parent + v1 LOCK addendum (this cycle)
- Lever D escalation criteria (this cycle)
- Lever E parent (closed; KEEP for closure-rationale audit trail)
- Lever menu parent
- EXEC-002 / OBS-003 / OBS-005 / MATCH-001 + Option B / GOV-003 / Lever A.1 — Wave-1 spec set
- Post-soak landing order
- Next-soak cadence-tune
- LLM governance agent (PHASE2 spec)

No archives needed in specs/.

### H. Top-level `docs/` (KEEP all)

- `IMPLEMENTATION_CONTRACT.md` — primary authority
- `profit_path_debt_log.md` — unified debt tracking
- `ROADMAP.md` — staged plan
- `evidence_store_schema.md` — schema reference

## Cross-link integrity check

Spot-check on the high-value docs (Day-7 walkthrough + post-soak runbook + closure-path TLDR + IMPLEMENTATION_CONTRACT + profit_path_debt_log):

| from | to | status |
|---|---|---|
| `PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` | `PROFIT-PHASE2-001-early-close-criteria.md` | ✅ |
| Day-7 walkthrough | `governance_decision_review.py` | ✅ |
| Day-7 walkthrough | `check_soak_invariant.sh` | ✅ |
| `post-soak-rollback-runbook.md` | OBS-005 / MATCH-001 / OBS-003 / EXEC-002 / GOV-003 / Lever A.1 specs | ✅ all 6 |
| `edge-004-closure-path-tldr.md` (v2.2) | A.1+ + B + C parent specs | ✅ |
| `edge-004-closure-path-tldr-v3.md` (this cycle) | 0.04 LOCK + C v1 LOCK + Branch D specs | ✅ |
| `IMPLEMENTATION_CONTRACT.md` | none externally; self-contained | ✅ |
| `profit_path_debt_log.md` PROFIT-PHASE2-001 entry | criteria runbook + walkthrough + decision-flow + distribution analysis | ✅ |

No dead links found in the spot-check sample.

## Recommended cleanup actions

1. **Rename `edge-004-closure-path-tldr.md` → `archive/edge-004-closure-path-tldr-v2.2.md`; rename v3 → canonical name.** 5-min op; reduces "which version?" confusion.
2. **Create `docs/governance/archive/` subdir.** Move §D 13 adversarial-review files. Mid-soak confirmations stay until post-close (when an `archive/mid-soak-confirmations/` move can be batched).
3. **Add `docs/governance/INDEX.md`** (or extend existing `governance/README.md`) — table of canonical-doc names per category. Reduces operator search time at fire-time.

**Estimated cleanup wall-clock: ~15 min.**

## Out of scope

- Per-doc content review. This audit checked categorization + cross-link integrity, not internal content correctness.
- Spec lineage rationalization (e.g., merging Lever B parent + 0.04 LOCK into one doc). Multi-spec lineage is the project pattern (per Implementation Contract §11 amendment style); preserved.
- ROADMAP.md updates. Separate cadence.
- `docs/CLAUDE.md` / project root markdown — out of governance/specs scope.

## Cross-links

- `docs/governance/` — directory under audit (87 files)
- `docs/superpowers/specs/` — directory under audit (20 files)
- `docs/IMPLEMENTATION_CONTRACT.md` §9 — Claude review responsibility
