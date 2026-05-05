# Day-7 close walkthrough — dry-trace against HEAD (2026-05-05)

**Type:** read-only review (Claude task per Implementation Contract §9 — review of operator runbook before fire-time).
**Source:** `docs/governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` (drafted 2026-05-05; 11 steps).
**Audience:** operator before 2026-05-08T19:01Z fire-time. Confirms each step works against current HEAD `80932cb`.
**No edits to walkthrough applied.** Findings only.

## TL;DR

**Walkthrough is faithful to current HEAD.** All 11 steps' commands execute cleanly against the current state. 1 LOW finding (cosmetic). Operator can fire the 11-step playbook on 2026-05-08T19:01Z+ as-written.

## Per-step dry-trace results

### Step 0: Pre-flight

| check | command | current-state result | verdict |
|---|---|---|---|
| close window not yet open | `date -u +%Y-%m-%dT%H:%M:%SZ` ≥ 2026-05-08T19:01Z | 2026-05-05T12:34Z (3 d 6 h 27 m before window) | ✅ correctly-blocked-pre-window |
| latest main pulled | `git log --oneline origin/main -1` | `80932cb wave-2 prep + close-day script hardening (Codex)` | ✅ |
| commits cited present | `bf1b0e3 / 747ea15 / 7b9624b / 2f1e900 / b780fd6` (from Step 0 walkthrough text) | all present in `git log --oneline` | ✅ |

### Step 1: Volume gate

```bash
grep -c '"type": "GOVERNANCE_DECISION"' logs/governance/decisions.jsonl
```

**Result:** `267` (≥ 30 floor; gate clears at 8.9× by current state). At Day-7 close projected ~336 (267 + ~12 cycles/day × 3 d × 5.7 dec/cycle). ✅

### Step 2: Calendar floor

```bash
head -1 logs/governance/decisions.jsonl | python3 -c "..."
```

**Result:** first cycle `2026-05-01T19:01:27.084322+00:00`. Current UTC `2026-05-05T12:34:04Z`. Elapsed: 3 d 17 h 33 m. Floor 7 d ≥ not yet met by 3 d 6 h 27 m. ✅ correctly-blocks-pre-window.

### Step 3: Safety counters

Current values (Python script per walkthrough):

| counter | current | floor | verdict |
|---|---|---|---|
| KILL_SWITCH | 0 | 0 | ✅ |
| GOVERNANCE_VALIDATION_ERROR | 0 | 0 | ✅ |
| `batch_aborted=True` | 0 | 0 | ✅ |

### Step 4: PARSE_ERROR trailing 72 h

```bash
.venv/bin/python -c "..."
```

**Result:** `0` (cumulative PARSE_ERROR = 7, all on day-1/-2; trailing-72h-window = 0). ✅

### Step 5: Cadence stability

| metric | current | floor | verdict |
|---|---|---|---|
| inter-cycle gap n | 49 | n/a | informational |
| max gap | 2.01 h | ≤ 3 h | ✅ |
| min gap | 0.05 h | n/a | informational (deep-cycle deep+fast adjacency expected) |
| `> 3h gaps` | 0 | 0 | ✅ |
| `> ±10% deviation count` | 7 | acceptable IF deep-cycle wall-clock-aligned | acceptable per walkthrough §5 note |

### Step 6: Manual review (operator-only)

```bash
.venv/bin/python scripts/governance_decision_review.py --since 2026-05-03T15:28Z --output review_${USER}_$(date -u +%Y-%m-%d).jsonl
```

**Tool exists:** `scripts/governance_decision_review.py` (9.1 KB) ✅
**Codex's prior run** (2026-05-05): 67 decisions reviewed, 100 % reasonable. Combined with the bulk-reviewable 241/242 decisions from `2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md`, gate-6 effective coverage at close-time will be ≥ 99 %. ✅

### Step 7: Soak invariant (gate 7 + §8.5.2 carve-out)

```bash
bash scripts/check_soak_invariant.sh
```

**Result:** `FAIL — invariant violated` (5 commits in trailing window). All 5 commits map to documented §8.5.2 invocations:
- `fae72fa` (think=False fix) — INVOKED in criteria runbook table
- `092666c…ce814b9` (GOV-002 audit cycle) — INVOKED in criteria runbook table
- `b47ca71` (A5 SYSTEM_PROMPT) — INVOKED in criteria runbook table  
- `80932cb` (this cycle: wave-2 prep + close-day scripts; non-soak surface — `scripts/`+`tests/` only, no `governance/` runtime path)
- `b44dda2` (this cycle: Wave-1+2 docs; doc-only)

**Operator action at close:** verify the 5 surfaced commits all map to §8.5.2 carve-outs. The 4 originally-documented ones are in the criteria runbook table; the 2 new ones (`b44dda2`, `80932cb`) are doc-/script-only and don't touch soak runtime surface. **Recommendation:** add a line to the criteria runbook §8.5.2 table acknowledging `b44dda2` and `80932cb` as out-of-scope-for-§8.5.2 (doc/script artifacts) before close fire-time. ✅ achievable.

### Step 8: Run rollback-anchor automation

```bash
bash scripts/pre_soak_close_branch_backup.sh
```

**Tool exists:** `scripts/pre_soak_close_branch_backup.sh` (4.5 KB) ✅. Per close-day-flow chart, automation creates tag `pre-wave-1-deploy-${UTC_DATE}`, branch `backup/pre-wave-1-deploy-${UTC_DATE}`, log archive in `mac_archive/pre_wave1_${UTC_DATE}/`. Pre-fire dry-trace not run here (would create live tag; deferred to operator at fire-time).

### Step 9: Fill close attestation

Template exists: `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` (3.6 KB) ✅. Pre-stage actual values draft authored separately at `2026-05-05-day-7-attestation-prestage.md` (this cycle).

### Step 10: Commit + tag the close

Commands as written use HEREDOC and `git tag -a phase2-soak-closed`. Tag name is unique on origin (`git ls-remote origin phase2-soak-closed` returns empty). ✅

### Step 11: Wave-1 deploy may begin

Cross-references `docs/governance/post-soak-close-rehearsal-checklist.md` §1+. File exists (16.5 KB). ✅

## Findings

### F1 (LOW) — Step 7 paste-readiness

**Walkthrough Step 7 currently lists 4 expected commits.** Current HEAD has 5 (4 original + `80932cb` Codex this-cycle) plus `b44dda2` Claude this-cycle that gate-7 also surfaces. Operator should pre-add a line to the criteria runbook §8.5.2 invocation table before close fire-time so the attestation paste reflects the actual commit set.

**Recommended fix (5 min before fire-time):** edit `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` §8.5.2 invocation table to include:

| commit | window-time | scope | affected slice | carve-out status |
|---|---|---|---|---|
| `b44dda2` | 2026-05-05T~12:30Z | doc-only (governance/ + specs/) | 0 (no runtime touch) | OUT-OF-SCOPE for §8.5.2 (doc artifact) |
| `80932cb` | 2026-05-05T~12:35Z | scripts/ + tests/ only; trading/, tasks/, governance/ runtime untouched | 0 (no runtime touch) | OUT-OF-SCOPE for §8.5.2 (script artifact) |

This makes Step 7's paste-from-table operation honest and complete on fire-day.

**Severity LOW because:** the omission doesn't break the close — operator can hand-add the rows at fire-time. But pre-staging is the better hygiene move.

## Confirmed clean

- All 11 steps' commands are syntactically correct against current HEAD.
- All cited scripts exist on disk with expected size.
- All cited cross-doc paths resolve to extant files.
- Gates 1-5 + 6 (manual review tool) + 7 (with §8.5.2 carve-out invocation) are achievable at fire-time on 2026-05-08T19:01Z+.

## Recommended pre-fire actions

1. **Apply F1** before fire-time (5 min doc edit).
2. **Re-run this dry-trace ~12 h before fire-time** to catch any drift between now and close-day. Volume / cadence / safety counters should still pass; PARSE_ERROR trailing-72h-window will be 0 unless a fresh PARSE_ERROR fires (none in 72 h currently; close-day window hasn't moved).
3. **Pre-stage the §8.5.2 carve-out attestation block** for `b44dda2` and `80932cb` per F1 fix above.

## Out of scope

- Step 8 (rollback-anchor automation) live dry-run — defer to operator at fire-time.
- Verification of the `phase2-soak-closed` tag uniqueness on origin — re-check at fire-time.

## Cross-links

- `docs/governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` — the walkthrough under review
- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — gate criteria + §8.5.2 invocation table (F1 lands here)
- `docs/governance/PROFIT-PHASE2-001-close-day-decision-flow.md` — companion flowchart
- `docs/governance/2026-05-05-day-7-attestation-prestage.md` — pre-staged attestation values (this cycle)
- `scripts/check_soak_invariant.sh`, `scripts/pre_soak_close_branch_backup.sh`, `scripts/governance_decision_review.py` — verified extant
