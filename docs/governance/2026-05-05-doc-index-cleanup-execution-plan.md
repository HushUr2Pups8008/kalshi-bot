# Doc index cleanup execution plan

**Type:** procedural (Claude task per Implementation Contract §9 — converts the doc-index audit recommendations into commit-ready operator commands).
**Drafted:** 2026-05-05.
**Audience:** operator post-Wave-1 close (cleanup is post-soak-close territory; not blocking).
**Source:** `docs/governance/2026-05-05-doc-index-audit.md` recommendations.

## TL;DR

3 commits, ~15 min wall-clock. Executes the audit's recommended 11-file archive + closure-path-TLDR rename + INDEX.md addition.

**Does NOT execute pre-Wave-1-close.** Mid-soak archive shuffles risk gate-7 false positives if the operator commits during the soak window. Run AFTER PROFIT-PHASE2-001 closes (≥ 2026-05-08T19:01Z+).

## Pre-flight (must hold before starting)

- [ ] PROFIT-PHASE2-001 closed (`phase2-soak-closed` tag on origin)
- [ ] Wave-1 deploy not yet started OR completed and stabilised (don't run mid-Wave-1)
- [ ] All this-cycle docs landed (current cycle's commits should reach `main` first)

## Commit 1 — Closure-path TLDR rename + archive v2.2

**Goal:** consolidate `edge-004-closure-path-tldr.md` (v2.2) and `edge-004-closure-path-tldr-v3.md` so the canonical name = the latest; v2.2 archived for history.

```bash
mkdir -p docs/governance/archive

# Move v2.2 to archive (preserve as historical record)
git mv docs/governance/edge-004-closure-path-tldr.md \
       docs/governance/archive/edge-004-closure-path-tldr-v2.2-2026-05-05.md

# Rename v3 to canonical name
git mv docs/governance/edge-004-closure-path-tldr-v3.md \
       docs/governance/edge-004-closure-path-tldr.md

# Update cross-references (any file that points to "edge-004-closure-path-tldr-v3.md")
grep -rln "edge-004-closure-path-tldr-v3.md" docs/ | while read -r f; do
    sed -i '' 's|edge-004-closure-path-tldr-v3.md|edge-004-closure-path-tldr.md|g' "$f"
done

# Verify no dangling references
grep -rln "edge-004-closure-path-tldr-v3" docs/  # should return nothing
grep -rln "edge-004-closure-path-tldr.md" docs/  # should return current refs

git add docs/governance/edge-004-closure-path-tldr.md \
        docs/governance/archive/edge-004-closure-path-tldr-v2.2-2026-05-05.md \
        $(git diff --name-only)

git commit -m "$(cat <<'EOF'
docs(closure-path): consolidate tldr — v3 becomes canonical; v2.2 archived

Per docs/governance/2026-05-05-doc-index-audit.md §B recommendation:
canonical name = edge-004-closure-path-tldr.md (the current TLDR);
v2.2 preserved as archive/edge-004-closure-path-tldr-v2.2-2026-05-05.md
for history. All cross-references updated to canonical name.
EOF
)"
```

## Commit 2 — Archive 13 adversarial-review docs

**Goal:** move resolved adversarial-review artifacts from `docs/governance/` to `docs/governance/archive/adversarial-reviews/`.

```bash
mkdir -p docs/governance/archive/adversarial-reviews

# Move all 13 resolved adversarial-review files
for f in \
    docs/governance/2026-05-03-claude-commit-adversarial-review.md \
    docs/governance/2026-05-03-claude-commits-4a7cc38-fee5003-adversarial-review.md \
    docs/governance/2026-05-03-claude-commits-56d641e-9e2fffa-adversarial-review.md \
    docs/governance/2026-05-03-claude-commits-728f3bd-cfc0b60-adversarial-review.md \
    docs/governance/2026-05-03-claude-commits-c5cbc6f-90c26cf-adversarial-review.md \
    docs/governance/2026-05-03-claude-commits-cdbf6ef-f786246-adversarial-review.md \
    docs/governance/2026-05-03-claude-latest-commits-adversarial-review.md \
    docs/governance/2026-05-03-post-soak-spec-adversarial-review.md \
    docs/governance/2026-05-04-claude-commits-681ceb9-2bf3da1-adversarial-review.md \
    docs/governance/2026-05-04-claude-latest-five-commits-adversarial-review-legal-cycle.md \
    docs/governance/2026-05-05-claude-latest-six-adversarial-review.md \
    docs/governance/2026-05-05-cross-set-adversarial-review-legal-doc-cycle.md \
    docs/governance/2026-05-05-latest-5plus5-adversarial-review.md
do
    if [ -f "$f" ]; then
        git mv "$f" "docs/governance/archive/adversarial-reviews/$(basename "$f")"
    fi
done

git commit -m "$(cat <<'EOF'
docs(governance): archive 13 resolved adversarial-review artifacts

Per docs/governance/2026-05-05-doc-index-audit.md §D recommendation.
Each file represents a resolved adversarial-review pass; preserved
under docs/governance/archive/adversarial-reviews/ for audit-trail
without cluttering the operator's daily-doc surface.

No cross-references from active operator-facing docs; no dead-link
risk.
EOF
)"
```

## Commit 3 — Add governance INDEX.md

**Goal:** add `docs/governance/README.md` (or extend existing) with table-of-canonical-doc-names per category.

```bash
# Check current state of governance/README.md
ls -la docs/governance/README.md 2>&1
# If exists: review and extend; if not: create.
```

Then write `docs/governance/README.md` with:

```markdown
# docs/governance/ index

**Purpose:** quick-reference for operator-facing governance + Wave-deploy + soak-management docs.
**Last updated:** ${UTC_DATE}.

## Operator-facing live docs (read these on deploy day)

### Soak management
- `PHASE2_RUNBOOK.md` — daily monitoring runbook
- `PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` — close-day playbook
- `PROFIT-PHASE2-001-close-day-decision-flow.md` — close flowchart
- `PROFIT-PHASE2-001-early-close-criteria.md` — gate criteria + §8.5.2 invocation table
- `PROFIT-PHASE2-001-early-close-attestation-template.md` — attestation skeleton
- `PROFIT-PHASE2-002-onboarding.md` — next-soak setup

### Wave-1 deploy
- `wave-1-deploy-commit-order-decision.md` — locked commit order
- `wave-1-changelog-entry-prestaged.md` — pre-staged CHANGELOG block
- `wave-1-post-deploy-observation-plan.md` — 24h regression watch
- `2026-05-05-wave-1-deploy-day-timing.md` — UTC timing recommendations
- `post-soak-close-rehearsal-checklist.md` — Wave-1 deploy plan
- `post-soak-rollback-runbook.md` — incident response

### Wave-2 deploy
- `wave-2-deploy-commit-order-decision.md` — Wave-2 commit order
- `wave-2-wave-3-changelog-entries-prestaged.md` — pre-staged CHANGELOG (Wave-2/3)
- `2026-05-05-wave-2-deploy-day-timing.md` — Wave-2 UTC timing
- `2026-05-05-wave-2-a1plus-branch-decision-table.md` — Branch A → C → option-A sequence
- `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` — Branch C feed selection

### Wave-3 deploy + escalation
- `2026-05-05-wave-3-deploy-day-timing.md` — Wave-3 UTC timing
- `2026-05-05-branch-d-fire-procedure-runbook.md` — Branch D fire procedure

### Closure-path
- `edge-004-closure-path-tldr.md` — current closure-path TLDR
- `post-edge-004-escalation-paths.md` — Branch D companion

## Empirical evidence anchors (cited by specs)

- (selection of forensics + audit reports — preserved per §E of doc-index audit)

## Archive

- `archive/edge-004-closure-path-tldr-v2.2-2026-05-05.md` — superseded
- `archive/adversarial-reviews/` — 13 resolved adversarial-review artifacts
- (post-PHASE2-001-close: `archive/mid-soak-confirmations/` — TBD per §C)

## See also

- `docs/superpowers/specs/` — design specs (Lever A/B/C/D + EXEC-002 + GOV-003 + OBS-003/005 + MATCH-001)
- `docs/IMPLEMENTATION_CONTRACT.md` — primary architectural authority
- `docs/profit_path_debt_log.md` — unified debt tracking
```

```bash
git add docs/governance/README.md
git commit -m "$(cat <<'EOF'
docs(governance): add INDEX/README per doc-index-audit recommendation

Per docs/governance/2026-05-05-doc-index-audit.md §"Recommended cleanup
actions" #3. Provides table-of-canonical-doc-names per category for
quick operator lookup at deploy time.
EOF
)"
```

## Post-cleanup verification

```bash
# Confirm no dead links from operator-facing docs
grep -rn "edge-004-closure-path-tldr-v3" docs/  # should return nothing
grep -rn "edge-004-closure-path-tldr-v2.2" docs/governance/ | grep -v archive  # should return nothing

# Confirm archive structure
ls docs/governance/archive/
ls docs/governance/archive/adversarial-reviews/

# Confirm docs/governance/ now ~10-15 fewer files (87 → ~73-75)
ls docs/governance/*.md | wc -l
```

## Rollback if needed

If a cross-reference was missed and broke a doc:

```bash
# Revert the offending commit
git revert <sha>
git push origin main

# Investigate the missed reference, fix manually, re-attempt
```

The rollback is non-destructive — git mv preserves history; reverting moves files back to original locations.

## Follow-up (post-PHASE2-001-close, separate cycle)

After PHASE2-001 closes (≥ 2026-05-08T19:01Z+):

- Move 12 mid-soak-confirmation files to `docs/governance/archive/mid-soak-confirmations/`. Cmd: same pattern as Commit 2.
- Recommended trigger: 7 days post-close (so confirmation files have stabilised as historical record).

## Out of scope

- Per-doc content review (audit was categorical, not content).
- Spec lineage rationalization (Lever B parent + 0.04 LOCK as separate docs is the project pattern).
- ROADMAP.md updates — separate cadence.

## Cross-links

- `docs/governance/2026-05-05-doc-index-audit.md` — source recommendations
- `docs/IMPLEMENTATION_CONTRACT.md` §9 — Claude review responsibility
