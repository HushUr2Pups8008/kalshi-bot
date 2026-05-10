# Plan — Safe `docs/` Work During OBS-003 24h Audit

**Date:** 2026-05-09
**Status:** DRAFT (controller plan — execution gated on user approval)
**Audit context:** OBS-003 BlendTask SKIPPED emission deployed at commit `92b1d11` and stabilized at `c9df364`. 24h paper-mode audit running per `2026-05-09-profit-obs-003-blendtask-skipped-emission.md` Task 6 in worktree `~/vscode/kalshi-bot-obs-003`. Closure invariant: `OPPORTUNITY = SKIPPED + PAPER_TRADE ± in-flight (<5)`.

---

## 1. Goal

Identify and execute non-conflicting `docs/` work in the **main worktree** while the OBS-003 audit runs in the **obs-003 worktree**, without:

- racing the audit's eventual closure write to `docs/profit_path_debt_log.md` (PROFIT-OBS-003 entry)
- mutating any input the audit reads while the window is open
- violating IC §16 (no behavioral code changes during observation windows)
- creating new parallel tracking surfaces (project CLAUDE.md invariant)

## 2. Audit Read/Write Set — Do Not Touch

| File | Reason |
|---|---|
| `docs/superpowers/plans/2026-05-09-profit-obs-003-blendtask-skipped-emission.md` | Audit follows this for invariant + Task 6/7 closure |
| `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md` | Spec §8 acceptance is the audit's exit criterion |
| `docs/profit_path_debt_log.md` — `PROFIT-OBS-003` section + Header counts (lines around 31–34) + High-Risk Area #4 (line 43) + Recommended Execution Order (line 47) | Audit closure writes here; edits race the closure |
| `docs/governance/2026-05-06-post-obs003-skipped-attribution-audit-refresh.md` | Quoted by audit invariant computation |
| `docs/ROADMAP.md` — Wave-1 row (line ~105) + cycle-trail block | Audit closure may flip Wave-1 line; ROADMAP touches debt log cross-refs |
| `docs/IMPLEMENTATION_CONTRACT.md` §16 | Audit cites IC §16 Rule 2 exemption |
| `~/vscode/kalshi-bot-obs-003/**` (entire obs-003 worktree) | Audit's working tree |

**Rule:** On main worktree, treat `profit_path_debt_log.md` and `ROADMAP.md` as **frozen** until audit closure commits.

## 3. Safe Work Streams (ranked by ratio of value : audit-conflict risk)

### Stream A — Commit Pending Worktree Cleanup (HIGHEST VALUE / ZERO RISK)

Main worktree currently has 33 staged housekeeping deletions and 1 untracked file. These predate the audit and don't intersect its read set.

**Tasks:**
1. **A1.** Verify untracked `docs/superpowers/plans/2026-05-09-profit-obs-003-blendtask-skipped-emission.md` matches what audit consumes — diff against `~/vscode/kalshi-bot-obs-003/docs/superpowers/plans/2026-05-09-profit-obs-003-blendtask-skipped-emission.md`. Same content → safe to add. Different → defer (audit owns it).
2. **A2.** Commit the OBS-003 plan file under `docs(plan): track OBS-003 plan in main worktree` (small, atomic, doesn't change audit-visible content).
3. **A3.** Commit the 33 housekeeping deletions under `docs(housekeeping): drop 2026-05-08/2026-05-09 audit working files post-consolidation`. Per prior session: user explicitly intentional ("removed because no longer necessary after housekeeping actions completed").

**Conflict risk:** None. Files are not in audit read set.

### Stream B — Archive Closed Lever Design Docs (LOW RISK)

`docs/superpowers/plans/` holds 7 EDGE-004 lever-design files dated 2026-05-03 to 2026-05-05. Most have been closed or superseded by Cycle-16E `scorer_fixed_no_signal_confirmed` and Cycle-17 operator decision pivot. Wave-2 / Wave-3 / Branch-D are HALTED per ROADMAP line 105 cluster.

**Tasks:**
1. **B1.** Build a closure-status table for each file in `docs/superpowers/plans/2026-05-03-edge-004-lever-*.md` and `2026-05-05-edge-004-*.md` (read frontmatter + last update date; cross-reference debt-log `PROFIT-EDGE-010` / `PROFIT-EDGE-011` and Cycle-16E entries). Output: in-place classification list (verify only — no archive yet).
2. **B2.** Move plans confirmed CLOSED/SUPERSEDED to `docs/_archive/plans/` (mirror existing `_archive/plans/` convention — see existing v0.20.0…v0.29.4 file naming). Files staying ACTIVE remain in place.
3. **B3.** Update any `docs/governance/edge-replay-cycle*.md` cross-refs that pointed at archived plans. **DEFER if a cross-ref touches debt log or ROADMAP** — those are frozen.

**Conflict risk:** Low. Audit doesn't consume EDGE-004 lever plans. Cross-ref updates skip frozen files.

### Stream C — Archive Stale Governance One-Offs (LOW RISK)

`docs/governance/` holds ~30+ dated audit / sizing / counterfactual one-offs from 2026-05-03 (cycle-12/13 era). Many are now historical because Cycle-15B / 16D / 16E supersede them.

**Tasks:**
1. **C1.** Inventory `docs/governance/2026-05-03-*.md` and `2026-05-04-*.md`. Classify each as ACTIVE (cycle-17 input) / SUPERSEDED (Cycle-16E withdrew framing) / HISTORICAL (single-use audit, value is in archive form).
2. **C2.** Move SUPERSEDED + HISTORICAL files to `docs/_archive/governance/<YYYY-MM-DD>-cycle-NN-foo.md`. Create `_archive/governance/` directory if not present (mirrors existing `_archive/2026-05-09-docs-consolidation/` pattern).
3. **C3.** Skip any file that is referenced by `docs/profit_path_debt_log.md` or `docs/ROADMAP.md` — those references rot if archived without parallel ref-update, and ref-updates touch frozen files.

**Conflict risk:** Low. Move-only; no content edit.

### Stream D — Spec / Plan Hygiene Sweep (LOW RISK)

Read-only audit of `docs/superpowers/specs/` and `docs/superpowers/plans/` for:

- broken internal links (after recent consolidation moved files into `_archive/2026-05-09-docs-consolidation/`)
- typos / dead refs to retired symbols (e.g., `MAX_BET_DOLLARS`, `KALSHI_GEOPOLITICAL_SERIES`)
- wrong-anchor links into `profit_path_debt_log.md` (its TOC moved during consolidation)

**Tasks:**
1. **D1.** Run a link-check pass over `docs/superpowers/{plans,specs}/*.md`. Output: broken-ref table.
2. **D2.** Fix broken refs in non-frozen files only. **Skip OBS-003 plan + spec, debt log, ROADMAP, IC.**
3. **D3.** Document remaining frozen-file fixes in this plan's Stream-G (post-audit).

**Conflict risk:** Low — edits to non-frozen files only. Frozen-file fixes deferred.

### Stream E — README + Top-of-Tree Docs Hygiene (LOWEST PRIORITY / LOW RISK)

`README.md` (project root) and `docs/_archive/README.md` (if present) — verify post-consolidation parity.

**Tasks:**
1. **E1.** Read `README.md` for stale references to deleted housekeeping dirs (post-Stream-A).
2. **E2.** Audit project `CLAUDE.md` for refs to deleted `docs/housekeeping/` paths and redirect them. (Note: `docs/_archive/2026-05-09-docs-consolidation/SUMMARY.md` does NOT exist — only `edge-004-closure-path-tldr*.md` and `soak-status-history/` live there. Use `docs/_archive/plans/2026-05-09-docs-directory-consolidation.md` as the consolidation plan reference.)
3. **E3.** No edits to `README.md` if VERSION badge is involved — VERSION/README parity gate could trip during audit. Defer until post-audit.

**Conflict risk:** Low. Read-mostly; defer VERSION-coupled edits.

### Stream F — Defer (POST-AUDIT)

Explicitly **NOT in scope** until OBS-003 audit closure commits:

- Any edit to `docs/profit_path_debt_log.md`
- Any edit to `docs/ROADMAP.md`
- VERSION bump (couples README + CHANGELOG; conflicts with audit closure cycle)
- New PROFIT-* debt entries (debt log frozen)
- Any code change in `tasks/blend_task.py`, `analysis/signal_analyzer.py`, `executor.py` (IC §16 forbids)
- Anything inside `~/vscode/kalshi-bot-obs-003/`

## 4. Execution Order

| # | Stream | Task | Estimated cost | Gate |
|---|---|---|---|---|
| 1 | A | A1 — diff OBS-003 plan untracked vs obs-003 worktree | trivial | none |
| 2 | A | A2 — commit OBS-003 plan add | small | A1 confirmed-equal |
| 3 | A | A3 — commit housekeeping deletions | small | none (independent of A2) |
| 4 | B | B1 — lever-plan classification table | medium | none |
| 5 | B | B2 — archive moves | medium | B1 reviewed by user |
| 6 | C | C1 — governance inventory | medium | none |
| 7 | C | C2 — governance archive moves | medium | C1 reviewed by user |
| 8 | D | D1 — link-check sweep | medium | none |
| 9 | D | D2 — non-frozen ref fixes | small | D1 reviewed |
| 10 | E | E1–E2 — README/index audit | small | A complete (deletions visible to README scan) |
| 11 | F | (deferred) | — | OBS-003 audit closes |

Streams A and B/C/D can interleave — A is independent. Default sequence: A first (clears worktree), then C (largest archive surface), then B (lever plans), then D, then E.

## 5. Acceptance Criteria

- Main worktree `git status` clean post-Stream-A.
- `docs/_archive/plans/` and (new) `docs/_archive/governance/` contain archived files with no debt-log / ROADMAP cross-ref breakage.
- Spec/plan link-check passes on non-frozen files.
- No commit during audit window mutates any file in §2 (`git diff main..HEAD -- <frozen list>` is empty).
- Audit closure (post-window) lands cleanly without merge conflict against this work.

## 6. Risk Register

| Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|
| Archive move breaks debt-log/ROADMAP cross-ref | Medium | High (frozen-file edit needed to fix) | C3 / D2 skip rule — never archive a file that frozen surfaces reference; record the deferred ref-fix in Stream-G post-audit |
| Untracked OBS-003 plan diverges from obs-003 worktree's copy | Low | Medium | A1 diff gate |
| User wants to restart audit because of cleanup commit on main | Low | Low | Cleanup commits are doc-only; do not affect runtime or audit invariant |
| Subagent in obs-003 worktree pushes closure commit during Stream-A→C run | Low | Low | Streams A–E touch disjoint paths from audit closure write set |
| New ECC tooling (housekeeping skill) re-creates `docs/housekeeping/2026-05-08/` mid-stream | Low | Medium | Stream-A re-runs idempotent; commit deletes once audit closes if regenerated |

## 7. Open Questions for User

1. **Scope confirmation:** Streams A, B, C, D, E all green-lit, or trim to A only (most conservative)?
2. **Archive convention:** Use `docs/_archive/plans/` and `docs/_archive/governance/` flat (matching existing flat `_archive/plans/` style), or date-prefixed subdirs (matching `_archive/2026-05-09-docs-consolidation/` style)?
3. **B1/C1 review checkpoint:** Want the classification tables presented for approval before any move (Stream-B/C step 2) executes, or auto-approve archives whose closure is unambiguous?
4. **Audit ETA:** Roughly when does the 24h window close? (Affects E3 / Stream-F sequencing.)

## 8. Post-Audit Stream (Stream G — out of scope until closure commits)

Recorded here so deferred work isn't lost:

- Frozen-file ref fixes from Stream D (none identified this pass)
- VERSION-coupled README edits from Stream E
- Any debt-log housekeeping (TOC drift, header count refresh)
- ROADMAP wave-1 row update once OBS-003 closure flips OPEN → COMPLETE
- Cross-ref update for any C3-skipped governance file

### 8.1 Stream B deferred — 12 EDGE-004 lever specs (≥2 refs each)

All 12 lever-design specs in `docs/superpowers/specs/` are network-entangled (2-19 refs each from sibling specs, governance docs, and edge-004-closure-path-tldr). B1 v2 classified them SUPERSEDED (Wave-2/3 HALTED per Cycle-17), but per-file ref counts make atomic archive too risky for a non-batched move. Defer to a coordinated Wave-2/3-archive sweep that batches the moves with a single ref-fix pass:

| filename | refs | classification |
|---|---|---|
| `2026-05-03-edge-004-lever-a-source-class-diversification-design.md` | 2 | SUPERSEDED |
| `2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` | 8 | SUPERSEDED |
| `2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` | 8 | SUPERSEDED |
| `2026-05-03-edge-004-lever-b-g1-calibration-design.md` | 7 | SUPERSEDED |
| `2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` | 8 | SUPERSEDED |
| `2026-05-03-edge-004-lever-e-multi-source-corroboration-design.md` | 2 | CLOSED |
| `2026-05-03-edge-004-lever-menu-design.md` | 10 | SUPERSEDED |
| `2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` | 14 | SUPERSEDED |
| `2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` | 4 | SUPERSEDED |
| `2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` | 13 | SUPERSEDED |
| `2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` | 12 | SUPERSEDED |
| `2026-05-05-edge-004-lever-d-escalation-criteria-design.md` | 19 | SUPERSEDED |

Plan-side counterparts (where they exist in `docs/superpowers/plans/`) defer with same constraint.

Two ACTIVE specs left in place this session: `2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md`, `2026-05-05-profit-llm-001-pre-sizing-scope-design.md`. D2 fixed their TLDR-v3 cross-links.

UNCERTAIN-NEEDS-MANUAL-REVIEW resolved post-audit (Stream G G2a):
- `2026-05-03-governance-monitor-fix-design.md` — CLOSED (shipped commit `e3d4e8d`, PROFIT-GOV-003). Spec exists only in `specs/`, no plan twin. Has 8 refs spanning wave-1 runbooks → defer to runbook-archive batch (post Wave-1 stabilisation).
- `2026-05-05-next-soak-cadence-tune-design.md` — ACTIVE (deferred to PHASE2-002 next-soak per `feedback_soak_acceleration_split.md`). Keep in place.

### 8.2 Stream C deferred — 44 ARCHIVE-WITH-REF-FIX + 15 HOLD governance files

C1 v2 reverse-ref pass produced three tiers across 75 SUPERSEDED+HISTORICAL governance candidates:
- 16 SAFE-TO-ARCHIVE — committed this session (commit on-record).
- 44 ARCHIVE-WITH-REF-FIX (1-3 refs each, 85 total ref updates) — defer.
- 15 HOLD-FROM-ARCHIVE (≥4 refs or task dependencies) — defer.

When picking up Stream G C2-phase-2:
- Archive moves and ref updates must commit atomically (mv + sed in one commit per file group).
- Best ordering: archive the most-isolated subgroup first (e.g. mid-soak snapshot chain that only references each other). The pre-wave1-baseline-interpretation cluster (4 baselines all reference the same parent) is also a clean batch.
- Flag any move that would break a ref into an active cycle ledger — those refs need substantive rewrites, not path swaps.

### 8.3 Stream D deferred — broken refs in archive-bound files

D1 found 17 broken refs inside `2026-05-09-docs-directory-consolidation.md` (now archived in `_archive/plans/`). Refs point at `docs/housekeeping/2026-05-09/docs-consolidation/*` (deleted by Stream A3). Fixes obviated — archive copy can carry stale refs, and original tracking content is recoverable via `git show ff5f35d^:docs/housekeeping/...`.

Six refs in SUPERSEDED EDGE-004 lever specs (lever-d-escalation-criteria-design lines 6/71/99, lever-b-2-0.03-floor-followup-stub line 77) defer with their parent files via §8.1.

D1 also flagged `2026-05-09-docs-directory-consolidation.md` line 473 (missing `docs/` prefix on `profit_path_debt_log.md`) — moot post-archive.

### 8.4 Session execution receipt (controller worklog)

**Pre-audit-close session (2026-05-09):**

| Stream | Action | Commit |
|---|---|---|
| A2 | Track OBS-003 plan | `bfe865b` |
| A3 | Drop 33 housekeeping working files | `ff5f35d` |
| D2 | Redirect 2 TLDR-v3 cross-links to `_archive/` | `2225bb3` |
| B2-narrowed | Archive 2 consolidation-initiative plans (0 refs) | `af0434f` |
| C2-phase-1 | Archive 16 SAFE-TO-ARCHIVE governance one-offs (0 refs each) | `046bed6` |
| E2 | Repair `CLAUDE.md` housekeeping ref → `_archive/plans/` redirect | `611dedd` |

OBS-003 audit closed at 9h (early-closed on operator decision; invariant `OPPORTUNITY(7) = SKIPPED(5) + PAPER_TRADE(2)`, delta=0; SKIPPED monoculture broken; 0 safety-counter trips). Closure commit `66003b4` flipped PROFIT-OBS-003 OPEN→COMPLETE; counters HIGH 5→4, COMPLETE 36→37.

**Post-audit Stream G session (2026-05-10):**

| Round | Action | Commit |
|---|---|---|
| G2a | Re-classify 2 UNCERTAINs: governance-monitor-fix CLOSED, next-soak-cadence-tune ACTIVE | (research only) |
| G2b | Sub-batch 44 ARCHIVE-WITH-REF-FIX into 6 clean batches | (research only) |
| G2c | Re-examine 15 HOLD: 1 SAFE / 2 ARCHIVE-WITH-REF-FIX / 12 HOLD | (research only) |
| R1 | Archive `doc-xref-audit-report` (0 refs verified) | `fe0395b` |
| R2 | Archive 4 pre-wave1 baselines + 8 ref updates in baseline-interpretation | `367e582` |
| R3 | Archive 9 adversarial reviews + 9 entries in doc-index-audit | `0905c37` |
| R4 | Archive 4 doc-index audit refs cluster + 4 list updates | `d7be17f` |
| R5 | Archive 4 source-of-truth cycle-10 cluster + 5 ref updates | `14ae2d6` |
| R6 | Archive 2 HOLD downgrades (legal-niche probe + lever-a1-plus-1-5 sizing) + 6 ref updates | `792c9da` |
| R7 | Archive cycle-16d m3-fetch + price-source-inventory pair + 1 ref update | `e46af55` |
| R8 | 3 SAFE singletons (mid-soak-snapshot-4, day-4-mid-soak-sanity-check, match001-bprime-spec-parity-verification) — newly safe after R3 archived parent referencers | `89b6d6b` |
| R9 | Mid-soak chain (health-report + snapshots 2-3); broken refs in archive-bound HOLD/spec parents acceptable | `b08d99a` |
| R10 | snapshot-5 + active ref update in day-4-mid-soak-confirmation | `b673a94` |
| R11 | PHASE2-001 baselines (llm-throughput + source-class-evolution) + soak-runtime-characterization-summary parent + risk-register ref update | `1ab9136` |
| R12+13 | vitallaw-archive-forensics + db-backup-gap-resolution combined | `335f028` |
| R14 | 4-file batch (lever-domain-normalized 0-refs + version-bump-dryrun + legal-cycle adv-review + pre-day7-dry-run-rehearsal) | `fae7f84` |
| R15 | cycle-15b 4-file artifact cluster (charter-extraction-rebuild, claude-independent-trace-read, pre-execution-criteria-verification, task-split) + 7 ref edits | `a7b1f0f` |
| R16 | cycle-16d 3-file artifact cluster (charter-price-reconstruction, pre-execution-criteria-verification, task-split) + 5 ref edits | `e6d2f9a` |
| R17 | HOLD adversarial-review chain (claude-commits-c5cbc6f, cdbf6ef, claude-latest-commits) + 9 ref edits | `4757fc1` + `8bb9efb` followup |
| R18 | cycle-15b-paper-trades-cohort-note + 7 ref redirects (incl. IC §16 Rule 6) | `895da41` |
| R19 | governance-monitor-fix-design.md spec → `_archive/specs/` + 5 wave-1 runbook ref edits | `36b0c6e` |
| R20 | cycle-16e-task-split + 4 ref redirects in cycle-16e rescope/forensics | `b884bcd` |
| R21 | post-verdict-action-checklists (cycle-15b + cycle-16d) + 7 ref edits incl. debt log (first post-audit debt log edit) | `b65d579` (incl. R21 + followup) |
| R22 | vitallaw-direct-rss-probe (HOLD downgrade, Branch B kill) + 5 ref edits | `9e3ec52` |
| R23 | day-7-pre-soak-confirmation (HOLD downgrade) + 2 ref edits | `190986f` |

**Stream G session moves: 59 files archived across 24 commits.**

### 8.5 Stream G unfinished work (defer to next session)

**12 EDGE-004 lever specs (§8.1) — biggest remaining batch.** Reverse-ref check (post-R23) shows 35+ active doc files reference these specs across `docs/governance/`, `docs/IMPLEMENTATION_CONTRACT.md`, `docs/profit_path_debt_log.md`, `docs/ROADMAP.md`, and other lever specs themselves. Refs use mixed formats (bare basename vs full path); bulk sed too risky without per-format scoping. Recommended approach for next session:
- Build a structured ref-update pass (extract every ref site by path/basename) before any `git mv`
- Atomic single commit covering all 12 mvs + all ref redirects
- ROADMAP touch is operator-call territory — coordinate with operator

**Smaller remaining items:**
- wave-1-deploy-dry-run-report (3 active refs: rollback-runbook-validation + doc-index-audit + cross-cycle-contract-adherence-review)
- HOLD wave-2 inputs (a1plus1-5-branch-c-feed-selection-rubric 17 refs — heavy)
- 2 governance files referenced by debt log (doc-index-audit, doc-index-cleanup-execution-plan) — touch debt log + 5 governance refs to archive both
- pre-wave1-source-of-truth-doc-index, pre-wave1-risk-register (HOLD parents — fewer refs each but still active)
- ROADMAP Wave-1 row "active" → "complete" flip (operator call)
- ~85 files remain in `docs/governance/` — most ACTIVE cycle ledgers / runbooks / wave-1 prestaged docs / cycle-17 charter cluster / day-7-attestation cluster (per C1 v2 ACTIVE list)

---

**Status:** Stream G partially executed. Audit-frozen invariant fully preserved across 13 commits this session-pair. Plan can be archived once all Stream G work clears; until then, this is the controller doc.
