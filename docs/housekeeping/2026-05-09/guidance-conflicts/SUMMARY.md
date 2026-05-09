# Phase 6 Conflict-Detection — SUMMARY

**Generated:** 2026-05-08
**Branch:** `housekeeping/guidance-consolidation` (off `main` @ `e0f94bb`)
**Inputs synthesized:**
- [`intra-cluster-conflicts.md`](./intra-cluster-conflicts.md) — Phase 6 Agent 1 (comment-analyzer)
- [`code-vs-doc-drift.md`](./code-vs-doc-drift.md) — Phase 6 Agent 2 (code-explorer)

---

## Headline

**Total findings:** 33 (30 intra-cluster + 3 code-vs-doc drift).
**Safety-critical regressions:** **none.** All 7 load-bearing project-CLAUDE.md claims verified present and correct in the live tree (signing alg, qwen3 `think`, polarity block, market-status filter, env-var rename, transaction wrapper, `_normalize_pem` duplication).
**Trust-hierarchy violations:** 4 HIGH conflicts where Layer 1 rules disagree with reality (Windows deprecation, format self-contradiction).
**Audit-document staleness:** 3 of the 30 intra-cluster findings are stale audit findings from Phase 1/2 that the live tree already addressed — Phase 7 should mark these "WON'T FIX (already resolved)" rather than acting on them.

## Severity buckets

| Bucket | Intra-cluster | Code-vs-doc | Total | Phase 7 default action |
|--------|--------------:|------------:|------:|------------------------|
| HIGH (direct behavior risk) | 9 | 0 | 9 | Resolve in Phase 8 batch 1 |
| MEDIUM (maintenance / clarity) | 9 | 1 | 10 | Resolve in Phase 8 batch 2 |
| LOW (informational / cosmetic) | 12 | 2 | 14 | Bundle into batch 3 or defer |

(Distribution sums match: 30 / 3 / 33.)

---

## Phase 7 work clusters

Six logical groups emerge from the findings. Phase 7 should produce a per-item action plan; these clusters predict the structure.

### Cluster 1 — Windows-runtime deprecation cleanup (HIGH)
- **#1** (HIGH): `~/.claude/rules/windows_local.md` NSSM rule still active, but PLATFORMS.md declares Windows runtime deprecated.
- **#14** (MEDIUM): `windows_local.md` `E:` drive rule — same root cause.
- **#28** (LOW): duplicate of #14 from a different angle.
- **Cut #35, #36** (deferred): Python 3.14 / aiohttp pin and Mac+Windows-Reddit-403 gotchas — Windows half no longer applicable.

**Resolution shape:** either (a) delete `windows_local.md` outright, (b) downgrade to `windows_local_archived.md` with a deprecation banner, or (c) retain only platform-neutral content. Phase 7 picks; Phase 8 executes.

### Cluster 2 — `documentation_format.md` self-contradiction + format-mix in project CLAUDE.md (HIGH)
- **#2** (HIGH): project CLAUDE.md "Release Versioning" uses Trigger/Action inside a narrative-only file.
- **#3** (HIGH): `documentation_format.md` says "split mixed-format files" AND "reject findings about CLAUDE.md format divergence" — internal self-contradiction.
- **#25** (LOW): project CLAUDE.md cites `executor.py:218` line number — soft violation of "cite behavior, not lines" rule.
- **#26** (LOW): global CLAUDE.md Tooling Preferences uses hybrid format.

**Resolution shape:** rewrite `documentation_format.md` Trigger 4 (the "reject" clause) to exclude existing tolerated mixes; OR split the Release Versioning section into a new `~/.claude/rules/release_versioning_kalshi.md` (or project-local `kalshi-bot/rules/release_versioning.md`). Decision feeds Cluster 6 (CLAUDE.md de-duplication).

### Cluster 3 — Code comment ↔ CLAUDE.md gotcha contradictions (HIGH)
All four originate from inline source comments contradicting the live behavior (or CLAUDE.md claim). These are *code edits*, not doc edits — but they're guidance because Phase 2 audit promoted them to guidance signals.

- **#4** (HIGH): `analysis/__init__.py:19` says "$50 hard cap"; CLAUDE.md says cap is dynamic.
- **#5** (HIGH): `analysis/evidence_scorer.py:48` docstring says "trigrams"; code is bigrams.
- **#6** (HIGH): `analysis/kelly.py:159` says "rounds down to stay within budget"; `max(1, int(...))` can exceed.
- **#7** (HIGH): `analysis/fade_signal.py:10-13` says "WebSocket-based replacement"; both fade strategies run in parallel.

**Resolution shape:** four targeted comment edits in Python files. Phase 8 batch covers these together; tests must remain at 1652 passing.

### Cluster 4 — Stale audit-document findings (HIGH/MEDIUM, but spurious)
These look like high-severity issues but the live tree already resolved them. Phase 7 should mark them "ALREADY RESOLVED — no action."

- **#8** (HIGH): `general-quality.md` G-7 says `domain_constraints.md` is "silent on stats"; live rule already says "no stats/aggregation modules."
- **#17** (MEDIUM): `gap-analysis.md` C-1..C-4 listed as "proposals" — all four are live in rule files.
- **#19** (LOW): `reference-integrity.md` flags RTK.md:29 BROKEN; live RTK.md is correct.
- **#21** (LOW): F-4 says `domain_constraints.md` cycle-17C label has "no path"; live file has the path.
- **#30** (LOW): G-6 says `documentation_format.md` not referenced from CLAUDE.md; cross-ref exists.

**Resolution shape:** either (a) annotate the audit docs in-place with "FIXED IN PHASE 3" markers, (b) move them to `docs/housekeeping/2026-05-09/foundational-docs-audit/RESOLVED/`, or (c) delete (initiative-completion docs are historical). Recommend option (a) — preserves trail.

### Cluster 5 — CLAUDE.md duplication between global + project (MEDIUM)
- **#10** (MEDIUM): Working Style — 4 of 5 bullets verbatim duplicate.
- **#11** (MEDIUM): Bug-Fixing Preference — exact duplicate.
- **#12** (MEDIUM): Continuous Improvement — first bullet verbatim duplicate.
- **#13** (MEDIUM): Rule cross-references — 3 of 4 verbatim duplicate.
- **#18** (MEDIUM): two AGENTS.md files differ on step 4 (`project-root` vs `project/`).
- **#24** (LOW): `~/.claude/project/AGENTS.md` architecture summary overlaps `domain_constraints.md`.
- **#31** (cut): same as #24.

**Resolution shape:** project CLAUDE.md keeps only project-additive content (e.g., the unified-tracking-system bullet); deletes verbatim duplicates and relies on inheritance. Phase 7 must confirm the inheritance path actually works for this user's setup.

### Cluster 6 — Stale inline labels / cross-references (MEDIUM-LOW)
- **#9** (HIGH): `feedback_edge_priority_over_deploy_safety.md` embeds 3-day-old P&L metrics + tags existing doc as "FUTURE."
- **#15** (MEDIUM): `signal_analyzer.py:547-552` "Revert if 12h re-run..." — P0.4 decision closed.
- **#16** (MEDIUM): `signal_analyzer.py:37` "Cycle-15B diagnostics" — current cycle 17C.
- **#22** (LOW): `regime_classifier.py:178` references `lessons.md` — file gone.
- **#23** (LOW): same auto-memory entry tags `edge-replay-cycle12-report.md` as FUTURE — file exists.
- **#27** (LOW): README dead links to `transfer/macbook_handoff_2026-05-01/`.
- **#29** (LOW): auto-memory cites commit SHA `c913ffd` — fragile.

Plus drift findings from Agent 2:
- **drift-#1** (MEDIUM): CLAUDE.md calls `resolve_market()` "thin wrapper"; it also emits calibration events.
- **drift-#2** (LOW): CLAUDE.md pins `executor.py:218` for guard rationale; line 218 actually documents data source.
- **drift-#3** (LOW): MEMORY.md uses symbol `p_yes_at_decision_time` not present in code (`market_yes_price` is the live name).

**Resolution shape:** mostly comment edits + auto-memory updates + README link cleanup. Phase 8 bundles these.

---

## Findings to *not* act on (stale audit reuse)

| # | Why skip |
|---|----------|
| #8 | `domain_constraints.md` already says what G-7 wants |
| #17 | C-1..C-4 already live in rule files |
| #19 | RTK.md:29 already fixed |
| #21 | `domain_constraints.md` cycle-17C path already added |
| #30 | `documentation_format.md` cross-ref already in project CLAUDE.md |

These appear in Phase 1/2 audit notes under `docs/housekeeping/2026-05-09/foundational-docs-audit/`. Phase 7 should produce a single "RESOLVED in Phase 3" annotation file rather than 5 separate edits.

---

## Trust-hierarchy summary

| Trust layer | Conflicts where this layer is "Side A" | Conflicts where this layer is "Side B" |
|-------------|---------------------------------------:|---------------------------------------:|
| 1 — `~/.claude/rules/*` | 7 | 5 |
| 2 — project CLAUDE.md Critical Gotchas | 4 | 5 |
| 3 — project CLAUDE.md Working Style | 1 | 4 |
| 4 — `~/.claude/CLAUDE.md` | 1 | 4 |
| 5 — auto-memory | 3 | 3 |
| 6 — `docs/superpowers/specs/` | 0 | 0 |
| 7 — scratch/loose `.md` (incl. inline code comments + audit docs) | 14 | 11 |

**Reading:** Most conflicts pit Layer 1/2 against Layer 7 (code comments and stale audit docs). The Layer-1-internal conflict (#3, `documentation_format.md` self-contradiction) is the highest-leverage fix because every downstream format decision depends on it.

---

## Recommendation for Phase 7

Phase 7 should produce **one consolidation plan file** with these sections, in this order:

1. **Layer-1 self-contradictions** (Cluster 2 — fix `documentation_format.md` first; everything else flows from this).
2. **Windows-deprecation purge** (Cluster 1 — single decision, then mechanical edits).
3. **Code-comment fixes** (Cluster 3 — four edits, all in `analysis/`).
4. **Stale-audit annotation** (Cluster 4 — single batch annotating Phase 1/2 audit docs).
5. **CLAUDE.md / AGENTS.md de-duplication** (Cluster 5 — depends on #1 outcome).
6. **Cross-reference + label cleanup** (Cluster 6 — bundle).

Each section uses the KEEP / MOVE / MERGE / DELETE / DEPRECATE / SPLIT verbs. Cap at 200 entries.

Phase 8 batches map 1:1 onto these six sections. Recommend executing in order — Cluster 2 (rule file) blocks Cluster 5 (CLAUDE.md), and Cluster 1 (Windows) is independent so can run in parallel with Cluster 3 (code comments).

---

## Test invariant

Phase 8 must hold: **1652 passed, 2 skipped, 116 xfailed**. Code-comment fixes (Cluster 3) are the only Phase 8 work that touches Python source — those edits change docstring/comment text only and must not move the test count.
