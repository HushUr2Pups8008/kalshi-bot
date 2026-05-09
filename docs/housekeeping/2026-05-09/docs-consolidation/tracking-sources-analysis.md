# Tracking-Sources Analysis — docs/ Consolidation Phase 3

**Date:** 2026-05-09
**Phase:** 3 — Synthesis (main thread, read-only)
**Inputs:** `inventory.md` (273 files), `classification.md` (275 rows), `tracking-purpose.md` (34 in-scope)
**Cap:** Top-30 cluster items (per plan)

---

## TL;DR

The docs/ tree has **6 distinct tracking clusters**, of which **4 carry true duplication** between parallel surfaces. The remaining 2 (Active-cycle ledgers; Audit-evidence pile-ups) follow the trust hierarchy correctly — they are not parallel-tracking; they are appropriate evidence artifacts.

Total estimated duplicate-tracking bloat across the 4 problem clusters: **~12.5K lines / ~50K tokens** of content that asserts the same state from more than one surface.

The single biggest finding: **the 4 problem clusters all overlap on `profit_path_debt_log.md` ↔ `ROADMAP.md` ↔ `EDGE_STATUS.md`**. Collapsing those three into one surface (with `ROADMAP.md` keeping its strategic horizon, `EDGE_STATUS.md` deprecated, debt log gaining a Status section) removes ~80% of the duplication.

---

## 1. Cluster Inventory

Each cluster names a single piece of state. Files listed contribute some assertion about that state. **Bold** = currently authoritative or highest-trust per the plan's trust hierarchy.

### Cluster A: Open-debt items (P0/P1/P2 with status)

- **`docs/profit_path_debt_log.md`** (TRACKING_DEBT) — 437K, 4760 lines. 48 items tracked.
- `docs/ROADMAP.md` (TRACKING_ROADMAP) — wave/cycle pipeline references same `PROFIT-*` IDs (PROFIT-PHASE2-001, PROFIT-EDGE-011, PROFIT-OBS-003, etc.).
- `docs/EDGE_STATUS.md` (TRACKING_STATUS) — references PROFIT-EDGE-011 verdict chain.

**Files contributing:** 3.

### Cluster B: Edge / signal status (current verdict, deploy gating)

- **`docs/EDGE_STATUS.md`** — operator-facing edge dashboard. Cycle-16E result; Cycle-17 decision pending; wave-1 cleanup-only.
- `docs/ROADMAP.md` — wave-1/2/3 deploy timeline + IC §16 gate references; restates the same wave-deploy state.
- `docs/governance/edge-004-closure-path-tldr.md` (TRACKING_STATUS) — TLDR v2.2.
- `docs/governance/edge-004-closure-path-tldr-v3.md` (TRACKING_STATUS) — TLDR v3 (2026-05-05 refresh).
- `docs/governance/edge-replay-cycle12-report.md` … `edge-replay-cycle17c-e1-report.md` (8 AUDIT_REPORT files) — point-in-time verdicts feeding the chain.
- `docs/profit_path_debt_log.md` — PROFIT-EDGE-011 entry tracks the same verdict chain.

**Files contributing:** 13. Replay reports are evidence (not parallel tracking) but they're the source of state that the 3 status surfaces re-state.

### Cluster C: Wave deploy timeline (Wave-1 / Wave-2 / Wave-3 status)

- **`docs/ROADMAP.md`** — current canonical wave timeline (Wave-1 ships 2026-05-08+; Wave-2/3 halted).
- `docs/EDGE_STATUS.md` — wave-dispatch matrix.
- `docs/governance/wave-1-changelog-entry-prestaged.md` (MIXED → TRACKING_LEDGER 2°)
- `docs/governance/wave-1-commit-messages-prestaged.md` (MIXED → TRACKING_LEDGER 2°)
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` (MIXED → TRACKING_LEDGER 2°)
- `docs/governance/wave-1-deploy-commit-order-decision.md` (PLAN)
- `docs/governance/wave-2-deploy-commit-order-decision.md` (PLAN)
- `docs/governance/wave-1-post-deploy-observation-plan.md` (PLAN)
- `docs/governance/2026-05-05-wave-{1,2,3}-deploy-day-timing.md` (3 PLAN files)
- `docs/governance/2026-05-05-wave-{1,2,3}-fire-time-per-commit-checklist.md` (3 RUNBOOK files)
- `docs/governance/2026-05-05-wave-1-deploy-dry-run-report.md` (AUDIT_REPORT)
- `docs/governance/2026-05-05-wave-{1,2}-decision-flow.md` + `wave-3-decision-flow.md` (3 RUNBOOK)
- `docs/profit_path_debt_log.md` — PROFIT-PHASE2-001 references soak gate; cross-references to wave entries.

**Files contributing:** ~16. The 3 PLAN/RUNBOOK Wave-1/2/3 timing+checklist clusters are appropriate as deploy-event runbooks — they're not parallel tracking. The 3 MIXED-TRACKING_LEDGER prestaged-changelog files DO duplicate state that lives elsewhere (deploy timeline).

### Cluster D: Soak status (Phase-2 governance shadow-soak, PROFIT-PHASE2-001)

- **`docs/profit_path_debt_log.md`** — PROFIT-PHASE2-001 entry is canonical (declared per CLAUDE.md gotcha).
- `docs/ROADMAP.md` — soak clock reference.
- `docs/governance/2026-05-03-mid-soak-health-report.md` (TRACKING_STATUS).
- `docs/governance/2026-05-03-mid-soak-snapshot-{2,3,4,5}.md` (4 TRACKING_STATUS).
- `docs/governance/2026-05-03-day-{3,4}-pending-mid-soak-confirmation*.md` (5 TRACKING_STATUS — multiple variants of the same Day-3/Day-4 placeholder).
- `docs/governance/2026-05-04-day-4-mid-soak-confirmation.md` + `2026-05-04-day-4-mid-soak-sanity-check.md` (2 TRACKING_STATUS).
- `docs/governance/2026-05-04-day-5-cycle-sanity-check-pending.md` (TRACKING_STATUS).
- `docs/governance/2026-05-05-day-5-cycle-sanity-check{,-canonical,-refresh}.md` (3 TRACKING_STATUS).
- `docs/governance/2026-05-07-day-7-pre-soak-confirmation.md` (TRACKING_STATUS).
- `docs/governance/PROFIT-PHASE2-001-{close-day-decision-flow,day-7-close-day-walkthrough,early-close-attestation,early-close-attestation-template,early-close-criteria}.md` (5 files; mix of RUNBOOK / TRACKING_STATUS / CHARTER / MIXED).
- `docs/governance/2026-05-06-soak-runtime-characterization-summary.md` (AUDIT_REPORT).
- `docs/governance/2026-05-05-PROFIT-PHASE2-001-{daily-trend-baseline,decision-distribution-analysis,llm-throughput-baseline,parse-error-retroactive-findings,source-class-evolution-baseline}.md` (5 AUDIT_REPORT — soak diagnostics).

**Files contributing:** ~22. The 16 mid-soak/day-N status files are **the worst duplication in the tree** — they're per-day stamps of "soak still healthy" that should have been a single ledger.

### Cluster E: Roadmap horizon (multi-week strategic priorities)

- **`docs/ROADMAP.md`** — canonical (was always the intent).
- `docs/profit_path_debt_log.md` — Open-questions section restates roadmap items.
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` (PLAN) — strategic redirect entry.
- `docs/governance/post-edge-004-escalation-paths.md` (PLAN) — escalation paths.
- `docs/governance/cycle-{15,16,17}-conditional-charter-skeletons.md` (3 CHARTER) — pre-staged future cycle scopes.

**Files contributing:** 6. Lower duplication than A–D but `ROADMAP.md` and the debt-log Open-Questions block restate each other.

### Cluster F: Active-cycle decision-state (current cycle's open decision)

- **Cycle 17C charter** = `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` (CHARTER — appropriately authoritative for cycle scope).
- `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` (SCHEMA — defines the ledger shape).
- `docs/governance/2026-05-07-cycle-17c-{e1-criteria-lock-bayesian-log-odds,first-axis-pick-rationale,e1-claude-verdict-appendix}.md` (3 CHARTER/AUDIT/MIXED files driving E1 decision).
- `docs/governance/2026-05-08-cycle-17c-{e2-g1-admission-sweep,e3-side-inference-hypothesis-sketch}.md` (2 AUDIT/PLAN files driving E2/E3).
- `docs/governance/edge-replay-cycle17c-e1-report.md` (AUDIT_REPORT) — E1 verdict.
- `docs/governance/cycle-17-conditional-charter-skeletons.md` (CHARTER) — pre-staged scope.

**Files contributing:** 8. **This cluster does NOT carry parallel-tracking duplication.** Per the trust hierarchy: active-cycle ledgers are #2 in priority and properly orthogonal to the debt log. They merge back to debt-log only at cycle close.

---

## 2. Canonical-Surface Assignment

Per the trust hierarchy in the plan header.

| Cluster | Canonical Surface | Justification |
|---|---|---|
| A — Open-debt items | `docs/profit_path_debt_log.md` | Already declared canonical by CLAUDE.md. Other files merely re-cite IDs. |
| B — Edge/signal status | **NEW** Section in `profit_path_debt_log.md` (absorbing `EDGE_STATUS.md` content) | Edge state IS a debt-status concern (PROFIT-EDGE-011 is its anchor). EDGE_STATUS is a redundant operator dashboard. |
| C — Wave deploy timeline | `docs/ROADMAP.md` (slim section) | Deploy timeline is forward-looking; ROADMAP is the right home. EDGE_STATUS dispatch matrix folds into ROADMAP wave section. The 3 prestaged-changelog MIXED files are deploy artifacts → ARCHIVE after deploy lands. |
| D — Soak status | `profit_path_debt_log.md` § PROFIT-PHASE2-001 (single rolled-up entry) | Already declared canonical in CLAUDE.md. The 16+ per-day status files are stamps that should never have proliferated; consolidate the surviving content into the existing PROFIT-PHASE2-001 entry, archive the rest. |
| E — Roadmap horizon | `docs/ROADMAP.md` | Already canonical. Open-Questions block in debt log should reference, not duplicate. |
| F — Active-cycle decision-state | Per-cycle CHARTER + AUDIT files in `docs/governance/` | **No change.** Trust hierarchy #2 places active-cycle ledgers correctly orthogonal to debt log. These merge back at cycle close. |

---

## 3. Duplicate-Tracking Pairs — Bloat / Drift / Confusion

Estimates: lines × ~6 tokens/line. Drift = how often the two surfaces diverged historically (sampled via `git log` co-edit pattern).

### Pair 1: `EDGE_STATUS.md` ↔ `ROADMAP.md` (Cluster B/C overlap)

- **Bloat:** EDGE_STATUS 104 lines × shared content ≈ 60 lines duplicated in ROADMAP. **~360 tokens.**
- **Drift risk:** HIGH. Both files reference Cycle-16E + Cycle-17 decision. EDGE_STATUS last modified 2026-05-07; ROADMAP 2026-05-07 — both updated together but in separate commits historically. Past co-edit pattern shows them updating in same-day burst, not same-commit (drift opportunity).
- **Confusion risk:** HIGH. New operator looking at "what's the edge state?" can't tell which is authoritative.
- **Recommendation:** DEPRECATE-WITH-NOTE on EDGE_STATUS pointing at debt log Status section.

### Pair 2: `EDGE_STATUS.md` ↔ `profit_path_debt_log.md` PROFIT-EDGE-011 entry (Cluster A/B)

- **Bloat:** EDGE_STATUS 104 lines × shared content ≈ 40 lines re-stated in PROFIT-EDGE-011. **~240 tokens.**
- **Drift risk:** MEDIUM. PROFIT-EDGE-011 has cycle-by-cycle verdict ladder that EDGE_STATUS summarizes; updates have stayed coherent so far but no enforcement.
- **Confusion risk:** MEDIUM.
- **Recommendation:** EDGE_STATUS content folds into a new "Current edge / verdict status" section within debt log header.

### Pair 3: `ROADMAP.md` Wave-1/2/3 timeline ↔ governance/wave-*-changelog/commit/timing files (Cluster C)

- **Bloat:** ~600 lines across the 3 prestaged-changelog files + 3 fire-time-checklist + 3 deploy-day-timing files re-state the same wave timeline that ROADMAP carries. **~3.6K tokens.**
- **Drift risk:** LOW (these are deploy artifacts — single-use, one-shot, not repeatedly edited).
- **Confusion risk:** LOW (they're naturally segregated to docs/governance/).
- **Recommendation:** Wave-1 prestaged-changelog and commit-messages files (3 MIXED-TRACKING_LEDGER) → ARCHIVE post-deploy. The 6 deploy-day-timing/fire-time-checklist files (PLAN/RUNBOOK) → KEEP as runbook artifacts; they're event-bound, not tracking. Lowest-priority cleanup.

### Pair 4: `profit_path_debt_log.md` PROFIT-PHASE2-001 ↔ 16 per-day soak status files (Cluster D)

- **Bloat:** 16 governance/ files at ~50-150 lines each ≈ 1200 lines, of which ~70% (~840 lines) is "soak still healthy, no events" boilerplate that PROFIT-PHASE2-001 already covers. **~5K tokens.**
- **Drift risk:** LOW (per-day status stamps don't drift; they're frozen at write).
- **Confusion risk:** HIGH. Operator looking for current soak state has 16 candidate files of similar names; canonical PROFIT-PHASE2-001 is buried in a 437K log.
- **Recommendation:** ARCHIVE all 16 per-day status files into `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/`. Single line in PROFIT-PHASE2-001 entry: "Per-day soak history archived 2026-05-09; see _archive/...".

### Pair 5: `IMPLEMENTATION_CONTRACT.md` § 10 (status update rules) ↔ debt-log status conventions (Cluster A)

- **Bloat:** ~30 lines in IC §10 prescribe debt-log update conventions that the debt log itself documents. **~180 tokens.**
- **Drift risk:** LOW (locked contract).
- **Confusion risk:** LOW (IC §10 is the rule, debt log is the application — appropriate separation).
- **Recommendation:** **No action.** IC and debt log roles are correctly distinct; this is not duplication, it's contract→artifact relationship.

---

## 4. Audit-Evidence Pile-Ups (NOT parallel tracking — secondary concern)

The 5 AUDIT_REPORT clusters surfaced in `tracking-purpose.md` (MATCH-001 B' family, soak-health monitoring, doc-hygiene, plist/launchd readiness, edge-replay cycle reports — totaling ~17 files / ~6.3K lines / ~38K tokens) are NOT parallel-tracking surfaces. They're one-shot evidence artifacts that proliferated during the Wave-1 prep burst (2026-05-03 → 2026-05-06).

**Recommendation:** ARCHIVE these into `docs/_archive/2026-05-09-docs-consolidation/audit-evidence-2026-05-burst/` rather than merge into debt log. They'd swell the debt log past its 600K soft ceiling and they don't belong there — they're evidence backing closed debt items, not active tracking.

This doesn't violate the "ONE document" goal because evidence != tracking. The trust hierarchy already places audit-event records at #5 (housekeeping/) and historical-only at #6 (_archive/).

---

## 5. Estimated Bloat Removal

| Action | Files affected | Lines removed/relocated | Tokens removed/relocated |
|---|---|---|---|
| Pair 1: DEPRECATE EDGE_STATUS, fold into debt-log Status section | 2 | ~100 | ~600 |
| Pair 2: PROFIT-EDGE-011 cleanup post Pair 1 | 1 | ~40 | ~240 |
| Pair 3: ARCHIVE wave-* prestaged | 3 | ~600 | ~3.6K |
| Pair 4: ARCHIVE per-day soak status files | 16 | ~1200 | ~5K |
| Audit-evidence ARCHIVE sweep | ~17 | ~6300 | ~38K |
| **Total bloat removed/relocated** | **~39** | **~8240** | **~47K tokens** |
| Net file count change | docs/ tree drops from 273 → ~234 active + ~39 archived | | |

`profit_path_debt_log.md` would gain a new "Status" section absorbing EDGE_STATUS content (~60-100 lines, +500-600 tokens). It stays well under the 600K ceiling (current 437K + ~6K = ~443K).

---

## 6. Open Questions for User Judgment

1. **Q1 (highest impact):** Should `EDGE_STATUS.md` be DEPRECATED-WITH-NOTE (banner pointing at new debt-log Status section) or fully MERGED + DELETED? Operator-facing dashboard convenience vs strict one-doc principle.

2. **Q2:** The 3 wave-* prestaged-changelog MIXED files (TRACKING_LEDGER secondary). They're explicitly pre-deploy artifacts. ARCHIVE now or wait until Wave-2/3 actually deploys (they're still "active" while deploys are halted at Cycle-17 decision)?

3. **Q3:** `IMPLEMENTATION_CONTRACT.md` is 55K, classified CONTRACT (not TRACKING). Phase 2's tracking-purpose.md noted IC §10 has status-update conventions. Should those rules be EXTRACTED into debt-log header (eliminate the indirect reference) or stay in IC as governance? Recommend: stay in IC — separation of "rules" from "applications" is the correct shape; IC stays out of consolidation scope.

4. **Q4:** The 16 per-day soak status files include 5 named `2026-05-03-day-4-pending-mid-soak-confirmation*.md` — clearly placeholder duplicates that were never reconciled. Confirm: ARCHIVE all 5 (preserving git history via `git mv`) rather than DELETE outright? Recommend ARCHIVE.

5. **Q5:** `docs/governance/edge-004-closure-path-tldr.md` and `-v3.md` are 2 versions of the same TLDR. ARCHIVE the older v2.2 and KEEP v3 as a status file? Or fold both into Cluster B Status section and ARCHIVE both? Recommend: fold v3 content into debt-log Status section, ARCHIVE both.

6. **Q6:** The 12 MIXED-classification files. Most are charter+plan combinations (cycle-Nb-task-split, cycle-N-post-verdict-action-checklist). Phase 4 SPLIT decisions: split into pure charter + pure plan, OR keep as-is since the combination is intentional? Recommend: keep MIXED files as-is unless Phase 4 finds specific drift evidence; SPLIT only if the file is being actively edited and the dual-purpose creates conflict.

---

Tracking-sources analysis complete. Phase 4 designs the One Document around these clusters.
