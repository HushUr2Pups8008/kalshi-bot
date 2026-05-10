# MacBook import post-Wave-1-close archive procedure

**Type:** operator runbook (Claude task per Implementation Contract §9 — operator decision input).
**Trigger:** Codex's `scripts/macbook_import_disposition_audit.sh` (cycle 6) returned `keep_through_wave1_close_then_archive`.
**Drafted:** 2026-05-05.
**Audience:** operator post-Wave-1 commit-6 + 48h watch clean (≈ 2026-05-18+).
**Companion:** `scripts/macbook_import_disposition_audit.sh`; `mac_archive/macbook_2026-05-01_import/` directory.

## TL;DR

After Wave-1 commit-6 + 48h watch passes clean: compress the 149MB `mac_archive/macbook_2026-05-01_import/` (47 files) into a single `.tar.gz` and remove the directory. Codex's audit script approves this disposition. Compressed tarball remains in `mac_archive/` for post-hoc forensic access.

**Don't do this pre-Wave-1-close.** The MacBook import contains the only on-disk record of the 13-day MacBook paper soak (260 OPPORTUNITY / 17 SKIPPED / 3 PAPER_TRADE on `KXFISAEXTEND` series via VitalLaw.com). If anything in Wave-1 deploy needs that data for post-hoc comparison, the directory needs to be readable, not archived.

## Why archive after Wave-1 close

1. **Wave-1's per-commit observation plan compares to current Mac Studio state**, not MacBook archive. The MacBook data informs strategic framing (per `README.md` Status block + `profit_path_debt_log.md` PROFIT-CUTOVER-001 entry), not deploy-time decision-making.
2. **Wave-2 Branch C selection rubric** uses Codex's `docs/_archive/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` audit (ARCHIVED Stream G R6) which itself reads the MacBook archive at audit time. Branch C operator decisions are made by reading the cycle-3 selection rubric (`docs/_archive/governance/2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` ARCHIVED Stream G R37), not the raw MacBook archive.
3. **149MB on local disk** isn't a space crisis but is non-zero overhead. Compressed `.tar.gz` should be 30-60MB.

## Procedure (post-Wave-1 commit 6 + 48h watch clean)

### 1. Verify the precondition

```bash
cd ~/vscode/kalshi-bot
# Confirm Wave-1 commit 6 (Lever A.1) deployed
git log --oneline | grep "Lever A.1"
# Confirm 48h has elapsed since commit 6 deploy
git log --pretty="%ai %s" | grep "Lever A.1" | head -1
date -u +%Y-%m-%dT%H:%M:%SZ
# Calculate: now - commit-6-deploy-time ≥ 48h
# Confirm post-deploy smoke clean
bash scripts/wave1_post_deploy_smoke.sh   # exit 0 = clean
```

If any precondition fails: defer archiving. Don't proceed.

### 2. Verify mac_archive/macbook_2026-05-01_import/ contents

```bash
du -sh mac_archive/macbook_2026-05-01_import/
# Expected: ~149M
ls mac_archive/macbook_2026-05-01_import/ | head
# Expected: logs/ subdir + maybe other items
find mac_archive/macbook_2026-05-01_import/ -type f | wc -l
# Expected: ~47 files per Codex's audit
```

### 3. Compress + verify

```bash
cd mac_archive/

# Create tarball with -z (gzip) and absolute integrity-check
tar czf macbook_2026-05-01_import.tar.gz macbook_2026-05-01_import/

# Verify the tarball
tar tzf macbook_2026-05-01_import.tar.gz | wc -l
# Expected: ≥ 47 entries

ls -lh macbook_2026-05-01_import.tar.gz
# Expected: 30-60M compressed

# Sanity-extract to a temp dir and compare file count
mkdir -p /tmp/macbook_archive_verify
tar xzf macbook_2026-05-01_import.tar.gz -C /tmp/macbook_archive_verify
diff -rq macbook_2026-05-01_import/ /tmp/macbook_archive_verify/macbook_2026-05-01_import/
# Expected: no output = byte-identical extraction

rm -rf /tmp/macbook_archive_verify
```

### 4. Remove the original directory (only after step 3 verified)

```bash
rm -rf macbook_2026-05-01_import/
ls mac_archive/
# Expected: macbook_2026-05-01_import.tar.gz remains; macbook_2026-05-01_import/ gone
```

### 5. Update .gitignore + verify gitignored

```bash
# .gitignore already has mac_archive/macbook_2026-05-01_import/
# Add the .tar.gz entry too (it's not in git; just being explicit)
echo "mac_archive/macbook_2026-05-01_import.tar.gz" >> .gitignore   # if not present
git status
# Expected: .gitignore modified (only); no other changes
```

### 6. Document in operator log

Append to `docs/profit_path_debt_log.md` PROFIT-CUTOVER-001 entry:

```markdown
##### MacBook import archive — ${UTC_DATE}

Per `2026-05-05-macbook-import-archive-procedure.md`. Post-Wave-1
commit-6 + 48h watch clean precondition met at ${precondition_UTC}.

- Original: `mac_archive/macbook_2026-05-01_import/` (149M / 47 files)
- Compressed: `mac_archive/macbook_2026-05-01_import.tar.gz` (XX M)
- Verification: byte-identical extraction confirmed
- Original directory removed: ${UTC_DATE}

Forensic access path: `tar xzf mac_archive/macbook_2026-05-01_import.tar.gz`.
```

### 7. Commit (.gitignore only)

```bash
git add .gitignore
git commit -m "$(cat <<'EOF'
chore(mac_archive): MacBook import archived post-Wave-1 close

Per docs/governance/2026-05-05-macbook-import-archive-procedure.md
trigger conditions met (Wave-1 commit-6 + 48h watch clean).

Original 149M / 47-file directory mac_archive/macbook_2026-05-01_import/
compressed to mac_archive/macbook_2026-05-01_import.tar.gz; original
directory removed. Compressed tarball stays gitignored on local disk
for post-hoc forensic access.

Operator log entry in profit_path_debt_log.md PROFIT-CUTOVER-001.
EOF
)"
```

## Rollback (if archive operation went wrong)

```bash
cd mac_archive/
tar xzf macbook_2026-05-01_import.tar.gz
# Original directory tree restored
```

Tarball preservation = no irreversible step.

## What NOT to do

- **DON'T archive pre-Wave-1-close.** MacBook archive may need ad-hoc reads during Day-7 close attestation OR Wave-1 commit deploys (e.g., operator wants to compare current trade-log shape against MacBook trade-log shape).
- **DON'T delete the .tar.gz to "clean up."** ~30-60MB is the durable cutover-state record. Operator's audit-trail.
- **DON'T move the .tar.gz into git.** Binary artifact; gitignored stays gitignored. The audit-trail is the operator-log entry, not the file in git.
- **DON'T skip step 3 verification.** Tarball integrity is critical; lost files in archive are silent data loss.

## Cross-links

- `scripts/macbook_import_disposition_audit.sh` — Codex cycle 6; recommended this procedure
- `mac_archive/macbook_2026-05-01_import/` — target directory (will become .tar.gz)
- `docs/profit_path_debt_log.md` PROFIT-CUTOVER-001 entry — receives the archive log
- `README.md` Status (2026-05-01) — contextualizes the MacBook → Mac Studio cutover
- `2026-05-05-wave-1-commit-by-commit-acceptance-matrix.md` — precondition source
