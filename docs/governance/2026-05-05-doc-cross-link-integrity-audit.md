# Documentation cross-link integrity audit

**Type:** read-only review (Claude task per Implementation Contract §9 — review).
**Source:** auto-discovery via `.venv/bin/python` regex over `docs/**/*.md` (excluding `_archive/`); `[text](*.md)` link extraction + filesystem existence check.
**Drafted:** 2026-05-05.
**Audience:** operator considering pre-Wave-1 doc-link hygiene pass.

## TL;DR

**39 cross-doc links scanned across 100+ governance + spec docs.** **2 broken refs identified, both in `wave-1-changelog-entry-prestaged.md`. 1 LOW; 1 MEDIUM.** Recommend single-commit fix (~5 min wall-clock) before Wave-1 commit-1 deploys, since the changelog block is operator-paste material at fire-time.

## Findings

### F1 (MEDIUM) — `wave-1-changelog-entry-prestaged.md` references `2026-05-03-obs-005-cooldown-sentinel-DEFAULT-fix-design.md`

**Broken ref:** `docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-default-fix-design.md`

**Reality at HEAD:** the file is named `2026-05-03-obs-005-cooldown-sentinel-fix-design.md` (no `-default-`). The prestaged CHANGELOG entry includes "default-" in the URL.

**Impact:** when operator pastes the prestaged Wave-1 CHANGELOG block at deploy time, the GitHub-rendered link to OBS-005 spec will 404. Cosmetic in `CHANGELOG.md`, but the changelog block is operator-facing audit trail.

**Recommended fix (1-line edit to `wave-1-changelog-entry-prestaged.md`):** remove `-default-` from the URL.

```diff
- [`docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-default-fix-design.md`]
+ [`docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md`]
```

**Severity MEDIUM** because operator deploy-day paste-from-prestaged operation will produce a broken link in shipped CHANGELOG; not blocking deploy but visible.

### F2 (LOW) — `wave-1-changelog-entry-prestaged.md` references `2026-05-07-day-7-pending-mid-soak-confirmation.md` (file doesn't exist yet)

**Broken ref:** `docs/governance/2026-05-07-day-7-pending-mid-soak-confirmation.md`

**Reality at HEAD:** no file with that name. Convention-matching files exist:
- `2026-05-03-day-3-pending-mid-soak-confirmation.md` (Day 3)
- `2026-05-03-day-4-pending-mid-soak-confirmation*.md` (Day 4)
- `2026-05-04-day-4-mid-soak-confirmation.md` (Day 4 actual)

The Day-7 mid-soak confirmation hasn't been authored. Per the prestaged CHANGELOG note: "filled at fire-time" — the Day-7 file is meant to be created at fire-time, not pre-staged.

**Impact:** at fire-time on 2026-05-08, operator will create the Day-7 confirmation file and it'll resolve. Pre-fire-time the link is broken.

**Recommended fix:** add a note to the prestaged CHANGELOG block clarifying the Day-7 file is fire-time-created. **OR** remove the ref entirely from the prestaged block; operator can hand-add at fire-time. Trivial.

**Severity LOW** because the broken link only manifests pre-fire-time; resolves automatically when Day-7 mid-soak confirmation lands at 2026-05-08T19:01Z+.

## Confirmed clean

- **All `docs/governance/*.md` ↔ `docs/governance/*.md` cross-references resolve** (this is the bulk of operator-facing playbook + runbook + decision-table cross-linking)
- **All `docs/governance/*.md` ↔ `docs/superpowers/specs/*.md` cross-references resolve** (except F1 above)
- **`docs/governance/edge-004-closure-path-tldr-v3.md`** — 8 cross-references to 0.04 LOCK / Lever C v1 LOCK / Branch D / sizing-scope specs — all resolve
- **`docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md`** — references PROFIT-LLM-001 + P4-GATE Appendix A sizing-scope specs — all resolve (ARCHIVED Stream G R35)
- **`docs/_archive/specs/2026-05-03-edge-004-lever-menu-design.md` §5.2 + §5.1** — references LOCK addenda + closure-path TLDR — all resolve (ARCHIVED Stream G R31)
- **`docs/profit_path_debt_log.md`** — extensive cycle integration entries; all internal cross-refs scanned + clean

## Audit method

```python
import os, re
from collections import defaultdict
docs_root = 'docs'
broken = defaultdict(list)
total = 0
for root, dirs, files in os.walk(docs_root):
    if '_archive' in root: continue
    for f in files:
        if not f.endswith('.md'): continue
        path = os.path.join(root, f)
        content = open(path).read()
        for m in re.finditer(r'\[[^\]]+\]\((?P<p>[^)\s#]+\.md)(?:#[^)]*)?\)', content):
            ref = m.group('p')
            total += 1
            if ref.startswith('http'): continue
            base = os.path.dirname(path)
            target = os.path.normpath(os.path.join(base, ref))
            if not os.path.exists(target) and not os.path.exists(ref):
                broken[path].append(ref)
```

**Limitations:**
- Audits only `[text](path)` markdown link form; doesn't catch backtick paths (`docs/...md`) without link wrapping. Backtick paths are less navigable but more common in this codebase.
- Doesn't audit `_archive/` (intentionally excluded; archive content is historical record).
- Doesn't audit anchor refs (`file.md#section`); section anchors aren't validated.
- 39 link count is artifact of the link-form filter; backtick refs are not counted.

For backtick refs, Codex's `scripts/doc_xref_audit.py` (cycle 7 task) covers a broader audit surface.

## Recommended single-commit fix

```bash
# F1 fix
sed -i '' 's|2026-05-03-obs-005-cooldown-sentinel-default-fix-design.md|2026-05-03-obs-005-cooldown-sentinel-fix-design.md|g' \
    docs/governance/wave-1-changelog-entry-prestaged.md

# F2 fix — add note OR remove the link (operator-discretion)
# Recommend: add note "(file created at Day-7 mid-soak confirmation fire-time)"

# Verify clean
.venv/bin/python -c "..."  # rerun the audit script
```

**Estimated wall-clock: 5 min.** Land pre-Wave-1 deploy so the prestaged CHANGELOG is paste-clean.

## Out of scope

- Backtick-style path refs (Codex `doc_xref_audit.py` covers).
- `docs/_archive/*.md` — historical record; not navigated.
- External links (http/https URLs).
- Anchor refs (`file.md#section`).
- Image refs.

## Cross-links

- `docs/governance/wave-1-changelog-entry-prestaged.md` — F1 + F2 fix target
- `scripts/doc_xref_audit.py` — Codex cycle 7 task (broader audit surface)
- `docs/governance/2026-05-05-doc-index-audit.md` — cycle 2 sibling (categorical inventory)
