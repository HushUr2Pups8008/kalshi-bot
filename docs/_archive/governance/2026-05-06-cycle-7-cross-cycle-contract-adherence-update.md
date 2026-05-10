# Cross-cycle Implementation Contract §9 adherence — cycle 7 update

**Type:** retrospective audit (Claude task per Implementation Contract §9 — review).
**Source:** commits `b44dda2` → `92b767d` → `ffc54b2` (7 cycles + Codex companions).
**Audience:** operator considering whether the Claude/Codex split has held discipline through 7 cycles + ~140 artifacts.
**Drafted:** 2026-05-06.
**Sibling (cycle 4 baseline):** `2026-05-05-cross-cycle-contract-adherence-review.md`.

## TL;DR

**7 cycles, ~140 artifacts, 100% §9 adherence on the Claude/Codex split.** Cycle-4 review's pattern observations hold through cycles 5/6/7. Cycle-6 Codex deviation (`2026-05-05-openclaw-orchestrator-integration-note.md` + `_archive/studies/future_plans.md` Edit) was operator-reviewed and approved at cycle-6 commit `afa1dc9`; doc-only and §11.B redesign-discussion-compliant. **No drift; pattern stable.**

## Cycles 5/6/7 audit (extending cycle 4 baseline)

### Cycle 5 (commits `3fb09eb` Claude + `fa07434` + `afa1dc9` Codex)

| artifact | authority surface | actual author | adherence |
|---|---|---|---|
| README.md / ROADMAP.md / wave-2-wave-3-changelog drift fixes | doc-edits per cycle-4 findings | Claude | ✅ |
| `2026-05-05-pre-wave1-baseline-interpretation.md` | review/synthesis | Claude | ✅ |
| `2026-05-05-wave-2-fire-time-per-commit-checklist.md` + Wave-3 sibling | planning | Claude | ✅ |
| `2026-05-05-launchd-plist-drift-audit.md` | review | Claude | ✅ |
| `2026-05-05-network-api-outage-runbook.md` | planning | Claude | ✅ |
| `2026-05-05-implementation-contract-cycle-4-5-review.md` | architectural review | Claude | ✅ |
| `tests/test_lever_b_g1_floor_lock.py` | precise impl per Claude addendum | Codex | ✅ |
| `tests/test_branch_c_feed_selection_rubric.py` | precise impl per Claude rubric | Codex | ✅ |
| 4 baseline reports (auto-generated) | data capture (Codex scope) | Codex | ✅ |
| 4 audit scripts (precommit, test_coverage, release_tags, calibration_drift) | precise impl | Codex | ✅ |

**Cycle 5 verdict:** ✅ 10+10 correctly routed.

### Cycle 6 (commits `fa07434` + `afa1dc9` Codex)

| artifact | authority surface | actual author | adherence |
|---|---|---|---|
| `scripts/pre_wave1_llm_call_budget_projection.py` | precise impl | Codex | ✅ |
| `scripts/wave2_fire_time_smoke.sh` + Wave-3 sibling | precise impl | Codex | ✅ |
| `scripts/macbook_import_disposition_audit.sh` | precise impl | Codex | ✅ |
| `scripts/launchd_plist_drift_audit.sh` | precise impl (Claude task spec'd it; Codex implemented + extended with `ops/launchd/` template-render approach) | Codex | ✅ |
| 5 strict-xfail harnesses (db_snapshot, calibration_drift, governance_parse_error, kill_switch_path, prompt_template A5-A8) | precise impl | Codex | ✅ |
| `2026-05-05-openclaw-orchestrator-integration-note.md` | **deviation:** §11.B redesign-discussion artifact (planning territory; nominally Claude scope) | Codex | **note: operator-reviewed + approved cycle-6 commit `afa1dc9`** |
| `_archive/studies/future_plans.md` 2-line edit | **deviation:** archive cross-link (doc-edit; nominally Claude scope) | Codex | **note: operator-reviewed + approved cycle-6 commit `afa1dc9`** |

**Cycle 6 verdict:** ✅ 8/10 correctly routed; 2 deviations operator-approved at commit time. **Both deviations are doc-only and INV-preserving** (OpenClaw note explicitly preserves §1+§5+§7+§11 per cycle-6+7 contract review §"Cycle 6 OpenClaw integration note assessment").

### Cycle 7 (commits `92b767d` Claude + `ffc54b2` Codex)

| artifact | authority surface | actual author | adherence |
|---|---|---|---|
| IMPLEMENTATION_CONTRACT.md §5 G1 alignment | architectural cosmetic; §11.A clarification | Claude | ✅ |
| `2026-05-05-launchd-plist-consolidation-decision.md` | ambiguity resolution | Claude | ✅ |
| `2026-05-05-day-7-to-wave-1-handoff-procedure.md` | planning | Claude | ✅ |
| `2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md` | planning | Claude | ✅ |
| `2026-05-05-wave-2-decision-flow.md` + Wave-3 sibling | planning | Claude | ✅ |
| `2026-05-05-macbook-import-archive-procedure.md` | planning | Claude | ✅ |
| `2026-05-05-doc-cross-link-integrity-audit.md` | review | Claude | ✅ |
| `2026-05-05-memory-hygiene-audit.md` | review | Claude | ✅ |
| `scripts/wave1_commit_chain_acceptance_audit.sh` | precise impl per Claude planning | Codex | ✅ |
| `scripts/doc_xref_audit.py` | precise impl per Claude review-tool need | Codex | ✅ |
| `scripts/release_tag_inventory_snapshot.py` | precise impl | Codex | ✅ |
| `scripts/macbook_import_archive_migration.sh` | precise impl per Claude planning | Codex | ✅ |
| 6 strict-xfail Wave-1 per-commit sentinels | precise impl per Claude commit-by-commit matrix | Codex | ✅ |
| `tests/test_wave1_operator_scripts_contracts.py` | precise impl | Codex | ✅ |

**Cycle 7 verdict:** ✅ 10+11 correctly routed.

## Pattern observations (extending cycle 4 baseline)

### Pattern 1 (cycle 4): "Claude consistently authors the architectural artifacts" — STILL HOLDS in cycles 5/6/7.

All §5 + §11 gate-touching artifacts continue to come from Claude (no cycle-5/6/7 spec lock addenda; only cycle-7 cosmetic G1 alignment). Codex implements consequent harnesses + scripts.

### Pattern 2 (cycle 4): "Claude commits Codex's deliverables" — STILL HOLDS.

All cycle-5/6/7 Codex commits routed through Claude commit-message authorship per `Co-Authored-By: Codex` convention.

### Pattern 3 (cycle 4): "Cycle-0 conflict cleanly resolved" — STILL HOLDS.

`feedback_codex_owns_heavy_coding.md` deletion stays the only memory-hygiene action; no drift toward the deleted preference.

### Pattern 4 (cycle 4): "Codex's audit scripts → Claude's strategic decisions" — STILL HOLDS.

Cycle-6 Codex audits (LLM call budget projection 352/day; macbook_import_disposition keep+archive; plist drift pass) inform cycle-7 Claude planning (PHASE2-002 cadence-tune confidence; macbook archive procedure; plist consolidation decision).

### Pattern 5 (cycle 4): "Harness pre-load discipline" — STILL HOLDS.

Cycle-5/6/7 added 11 new strict-xfail harnesses pre-loaded for Wave-1/2/3 deploys + failure-mode runbooks. Marker-removal-in-deploy-commit pattern preserved.

### NEW Pattern 6 (cycle 5+): "Operator-approved doc-only deviations are §11-compliant when explicit"

Cycle-6 OpenClaw note + future_plans.md edit illustrate that doc-only deviations from the proposed split are §11.B-compliant when:
1. Doc-only (no code/runtime touch)
2. Explicitly preserves §1 + §5 + §7 + §11 boundaries
3. Operator-reviewed at commit time
4. The deviation IS the redesign-discussion artifact

Future cycles can use this pattern for similar planning notes; the boundary is "doc-only + operator-reviewed."

## Risk surfaces (extending cycle 4)

Same surfaces; no fresh risk emerged through cycles 5/6/7:

1. Cross-cycle commits where Claude wrote a script — 0 instances cycles 1-7
2. Cross-cycle commits where Codex wrote a spec — 0 instances cycles 1-7 (cycle-6 OpenClaw note is a doc, not a spec; clarifying)
3. Cycles where heavy-coding work routed to Claude — 0 instances cycles 1-7

## Recommended action

**No action.** Pattern is healthy. Continue current cycle structure.

If future cycles produce more doc-only deviations from the proposed split (like cycle-6 OpenClaw): apply the pattern-6 criteria; operator-review at commit-time; land if compliant.

## Out of scope

- Cycle 8+ work (in flight; not yet committed at this audit time)
- Cross-project §9 patterns (project-scoped audit)
- Operator-side cycle-pattern preferences (out of agent-side review)

## Cross-links

- `docs/governance/2026-05-05-cross-cycle-contract-adherence-review.md` — cycle 4 baseline audit
- `docs/governance/2026-05-05-implementation-contract-cycle-4-5-review.md` — cycles 4+5 invariant review
- `docs/governance/2026-05-06-implementation-contract-cycle-6-7-review.md` — cycles 6+7 invariant review (sibling)
- `docs/IMPLEMENTATION_CONTRACT.md` §9 — authority basis
- Commits: `b44dda2` / `80932cb` / `0007c3f` / `753ec36` / `a505a08` / `3a400c1` / `afa1dc9` / `92b767d` / `ffc54b2` — full audit corpus
