# 2026-05-06 doc xref audit report

**Scope:** full `docs/` tree.
**Commands:**

```bash
.venv/bin/python scripts/doc_xref_audit.py
```

Plus a backtick-style reference sweep over Markdown code spans that look like
repo-local paths.

## Results

| audit | checked | broken |
|---|---:|---:|
| Markdown links (`[text](path)`) | full `docs/` tree | 63 |
| Backtick-style repo-local refs | 187 Markdown files | 121 |

## Notable Markdown link failures

- `docs/governance/wave-1-changelog-entry-prestaged.md:35` -> `docs/governance/post-soak-close-rehearsal-checklist.md`
- `docs/governance/wave-1-changelog-entry-prestaged.md:44` -> `docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md`
- `docs/governance/wave-1-changelog-entry-prestaged.md:74` -> `docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md`
- `docs/governance/wave-1-changelog-entry-prestaged.md:79` -> `docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md`
- `docs/governance/wave-1-changelog-entry-prestaged.md:90` -> `docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md`
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md:136` -> `docs/governance/post-edge-004-escalation-paths.md`

## Notable backtick-style failures

- `docs/governance/wave-1-changelog-entry-prestaged.md:107` -> `docs/governance/2026-05-07-day-7-pending-mid-soak-confirmation.md`
- `docs/governance/2026-05-05-launchd-plist-drift-audit.md:34` -> `scripts/launchd/com.kalshi.db-backup.plist`
- `docs/governance/2026-05-05-launchd-plist-drift-audit.md:38` -> `scripts/launchd/com.kalshi.db-backup.plist`
- `docs/profit_path_debt_log.md:2254` -> `scripts/launchd/com.kalshi.db-backup.plist`
- `docs/profit_path_debt_log.md:2255` -> `scripts/launchd/README.md`
- `docs/profit_path_debt_log.md:3364` -> `tests/test_paper_trader_async.py`

## Interpretation

The script audit is useful but strict: links written from `docs/governance/`
with repo-root prefixes such as `docs/governance/...` are reported dead because
the current resolver treats them as relative to the source file. Backtick audit
also flags intentional globs and archived log patterns. Treat the list as a
triage queue, not a direct auto-fix list.
