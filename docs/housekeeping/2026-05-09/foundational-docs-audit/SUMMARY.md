# Foundational Docs Audit — SUMMARY (Phase 2)

> Read-only multi-lens audit of foundational docs (CLAUDE.md project + global, AGENTS.md, RTK.md, all 10 rule files, README cross-check).
>
> **Audit date:** 2026-05-09 UTC.
> **Branch:** `housekeeping/foundational-docs`.
> **Predecessor:** `docs/housekeeping/2026-05-08/claude-md-audit.md` (Phase 1, 2026-05-08).
> **Output dir:** `docs/housekeeping/2026-05-09/foundational-docs-audit/`.
>
> No source files modified. All findings are recommendations awaiting user approval.

---

## 1. Lens Reports

| # | Lens | File | Findings | High points |
|---|------|------|----------|-------------|
| 1 | Drift detection | [`drift-detection.md`](drift-detection.md) | 0 DRIFT, 0 BROKEN; 17/17 gotchas PASS | 3 Phase 1 P1s resolved between 2026-05-08 and 2026-05-09; line-level verification |
| 2 | Reference integrity | [`reference-integrity.md`](reference-integrity.md) | 46/55 RESOLVES, 2 BROKEN, 2 AMBIGUOUS, 7 NOT-VERIFIABLE | All 23 project CLAUDE.md refs resolve; only RTK.md:29 + README handoff links broken |
| 3 | General quality | [`general-quality.md`](general-quality.md) | Avg 84/100 (B); project CLAUDE.md 90/100 (A−) | New finding: `/analysis` rule may have drifted vs `tasks/stats/` move |
| 4 | Gap analysis | [`gap-analysis.md`](gap-analysis.md) | 4 candidate rules (3 high-confidence, 1 medium) | C-1 fail-fast / C-2 line-citation / C-3 absolute-dates / C-4 verify-before-recommend |
| 5 | Context cost | [`context-cost.md`](context-cost.md) | ~4,915 tokens, ~2.5% of 200K window | Not a context-pressure problem; max optimization ceiling ~120 tokens |

---

## 2. Severity Matrix

| Severity | Count | Items |
|----------|-------|-------|
| **P0** | 0 | — |
| **P1** | 0 | — (all Phase 1 P1s resolved between 2026-05-08 and 2026-05-09) |
| **P2** | 4 | F-1, G-7, C-1, C-3 |
| **P3** | 14 | F-2, F-3, F-4, D-2, D-3, M-4, R-1–R-4, G-2, G-4, G-5, G-6, C-2, C-4, hook-launchd-undocumented |

### P2 detail

| ID | Finding | Source | Effort | Action |
|----|---------|--------|--------|--------|
| **F-1** | README:243-258 — 9 dead links to `transfer/macbook_handoff_2026-05-01/` (dir removed from git history). README:219 self-documents the removal but markdown links remain broken. | reference-integrity §3 | S | Annotate as historical or convert to plain text |
| **G-7** | `domain_constraints.md` `/analysis` purity rule may have drifted vs commit `8acfc47` move of stats modules from `analysis/` to `tasks/stats/`. Rule literal wording forbids only "API calls or trade execution"; current practice is stricter. | general-quality §2.5 | M | **Open Question — see §4** |
| **C-1** | Cross-cutting "fail-fast on security/observability paths" not codified. Backed by `ce70924` (PROFIT-SEC-001) and `1d0714c` (PROFIT-OBS-006). | gap-analysis §3 | S | New Trigger/Action in `risk_review.md` |
| **C-3** | "Convert relative dates to absolute UTC when persisting" not codified. Backed by user-level auto-memory guidance and project's commit-naming practice. | gap-analysis §3 | S | New Trigger/Action in `portability.md` |

### P3 detail (14 items)

**Phase 1 carry-overs (still open):**
- **D-2** — `resolve_market()` naming imprecision (transaction logic in `_resolve_market_sync()`). Cosmetic.
- **D-3** — Same-signal guard "must query all open trades" implies DB; implementation is in-memory Portfolio. Code now has self-documenting comment at `executor.py:218`.
- **M-4** — `_normalize_pem()` duplication note missing from PEM gotcha (identical copies in `rest_client.py` and `websocket_client.py`).
- **R-1 / R-2 / R-3 / R-4** — Working Style / Bug-Fixing / Continuous Improvement sections ~90% verbatim copies of global CLAUDE.md (~14 lines, ~100 tokens dedup-able).
- **F-2 (Phase 1 SR-1)** — RTK.md:29 circular reference ("Refer to CLAUDE.md for full command reference" — global CLAUDE.md has no RTK section).

**New since Phase 1:**
- **F-3** — AGENTS.md:13 `project/AGENTS.md` path notation; no `project/` directory exists.
- **F-4** — `domain_constraints.md:19` cites `cycle-17C` label without a charter path.
- **G-2** — Recent fail-fast / SQLite-silent fixes (PROFIT-SEC-001, PROFIT-OBS-006) not captured as gotchas. Optional — see C-1 instead.
- **G-4** — Line-citation rot risk after `signal_analyzer.py` 4-commit refactor. Existing gotchas survived because they cite behavior, not lines.
- **G-5** — Cross-cutting "fail-fast on security/observability" lesson not yet captured anywhere. (Same root as C-1.)
- **G-6** — `documentation_format.md` (new since Phase 1) not cross-referenced from project CLAUDE.md.
- **C-2** — Line-citation convention not codified in `documentation_format.md`. Append candidate, high confidence.
- **C-4** — "Verify before recommending" not explicitly codified in `editing_safety.md`. Medium confidence — could be argued as already covered.
- **Hook launchd-audit undocumented** — `.githooks/pre-commit:15-21` runs `scripts/launchd_template_equivalence_audit.py` on `.plist.template` staging; not in CLAUDE.md Release Versioning section.

---

## 3. Cross-Lens Resolution Snapshot

Phase 1 → Phase 2 status:

| Phase 1 ID | Description | Phase 1 severity | Phase 2 status |
|-----------|-------------|------------------|----------------|
| **D-1** | `KalshiRestClient` "HMAC-SHA256" docstring | P2 | **RESOLVED** (`rest_client.py:55-56`) |
| **M-1** | Missing anchor_rate polarity gotcha | P1 | **RESOLVED** (project CLAUDE.md:68) |
| **M-2** | `think: False` endpoint warning missing | P1 | **RESOLVED** (project CLAUDE.md:67) |
| **M-3** | Missing `/governance` domain constraint | P2 | **RESOLVED** (`domain_constraints.md:15-22`) |
| D-2 | `resolve_market()` naming | P3 | UNCHANGED |
| D-3 | Same-signal guard mechanism | P3 | UNCHANGED |
| M-4 | `_normalize_pem` dup note | P3 | UNCHANGED |
| R-1 / R-2 / R-3 / R-4 | Working Style etc. dedup | P3 | UNCHANGED |
| **SR-1** | RTK.md:29 circular reference | P3 | UNCHANGED (= Phase 2 F-2) |

3 of 4 P1 recommendations and 1 of 1 P2 recommendation from Phase 1 were resolved in the day between audits. Carry-overs are all P3 cosmetic.

---

## 4. Open Questions (User Judgment Required)

### Q1 — `/analysis` purity rule update (G-7, P2, M)
Commit `8acfc47` moved stats modules from `analysis/` to `tasks/stats/`. The current `domain_constraints.md` rule says `/analysis: keep it pure; do not add API calls or trade execution.` Stats are neither, but the move suggests stricter practice than the rule's literal wording.

Options:
- **(a) Tighten** — change to "pure analysis only; no stats/aggregation, no API calls, no execution." Matches practice.
- **(b) Loosen with note** — leave as-is; add a note that stats happen to live in `tasks/stats/` for orchestration reasons.
- **(c) No change** — rule is technically still satisfied; the move was organizational.

Recommend (a) or (c). (b) creates a maintenance burden without a clear win.

### Q2 — C-4 "verify before recommending" rule (P2, S, medium confidence)
Argument for new rule: failure mode (recommending without verifying) is distinct from existing `editing_safety.md` "before editing" trigger.
Argument against: implicit in existing "inspect the relevant files" guidance; adds another trigger to evaluate.
**Recommend:** add the new section. Cheap (~30 tokens), clarifies the recommend-vs-edit distinction surfaced in user-level auto-memory rules.

### Q3 — G-6 cross-reference `documentation_format.md` from project CLAUDE.md?
Project CLAUDE.md cross-refs `planning`, `validation`, `git_workflow`, `release_versioning` — but not `documentation_format`. One-line addition would make the format-by-purpose principle visible without diving into rule files.
**Recommend:** add it. One line, no downside.

### Q4 — F-3 AGENTS.md `project/AGENTS.md` notation
What was originally intended? Literal directory path? Conceptual role label?
**Recommend:** clarify to "project-root `AGENTS.md`" — matches the actual filesystem layout.

### Q5 — F-5 / Global CLAUDE.md MCP tool names re-verification
7 MCP tool names (semantic_search_nodes, query_graph, get_impact_radius, detect_changes, ctx_batch_execute, ctx_execute, ctx_execute_file) could not be verified this session. Re-verify in a session with both `code-review-graph` and `context-mode` plugins loaded.
**Recommend:** mark as a deferred audit task; not blocking.

### Q6 — Append findings to `docs/profit_path_debt_log.md`?
Per the original task constraints: "DO NOT append findings to docs/profit_path_debt_log.md without user approval." This audit is more meta than the others. Suggested entries (only if user approves):
- One line for G-7 (domain_constraints `/analysis` rule drift)
- One line for the four new-rule candidates if accepted (C-1, C-2, C-3, C-4)

**Recommend:** wait for explicit approval before any debt-log append.

---

## 5. New Rules to Consider (from gap-analysis.md)

All four are small additions to existing rule files — no new rule file needed.

### C-1 (high confidence, P2, S) — Fail-fast on security/observability paths
**Target:** New Trigger/Action in `~/.claude/rules/risk_review.md`.

```
- Trigger: when adding or modifying code on a security-critical path (auth, signing, secrets, money movement) or an observability write path (metrics, audit logs, decision traces).
  Action: prefer raising over swallowing exceptions; let the caller decide whether to recover. Silent failure on these paths produces false-pass conditions that hide real defects from operators.
```

### C-2 (high confidence, P3, S) — Cite behavior, not line numbers
**Target:** Append to `~/.claude/rules/documentation_format.md`.

```
- Trigger: when authoring or editing CLAUDE.md gotchas.
  Action: cite behavior or function/class names, not line numbers. Line citations rot at the next refactor. Exception: cite line numbers when the rule is "do not touch these specific lines" (e.g. governance/prompts.py:27-31 polarity block).
```

### C-3 (high confidence, P2, S) — Convert relative dates to absolute UTC
**Target:** Append to `~/.claude/rules/portability.md`.

```
- Trigger: when persisting a date that originated as a relative reference (e.g. "Thursday", "next week", "in two days") to memory, logs, plans, or any durable surface.
  Action: convert it to absolute UTC at write time before persisting. Relative anchors lose meaning once the originating conversation rolls off.
```

### C-4 (medium confidence, P2, S) — Verify before recommending
**Target:** New section in `~/.claude/rules/editing_safety.md`.

```
- Trigger: before recommending an action that depends on a specific file path, function name, env var, or flag still existing.
  Action: verify the reference resolves now (Read, grep, or graph query). Memory and prior documentation can be stale; the live working tree is authoritative when the user is about to act.
```

---

## 6. Recommended Action Plan (if user approves)

Grouped for batched commits — not for execution without approval.

### Batch A — P2 (worth doing) — 1 commit
1. **F-1** Annotate or rewrite README:243-258 dead handoff links.
2. **G-7** Resolve `/analysis` purity rule per Q1 — recommend (a) tighten.
3. **C-1** Add fail-fast Trigger/Action to `risk_review.md`.
4. **C-3** Add absolute-date Trigger/Action to `portability.md`.

### Batch B — P3 (carry-overs + new rules) — 1 commit
5. **D-2** Clarify `resolve_market` naming in CLAUDE.md.
6. **D-3** Tighten same-signal guard description.
7. **M-4** Add `_normalize_pem` duplication note.
8. **F-2 (Phase 1 SR-1)** Fix or delete RTK.md:29.
9. **F-3** Clarify AGENTS.md:13 `project/AGENTS.md` path.
10. **F-4** Add `cycle-17C` charter path to `domain_constraints.md:19`.
11. **C-2** Add line-citation Trigger/Action to `documentation_format.md`.
12. **C-4** (subject to Q2) Add verify-before-recommend section to `editing_safety.md`.

### Batch C — P3 cosmetic (optional) — 1 commit
13. **R-1 to R-4** Dedup project CLAUDE.md Working Style / Bug-Fixing / Continuous Improvement.
14. **G-6** Cross-reference `documentation_format.md` from project CLAUDE.md.
15. **Hook launchd-audit** Add one sentence to Release Versioning section.

### Deferred
- **F-5** Re-verify MCP tool names in a session with plugins loaded.

---

## 7. Three-Bullet Summary

- **Foundational docs are in excellent shape.** Zero P0 or P1 findings. All 17 project CLAUDE.md gotchas verified accurate at line level. Three Phase 1 P1 recommendations resolved in the intervening day. Context cost ~4,915 tokens (~2.5% of 200K) — not a pressure point.
- **Four P2 items worth user attention.** F-1 (README dead links), G-7 (`/analysis` rule vs `tasks/stats/` move — open question), C-1 (codify fail-fast principle from two recent fix commits), C-3 (codify absolute-date conversion). All four are small, surgical changes.
- **Four new rule candidates proposed; none require a new rule file.** All four (C-1 through C-4) append or add a section to existing rule files. C-1, C-2, C-3 are high-confidence; C-4 is medium and the only candidate where "already covered" is debatable.

---

> Foundational docs audit complete. Review SUMMARY.md before approving any doc edits or new rule additions.
