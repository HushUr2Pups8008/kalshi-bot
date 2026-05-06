# 2026-05-06 doc xref audit triage

**Type:** read-only triage of cycle-7 + cycle-8 audit findings (Claude task per Implementation Contract §9 — review).
**Source:** Codex's `scripts/doc_xref_audit.py` (cycle 7) + cycle-8 audit-report.md.
**Drafted:** 2026-05-06 (cycle 9).
**Companion:** `2026-05-06-doc-xref-audit-report.md` (Codex's findings inventory); `2026-05-05-doc-cross-link-integrity-audit.md` (Claude cycle-7 narrower audit).

## TL;DR

**184 broken refs surfaced by Codex's broader audit (63 markdown link + 121 backtick) reduce to 0 real Wave-1-blocking breakage after triage.** Categorical breakdown:

| category | count | classification | action |
|---|---:|---|---|
| Archive doc off-by-one `../` paths | 44 | non-load-bearing per cycle-2 doc-index audit; legitimate breakage but irrelevant | leave |
| Prestaged-changelog repo-root paths from `docs/governance/` source | 13 | semantically correct for paste-target (`CHANGELOG.md`); audit doesn't understand block context | annotate (cycle 9 fix) |
| Placeholder strings in audit/explanation text | 4 | syntax fragments (`...`, `path`); not actual links | leave |
| Memory file cross-refs to `~/.claude/projects/` | 2 | intentional cross-system reference | leave |
| Self-references in audit reports | 1 | self-referential by design | leave |
| Backtick-formatted module names in prose | 121 | code-formatting backticks (e.g., `` `dossier_builder.py` ``); not hyperlinks | improve audit script |

**0 file-content fixes warranted.** 2 process improvements applied this cycle.

## Detailed triage

### Category 1: Archive doc off-by-one `../` paths (44 of 64)

Examples:
- `docs/_archive/studies/profit_cal_001_calibration_wiring.md:185 -> ../profit_path_debt_log.md` (should be `../../profit_path_debt_log.md`)
- `docs/_archive/studies/polymarket_venue_integration_investigation.md:9 -> ../ROADMAP.md` (should be `../../ROADMAP.md`)
- `docs/_archive/studies/polymarket_venue_integration_investigation.md:59 -> ../../CLAUDE.md` (resolves to `docs/_archive/CLAUDE.md` which doesn't exist; should be `../../../CLAUDE.md`)

**Root cause:** archive docs were authored when they lived at `docs/` directly (or `docs/<subdir>/`) and got moved to `docs/_archive/studies/` without updating relative paths. The path drift is consistent with a directory-level move, not editorial neglect.

**Action: NONE.** Per `2026-05-05-doc-index-audit.md` §"Recommended cleanup actions" + `2026-05-05-doc-index-cleanup-execution-plan.md`: archive docs are non-load-bearing historical record. Cross-link integrity in archive is not maintained.

### Category 2: Prestaged-changelog repo-root paths (13 of 64)

Examples:
- `docs/governance/wave-1-changelog-entry-prestaged.md:35 -> docs/governance/post-soak-close-rehearsal-checklist.md`
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md:39 -> docs/governance/edge-004-closure-path-tldr.md`

**Root cause:** these are written inside ` ```markdown ` fenced blocks intended to be pasted into `CHANGELOG.md` at repo root. From `CHANGELOG.md` at repo root, `docs/governance/<file>.md` resolves correctly. The audit script resolves links from the SOURCE file's location (`docs/governance/`), where these paths are dead — but that's the wrong resolution context for prestaged-block content.

**Action: ANNOTATE both prestaged-changelog files** with explicit "audit note" explaining the paste-target semantics. Applied this cycle:
- `docs/governance/wave-1-changelog-entry-prestaged.md` (cycle 9)
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` (cycle 9)

Future: if `doc_xref_audit.py` learns prestaged-block awareness (e.g., via a `<!-- audit-skip-block -->` HTML comment), the annotation can be replaced with a marker. For now, annotation suffices.

### Category 3: Placeholder strings in audit/explanation text (4 of 64)

Examples:
- `docs/governance/2026-05-05-changelog-drift-check.md:60 -> ...`
- `docs/governance/2026-05-05-doc-cross-link-integrity-audit.md:4 -> *.md`
- `docs/governance/2026-05-05-doc-cross-link-integrity-audit.md:82 -> path`
- `docs/governance/2026-05-06-doc-xref-audit-report.md:17 -> path`

**Root cause:** the strings `...`, `*.md`, `path` appear in code-snippet examples / prose explanations. The audit's link regex pattern `[text](path)` matches them but they're not real links.

**Action: NONE.** False positives by audit-construction. Improving regex to exclude single-token / glob-pattern targets would help; out of scope for this cycle.

### Category 4: Memory file cross-refs (2 of 64)

Examples:
- `docs/governance/2026-05-05-memory-hygiene-audit.md:61 -> feedback_soak_confirmation_cadence.md`
- `docs/governance/2026-05-05-memory-hygiene-audit.md:62 -> feedback_soak_acceleration_split.md`

**Root cause:** intentional cross-references to memory files in `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/`. Not in `docs/`; not meant to be repo-resolvable.

**Action: NONE.** Intentional out-of-repo references.

### Category 5: Self-reference (1 of 64)

- `docs/governance/2026-05-06-doc-xref-audit-report.md:17 -> path`

Already covered in Category 3.

### Category 6: Backtick-formatted module names in prose (121 of 121 backtick-style)

Examples (all from `docs/IMPLEMENTATION_CONTRACT.md`):
- `` `dossier_builder.py` ``
- `` `evidence_store.db` ``
- `` `decision_blender.py` ``
- `` `regime_classifier.py` ``
- (...and 117 more)

**Root cause:** Markdown backticks are used for inline code formatting (`` `text` `` renders as monospace). The audit's backtick sweep flags any backtick-formatted text matching a path-like pattern (e.g., `*.py`, `*.md`) as a potential dead link. But these are prose-level mentions of module names, not hyperlinks.

**Action: improve `doc_xref_audit.py` to drop the backtick sweep entirely.** Backtick-formatted code in markdown is fundamentally NOT a link — distinguishing prose-mention from intended-link without rich semantic analysis is intractable. The markdown-link audit (`[text](path)`) is reliable; the backtick sweep is fundamentally noisy.

Cycle 9 will improve the audit script.

## Per-source-file impact summary

After triage, real broken links in load-bearing docs:

| source file | dead-link count | category | real fix needed? |
|---|---:|---|---|
| `_archive/studies/polymarket_venue_integration_investigation.md` | 26 | Category 1 | NO (archive) |
| `_archive/studies/profit_cal_001_calibration_wiring.md` | 13 | Category 1 | NO (archive) |
| `_archive/studies/news_sources_evaluation.md` | 5 | Category 1 | NO (archive) |
| `wave-1-changelog-entry-prestaged.md` | 7 | Category 2 | NO (annotated) |
| `wave-2-wave-3-changelog-entries-prestaged.md` | 6 | Category 2 | NO (annotated) |
| `2026-05-05-changelog-drift-check.md` | 2 | Category 3 | NO (placeholder) |
| `2026-05-05-doc-cross-link-integrity-audit.md` | 2 | Category 3 | NO (placeholder) |
| `2026-05-05-memory-hygiene-audit.md` | 2 | Category 4 | NO (intentional) |
| `2026-05-06-doc-xref-audit-report.md` | 1 | Category 5 | NO (self-ref) |

**Net real fixes: 0 file-content edits.** 2 annotations applied (cycle-9 process improvement). 1 audit-script improvement queued.

## Summary

Codex's broader audit was valuable for surfacing the prestaged-changelog confusion + the backtick-pattern noise — both informative even though they don't translate to real breakage. The 184-finding number is misleading without triage; **post-triage, real Wave-1-blocking breakage is 0.**

Pre-Wave-1: no operator action required. Annotations + audit-script improvement land in cycle 9.

## Out of scope

- Fixing archive doc paths. Per cycle-2 audit, archive is non-load-bearing.
- Re-running the audit on `_archive/` after fixes. Same reason.
- Adding `<!-- audit-skip -->` markers to backtick-formatted modules. The audit-script improvement (drop backtick sweep) is cleaner.

## Cross-links

- `docs/governance/2026-05-06-doc-xref-audit-report.md` — Codex's findings inventory (cycle 8)
- `docs/governance/2026-05-05-doc-cross-link-integrity-audit.md` — Claude's cycle-7 narrower audit
- `docs/governance/2026-05-05-doc-index-audit.md` — cycle-2 archive-non-load-bearing rationale
- `docs/governance/2026-05-05-doc-index-cleanup-execution-plan.md` — archive cleanup procedure
- `scripts/doc_xref_audit.py` — Codex audit script (cycle-9 improvement queued)
- `docs/governance/wave-1-changelog-entry-prestaged.md` — cycle-9 annotation applied
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` — cycle-9 annotation applied
