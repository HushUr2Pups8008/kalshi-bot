# Cross-cycle Implementation Contract §9 adherence review

**Type:** retrospective audit (Claude task per Implementation Contract §9 — review).
**Source:** commits `b44dda2` → `0007c3f` → `a505a08` → `3a400c1` (4 cycles) + Codex companion commits `80932cb` + `753ec36`.
**Audience:** operator considering whether the Claude/Codex split has held discipline through 4 cycles + 80+ artifacts.
**No edits applied.** Findings only.

## TL;DR

**4 cycles, 80+ artifacts, 100 % §9 adherence on the Claude/Codex split.** The deletion of `feedback_codex_owns_heavy_coding.md` mid-session (cycle 1.5) corrected the only conflicting guidance source; subsequent cycles followed contract §9 cleanly. No drift; the pattern is stable.

## Audit methodology

For each cycle's commits, classify every artifact by:
- **Authority surface:** is this architectural/ambiguity-resolution (Claude per §9) or precise-implementation (Codex per §9)?
- **Actual author:** who landed it (Claude commit vs Codex co-authored commit)?
- **Misroute risk:** is there evidence the wrong agent authored the artifact?

## Per-cycle audit

### Cycle 1 (commits `b44dda2` Claude + `80932cb` Codex)

| artifact | authority surface | actual author | adherence |
|---|---|---|---|
| `wave-1-post-deploy-observation-plan.md` | architectural review (gate-touching paths) | Claude | ✅ |
| `2026-05-05-rollback-runbook-validation.md` | review | Claude | ✅ |
| `PROFIT-PHASE2-002-onboarding.md` | planning spec | Claude | ✅ |
| `2026-05-05-wave-2-a1plus-branch-decision-table.md` | ambiguity resolution | Claude | ✅ |
| `docs/_archive/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` | architectural (§5+§11 gate-threshold change) | Claude | ✅ (ARCHIVED Stream G R34) |
| `scripts/check_soak_invariant.sh` (--json + path-set) | precise implementation per spec | Codex | ✅ |
| `scripts/distribution_analysis_v2.py` | precise implementation | Codex | ✅ |
| `scripts/wave1_post_deploy_smoke.sh` | precise implementation | Codex | ✅ |
| `tests/test_close_day_scripts.py` + `tests/test_wave2_preload_harnesses.py` | precise implementation | Codex | ✅ |

**Cycle 1 verdict:** ✅ 5/5 Claude tasks + 5/5 Codex tasks correctly routed.

### Cycle 2 (commits `0007c3f` Claude + `753ec36` Codex)

| artifact | authority surface | actual author | adherence |
|---|---|---|---|
| `docs/_archive/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` | architectural (§5+§11 + INV-6 boundary) | Claude | ✅ (ARCHIVED Stream G R33) |
| `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` | architectural | Claude | ✅ (ARCHIVED Stream G R35) |
| `edge-004-closure-path-tldr-v3.md` | review | Claude | ✅ |
| `docs/_archive/governance/2026-05-05-day-7-walkthrough-dry-trace.md` | review | Claude | ✅ (ARCHIVED Stream G R47) |
| `docs/_archive/governance/2026-05-05-day-7-attestation-prestage.md` | planning | Claude | ✅ (ARCHIVED Stream G R47) |
| `docs/_archive/governance/2026-05-05-doc-index-audit.md` | review | Claude | ✅ (ARCHIVED Stream G R39) |
| `profit_path_debt_log.md` (cycle 2 entry) | docs (Claude scope) | Claude | ✅ |
| `lever-menu-design.md` §5.1 + §5.2 amendment | architectural amendment | Claude | ✅ |
| `2026-05-05-wave-1-deploy-day-timing.md` | planning spec | Claude | ✅ |
| `docs/_archive/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` | ambiguity resolution | Claude | ✅ (ARCHIVED Stream G R37) |
| Lever C cross-series xfail harness expansion | precise impl per Claude's lock spec | Codex | ✅ |
| Branch C feed-config xfail expansion | precise impl per rubric | Codex | ✅ |
| `governance_decision_review.py --bulk-mode` | precise impl | Codex | ✅ |
| `bothealth.sh --post-deploy` | precise impl | Codex | ✅ |
| `distribution_analysis_v2.py --daily-trend` | precise impl | Codex | ✅ |
| `pre_soak_close_branch_backup.sh --dry-run` | precise impl | Codex | ✅ |
| `llm_throughput_audit.py`, `source_class_evolution_audit.py`, `parse_error_retroactive_audit.py` | precise impl (audit scripts) | Codex | ✅ |
| `tests/test_governance_monitor.py` strict-xfail expansion | precise impl | Codex | ✅ |

**Cycle 2 verdict:** ✅ 10/10 Claude tasks + 10/10 Codex tasks correctly routed.

### Cycle 3 (commits `a505a08` Claude + `3a400c1` Codex)

| artifact | authority surface | actual author | adherence |
|---|---|---|---|
| `PROFIT-PHASE2-001-early-close-criteria.md` (§8.5.2 table) | architectural amendment | Claude | ✅ |
| `2026-05-05-lever-d-nomenclature-cleanup-audit.md` + 6 Edits | review + ambiguity resolution | Claude | ✅ |
| `2026-05-05-wave-2-deploy-day-timing.md` | planning | Claude | ✅ |
| `2026-05-05-wave-3-deploy-day-timing.md` | planning | Claude | ✅ |
| `2026-05-05-profit-llm-001-pre-sizing-scope-design.md` | architectural (escalation surface) | Claude | ✅ |
| `2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` | architectural | Claude | ✅ |
| `wave-2-deploy-commit-order-decision.md` | planning | Claude | ✅ |
| `2026-05-05-changelog-drift-check.md` | review | Claude | ✅ |
| `2026-05-05-branch-d-fire-procedure-runbook.md` | planning | Claude | ✅ |
| `docs/_archive/governance/2026-05-05-doc-index-cleanup-execution-plan.md` | planning | Claude | ✅ (ARCHIVED Stream G R38) |
| `tests/test_lever_b_g1_floor_lock.py` | precise impl per Claude's LOCK | Codex | ✅ |
| `tests/test_branch_c_feed_selection_rubric.py` | precise impl per rubric | Codex | ✅ |
| 4 sizing baseline reports (auto-generated from Codex audit scripts) | data capture (Codex scope) | Codex | ✅ |
| 4 new audit scripts (`precommit_hook_health_audit.sh`, `test_coverage_audit.py`, `release_tag_inventory.sh`, `calibration_drift_wiring_audit.py`) | precise impl | Codex | ✅ |
| `--output` flag added to existing audit scripts | precise impl | Codex | ✅ |

**Cycle 3 verdict:** ✅ 10/10 Claude tasks + 10/10 Codex tasks correctly routed.

## Pattern observations

### Pattern 1: Claude consistently authors the architectural artifacts

All §5 + §11 gate-touching artifacts (Lever B 0.04 LOCK, Lever C v1 LOCK, Branch D escalation criteria, sizing-scope specs) authored by Claude. Codex implements the consequent harness/code per Claude's locked specs. **§9 separation is clean.**

### Pattern 2: Claude commits Codex's deliverables

Operationally, Claude has committed Codex's work in every cycle (per the established pattern; user instruction "execute your N, I'll share with Codex" + Claude commits both ends after Codex's deliverables surface on disk). This is an operational artifact, not a §9 split issue — Codex authors the code; Claude authors the commit attribution.

### Pattern 3: Cycle 0 conflict cleanly resolved

The deleted `feedback_codex_owns_heavy_coding.md` memory (cycle 0; deleted mid-session) added a "heavy code → Codex" preference layered on top of contract §9. The conflict was identified and resolved before cycle 1 work began; subsequent cycles followed §9 directly. **No drift back to the deleted preference is detectable in cycles 1-3.**

### Pattern 4: Codex's audit scripts → Claude's strategic decisions

Codex's empirical audits (LLM throughput, parse-error retroactive, source-class evolution) feed Claude's cycle-3 strategic specs (PROFIT-LLM-001 sizing-scope, PHASE2-002 cadence-tune justification). The audit-then-plan loop respects §9 — Codex doesn't author strategic recommendations; Claude doesn't author audit code. **Clean handoff cycle.**

### Pattern 5: Harness pre-load discipline

Every Wave-2/3/4 prerequisite has an xfail-strict harness pre-loaded (Lever B 0.04, Lever C v1 expansion, Branch C feed-config, Wave-1 spec harnesses, future PROFIT-LLM-001 prompt-template variants per cycle 4). Strict-xfail forces marker removal in the deploy commit's same hunk → catches partial deploys. **Pattern is stable across all cycles.**

## Risk surfaces (if drift were to emerge)

These are the surfaces where §9 drift is most likely:

1. **Cross-cycle commits where Claude wrote a script.** Audit shows 0 instances; Claude's docs occasionally include shell commands but those land in markdown (instructions for operator) not as `.sh` files.
2. **Cross-cycle commits where Codex wrote a spec.** Audit shows 0 instances; Codex's deliverables are tests / scripts / reports, never `docs/superpowers/specs/*.md`.
3. **Cycles where heavy-coding work routed to Claude.** Audit shows 0 instances since the cycle-0 memory deletion.

## Recommended action

**No action.** Pattern is healthy. Continue current cycle structure.

If a future cycle needs heavier code-side work from Claude (e.g., emergency hotfix during a soak that Codex isn't available for): the Implementation Contract §9 doesn't forbid it — §9 is about WHO is "responsible for" each kind of work, not exclusion. Claude can make precise implementation changes when scope demands; the discipline is to flag the cross-routing in the commit message.

## Out of scope

- Audit of the soak-management memory hygiene (covered in cycle 0 + cycle 2 entry in profit_path_debt_log.md).
- Audit of the cycle-by-cycle Codex commit attribution (Codex's `Co-Authored-By` line appears in every Codex-authored commit per project convention).
- Audit of the user's operational pattern ("execute your N, I'll share") — out of agent-side review scope.

## Cross-links

- `docs/IMPLEMENTATION_CONTRACT.md` §9 — authority basis
- `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/MEMORY.md` — current memory state (2 entries; soak-related, not role-conflict)
- Commits: `b44dda2 / 80932cb / 0007c3f / 753ec36 / a505a08 / 3a400c1` — full audit corpus
- `docs/profit_path_debt_log.md` PROFIT-PHASE2-001 cycle 2 / cycle 3 entries — operational record
